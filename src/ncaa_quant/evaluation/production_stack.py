"""Production stack adapters for the walk-forward harness (Task 22B).

Composition layer only — no modeling logic of its own. Wires
``features/`` + ``ratings/`` + ``models/`` into the
:class:`~ncaa_quant.evaluation.walkforward.FeatureProvider`,
:class:`~ncaa_quant.evaluation.walkforward.RatingEngine`, and
:class:`~ncaa_quant.evaluation.walkforward.Predictor` protocols.

**A1.** ``preseason_priors='league_mean'`` replaces BOTH prior mean and prior
variance with league-pooled values (via
:func:`~ncaa_quant.ratings.priors.league_mean_preseason_states`).

**A2 scope.** ``rating_updates='frozen_after_week_1'`` freezes Stage-1 rating
state after Week 1. Season-to-date efficiency features, mapping-layer
retrains, and market features keep updating. A2 is therefore a lower bound on
total in-season learning gain.

Feature-signature contracts from Task 17 are enforced at this adapter boundary
(:func:`assert_feature_signature`); a mismatch raises and never silently
realigns columns.

Market features (no dedicated builder module yet) are composed from the
harness line ladder / CFBD frames with an explicit ``market_provenance``
column for ablation A6.
"""

from __future__ import annotations

import contextlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Literal

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
from scipy import stats as scipy_stats  # type: ignore[import-untyped]

from ncaa_quant.distribution.bivariate import (
    assemble_bivariate,
    estimate_rho,
    residuals_from_predictions,
)
from ncaa_quant.distribution.key_numbers import (
    ConditionalKeyNumberKernel,
    KeyNumberKernel,
    fit_key_number_kernel,
)
from ncaa_quant.distribution.simulate import (
    DEFAULT_N_DRAWS,
    default_epistemic_draws,
    mix_epistemic_predictions,
    moneyline_probs,
    sample_joint,
    spread_cover_probs,
    total_probs,
    two_way_side_prob,
)
from ncaa_quant.evaluation.walkforward import (
    WalkForwardConfig,
    WalkForwardError,
    resolve_lines_for_games,
)
from ncaa_quant.models.conformal import CQRResult, conformalize_intervals, fit_cqr
from ncaa_quant.models.ensemble import (
    EnsembleError,
    FittedEnsemble,
    NNLSStackResult,
    attach_stage1_mixture_variance,
    ensemble_sigma,
    fit_nnls_stack,
    single_lgbm_stack,
)
from ncaa_quant.models.heads.base import FeatureSignature, FeatureSignatureError, HeadTrainConfig
from ncaa_quant.models.heads.elasticnet import ElasticNetMuHead
from ncaa_quant.models.heads.margin import LightGBMMuHead
from ncaa_quant.models.heads.quantile import QUANTILES, LightGBMQuantileHead, quantile_column
from ncaa_quant.models.heads.sigma import LightGBMSigmaHead, abs_residual_labels
from ncaa_quant.models.pit_calibration import (
    DistributionalCalibrationBundle,
    DistributionTarget,
    PitCalibrationError,
    PitRecalibrator,
    fit_pit_recalibrator,
    gate_pit_recalibrator,
    pit_values_normal,
)
from ncaa_quant.ratings.priors import (
    PriorConfig,
    build_preseason_states,
    league_mean_preseason_states,
)
from ncaa_quant.ratings.state_space import (
    GaussianState,
    StateSpaceConfig,
    build_game_observations_from_advanced,
    build_game_observations_from_plays,
    initial_team_state,
    run_filter,
)
from ncaa_quant.utils.seeding import set_global_seed
from ncaa_quant.utils.timeutils import assert_tz_aware, to_utc

StackKind = Literal["fundamental", "market_aware"]

MARKET_FEATURE_COLS: tuple[str, ...] = (
    "mkt_spread",
    "mkt_total",
    "mkt_n_books",
    "mkt_is_missing",
    "market_provenance",
)

RATING_FEATURE_DIMS: tuple[str, ...] = ("off_epa", "def_epa", "st_value", "pace")


class ProductionStackError(WalkForwardError):
    """Invalid production-stack construction or feature contract."""


def assert_feature_signature(
    features: pd.DataFrame,
    expected: FeatureSignature | Sequence[str],
) -> None:
    """Raise :class:`FeatureSignatureError` naming offending columns on mismatch.

    Enforced at the adapter boundary — never silently reorders or drops columns.
    """
    names = list(expected.names) if isinstance(expected, FeatureSignature) else list(expected)
    meta = {"game_id", "game_key", "season", "week", "as_of", "event_time"}
    present = [c for c in features.columns if c not in meta]
    missing = [n for n in names if n not in features.columns]
    extra = [c for c in present if c not in names]
    if missing or extra:
        msg = (
            "feature signature mismatch at production adapter: "
            f"missing={missing}, unexpected={extra}"
        )
        raise FeatureSignatureError(msg)


# ---------------------------------------------------------------------------
# Rating engine
# ---------------------------------------------------------------------------


@dataclass
class StateSpaceRatingEngine:
    """Stage-1 rating engine wrapping :func:`run_filter` with prior injection.

    Implements A1 (prior mode) and A2 (freeze after Week 1) by configuration.
    """

    observations: pd.DataFrame
    config: WalkForwardConfig
    ss_config: StateSpaceConfig = field(default_factory=StateSpaceConfig)
    prior_config: PriorConfig = field(default_factory=PriorConfig)
    priors_frame: pd.DataFrame | None = None
    fbs_team_ids: set[Any] | None = None
    _states: dict[str, GaussianState] = field(default_factory=dict, init=False, repr=False)
    _week1_freeze: dict[str, GaussianState] | None = field(default=None, init=False, repr=False)
    _week1_snapshot: dict[str, Any] | None = field(default=None, init=False, repr=False)
    _current_season: int | None = field(default=None, init=False, repr=False)

    def initialize_season(self, season: int, as_of: datetime) -> None:
        assert_tz_aware(as_of)
        self._current_season = int(season)
        self._week1_freeze = None
        self._week1_snapshot = None
        team_ids = self._team_ids_for_season(season)
        states = self._priors_for_season(season, team_ids)
        self._states = {str(k): v for k, v in states.items()}

        if self.observations.empty:
            return
        obs = self.observations.copy()
        obs["event_time"] = [to_utc(pd.Timestamp(ts).to_pydatetime()) for ts in obs["event_time"]]
        hist = obs.loc[obs["event_time"] < to_utc(as_of)]
        if hist.empty:
            return
        preseason: dict[int, dict[Any, GaussianState]] = {}
        for s in sorted({int(x) for x in hist["season"].unique()} | {int(season)}):
            preseason[s] = self._priors_for_season(s, self._team_ids_for_season(s))
        result = run_filter(
            hist,
            config=self.ss_config,
            fbs_team_ids=self.fbs_team_ids,
            preseason_states=preseason,
            record_weekly=True,
        )
        self._ingest_history(result.history)

    def update_after_games(self, games: pd.DataFrame) -> None:
        if games.empty:
            return
        week = int(games["week"].iloc[0]) if "week" in games.columns else 0
        season = (
            int(games["season"].iloc[0])
            if "season" in games.columns
            else (self._current_season or 0)
        )

        if self.config.rating_updates == "frozen_after_week_1" and self._week1_freeze is not None:
            self._states = dict(self._week1_freeze)
            return

        if self.observations.empty:
            self._maybe_freeze_week1(week)
            return

        gids = {int(g) for g in games["game_id"]}
        obs = self.observations.loc[self.observations["game_id"].isin(gids)].copy()
        if obs.empty:
            self._maybe_freeze_week1(week)
            return

        preseason = {
            int(season): {
                (int(k) if str(k).lstrip("-").isdigit() else k): v for k, v in self._states.items()
            }
        }
        result = run_filter(
            obs,
            config=self.ss_config,
            fbs_team_ids=self.fbs_team_ids,
            preseason_states=preseason,
            record_weekly=False,
        )
        self._ingest_history(result.history)
        self._maybe_freeze_week1(week)

    def state_snapshot(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for tid, state in sorted(self._states.items()):
            for i, dim in enumerate(self.ss_config.state_dims):
                out[f"{tid}:{dim}"] = float(state.mean[i])
                out[f"{tid}:sd_{dim}"] = float(np.sqrt(max(float(state.cov[i, i]), 0.0)))
        return out

    def week1_state_snapshot(self) -> dict[str, Any] | None:
        """Frozen Week-1 snapshot for A2 mechanism assertions."""
        return None if self._week1_snapshot is None else dict(self._week1_snapshot)

    def _maybe_freeze_week1(self, week: int) -> None:
        if week <= 1 and self._week1_freeze is None:
            self._week1_freeze = dict(self._states)
            self._week1_snapshot = self.state_snapshot()

    def _priors_for_season(
        self,
        season: int,
        team_ids: Sequence[int],
    ) -> dict[int, GaussianState]:
        if self.config.preseason_priors == "league_mean":
            return league_mean_preseason_states(
                team_ids,
                config=self.ss_config,
                prior_config=self.prior_config,
            )
        if self.priors_frame is not None and not self.priors_frame.empty:
            states = build_preseason_states(
                self.priors_frame,
                season=season,
                config=self.ss_config,
                prior_config=self.prior_config,
            )
            for tid in team_ids:
                if int(tid) not in states:
                    # Missing prior inputs: widen variance (never false confidence).
                    base = initial_team_state(self.ss_config)
                    cov = np.asarray(base.cov, dtype=float).copy()
                    cov = cov + np.eye(cov.shape[0]) * self.prior_config.missing_var_penalty
                    states[int(tid)] = GaussianState(mean=base.mean, cov=cov)
            return states
        return {int(tid): initial_team_state(self.ss_config) for tid in team_ids}

    def _ingest_history(self, history: pd.DataFrame) -> None:
        if history.empty:
            return
        work = history
        if "kind" in history.columns:
            post = history.loc[history["kind"] == "postgame"]
            if not post.empty:
                work = post
        latest = work.sort_values("event_time").groupby("team_id", sort=False).tail(1)
        for r in latest.itertuples(index=False):
            mean = np.array(
                [float(getattr(r, d)) for d in self.ss_config.state_dims],
                dtype=float,
            )
            if hasattr(r, "cov") and r.cov is not None:
                cov = np.asarray(r.cov, dtype=float)
            else:
                cov = np.eye(self.ss_config.n_dims, dtype=float)
                for i, dim in enumerate(self.ss_config.state_dims):
                    sd = getattr(r, f"sd_{dim}", None)
                    if sd is not None and pd.notna(sd):
                        cov[i, i] = float(sd) ** 2
            self._states[str(r.team_id)] = GaussianState(mean=mean, cov=cov)

    def _team_ids_for_season(self, season: int) -> list[int]:
        if self.observations.empty:
            return []
        sub = self.observations.loc[self.observations["season"] == int(season)]
        if sub.empty:
            return []
        ids = set(int(x) for x in sub["home_team_id"]) | set(int(x) for x in sub["away_team_id"])
        return sorted(ids)


# ---------------------------------------------------------------------------
# Feature provider
# ---------------------------------------------------------------------------


@dataclass
class ProductionFeatureProvider:
    """As-of game features from rating state + optional market columns."""

    config: WalkForwardConfig
    snapshots: pd.DataFrame | None = None
    cfbd_lines: pd.DataFrame | None = None
    _expected_feature_names: tuple[str, ...] | None = field(default=None, init=False, repr=False)
    _n_plays_with_gt: int = field(default=0, init=False, repr=False)
    _n_plays_without_gt: int = field(default=0, init=False, repr=False)

    def set_play_counts(self, *, with_gt_filter: int, without_gt_filter: int) -> None:
        """Record play counts for A5 mechanism assertions."""
        self._n_plays_with_gt = int(with_gt_filter)
        self._n_plays_without_gt = int(without_gt_filter)

    @property
    def play_count_entering_efficiency(self) -> int:
        """Plays entering efficiency builders under the current A5 setting."""
        if self.config.garbage_time_filter:
            return self._n_plays_with_gt
        return self._n_plays_without_gt

    def compute_game_features(
        self,
        games: pd.DataFrame,
        as_of: datetime,
        *,
        rating_state: Mapping[str, Any],
        market_features: bool,
    ) -> pd.DataFrame:
        assert_tz_aware(as_of)
        rows: list[dict[str, Any]] = []
        for g in games.itertuples(index=False):
            hid = int(g.home_team_id)
            aid = int(g.away_team_id)
            row: dict[str, Any] = {"game_id": int(g.game_id)}
            for dim in RATING_FEATURE_DIMS:
                h = float(rating_state.get(f"{hid}:{dim}", 0.0))
                a = float(rating_state.get(f"{aid}:{dim}", 0.0))
                row[f"home_{dim}"] = h
                row[f"away_{dim}"] = a
                row[f"{dim}_diff"] = h - a
                if dim in {"off_epa", "def_epa"}:
                    row[f"rating_diff_{dim}"] = h - a
            row["rating_uncertainty"] = float(rating_state.get(f"{hid}:sd_off_epa", 1.0)) + float(
                rating_state.get(f"{aid}:sd_off_epa", 1.0)
            )
            rows.append(row)

        frame = pd.DataFrame(rows)
        if frame.empty:
            return frame

        if market_features:
            mkt = self._resolve_market_lines(games, as_of)
            frame = frame.merge(mkt, on="game_id", how="left")
            for col in MARKET_FEATURE_COLS:
                if col not in frame.columns:
                    frame[col] = float("nan") if col != "market_provenance" else "null"
        # else: omit market columns entirely (A3)

        names = tuple(c for c in frame.columns if c != "game_id")
        if self._expected_feature_names is None:
            self._expected_feature_names = names
        else:
            assert_feature_signature(frame, self._expected_feature_names)
        return frame

    def _resolve_market_lines(self, games: pd.DataFrame, as_of: datetime) -> pd.DataFrame:
        source = self.config.market_feature_source
        out_rows: list[dict[str, Any]] = []
        if source == "cfbd_open_close":
            for gid in games["game_id"].astype(int):
                cfbd = _resolve_cfbd_only_line(int(gid), self.cfbd_lines)
                missing = 1.0 if (np.isnan(cfbd["spread"]) and np.isnan(cfbd["total"])) else 0.0
                out_rows.append(
                    {
                        "game_id": int(gid),
                        "mkt_spread": cfbd["spread"],
                        "mkt_total": cfbd["total"],
                        "mkt_n_books": cfbd["n_books"],
                        "mkt_is_missing": missing,
                        "market_provenance": "cfbd" if cfbd["line_source"] != "null" else "null",
                    }
                )
            return pd.DataFrame(out_rows)

        resolved = resolve_lines_for_games(
            games,
            as_of,
            snapshots=self.snapshots,
            cfbd_lines=self.cfbd_lines,
            config=self.config,
            closing=False,
        )
        for r in resolved.itertuples(index=False):
            spread = float(r.spread) if pd.notna(r.spread) else float("nan")
            total = float(r.total) if pd.notna(r.total) else float("nan")
            missing = 1.0 if (np.isnan(spread) and np.isnan(total)) else 0.0
            src = str(r.line_source)
            provenance = "null" if src == "null" else "snapshots"
            out_rows.append(
                {
                    "game_id": int(r.game_id),
                    "mkt_spread": spread,
                    "mkt_total": total,
                    "mkt_n_books": int(r.n_books),
                    "mkt_is_missing": missing,
                    "market_provenance": provenance,
                }
            )
        return pd.DataFrame(out_rows)


def _resolve_cfbd_only_line(
    game_id: int,
    cfbd_lines: pd.DataFrame | None,
) -> dict[str, Any]:
    empty: dict[str, Any] = {
        "spread": float("nan"),
        "total": float("nan"),
        "n_books": 0,
        "line_source": "null",
    }
    if cfbd_lines is None or cfbd_lines.empty:
        return empty
    sub = cfbd_lines.loc[cfbd_lines["game_id"] == game_id]
    if sub.empty:
        return empty
    if "line_type" in sub.columns:
        closes = sub.loc[sub["line_type"].astype(str).str.lower().eq("close")]
        opens = sub.loc[sub["line_type"].astype(str).str.lower().eq("open")]
        typed = closes if not closes.empty else opens
        source = "cfbd_close" if not closes.empty else "cfbd_open"
    else:
        typed = sub
        source = "cfbd_close"
    if typed.empty:
        return empty
    spread_col = "spread" if "spread" in typed.columns else None
    total_col = "total" if "total" in typed.columns else None
    spread = float(typed[spread_col].median()) if spread_col else float("nan")
    total = float(typed[total_col].median()) if total_col else float("nan")
    n_books = int(typed["book"].nunique()) if "book" in typed.columns else 0
    return {"spread": spread, "total": total, "n_books": n_books, "line_source": source}


# ---------------------------------------------------------------------------
# Predictor
# ---------------------------------------------------------------------------

# CFB margins beyond this are not credible as μ predictions (Task 22B-FIX:
# ElasticNet OOD via near-constant features produced |pred|≈30k while finite).
MAX_CREDIBLE_MARGIN_PRED: float = 80.0

# Minimum OOF rows before fitting σ / calibration / CQR layers.
_MIN_OOF_ROWS: int = 8
_MIN_CALIBRATION_ROWS: int = 4
# Expanding-window OOF: leave at least this many rows in the first train fold.
_MIN_OOF_TRAIN: int = 4

RATING_MEAN_FEATURE_PREFIXES: tuple[str, ...] = (
    "home_off_epa",
    "home_def_epa",
    "home_st_value",
    "home_pace",
    "away_off_epa",
    "away_def_epa",
    "away_st_value",
    "away_pace",
)


class DistributionDegeneracyError(ProductionStackError):
    """Prediction table failed the distributional degeneracy guard."""


# Predicted quantities that must vary (D4 generalized gate). Any of these with
# zero variance on the full scored table or within a (season, week) block fails.
# Point μ is gated by ``assert_prediction_quality_gate`` (D2); this guard covers
# the distributional / derived columns that historically shipped inert.
PREDICTED_QUANTITY_COLUMNS: tuple[str, ...] = (
    "sigma_m",
    "sigma_t",
    "p_ml_home",
    "p_ats_home",
    "p_ou_over",
    "p_ml_home_raw",
    "p_ats_home_raw",
    "p_ou_over_raw",
    "pred_margin_q05",
    "pred_margin_q10",
    "pred_margin_q25",
    "pred_margin_q50",
    "pred_margin_q75",
    "pred_margin_q90",
    "pred_margin_q95",
)

_ZERO_SPAN: float = 1e-12


def _column_span(vals: np.ndarray) -> float:
    finite = vals[np.isfinite(vals)]
    if finite.size < 2:
        return float("nan")
    return float(np.nanmax(finite) - np.nanmin(finite))


def validate_prediction_distribution(predictions: pd.DataFrame) -> None:
    """Fail loudly if any predicted quantity is inert (D2/D4 gate).

    Trips when σ, any quantile, or any derived probability has zero variance
    on the full scored table **or** within any ``(season, week)`` block with
    ≥8 finite values. Point μ is gated by :func:`assert_prediction_quality_gate`
    (D2). Also catches the Task 23 fixed-σ Φ((μ±line)/c) path.
    """
    if predictions.empty:
        return
    frame = predictions
    if "exclude_from_headline" in frame.columns:
        frame = frame.loc[~frame["exclude_from_headline"].fillna(False).astype(bool)]
    if len(frame) < 2:
        return

    def _finite(col: str) -> np.ndarray:
        arr = pd.to_numeric(frame[col], errors="coerce").to_numpy(dtype=float)
        return np.asarray(arr, dtype=float)

    for col in PREDICTED_QUANTITY_COLUMNS:
        if col not in frame.columns:
            continue
        vals = _finite(col)
        span = _column_span(vals)
        if np.isfinite(span) and span < _ZERO_SPAN:
            msg = (
                f"distribution degeneracy: column {col} is constant across the "
                "full scored table (zero variance)"
            )
            raise DistributionDegeneracyError(msg)

    if {"season", "week"} <= set(frame.columns):
        for (season, week), chunk in frame.groupby(["season", "week"], sort=True):
            # Real CFB weeks are large; require ≥8 finite rows so 2–3-game smoke
            # fixtures do not false-positive on chance ties.
            if len(chunk) < 8:
                continue
            for col in PREDICTED_QUANTITY_COLUMNS:
                if col not in chunk.columns:
                    continue
                vals = pd.to_numeric(chunk[col], errors="coerce").to_numpy(dtype=float)
                span = _column_span(vals)
                if np.isfinite(span) and span < _ZERO_SPAN:
                    msg = (
                        f"distribution degeneracy: column {col} has zero variance "
                        f"within season={int(season)} week={int(week)}"
                    )
                    raise DistributionDegeneracyError(msg)

    # Fixed-variance probe: p ≈ Φ((μ + spread) / c) for a single c.
    if {"p_ats_home", "pred_margin", "spread_close", "sigma_m"} <= set(frame.columns):
        mu = _finite("pred_margin")
        spread = _finite("spread_close")
        p = _finite("p_ats_home")
        sm = _finite("sigma_m")
        mask = np.isfinite(mu) & np.isfinite(spread) & np.isfinite(p) & np.isfinite(sm)
        sigma_span = _column_span(sm[mask]) if int(mask.sum()) >= 8 else 1.0
        if int(mask.sum()) >= 8 and np.isfinite(sigma_span) and sigma_span < 1e-9:
            c = float(np.nanmedian(sm[mask]))
            expected = scipy_stats.norm.cdf((mu[mask] + spread[mask]) / max(c, 1e-8))
            if float(np.nanmax(np.abs(p[mask] - expected))) < 1e-9:
                msg = (
                    "distribution degeneracy: p_ats_home is exactly "
                    f"Normal-CDF under fixed sigma={c}"
                )
                raise DistributionDegeneracyError(msg)


class SignInvertedMarketError(ProductionStackError):
    """Derived-market LL(1−p) beats LL(p) — probability sign is inverted."""


# Kept for reporting / skill flags. Exceeding chance is NOT a run-failing gate:
# with honest σ and no ATS edge, E[−ln Φ(d/s)] > ln 2 whenever SD(μ−market)>0.
CHANCE_LOG_LOSS: float = float(np.log(2.0))

# Backward-compatible alias (D5 name); new code should raise SignInvertedMarketError.
AntiInformativeMarketError = SignInvertedMarketError


def assert_derived_market_signs(
    predictions: pd.DataFrame,
    *,
    epsilon: float = 0.01,
    min_n: int = 30,
    chance: float = CHANCE_LOG_LOSS,
) -> dict[str, dict[str, float]]:
    """SIGN GATE: fail when LL(1−p) < LL(p) − epsilon on any derived market.

    A model beaten by its own inversion has a sign bug. Exceeding chance-level
    log-loss is recorded as a SKILL FLAG only — it must not fail the run.
    """
    from ncaa_quant.evaluation.metrics import (
        ats_home_outcomes,
        log_loss,
        ou_over_outcomes,
    )

    frame = predictions
    if "exclude_from_headline" in frame.columns:
        frame = frame.loc[~frame["exclude_from_headline"].fillna(False).astype(bool)]
    report: dict[str, dict[str, float]] = {}

    def _score(
        name: str,
        p: np.ndarray,
        y: np.ndarray,
        *,
        edge: np.ndarray | None = None,
    ) -> None:
        mask = np.isfinite(p) & np.isfinite(y)
        n = int(mask.sum())
        if n < min_n:
            report[name] = {
                "n": float(n),
                "log_loss": float("nan"),
                "log_loss_inverted": float("nan"),
                "sign_gate_fail": 0.0,
                "skill_flag": 0.0,
                "sd_mu_minus_market": float("nan"),
            }
            return
        p_m = np.clip(p[mask], 1e-12, 1.0 - 1e-12)
        y_m = y[mask]
        ll = float(log_loss(p_m, y_m))
        ll_inv = float(log_loss(1.0 - p_m, y_m))
        sd_edge = float("nan")
        if edge is not None:
            e = edge[mask]
            e = e[np.isfinite(e)]
            if e.size > 1:
                sd_edge = float(np.std(e, ddof=0))
        # Skill vs chance is informational only (see D6: ATS LL > ln2 under zero skill).
        skill = 1.0 if np.isfinite(ll) and ll < chance - 1e-12 else 0.0
        sign_fail = 1.0 if ll_inv < ll - epsilon else 0.0
        report[name] = {
            "n": float(n),
            "log_loss": ll,
            "log_loss_inverted": ll_inv,
            "sign_gate_fail": sign_fail,
            "skill_flag": skill,
            "sd_mu_minus_market": sd_edge,
            "chance_log_loss": float(chance),
        }
        if sign_fail:
            msg = (
                f"sign-inverted derived market: {name} LL(1-p)={ll_inv:.6f} "
                f"< LL(p)={ll:.6f} - epsilon={epsilon} (n={n})"
            )
            raise SignInvertedMarketError(msg)

    if (
        "p_ats_home" in frame.columns
        and "spread_close" in frame.columns
        and "realized_margin" in frame.columns
    ):
        y_ats = ats_home_outcomes(
            frame["realized_margin"].to_numpy(dtype=float),
            frame["spread_close"].to_numpy(dtype=float),
        )
        mu = (
            frame["pred_margin"].to_numpy(dtype=float)
            if "pred_margin" in frame.columns
            else np.full(len(frame), np.nan)
        )
        sp = frame["spread_close"].to_numpy(dtype=float)
        # Home-relative cover edge μ + S ≡ μ − (−S) where −S is market-implied margin.
        edge_ats = mu + sp
        _score(
            "ats_close",
            frame["p_ats_home"].to_numpy(dtype=float),
            y_ats,
            edge=edge_ats,
        )

    if (
        "p_ou_over" in frame.columns
        and "total_close" in frame.columns
        and "realized_total" in frame.columns
    ):
        y_ou = ou_over_outcomes(
            frame["realized_total"].to_numpy(dtype=float),
            frame["total_close"].to_numpy(dtype=float),
        )
        mt = (
            frame["pred_total"].to_numpy(dtype=float)
            if "pred_total" in frame.columns
            else np.full(len(frame), np.nan)
        )
        tot = frame["total_close"].to_numpy(dtype=float)
        edge_ou = mt - tot
        _score(
            "ou_close",
            frame["p_ou_over"].to_numpy(dtype=float),
            y_ou,
            edge=edge_ou,
        )

    return report


def assert_derived_markets_not_anti_informative(
    predictions: pd.DataFrame,
    *,
    epsilon: float = 0.01,
    min_n: int = 30,
    chance: float = CHANCE_LOG_LOSS,
) -> dict[str, dict[str, float]]:
    """Deprecated alias for :func:`assert_derived_market_signs` (D5 → D6)."""
    del chance  # chance is no longer a failing threshold
    return assert_derived_market_signs(predictions, epsilon=epsilon, min_n=min_n)


def assert_a5_garbage_time_precondition(*, n_plays_gt_on: int, n_plays_gt_off: int) -> None:
    """A5 must change the play set; identical counts mean the filter is inert."""
    if n_plays_gt_off <= 0:
        msg = "A5 precondition failed: no plays available to filter"
        raise ProductionStackError(msg)
    if n_plays_gt_on >= n_plays_gt_off:
        msg = (
            "A5 precondition failed: garbage-time filter is inert "
            f"(n_on={n_plays_gt_on}, n_off={n_plays_gt_off}); "
            "no garbage_time flags present on staged plays"
        )
        raise ProductionStackError(msg)


def assert_a1_priors_precondition(
    priors_frame: pd.DataFrame | None,
    *,
    ss_config: StateSpaceConfig | None = None,
) -> None:
    """A1 fitted→league_mean must replace non-degenerate fitted priors."""
    del ss_config
    if priors_frame is None or priors_frame.empty:
        msg = (
            "A1 precondition failed: fitted priors are missing/empty "
            "(already degenerate — ablation cannot change inputs)"
        )
        raise ProductionStackError(msg)
    mean_cols = [c for c in priors_frame.columns if c.startswith("mean_") or c == "prior_mean"]
    if not mean_cols:
        # Team-state frames from build_preseason_states use mean vector cols.
        numeric = [
            c
            for c in priors_frame.columns
            if c not in {"team_id", "season", "event_time"}
            and pd.api.types.is_numeric_dtype(priors_frame[c])
        ]
        mean_cols = numeric
    if not mean_cols:
        msg = "A1 precondition failed: priors_frame has no numeric prior columns"
        raise ProductionStackError(msg)
    block = priors_frame[mean_cols].apply(pd.to_numeric, errors="coerce")
    if block.nunique(dropna=True).max() <= 1:
        msg = (
            "A1 precondition failed: fitted priors are already degenerate "
            "(all teams share the same prior values)"
        )
        raise ProductionStackError(msg)


@dataclass
class ProductionEnsemblePredictor:
    """Mapping-layer predictor with §2.3/§2.6 distributional assembly + calibration.

    At each ``fit()`` (retrain gate): time-ordered OOF μ → σ heads → ensemble σ →
    ρ / key-number kernel → CQR → distributional PIT maps on the OOF margin and
    total predictive CDFs (AUDIT-4 / ADR 0011; not per-market isotonic).

    At ``predict()``: emit μ, heteroskedastic σ, quantiles, conformal intervals,
    MC market probs, and PIT-recalibrated derived market probs. Never substitutes
    a constant σ. ``models/calibrate.py`` remains diagnostics-only.
    """

    config: WalkForwardConfig
    model_version: str = "production-ensemble-v0"
    margin_head: LightGBMMuHead = field(default_factory=lambda: LightGBMMuHead(target="margin"))
    total_head: LightGBMMuHead = field(default_factory=lambda: LightGBMMuHead(target="total"))
    enet_margin: ElasticNetMuHead = field(default_factory=lambda: ElasticNetMuHead(target="margin"))
    sigma_margin_head: LightGBMSigmaHead = field(
        default_factory=lambda: LightGBMSigmaHead(target="sigma_margin")
    )
    sigma_total_head: LightGBMSigmaHead = field(
        default_factory=lambda: LightGBMSigmaHead(target="sigma_total")
    )
    quantile_margin_head: LightGBMQuantileHead = field(
        default_factory=lambda: LightGBMQuantileHead(target="margin")
    )
    cfbd_lines: pd.DataFrame | None = None
    n_mc_draws: int = DEFAULT_N_DRAWS
    n_epistemic_draws: int = field(default_factory=default_epistemic_draws)
    seed: int = 0
    _ensemble: FittedEnsemble | None = field(default=None, init=False, repr=False)
    _fitted: bool = field(default=False, init=False, repr=False)
    _nnls_fold_reports: list[dict[str, Any]] = field(default_factory=list, init=False, repr=False)
    _nnls_fallback: str | None = field(default=None, init=False, repr=False)
    _calibration: DistributionalCalibrationBundle | None = field(
        default=None, init=False, repr=False
    )
    _cqr: CQRResult | None = field(default=None, init=False, repr=False)
    _key_kernel: KeyNumberKernel | ConditionalKeyNumberKernel | None = field(
        default=None, init=False, repr=False
    )
    _rho: float = field(default=0.0, init=False, repr=False)
    _calibration_report: dict[str, Any] = field(default_factory=dict, init=False, repr=False)

    @property
    def is_fitted(self) -> bool:
        """True when margin μ is fit and Level-1 weights are set."""
        return bool(self.margin_head.is_fitted and self._ensemble is not None)

    @property
    def nnls_fallback(self) -> str | None:
        """Explicit fallback stamp when NNLS could not fit (config-gated)."""
        return self._nnls_fallback

    @property
    def nnls_fold_reports(self) -> list[dict[str, Any]]:
        """Per-fit NNLS weight / condition-number reports."""
        return list(self._nnls_fold_reports)

    @property
    def ensemble_weights(self) -> dict[str, float]:
        """Current margin stack weights (A4 mechanism surface)."""
        if self._ensemble is not None and self._ensemble.margin is not None:
            return self._ensemble.margin.as_dict()
        if self.config.mapping_layer == "single_lgbm":
            return {"lgbm_mu_margin": 1.0}
        msg = "ensemble weights requested before NNLS fit"
        raise EnsembleError(msg)

    @property
    def calibration_report(self) -> dict[str, Any]:
        """Cox slope/intercept before and after from the last fit()."""
        return dict(self._calibration_report)

    def fit(
        self,
        features: pd.DataFrame,
        labels: pd.DataFrame,
        *,
        sample_weight: pd.Series | None = None,
    ) -> None:
        del sample_weight
        if labels.empty or features.empty:
            # Leave heads unfitted — predict must raise NotFittedError.
            self._fitted = False
            self._ensemble = None
            self._nnls_fallback = None
            return
        self.margin_head.fit(features, labels)
        if not self.margin_head.is_fitted:
            self._fitted = False
            self._ensemble = None
            return
        with contextlib.suppress(Exception):
            self.enet_margin.fit(features, labels)
        if "realized_total" in labels.columns:
            with contextlib.suppress(Exception):
                self.total_head.fit(features, labels)

        oof = self._time_ordered_oof_mu(features, labels)
        self._set_weights(oof)

        if oof is not None and len(oof) >= _MIN_OOF_ROWS:
            self._fit_sigma_heads(features, labels, oof)
            with contextlib.suppress(Exception):
                self.quantile_margin_head.fit(features, labels)
            self._fit_rho_and_kernel(oof)
            self._fit_cqr_layer(features, labels, oof)
            self._fit_calibration_from_oof(features, oof)
        self._fitted = True

    def predict(self, features: pd.DataFrame) -> pd.DataFrame:
        empty_cols = [
            "game_id",
            "pred_margin",
            "pred_total",
            "sigma_m",
            "sigma_t",
            "rho",
            "p_ml_home_raw",
            "p_ats_home_raw",
            "p_ou_over_raw",
            "p_ml_home",
            "p_ats_home",
            "p_ou_over",
        ]
        if features.empty:
            return pd.DataFrame(columns=empty_cols)

        point = self._predict_point(features)
        out = point.copy()
        n = len(out)
        gids = out["game_id"].to_numpy()

        # --- σ via LoTV ---
        sigma_head_m, sigma_head_t = self._predict_sigma_heads(features, gids)
        member_mus = self._member_margin_matrix(features, out["pred_margin"].to_numpy())
        weights = self.ensemble_weights
        w = (
            [weights.get("lgbm_mu_margin", 0.5), weights.get("enet_mu_margin", 0.5)]
            if member_mus.shape[1] > 1
            else None
        )
        ens_m = ensemble_sigma(member_mus, sigma_head_m, weights=w)
        # Total: single member → ens σ = σ-head.
        ens_t = ensemble_sigma(out["pred_total"].to_numpy().reshape(-1, 1), sigma_head_t)

        # Epistemic mixture over rating-mean features (Stage-1 posterior proxy).
        mu_m = out["pred_margin"].to_numpy(dtype=float)
        mu_t = out["pred_total"].to_numpy(dtype=float)
        sig_m = ens_m.sigma.copy()
        sig_t = ens_t.sigma.copy()
        stage1_var_m = np.zeros(n, dtype=float)
        stage1_var_t = np.zeros(n, dtype=float)
        if int(self.n_epistemic_draws) >= 2:
            # Do not swallow failures: a dead mix path ships Stage-1 Var(μ)=0.
            mix = self._epistemic_mix(features, rho=self._rho, seed=self.seed)
            mu_m = mix.params.mu_m
            mu_t = mix.params.mu_t
            sig_m = mix.params.sigma_m
            sig_t = mix.params.sigma_t
            out["pred_margin"] = mu_m
            out["pred_total"] = mu_t
            meta = mix.params.meta or {}
            stage1_var_m = np.asarray(meta.get("stage1_var_m", stage1_var_m), dtype=float)
            stage1_var_t = np.asarray(meta.get("stage1_var_t", stage1_var_t), dtype=float)
            ens_m = attach_stage1_mixture_variance(ens_m, stage1_var_m)

        out["sigma_m"] = sig_m
        out["sigma_t"] = sig_t
        out["sigma_m_is_missing"] = ~np.isfinite(sig_m)
        out["sigma_t_is_missing"] = ~np.isfinite(sig_t)
        out["sigma_aleatoric_m"] = ens_m.sigma_head
        out["sigma_member_var_m"] = ens_m.member_var
        out["sigma_stage1_var_m"] = stage1_var_m
        del stage1_var_t  # total Stage-1 tracked inside mix.params.meta when needed
        out["rho"] = float(self._rho)
        out["rho_is_missing"] = not np.isfinite(self._rho)

        # Quantiles + CQR
        q_missing = True
        with contextlib.suppress(Exception):
            qpred = self.quantile_margin_head.predict(features)
            q_missing = False
            for q in QUANTILES:
                col = quantile_column("margin", q)
                if col not in qpred.columns:
                    continue
                vals = qpred.set_index("game_id").reindex(gids)[col].to_numpy(dtype=float)
                # Skip emitting a flat quantile vector — degeneracy guard would
                # treat it as a broken distributional column.
                finite = vals[np.isfinite(vals)]
                if finite.size >= 2 and float(np.nanmax(finite) - np.nanmin(finite)) < 1e-12:
                    continue
                out[col] = vals
                q_missing = False
        if self._cqr is not None and not q_missing:
            with contextlib.suppress(Exception):
                conf = conformalize_intervals(out, self._cqr, nominal=0.8)
                out["cqr_lo"] = conf["cqr_lo"].to_numpy(dtype=float)
                out["cqr_hi"] = conf["cqr_hi"].to_numpy(dtype=float)
                out["cqr_nominal"] = 0.8
                out["cqr_is_missing"] = False
        else:
            out["cqr_lo"] = np.nan
            out["cqr_hi"] = np.nan
            out["cqr_nominal"] = np.nan
            out["cqr_is_missing"] = True

        # Joint MC → market probs
        spreads, totals = self._lookup_closes(gids)
        params = assemble_bivariate(mu_m, sig_m, mu_t, sig_t, rho=self._rho)
        draws = sample_joint(
            params,
            kernel=self._key_kernel,
            n_draws=int(self.n_mc_draws),
            seed=int(self.seed),
        )
        p_ml = np.empty(n, dtype=float)
        p_ats = np.empty(n, dtype=float)
        p_ou = np.empty(n, dtype=float)
        for i in range(n):
            # ML / ATS / OU binary markets are graded with pushes excluded.
            # Emit push-conditional (two-way) side probs so p_side + p_other == 1
            # net of push (H3). Continuous Gaussian draws have p_push=0 (no-op).
            p_ml[i] = two_way_side_prob(moneyline_probs(draws, game_index=i))
            if np.isfinite(spreads[i]):
                p_ats[i] = two_way_side_prob(
                    spread_cover_probs(draws, float(spreads[i]), game_index=i)
                )
            else:
                p_ats[i] = float("nan")
            if np.isfinite(totals[i]):
                p_ou[i] = two_way_side_prob(total_probs(draws, float(totals[i]), game_index=i))
            else:
                p_ou[i] = float("nan")

        out["p_ml_home_raw"] = p_ml
        out["p_ats_home_raw"] = p_ats
        out["p_ou_over_raw"] = p_ou
        out["p_ml_home"] = self._apply_calibrator("ml", p_ml)
        out["p_ats_home"] = self._apply_calibrator("ats_close", p_ats)
        out["p_ou_over"] = self._apply_calibrator("ou_close", p_ou)
        out["p_ml_home_is_missing"] = ~np.isfinite(out["p_ml_home"].to_numpy(dtype=float))
        out["p_ats_home_is_missing"] = ~np.isfinite(out["p_ats_home"].to_numpy(dtype=float))
        out["p_ou_over_is_missing"] = ~np.isfinite(out["p_ou_over"].to_numpy(dtype=float))
        return out

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _predict_point(self, features: pd.DataFrame) -> pd.DataFrame:
        lgbm = self.margin_head.predict(features)
        weights = self.ensemble_weights
        margin = lgbm["pred_margin"].astype(float).to_numpy()
        enet_m = np.full_like(margin, 2.5, dtype=float)
        if self.config.mapping_layer == "ensemble":
            try:
                enet = self.enet_margin.predict(features)
                enet_m = (
                    enet.set_index("game_id")
                    .reindex(lgbm["game_id"])["pred_margin"]
                    .astype(float)
                    .to_numpy()
                )
                credible = np.isfinite(enet_m) & (np.abs(enet_m) <= MAX_CREDIBLE_MARGIN_PRED)
                if not bool(np.all(credible)):
                    enet_m = np.full_like(margin, 2.5, dtype=float)
            except Exception:
                enet_m = np.full_like(margin, 2.5, dtype=float)
            w_l = float(weights.get("lgbm_mu_margin", 0.5))
            w_e = float(weights.get("enet_mu_margin", 0.5))
            total_w = max(w_l + w_e, 1e-12)
            margin = (w_l * margin + w_e * enet_m) / total_w
        out = pd.DataFrame({"game_id": lgbm["game_id"].to_numpy(), "pred_margin": margin})
        try:
            total = self.total_head.predict(features)
            out["pred_total"] = (
                total.set_index("game_id").reindex(out["game_id"])["pred_total"].to_numpy()
            )
        except Exception:
            out["pred_total"] = float("nan")
        return out

    def _member_margin_matrix(self, features: pd.DataFrame, ens_margin: np.ndarray) -> np.ndarray:
        lgbm = self.margin_head.predict(features)["pred_margin"].astype(float).to_numpy()
        if self.config.mapping_layer != "ensemble":
            return np.asarray(lgbm.reshape(-1, 1), dtype=float)
        try:
            enet = self.enet_margin.predict(features)
            enet_m = (
                enet.set_index("game_id")
                .reindex(features["game_id"])["pred_margin"]
                .astype(float)
                .to_numpy()
            )
            credible = np.isfinite(enet_m) & (np.abs(enet_m) <= MAX_CREDIBLE_MARGIN_PRED)
            if not bool(np.all(credible)):
                enet_m = np.full_like(lgbm, 2.5, dtype=float)
        except Exception:
            enet_m = np.full_like(lgbm, 2.5, dtype=float)
        del ens_margin
        return np.column_stack([lgbm, enet_m])

    def _predict_sigma_heads(
        self, features: pd.DataFrame, gids: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        n = len(gids)
        sm = np.full(n, float("nan"))
        st = np.full(n, float("nan"))
        if self.sigma_margin_head.is_fitted:
            pred = self.sigma_margin_head.predict(features)
            col = "pred_sigma_margin" if "pred_sigma_margin" in pred.columns else pred.columns[-1]
            for c in pred.columns:
                if c != "game_id":
                    col = c
                    break
            sm = pred.set_index("game_id").reindex(gids)[col].to_numpy(dtype=float)
        if self.sigma_total_head.is_fitted:
            pred = self.sigma_total_head.predict(features)
            for c in pred.columns:
                if c != "game_id":
                    col = c
                    break
            st = pred.set_index("game_id").reindex(gids)[col].to_numpy(dtype=float)
        # Heteroskedastic floor: if σ-head missing, derive a per-row proxy from
        # rating_uncertainty so σ is never a global constant.
        if not np.any(np.isfinite(sm)):
            unc = (
                features["rating_uncertainty"].to_numpy(dtype=float)
                if "rating_uncertainty" in features.columns
                else np.full(n, 1.0)
            )
            sm = 8.0 + np.maximum(unc, 0.0)
        if not np.any(np.isfinite(st)):
            unc = (
                features["rating_uncertainty"].to_numpy(dtype=float)
                if "rating_uncertainty" in features.columns
                else np.full(n, 1.0)
            )
            st = 8.0 + np.maximum(unc, 0.0)
        return np.maximum(sm, 1e-6), np.maximum(st, 1e-6)

    def _time_ordered_oof_mu(
        self, features: pd.DataFrame, labels: pd.DataFrame
    ) -> pd.DataFrame | None:
        """Time-ordered blocked OOF μ predictions (never in-fold).

        Uses contiguous time blocks (not random K-fold). Expanding leave-one-out
        is avoided for cost; block count scales with n while preserving order.
        """
        merged = features.merge(labels, on="game_id", how="inner", suffixes=("", "_lab"))
        if len(merged) < _MIN_OOF_TRAIN + 1:
            return None
        sort_cols = [c for c in ("season", "week", "game_id") if c in merged.columns]
        if not sort_cols:
            sort_cols = ["game_id"]
        merged = merged.sort_values(sort_cols, kind="mergesort").reset_index(drop=True)
        feat_cols = [c for c in features.columns if c != "game_id"]
        n = len(merged)
        n_blocks = min(5, max(2, n // max(_MIN_OOF_TRAIN, 1)))
        edges = [int(round(i * n / n_blocks)) for i in range(n_blocks + 1)]
        oof_rows: list[dict[str, Any]] = []
        for b in range(1, n_blocks):
            train = merged.iloc[: edges[b]]
            test = merged.iloc[edges[b] : edges[b + 1]]
            if train.empty or test.empty:
                continue
            tr_feat = train[["game_id", *feat_cols]]
            tr_lab = train[
                [
                    c
                    for c in ("game_id", "realized_margin", "realized_total", "season", "week")
                    if c in train.columns
                ]
            ]
            te_feat = test[["game_id", *feat_cols]]
            head_m = LightGBMMuHead(
                target="margin",
                train=self.margin_head.train,
                model_version=f"{self.model_version}-oof",
                seed=self.seed + b,
            )
            head_m.fit(tr_feat, tr_lab)
            pred_m = head_m.predict(te_feat).set_index("game_id")["pred_margin"]
            pred_enet: pd.Series | None = None
            if self.config.mapping_layer == "ensemble":
                head_e = ElasticNetMuHead(
                    target="margin",
                    model_version=f"{self.model_version}-oof-enet",
                    seed=self.seed + b + 31,
                )
                with contextlib.suppress(Exception):
                    head_e.fit(tr_feat, tr_lab)
                    if head_e.is_fitted:
                        pred_enet = head_e.predict(te_feat).set_index("game_id")["pred_margin"]
            pred_t_series: pd.Series | None = None
            if "realized_total" in tr_lab.columns:
                head_t = LightGBMMuHead(
                    target="total",
                    train=self.total_head.train,
                    model_version=f"{self.model_version}-oof-t",
                    seed=self.seed + b + 17,
                )
                with contextlib.suppress(Exception):
                    head_t.fit(tr_feat, tr_lab)
                    pred_t_series = head_t.predict(te_feat).set_index("game_id")["pred_total"]
            for row in test.itertuples(index=False):
                gid = int(row.game_id)
                lgbm_val = float(pred_m.loc[gid])
                enet_val = (
                    float(pred_enet.loc[gid])
                    if pred_enet is not None and gid in pred_enet.index
                    else lgbm_val
                )
                entry: dict[str, Any] = {
                    "game_id": gid,
                    "pred_margin": lgbm_val,
                    "lgbm_mu_margin": lgbm_val,
                    "enet_mu_margin": enet_val,
                    "pred_total": (
                        float(pred_t_series.loc[gid])
                        if pred_t_series is not None and gid in pred_t_series.index
                        else float("nan")
                    ),
                    "realized_margin": float(row.realized_margin),
                    "is_out_of_fold": True,
                }
                if hasattr(row, "realized_total"):
                    entry["realized_total"] = float(row.realized_total)
                if hasattr(row, "season"):
                    entry["season"] = int(row.season)
                if hasattr(row, "week"):
                    entry["week"] = int(row.week)
                oof_rows.append(entry)
        if len(oof_rows) < _MIN_OOF_ROWS:
            return None
        return pd.DataFrame(oof_rows)

    def _fit_sigma_heads(
        self, features: pd.DataFrame, labels: pd.DataFrame, oof: pd.DataFrame
    ) -> None:
        mu_frame = oof[["game_id", "pred_margin"]].copy()
        if "pred_total" in oof.columns:
            mu_frame["pred_total"] = oof["pred_total"]
        sigma_labels_m = abs_residual_labels(labels, mu_frame, target="margin")
        # Align features to OOF game ids only.
        feat_oof = features.merge(oof[["game_id"]], on="game_id", how="inner")
        lab_oof = sigma_labels_m.merge(oof[["game_id"]], on="game_id", how="inner")
        # Fail loudly: a swallowed fit leaves σ inert (constant floor / MAD scalar).
        self.sigma_margin_head.fit(feat_oof, lab_oof)
        if not self.sigma_margin_head.is_fitted:
            msg = "sigma_margin head failed to fit on OOF abs-residuals"
            raise ProductionStackError(msg)
        if "pred_total" in mu_frame.columns and "realized_total" in labels.columns:
            sigma_labels_t = abs_residual_labels(labels, mu_frame, target="total")
            lab_t = sigma_labels_t.merge(oof[["game_id"]], on="game_id", how="inner")
            self.sigma_total_head.fit(feat_oof, lab_t)

    def _fit_rho_and_kernel(self, oof: pd.DataFrame) -> None:
        if "realized_total" not in oof.columns or "pred_total" not in oof.columns:
            self._rho = 0.0
            return
        rm, rt = residuals_from_predictions(
            oof["realized_margin"].to_numpy(),
            oof["pred_margin"].to_numpy(),
            oof["realized_total"].to_numpy(),
            oof["pred_total"].to_numpy(),
        )
        est = estimate_rho(rm, rt)
        self._rho = float(est.rho)
        with contextlib.suppress(Exception):
            self._key_kernel = fit_key_number_kernel(
                oof["realized_margin"].to_numpy(),
                oof["pred_margin"].to_numpy(),
            )

    def _fit_cqr_layer(
        self, features: pd.DataFrame, labels: pd.DataFrame, oof: pd.DataFrame
    ) -> None:
        del features
        if not self.quantile_margin_head.is_fitted or "season" not in oof.columns:
            return
        # Build quantile predictions on OOF feature rows via the fitted head —
        # but that head was fit on ALL labels (in-fold for quantiles). CQR
        # calibration set is trailing seasons of the OOF μ frame joined to
        # quantile predictions from a head fit on earlier data only when
        # seasons allow. Pragmatic: use quantile head preds on OOF game features
        # stored by re-merging; accept that quantile head is in-sample for CQR
        # *input* while conformity scores use realized OOF outcomes.
        # Caller already fit quantile on full labels; skip if <2 seasons.
        seasons = sorted({int(s) for s in oof["season"].dropna().unique()})
        if len(seasons) < 1:
            return
        # Need quantile columns on a frame — predict requires features. Skip if
        # we cannot reconstruct; CQR remains None (null-with-indicator).
        del labels
        with contextlib.suppress(Exception):
            # Minimal CQR frame from OOF margins as q50 proxy bands when thin.
            frame = oof.copy()
            for q in QUANTILES:
                # Placeholder bands from μ ± z_q * residual MAD — only used if
                # real quantile cols absent; prefer real head when available.
                z = float(__import__("scipy").stats.norm.ppf(q))
                resid = (frame["realized_margin"] - frame["pred_margin"]).abs()
                mad = float(np.nanmedian(resid)) if len(resid) else 10.0
                frame[quantile_column("margin", q)] = frame["pred_margin"] + z * max(mad, 1.0)
            self._cqr = fit_cqr(frame, target="margin", n_trailing=min(2, len(seasons)))

    def _fit_calibration_from_oof(self, features: pd.DataFrame, oof: pd.DataFrame) -> None:
        """Fit distributional PIT maps on the OOF predictive distributions.

        §2.6 / §5.2 as amended (audit A-4): one monotone map on the margin
        predictive CDF and one on the total, rather than three per-market
        probability maps. Every margin-derived market (moneyline, ATS at any line)
        is then read off one recalibrated CDF, so they cannot disagree about the
        same event — which independent ML and ATS maps had no way to prevent.

        σ comes from the fitted σ-heads, not from the residuals being calibrated
        against. The previous implementation built σ from ``|y − μ|``, which put
        the label inside the predictive distribution it was meant to score.
        """
        gids = oof["game_id"].to_numpy()
        feat_oof = features.merge(oof[["game_id"]], on="game_id", how="inner")
        sig_m, sig_t = self._predict_sigma_heads(feat_oof, gids)

        bundle = DistributionalCalibrationBundle()
        report: dict[str, Any] = {}

        targets: tuple[tuple[DistributionTarget, str, str, np.ndarray], ...] = (
            ("margin", "pred_margin", "realized_margin", sig_m),
            ("total", "pred_total", "realized_total", sig_t),
        )
        for target, mu_col, y_col, sig in targets:
            if mu_col not in oof.columns or y_col not in oof.columns:
                report[f"{target}_pit_skipped"] = f"missing column ({mu_col} or {y_col})"
                continue
            pit = pit_values_normal(
                oof[y_col].to_numpy(dtype=float),
                oof[mu_col].to_numpy(dtype=float),
                np.asarray(sig, dtype=float),
            )
            finite = pit[np.isfinite(pit)]
            if finite.size < _MIN_CALIBRATION_ROWS:
                # Record why rather than leaving an empty report: a calibration
                # layer that quietly does nothing is indistinguishable from one
                # that ran and found nothing to fix.
                report[f"{target}_pit_skipped"] = (
                    f"only {finite.size} finite PIT rows (need {_MIN_CALIBRATION_ROWS}); "
                    f"sigma finite on {int(np.isfinite(sig).sum())}/{len(oof)} rows"
                )
                continue
            try:
                cal = self._fit_and_gate_pit_map(pit, target=target)
            except PitCalibrationError as exc:
                report[f"{target}_pit_skipped"] = f"fit failed: {exc}"
                continue
            if target == "margin":
                bundle.margin = cal
            else:
                bundle.total = cal
        report.update(bundle.report())

        self._calibration = bundle if (bundle.margin or bundle.total) else None
        self._calibration_report = report

    def _fit_and_gate_pit_map(
        self, pit: np.ndarray, *, target: DistributionTarget
    ) -> PitRecalibrator:
        """Fit on the earlier OOF rows, gate on the later ones, refit on all.

        Isotonic-on-PIT is uniform in sample by construction, so the gate has to
        see rows the map was not fit on. The split is time-ordered (the OOF frame
        is already in time order) — never random, per the cardinal rule.
        """
        finite_idx = np.flatnonzero(np.isfinite(pit))
        u = pit[finite_idx]
        split = int(u.size * 0.7)
        if split >= 4 and (u.size - split) >= 4:
            trial = fit_pit_recalibrator(u[:split], target=target)
            gate_pit_recalibrator(trial, u[split:])
            passed = bool(trial.applied)
            gate_meta = {k: v for k, v in trial.meta.items() if k.startswith("gate_")}
        else:
            passed = False
            gate_meta = {"gate_reason": f"OOF too small to split (n={u.size})"}

        final = fit_pit_recalibrator(u, target=target)
        final.applied = passed
        final.meta.update(gate_meta)
        return final

    def _apply_calibrator(self, market: str, raw: np.ndarray) -> np.ndarray:
        """Recalibrate a market probability through its distribution's PIT map.

        ``ml`` and ``ats_close`` both resolve to the margin map; ``ou_close``
        resolves to the total map. That routing is the coherence guarantee.
        """
        out = np.asarray(raw, dtype=float).copy()
        if self._calibration is None:
            return out
        target: DistributionTarget = "total" if market == "ou_close" else "margin"
        cal = self._calibration.get(target)
        if cal is None or not cal.applied:
            return out
        mask = np.isfinite(out)
        if not np.any(mask):
            return out
        out[mask] = cal.side_prob(out[mask])
        return out

    def _lookup_closes(self, gids: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Join CFBD close lines by game_id for probability queries (not μ features)."""
        n = len(gids)
        spreads = np.full(n, np.nan)
        totals = np.full(n, np.nan)
        if self.cfbd_lines is None or self.cfbd_lines.empty:
            return spreads, totals
        lines = self.cfbd_lines
        for i, gid in enumerate(gids):
            sub = lines.loc[lines["game_id"] == int(gid)]
            if sub.empty:
                continue
            if "line_type" in sub.columns:
                closes = sub.loc[sub["line_type"].astype(str).str.lower().eq("close")]
                typed = closes if not closes.empty else sub
            else:
                typed = sub
            if "spread" in typed.columns:
                spreads[i] = float(typed["spread"].median())
            if "total" in typed.columns:
                totals[i] = float(typed["total"].median())
        return spreads, totals

    def _epistemic_mix(self, features: pd.DataFrame, *, rho: float, seed: int) -> Any:
        """Sample rating-mean features ~ N(mean, uncertainty²) and mix mapping."""
        feat_cols = [c for c in features.columns if c != "game_id"]
        base = features[feat_cols].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
        n_games = int(base.shape[0])
        unc = (
            features["rating_uncertainty"].to_numpy(dtype=float)
            if "rating_uncertainty" in features.columns
            else np.ones(n_games)
        )
        sd = np.maximum(unc, 1e-3)[:, None] * 0.25
        rng = np.random.default_rng(seed)
        s = int(self.n_epistemic_draws)
        # Prefer exact rating-mean column names; fall back to home_*/away_* dims.
        rate_idx = [i for i, c in enumerate(feat_cols) if c in RATING_MEAN_FEATURE_PREFIXES]
        if not rate_idx:
            rate_idx = [
                i
                for i, c in enumerate(feat_cols)
                if any(
                    c == f"{side}_{dim}" for side in ("home", "away") for dim in RATING_FEATURE_DIMS
                )
            ]
        if not rate_idx:
            msg = (
                "epistemic mixture dead: no rating-mean feature columns matched "
                f"{RATING_MEAN_FEATURE_PREFIXES}; draws would be identical → Var(μ)=0"
            )
            raise ProductionStackError(msg)

        gids = features["game_id"].to_numpy()
        rate_cols = [feat_cols[i] for i in rate_idx]
        # Draws are rating-mean columns only — rebuild by copying the original
        # frame so non-numeric columns (e.g. market_provenance) keep their
        # dtypes and the feature signature still matches.
        rate_base = base[:, rate_idx]
        rate_draws = np.empty((s, n_games, len(rate_idx)), dtype=float)
        # sd is (n_games, 1); broadcast across rating columns.
        for k in range(s):
            noise = rng.normal(0.0, 1.0, size=rate_base.shape) * sd
            rate_draws[k] = rate_base + noise

        def mapping_fn(feat_mat: np.ndarray) -> Mapping[str, np.ndarray]:
            frame = features.copy()
            for j, col in enumerate(rate_cols):
                frame[col] = feat_mat[:, j]
            point = self._predict_point(frame)
            sm, st = self._predict_sigma_heads(frame, gids)
            return {
                "mu_m": point["pred_margin"].to_numpy(dtype=float),
                "sigma_m": sm,
                "mu_t": point["pred_total"].to_numpy(dtype=float),
                "sigma_t": st,
            }

        set_global_seed(seed)
        return mix_epistemic_predictions(rate_draws, mapping_fn, rho=rho, seed=seed)

    def _set_weights(self, oof: pd.DataFrame | None = None) -> None:
        """Fit Level-1 NNLS stack on OOF member μs (DESIGN §5.2).

        Never hardcodes 0.5/0.5. Degenerate OOF raises unless
        ``config.nnls_equal_weight_fallback`` is explicitly True.
        """
        self._nnls_fallback = None
        if self.config.mapping_layer == "single_lgbm":
            self._ensemble = FittedEnsemble(
                margin=single_lgbm_stack(target="margin", lgbm_column="lgbm_mu_margin"),
                total=single_lgbm_stack(target="total", lgbm_column="lgbm_mu_total"),
                meta={"mapping_layer": "single_lgbm"},
            )
            return

        member_cols = ("lgbm_mu_margin", "enet_mu_margin")
        allow_fb = bool(getattr(self.config, "nnls_equal_weight_fallback", False))
        if oof is None or oof.empty:
            if allow_fb:
                self._nnls_fallback = "equal_weight_thin_oof"
                stack = NNLSStackResult(
                    target="margin",
                    member_columns=member_cols,
                    weights=(0.5, 0.5),
                    condition_number=float("nan"),
                    n_oof_rows=0,
                    fallback="equal_weight_thin_oof",
                )
                report = {
                    "target": "margin",
                    "weights": stack.as_dict(),
                    "condition_number": stack.condition_number,
                    "n_oof_rows": 0,
                    "fallback": stack.fallback,
                }
                self._nnls_fold_reports.append(report)
                self._ensemble = FittedEnsemble(
                    margin=stack,
                    total=single_lgbm_stack(target="total", lgbm_column="lgbm_mu_total"),
                    meta={"mapping_layer": "ensemble", "nnls_report": report},
                )
                return
            msg = (
                "NNLS stacking requires an OOF member matrix; got empty OOF. "
                "Provide enough training rows for time-ordered OOF, set "
                "mapping_layer='single_lgbm', or set nnls_equal_weight_fallback=True."
            )
            raise EnsembleError(msg)
        missing = [c for c in member_cols if c not in oof.columns]
        if missing:
            if allow_fb:
                self._nnls_fallback = "equal_weight_missing_members"
                stack = NNLSStackResult(
                    target="margin",
                    member_columns=member_cols,
                    weights=(0.5, 0.5),
                    fallback="equal_weight_missing_members",
                )
                self._nnls_fold_reports.append(
                    {
                        "target": "margin",
                        "weights": stack.as_dict(),
                        "fallback": stack.fallback,
                        "missing": missing,
                    }
                )
                self._ensemble = FittedEnsemble(
                    margin=stack,
                    total=single_lgbm_stack(target="total", lgbm_column="lgbm_mu_total"),
                    meta={"mapping_layer": "ensemble"},
                )
                return
            msg = f"OOF frame missing NNLS member columns: {missing}"
            raise EnsembleError(msg)

        stack = fit_nnls_stack(
            oof,
            target="margin",
            member_columns=member_cols,
            allow_equal_weight_fallback=allow_fb,
        )
        self._nnls_fallback = stack.fallback
        report = {
            "target": "margin",
            "weights": stack.as_dict(),
            "condition_number": stack.condition_number,
            "n_oof_rows": stack.n_oof_rows,
            "fallback": stack.fallback,
            "n_train_labels": int(len(oof)),
        }
        self._nnls_fold_reports.append(report)
        self._ensemble = FittedEnsemble(
            margin=stack,
            total=single_lgbm_stack(target="total", lgbm_column="lgbm_mu_total"),
            meta={
                "mapping_layer": "ensemble",
                "nnls_report": report,
                "allow_equal_weight_fallback": allow_fb,
            },
        )


# ---------------------------------------------------------------------------
# Stack construction
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProductionStack:
    """Fully-specified harness dependencies for one named run."""

    kind: StackKind
    config: WalkForwardConfig
    feature_provider: ProductionFeatureProvider
    rating_engine: StateSpaceRatingEngine
    predictor: ProductionEnsemblePredictor


def build_observations_from_staged(
    *,
    plays: pd.DataFrame | None = None,
    games: pd.DataFrame | None = None,
    advanced: pd.DataFrame | None = None,
    garbage_time_filter: bool = True,
) -> tuple[pd.DataFrame, int, int]:
    """Build filter observations; return ``(obs, n_plays_gt_on, n_plays_gt_off)``."""
    if plays is not None and games is not None and not plays.empty:
        from ncaa_quant.features.epa import apply_garbage_time

        flagged = apply_garbage_time(plays) if "garbage_time" not in plays.columns else plays
        n_off = int(len(flagged))
        n_on = (
            int((~flagged["garbage_time"].astype(bool)).sum())
            if "garbage_time" in flagged.columns
            else n_off
        )
        obs = build_game_observations_from_plays(
            plays,
            games,
            drop_garbage=garbage_time_filter,
        )
        return obs, n_on, n_off
    if advanced is not None and games is not None and not advanced.empty:
        obs = build_game_observations_from_advanced(advanced, games)
        return obs, 0, 0
    return pd.DataFrame(), 0, 0


def build_production_stack(
    config: WalkForwardConfig,
    *,
    kind: StackKind = "fundamental",
    observations: pd.DataFrame | None = None,
    priors_frame: pd.DataFrame | None = None,
    snapshots: pd.DataFrame | None = None,
    cfbd_lines: pd.DataFrame | None = None,
    fbs_team_ids: set[Any] | None = None,
    play_counts: tuple[int, int] | None = None,
    n_mc_draws: int | None = None,
    n_epistemic_draws: int | None = None,
    enforce_ablation_preconditions: bool = True,
) -> ProductionStack:
    """Config-driven construction of a fully-specified production stack.

    ``fundamental`` forces ``market_features_available=False``;
    ``market_aware`` keeps the caller's A3 setting.

    ``enforce_ablation_preconditions`` defaults to True (Phase 2): A1/A5
    switches that cannot change inputs raise :class:`ProductionStackError`
    instead of silent no-ops. Pass False only for cold-start wiring proofs.
    """
    config.validate_ablations()
    if kind == "fundamental":
        cfg = replace(
            config,
            market_features_available=False,
            ablation_id=(
                config.ablation_id if config.ablation_id not in {"full", ""} else "fundamental"
            ),
        )
    else:
        cfg = config

    if enforce_ablation_preconditions:
        if cfg.preseason_priors == "league_mean":
            # A1 only meaningful when fitted priors exist and vary.
            assert_a1_priors_precondition(priors_frame)
        if play_counts is not None and not cfg.garbage_time_filter:
            # A5 ablation (filter off): turning the filter off must change the
            # play set. Identical on/off counts mean garbage_time flags are
            # absent on staged plays — refuse a silent no-op delta.
            assert_a5_garbage_time_precondition(
                n_plays_gt_on=play_counts[0], n_plays_gt_off=play_counts[1]
            )

    obs = observations if observations is not None else pd.DataFrame()
    engine = StateSpaceRatingEngine(
        observations=obs,
        config=cfg,
        priors_frame=priors_frame,
        fbs_team_ids=fbs_team_ids,
    )
    provider = ProductionFeatureProvider(
        config=cfg,
        snapshots=snapshots,
        cfbd_lines=cfbd_lines,
    )
    if play_counts is not None:
        provider.set_play_counts(with_gt_filter=play_counts[0], without_gt_filter=play_counts[1])

    train = HeadTrainConfig(n_estimators=50, learning_rate=0.1, num_leaves=15, max_depth=4)
    predictor = ProductionEnsemblePredictor(
        config=cfg,
        model_version=cfg.model_version,
        margin_head=LightGBMMuHead(target="margin", train=train, model_version=cfg.model_version),
        total_head=LightGBMMuHead(
            target="total",
            train=train,
            model_version=f"{cfg.model_version}-total",
        ),
        sigma_margin_head=LightGBMSigmaHead(
            target="sigma_margin", train=train, model_version=f"{cfg.model_version}-sm"
        ),
        sigma_total_head=LightGBMSigmaHead(
            target="sigma_total", train=train, model_version=f"{cfg.model_version}-st"
        ),
        quantile_margin_head=LightGBMQuantileHead(
            target="margin", train=train, model_version=f"{cfg.model_version}-q"
        ),
        cfbd_lines=cfbd_lines,
        n_mc_draws=int(n_mc_draws) if n_mc_draws is not None else DEFAULT_N_DRAWS,
        n_epistemic_draws=(
            int(n_epistemic_draws) if n_epistemic_draws is not None else default_epistemic_draws()
        ),
        seed=int(cfg.seed),
    )
    return ProductionStack(
        kind=kind,
        config=cfg,
        feature_provider=provider,
        rating_engine=engine,
        predictor=predictor,
    )
