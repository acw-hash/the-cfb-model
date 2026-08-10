"""Walk-forward evaluation harness (DESIGN §7.1 / §7.2 / §15 item 16 / Task 22B).

Rolling-origin season replay: for each test season ``Y``, initialize Stage-1
priors from pre-Week-1 information, then loop weeks — compute features as-of
the configured decision timestamp (default Tuesday 06:00 ET), call the
predictor, record predictions against lines-as-of that timestamp **and**
closing lines, reveal results, and update ratings.

No mapping-layer models live here. The harness is predictor-agnostic; production
stacks are composed in :mod:`ncaa_quant.evaluation.production_stack`. Toy
placeholders live under ``tests/fixtures/`` only.

Ablation switches (A1–A6) live on :class:`WalkForwardConfig` and configure the
production path — they never fork a parallel code path or post-process the
full-system output.

**A1 prior discipline.** ``preseason_priors='league_mean'`` replaces BOTH the
prior mean with the league mean AND the prior variance with a single pooled
variance. Replacing only the mean would confound the prior's location with its
uncertainty.

**A2 scope boundary (headline ablation).** ``rating_updates='frozen_after_week_1'``
freezes the Stage-1 rating *state* after Week 1. It does **not** freeze
season-to-date efficiency features, mapping-layer retrains, or market features,
all of which keep updating. A2 therefore measures the rating engine's
continual-learning contribution specifically and is a **lower bound** on the
system's total in-season learning gain. Task 23 results memos must state this
boundary.

Cardinal rule: every feature / line / rating lookup is bounded by
``event_time < as_of`` (exclusive). The information-set audit recomputes
feature vectors from raw history under that cut and asserts equality with
what the harness fed the predictor.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from ncaa_quant.evaluation.lockbox import assert_lockbox_excluded
from ncaa_quant.utils.seeding import SeedManifest, set_global_seed
from ncaa_quant.utils.timeutils import assert_tz_aware, to_utc

PreseasonPriorsMode = Literal["fitted", "league_mean"]
RatingUpdatesMode = Literal["continual", "frozen_after_week_1"]
MappingLayerMode = Literal["ensemble", "single_lgbm"]
MarketFeatureSource = Literal["snapshots", "cfbd_open_close"]
RunKind = Literal["smoke", "backtest", "production"]

# ---------------------------------------------------------------------------
# Constants / column contracts
# ---------------------------------------------------------------------------

#: Seasons where Odds-API snapshots (not CFBD open/close) back bet-time prices.
SNAPSHOT_BACKED_FROM_SEASON = 2021

#: Default COVID continuity season: ratings update, headline metrics exclude.
DEFAULT_CONTINUITY_SEASONS: tuple[int, ...] = (2020,)

#: Default outer-loop test seasons per §7.2 item 1 (2020 omitted from headline;
#: 2025 is the lockbox per §7.2 item 9 and is excluded from development runs).
DEFAULT_TEST_SEASONS: tuple[int, ...] = (2019, 2021, 2022, 2023, 2024)

#: The season set the frozen D2-D7 canonical frames were built on, which predates
#: the lockbox designation. Kept only so those historical artifacts stay
#: reproducible — never use it for a new evaluation.
HISTORICAL_CANONICAL_SEASONS: tuple[int, ...] = (2019, 2021, 2022, 2023, 2024, 2025)

#: Default mapping-layer warm-up seasons (feature bank + fit before first test).
DEFAULT_WARMUP_SEASONS: tuple[int, ...] = (2014, 2015, 2016, 2017, 2018)

#: Gate: maximum allowed share of exact-zero μ on scored rows.
DEFAULT_MAX_ZERO_MU_RATE: float = 0.001

#: Gate: minimum training games that must back any scored fold.
DEFAULT_MIN_TRAIN_GAMES: int = 50

PREDICTION_ID_NAMESPACE = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")

REQUIRED_GAME_COLS: tuple[str, ...] = (
    "game_id",
    "season",
    "week",
    "event_time",
    "home_team_id",
    "away_team_id",
    "home_points",
    "away_points",
)

# Optional distributional fields emitted by the production predictor (Task 23-FIX).
# When absent from predict(), the row builder writes null-with-indicator — never
# zero-fill or a default constant σ.
DISTRIBUTIONAL_PASS_THROUGH: tuple[str, ...] = (
    "sigma_m",
    "sigma_t",
    "sigma_m_is_missing",
    "sigma_t_is_missing",
    "rho",
    "rho_is_missing",
    "pred_margin_q05",
    "pred_margin_q10",
    "pred_margin_q25",
    "pred_margin_q50",
    "pred_margin_q75",
    "pred_margin_q90",
    "pred_margin_q95",
    "cqr_lo",
    "cqr_hi",
    "cqr_nominal",
    "cqr_is_missing",
    "p_ml_home_raw",
    "p_ats_home_raw",
    "p_ou_over_raw",
    "p_ml_home",
    "p_ats_home",
    "p_ou_over",
    "p_ml_home_is_missing",
    "p_ats_home_is_missing",
    "p_ou_over_is_missing",
)

PREDICTION_COLUMNS: tuple[str, ...] = (
    "prediction_id",
    "run_id",
    "ablation_id",
    "run_kind",
    "n_train_games",
    "game_key",
    "game_id",
    "season",
    "week",
    "as_of",
    "model_version",
    "feature_hash",
    "pred_margin",
    "pred_total",
    "spread_asof",
    "total_asof",
    "line_source_asof",
    "n_books_asof",
    "spread_close",
    "total_close",
    "line_source_close",
    "n_books_close",
    "home_points",
    "away_points",
    "realized_margin",
    "realized_total",
    "exclude_from_headline",
    "is_week1",
    "retrain_epoch",
    "continuity_season",
    "warmup_season",
    "nnls_fallback",
    *DISTRIBUTIONAL_PASS_THROUGH,
)

#: Seasons where A6 ``cfbd_open_close`` market-feature source is valid.
A6_CFBD_SOURCE_SEASONS: tuple[int, ...] = (2021, 2022, 2023, 2024, 2025)


class WalkForwardError(ValueError):
    """Invalid walk-forward inputs or configuration."""


class InformationSetAuditError(AssertionError):
    """Harness features disagree with a strict as-of recompute."""


class PredictionQualityGateError(WalkForwardError):
    """Scored prediction table failed the hard quality gate (D2)."""


@dataclass(frozen=True)
class PredictionQualityGateResult:
    """Diagnostics emitted by :func:`assert_prediction_quality_gate`."""

    n_scored: int
    zero_mu_rate: float
    n_null_mu: int
    n_zero_sd_blocks: int
    zero_sd_blocks: tuple[tuple[int, int], ...]
    min_n_train_games: int
    max_zero_mu_rate: float
    min_train_games_required: int
    passed: bool
    failures: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "n_scored": self.n_scored,
            "zero_mu_rate": self.zero_mu_rate,
            "n_null_mu": self.n_null_mu,
            "n_zero_sd_blocks": self.n_zero_sd_blocks,
            "zero_sd_blocks": [list(b) for b in self.zero_sd_blocks],
            "min_n_train_games": self.min_n_train_games,
            "max_zero_mu_rate": self.max_zero_mu_rate,
            "min_train_games_required": self.min_train_games_required,
            "passed": self.passed,
            "failures": list(self.failures),
        }


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WalkForwardConfig:
    """Configurable replay parameters (Task 16 / Task 22B ablations A1–A6).

    Ablation switches configure the production path; they never fork a parallel
    implementation or post-process full-system predictions. See module docstring
    for A1 prior discipline and A2 scope boundary.

    Parameters
    ----------
    test_seasons:
        Outer-loop seasons to score. Default matches §7.2 item 1.
    continuity_seasons:
        Seasons included for Stage-1 rating continuity but excluded from
        headline metrics unless ``include_continuity_in_headline`` is True
        (§7.2 item 5 — 2020 COVID).
    retrain_weeks:
        Mapping-layer retrain gate weeks within each test season
        (default Weeks 5 and 10 per §9.7). An empty sequence means fit once
        before Week 1 only.
    as_of_weekday:
        Weekday of the decision as-of (Monday=0 … Sunday=6). Default Tuesday.
    as_of_hour / as_of_minute:
        Local clock time in ``as_of_tz``.
    as_of_tz:
        IANA timezone for the decision clock (default America/New_York).
    market_features_available:
        Ablation A3. When False, the feature provider must omit market columns.
    preseason_priors:
        Ablation A1. ``fitted`` uses Task 15 priors; ``league_mean`` replaces
        both prior mean and prior variance with league-pooled values.
    rating_updates:
        Ablation A2. ``frozen_after_week_1`` freezes Stage-1 rating state after
        Week 1 only (see module docstring for scope boundary).
    mapping_layer:
        Ablation A4. ``single_lgbm`` selects unit weight on the LGBM member.
    garbage_time_filter:
        Ablation A5. When False, efficiency/tempo builders keep garbage-time plays.
    market_feature_source:
        Ablation A6. ``cfbd_open_close`` is valid only for 2021–2025; requesting
        it outside that window is a hard error.
    run_id / ablation_id:
        Written onto every prediction row so two runs cannot be confused.
    include_continuity_in_headline:
        Sensitivity flag: if True, continuity seasons are *not* flagged
        ``exclude_from_headline`` (with-and-without comparison per §7.2 item 5).
    seed:
        Global seed for any harness RNG (sampling audits, placeholder fit).
    model_version:
        Written onto every prediction row.
    snapshot_tolerance_minutes_pre_2022_09:
        As-of fallback window for Odds snapshots before Sept 2022 (§3.4).
    snapshot_tolerance_minutes_post_2022_09:
        As-of fallback window for Odds snapshots from Sept 2022 onward.
    """

    test_seasons: tuple[int, ...] = DEFAULT_TEST_SEASONS
    continuity_seasons: tuple[int, ...] = DEFAULT_CONTINUITY_SEASONS
    warmup_seasons: tuple[int, ...] = ()
    retrain_weeks: tuple[int, ...] = (5, 10)
    as_of_weekday: int = 1
    as_of_hour: int = 6
    as_of_minute: int = 0
    as_of_tz: str = "America/New_York"
    market_features_available: bool = True
    preseason_priors: PreseasonPriorsMode = "fitted"
    rating_updates: RatingUpdatesMode = "continual"
    mapping_layer: MappingLayerMode = "ensemble"
    garbage_time_filter: bool = True
    market_feature_source: MarketFeatureSource = "snapshots"
    run_id: str = "default"
    ablation_id: str = "full"
    run_kind: RunKind = "backtest"
    include_continuity_in_headline: bool = False
    seed: int = 42
    model_version: str = "production-v0"
    snapshot_tolerance_minutes_pre_2022_09: int = 10
    snapshot_tolerance_minutes_post_2022_09: int = 5
    min_train_games: int = DEFAULT_MIN_TRAIN_GAMES
    max_zero_mu_rate: float = DEFAULT_MAX_ZERO_MU_RATE
    nnls_equal_weight_fallback: bool = False
    enforce_prediction_quality_gate: bool = False
    lockbox_confirmatory_read: bool = False
    """Permit the lockbox season. Only for the logged annual confirmatory read."""

    def all_replay_seasons(self) -> tuple[int, ...]:
        """Warm-up ∪ test ∪ continuity seasons, sorted unique."""
        return tuple(
            sorted(set(self.warmup_seasons) | set(self.test_seasons) | set(self.continuity_seasons))
        )

    def is_continuity_season(self, season: int) -> bool:
        return int(season) in set(self.continuity_seasons)

    def is_warmup_season(self, season: int) -> bool:
        """Warm-up seasons bank features / fit; not in headline metrics."""
        s = int(season)
        if s in set(self.test_seasons) or s in set(self.continuity_seasons):
            return False
        return s in set(self.warmup_seasons)

    def exclude_from_headline(self, season: int) -> bool:
        if self.is_warmup_season(season):
            return True
        if not self.is_continuity_season(season):
            return False
        return not self.include_continuity_in_headline

    def ablation_settings(self) -> dict[str, Any]:
        """Manifest-ready ablation switch snapshot (A1–A6)."""
        return {
            "A1_preseason_priors": self.preseason_priors,
            "A2_rating_updates": self.rating_updates,
            "A3_market_features_available": self.market_features_available,
            "A4_mapping_layer": self.mapping_layer,
            "A5_garbage_time_filter": self.garbage_time_filter,
            "A6_market_feature_source": self.market_feature_source,
            "run_kind": self.run_kind,
            "nnls_equal_weight_fallback": self.nnls_equal_weight_fallback,
        }

    def validate_ablations(self) -> None:
        """Raise if ablation settings are illegal for the configured seasons."""
        if self.run_kind not in ("smoke", "backtest", "production"):
            msg = f"run_kind must be smoke|backtest|production, got {self.run_kind!r}"
            raise WalkForwardError(msg)
        assert_lockbox_excluded(
            self.all_replay_seasons(),
            context=f"walk-forward run {self.run_id}/{self.ablation_id}",
            confirmatory_read=self.lockbox_confirmatory_read,
        )
        if self.market_feature_source == "cfbd_open_close":
            bad = [s for s in self.all_replay_seasons() if s not in A6_CFBD_SOURCE_SEASONS]
            if bad:
                msg = (
                    "A6 market_feature_source='cfbd_open_close' is valid only for "
                    f"2021-2025; illegal seasons: {bad}"
                )
                raise WalkForwardError(msg)


# ---------------------------------------------------------------------------
# Predictor / feature / rating protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class Predictor(Protocol):
    """Minimal predictor surface for the harness (Task 17 widens this).

    ``predict`` must return a frame with at least ``game_id`` and
    ``pred_margin``. ``pred_total`` is optional (null when absent).
    """

    @property
    def model_version(self) -> str: ...

    def fit(
        self,
        features: pd.DataFrame,
        labels: pd.DataFrame,
        *,
        sample_weight: pd.Series | None = None,
    ) -> None: ...

    def predict(self, features: pd.DataFrame) -> pd.DataFrame: ...


@runtime_checkable
class FeatureProvider(Protocol):
    """Compute per-game feature rows as-of a decision timestamp.

    Implementations must honor ``event_time < as_of`` on every historical join.
    ``market_features`` mirrors :attr:`WalkForwardConfig.market_features_available`.
    """

    def compute_game_features(
        self,
        games: pd.DataFrame,
        as_of: datetime,
        *,
        rating_state: Mapping[str, Any],
        market_features: bool,
    ) -> pd.DataFrame:
        """Return one row per game; must include ``game_id`` and feature columns."""
        ...


@runtime_checkable
class RatingEngine(Protocol):
    """Stage-1 state that initializes from priors and updates after reveals."""

    def initialize_season(self, season: int, as_of: datetime) -> None: ...

    def update_after_games(self, games: pd.DataFrame) -> None: ...

    def state_snapshot(self) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# Decision timestamp + line resolution
# ---------------------------------------------------------------------------


def week1_monday_utc(season: int) -> datetime:
    """UTC Monday 00:00 of CFB Week 1 (Labor Day Monday of ``season``)."""
    for day in range(1, 8):
        candidate = datetime(season, 9, day, tzinfo=UTC)
        if candidate.weekday() == 0:
            return candidate
    msg = f"Labor Day Monday not found for {season}"  # pragma: no cover
    raise RuntimeError(msg)


def week_decision_as_of(
    season: int,
    week: int,
    config: WalkForwardConfig,
) -> datetime:
    """Decision ``as_of`` for ``(season, week)`` in UTC.

    Calendar matches :func:`ncaa_quant.utils.timeutils.week_of`: Week 1's Monday
    is Labor Day; the decision clock is ``as_of_weekday`` / hour / minute in
    ``as_of_tz`` of that week (default Tuesday 06:00 America/New_York).
    """
    if week < 0:
        msg = f"week must be >= 0, got {week}"
        raise WalkForwardError(msg)
    monday_utc = week1_monday_utc(season) + timedelta(weeks=max(0, week) - 1)
    if week == 0:
        # Week 0 sits in the UTC week before Week 1 Monday.
        monday_utc = week1_monday_utc(season) - timedelta(weeks=1)
    tz = ZoneInfo(config.as_of_tz)
    monday_date = monday_utc.date()
    local = datetime(
        monday_date.year,
        monday_date.month,
        monday_date.day,
        config.as_of_hour,
        config.as_of_minute,
        0,
        tzinfo=tz,
    ) + timedelta(days=config.as_of_weekday)
    return to_utc(local)


def _snapshot_tolerance_minutes(as_of: datetime, config: WalkForwardConfig) -> int:
    cutoff = datetime(2022, 9, 1, tzinfo=UTC)
    if to_utc(as_of) < cutoff:
        return config.snapshot_tolerance_minutes_pre_2022_09
    return config.snapshot_tolerance_minutes_post_2022_09


def resolve_lines_for_games(
    games: pd.DataFrame,
    as_of: datetime,
    *,
    snapshots: pd.DataFrame | None,
    cfbd_lines: pd.DataFrame | None,
    config: WalkForwardConfig,
    closing: bool = False,
) -> pd.DataFrame:
    """Resolve spread/total per game under the §7.2 item 8 fallback ladder.

    Bet-time / as-of (``closing=False``):
      Snapshot-backed seasons (``>= 2021``): Odds snapshots only — CFBD never
      enters the feature information set. CFBD-only seasons (``< 2021``):
      median CFBD open (else close).

    Closing lines for evaluation (``closing=True``):
      Prefer Odds snapshot at/before kickoff when present; if unresolved,
      fall back to median CFBD ``line_type=close``. Closing lines are
      evaluation-only (ATS/OU@close, encompassing, CLV) — never features.
    """
    assert_tz_aware(as_of)
    as_of_utc = to_utc(as_of)
    rows: list[dict[str, Any]] = []
    snap = snapshots if snapshots is not None else pd.DataFrame()
    lines = cfbd_lines if cfbd_lines is not None else pd.DataFrame()

    for row in games.itertuples(index=False):
        season = int(row.season)
        game_id = int(row.game_id)
        kickoff = to_utc(pd.Timestamp(row.event_time).to_pydatetime())
        bound = kickoff if closing else as_of_utc
        if season >= SNAPSHOT_BACKED_FROM_SEASON:
            resolved = _resolve_from_snapshots(
                snap,
                game_id=game_id,
                game_key=str(getattr(row, "game_key", "") or ""),
                bound=bound,
                config=config,
            )
            # Evaluation closes: fill gaps from CFBD so ATS/encompassing are
            # not starved when Odds snapshots were never ingested. Bet-time
            # as-of paths keep the null (no CFBD leak into features).
            if closing and (
                resolved["line_source"] == "null" or not np.isfinite(float(resolved["spread"]))
            ):
                cfbd = _resolve_from_cfbd(lines, game_id=game_id, closing=True)
                if cfbd["line_source"] != "null" and np.isfinite(float(cfbd["spread"])):
                    resolved = {**cfbd, "line_source": "cfbd_close_eval"}
        else:
            resolved = _resolve_from_cfbd(lines, game_id=game_id, closing=closing)
        rows.append({"game_id": game_id, **resolved})
    return (
        pd.DataFrame(rows)
        if rows
        else pd.DataFrame(
            columns=[
                "game_id",
                "spread",
                "total",
                "line_source",
                "n_books",
            ]
        )
    )


def _resolve_from_snapshots(
    snapshots: pd.DataFrame,
    *,
    game_id: int,
    game_key: str,
    bound: datetime,
    config: WalkForwardConfig,
) -> dict[str, Any]:
    empty = {
        "spread": float("nan"),
        "total": float("nan"),
        "line_source": "null",
        "n_books": 0,
    }
    if snapshots.empty:
        return empty
    work = snapshots.copy()
    if "event_time" not in work.columns:
        return empty
    work["event_time"] = pd.to_datetime(work["event_time"], utc=True)
    mask = work["event_time"] < pd.Timestamp(bound)
    if "game_id" in work.columns:
        id_mask = work["game_id"].notna() & (work["game_id"].astype("Int64") == game_id)
        mask = mask & id_mask
    elif game_key and "game_key" in work.columns:
        mask = mask & (work["game_key"] == game_key)
    else:
        return empty
    eligible = work.loc[mask]
    if eligible.empty:
        return empty

    # Prefer exact decision_point when present; else latest before bound.
    tol = timedelta(minutes=_snapshot_tolerance_minutes(bound, config))
    latest_ts = eligible["event_time"].max()
    if latest_ts < pd.Timestamp(bound) - tol:
        # Outside tolerance — still take nearest earlier (logged as fallback).
        source = "odds_api_snapshot_fallback"
    else:
        source = "odds_api_snapshot"

    window = eligible.loc[eligible["event_time"] == latest_ts]
    n_books = int(window["book"].nunique()) if "book" in window.columns else 0
    if "n_books_available" in window.columns and window["n_books_available"].notna().any():
        n_books = int(window["n_books_available"].dropna().iloc[0])

    spread = float("nan")
    total = float("nan")
    if "market" in window.columns and "line" in window.columns:
        spreads = window.loc[window["market"] == "spread", "line"].dropna()
        totals = window.loc[window["market"] == "total", "line"].dropna()
        if not spreads.empty:
            spread = float(spreads.median())
        if not totals.empty:
            total = float(totals.median())
    return {
        "spread": spread,
        "total": total,
        "line_source": source,
        "n_books": n_books,
    }


def _resolve_from_cfbd(
    lines: pd.DataFrame,
    *,
    game_id: int,
    closing: bool,
) -> dict[str, Any]:
    empty = {
        "spread": float("nan"),
        "total": float("nan"),
        "line_source": "null",
        "n_books": 0,
    }
    if lines.empty or "game_id" not in lines.columns:
        return empty
    sub = lines.loc[lines["game_id"] == game_id]
    if sub.empty:
        return empty
    preferred = "close" if closing else "close"
    # As-of Tuesday for CFBD-only seasons: prefer open when present, else close.
    if not closing and "line_type" in sub.columns and (sub["line_type"] == "open").any():
        preferred = "open"
    typed = sub.loc[sub["line_type"] == preferred] if "line_type" in sub.columns else sub
    if typed.empty:
        typed = sub
    n_books = int(typed["book"].nunique()) if "book" in typed.columns else 0
    spread = (
        float(typed["spread"].median())
        if "spread" in typed.columns and typed["spread"].notna().any()
        else float("nan")
    )
    total = (
        float(typed["total"].median())
        if "total" in typed.columns and typed["total"].notna().any()
        else float("nan")
    )
    source = f"cfbd_{preferred}"
    return {
        "spread": spread,
        "total": total,
        "line_source": source,
        "n_books": n_books,
    }


# ---------------------------------------------------------------------------
# Feature hashing + prediction ids
# ---------------------------------------------------------------------------


def _optional_pred_value(pred_map: pd.DataFrame, gid: int, col: str) -> Any:
    """Read an optional distributional column; NaN when absent (never default-filled)."""
    if col not in pred_map.columns or gid not in pred_map.index:
        return float("nan")
    val = pred_map.loc[gid, col]
    if isinstance(val, (pd.Series, np.ndarray)):
        val = val.iloc[0] if hasattr(val, "iloc") else val[0]
    if pd.isna(val):
        return float("nan")
    if col.endswith("_is_missing") or col == "cqr_is_missing":
        return bool(val)
    if col in {"cqr_nominal"}:
        return float(val)
    return float(val)


def _distributional_row_fields(pred_map: pd.DataFrame, gid: int) -> dict[str, Any]:
    """Copy optional distributional columns; null-with-indicator when absent.

    Never zero-fills. Never substitutes a default constant (e.g. σ=14).
    """
    out: dict[str, Any] = {}
    for col in DISTRIBUTIONAL_PASS_THROUGH:
        if col.endswith("_is_missing"):
            if col == "cqr_is_missing":
                present = all(
                    c in pred_map.columns
                    and gid in pred_map.index
                    and pd.notna(pred_map.loc[gid, c])
                    for c in ("cqr_lo", "cqr_hi")
                )
            else:
                base = col[: -len("_is_missing")]
                present = (
                    base in pred_map.columns
                    and gid in pred_map.index
                    and pd.notna(pred_map.loc[gid, base])
                )
            if col in pred_map.columns and gid in pred_map.index:
                raw = pred_map.loc[gid, col]
                out[col] = bool(raw) if pd.notna(raw) else (not present)
            else:
                out[col] = not present
        else:
            out[col] = _optional_pred_value(pred_map, gid, col)
    return out


def feature_hash_row(row: Mapping[str, Any], feature_cols: Sequence[str]) -> str:
    """Stable SHA-256 of sorted feature name/value pairs (12-char hex prefix)."""
    payload: dict[str, Any] = {}
    for col in sorted(feature_cols):
        val = row.get(col, None)
        if val is None or (isinstance(val, float) and math.isnan(val)):
            payload[col] = None
        elif isinstance(val, (np.floating, float)):
            payload[col] = float(val)
        elif isinstance(val, (np.integer, int)):
            payload[col] = int(val)
        else:
            payload[col] = str(val)
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return digest[:16]


def make_prediction_id(
    *,
    game_key: str,
    as_of: datetime,
    model_version: str,
    feature_hash: str,
) -> str:
    """Deterministic UUIDv5 from identity fields (byte-stable across runs)."""
    as_of_utc = to_utc(as_of).isoformat()
    name = f"{model_version}|{game_key}|{as_of_utc}|{feature_hash}"
    return str(uuid.uuid5(PREDICTION_ID_NAMESPACE, name))


def game_key_for_row(row: Any) -> str:
    """Prefer explicit ``game_key``; else deterministic id-based fallback."""
    explicit = getattr(row, "game_key", None)
    if (
        explicit is not None
        and str(explicit)
        and not (isinstance(explicit, float) and math.isnan(explicit))
    ):
        return str(explicit)
    kickoff = pd.Timestamp(row.event_time).tz_convert("UTC")
    return (
        f"{int(row.season)}:{int(row.home_team_id)}:"
        f"{int(row.away_team_id)}:{kickoff.date().isoformat()}"
    )


def _feature_columns(features: pd.DataFrame) -> list[str]:
    skip = {
        "game_id",
        "game_key",
        "season",
        "week",
        "as_of",
        "home_team_id",
        "away_team_id",
        "event_time",
    }
    return [c for c in features.columns if c not in skip]


def scored_prediction_rows(predictions: pd.DataFrame) -> pd.DataFrame:
    """Rows eligible for the quality gate and headline metrics.

    Excludes continuity / warm-up rows flagged ``exclude_from_headline``.
    """
    if predictions.empty:
        return predictions.copy()
    frame = predictions
    if "exclude_from_headline" in frame.columns:
        frame = frame.loc[~frame["exclude_from_headline"].fillna(False).astype(bool)]
    if "warmup_season" in frame.columns:
        frame = frame.loc[~frame["warmup_season"].fillna(False).astype(bool)]
    return frame.copy()


def assert_prediction_quality_gate(
    predictions: pd.DataFrame,
    *,
    max_zero_mu_rate: float = DEFAULT_MAX_ZERO_MU_RATE,
    min_train_games: int = DEFAULT_MIN_TRAIN_GAMES,
    raise_on_fail: bool = True,
) -> PredictionQualityGateResult:
    """Hard gate on every scored prediction table before metrics (D2).

    Fails loudly when any of the following hold on scored rows:
    - ``zero_mu_rate > max_zero_mu_rate``
    - ``SD(mu) == 0`` within any ``(season, week)`` block with ≥2 games
    - any scored row has a null μ
    - fewer than ``min_train_games`` backed any scored fold
    """
    scored = scored_prediction_rows(predictions)
    failures: list[str] = []
    if scored.empty:
        result = PredictionQualityGateResult(
            n_scored=0,
            zero_mu_rate=float("nan"),
            n_null_mu=0,
            n_zero_sd_blocks=0,
            zero_sd_blocks=(),
            min_n_train_games=0,
            max_zero_mu_rate=float(max_zero_mu_rate),
            min_train_games_required=int(min_train_games),
            passed=False,
            failures=("no scored prediction rows",),
        )
        if raise_on_fail:
            raise PredictionQualityGateError(str(result.failures))
        return result

    if "pred_margin" not in scored.columns:
        msg = "scored predictions missing pred_margin"
        if raise_on_fail:
            raise PredictionQualityGateError(msg)
        return PredictionQualityGateResult(
            n_scored=len(scored),
            zero_mu_rate=float("nan"),
            n_null_mu=0,
            n_zero_sd_blocks=0,
            zero_sd_blocks=(),
            min_n_train_games=0,
            max_zero_mu_rate=float(max_zero_mu_rate),
            min_train_games_required=int(min_train_games),
            passed=False,
            failures=(msg,),
        )

    mu = pd.to_numeric(scored["pred_margin"], errors="coerce")
    n_null = int(mu.isna().sum())
    if n_null > 0:
        failures.append(f"null mu on {n_null} scored row(s)")

    finite = mu[mu.notna()]
    zero_mu_rate = float((finite == 0.0).mean()) if len(finite) else float("nan")
    if np.isfinite(zero_mu_rate) and zero_mu_rate > float(max_zero_mu_rate):
        failures.append(f"zero_mu_rate={zero_mu_rate:.6f} > max_zero_mu_rate={max_zero_mu_rate}")

    zero_sd_blocks: list[tuple[int, int]] = []
    if {"season", "week"} <= set(scored.columns):
        for (season, week), chunk in scored.groupby(["season", "week"], sort=True):
            vals = pd.to_numeric(chunk["pred_margin"], errors="coerce").dropna()
            if len(vals) < 2:
                continue
            if float(vals.std(ddof=0)) == 0.0:
                zero_sd_blocks.append((int(season), int(week)))
    if zero_sd_blocks:
        failures.append(
            f"SD(mu)=0 in {len(zero_sd_blocks)} (season, week) block(s): "
            f"{zero_sd_blocks[:8]}{'…' if len(zero_sd_blocks) > 8 else ''}"
        )

    min_train = 0
    if "n_train_games" in scored.columns:
        train_vals = pd.to_numeric(scored["n_train_games"], errors="coerce").dropna()
        if len(train_vals):
            min_train = int(train_vals.min())
            if min_train < int(min_train_games):
                failures.append(f"min n_train_games={min_train} < required {min_train_games}")
        else:
            failures.append("n_train_games column present but all null")
    else:
        failures.append("n_train_games column missing from prediction table")

    result = PredictionQualityGateResult(
        n_scored=int(len(scored)),
        zero_mu_rate=float(zero_mu_rate) if np.isfinite(zero_mu_rate) else float("nan"),
        n_null_mu=n_null,
        n_zero_sd_blocks=len(zero_sd_blocks),
        zero_sd_blocks=tuple(zero_sd_blocks),
        min_n_train_games=min_train,
        max_zero_mu_rate=float(max_zero_mu_rate),
        min_train_games_required=int(min_train_games),
        passed=not failures,
        failures=tuple(failures),
    )
    if raise_on_fail and failures:
        raise PredictionQualityGateError("prediction quality gate failed: " + "; ".join(failures))
    return result


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------


@dataclass
class FeatureLogEntry:
    """One feature vector the harness fed the predictor (for PIT audit)."""

    season: int
    week: int
    as_of: datetime
    game_id: int
    game_key: str
    features: dict[str, Any]
    feature_hash: str


@dataclass
class WalkForwardResult:
    """Outputs of a full harness run."""

    predictions: pd.DataFrame
    feature_log: pd.DataFrame
    config: WalkForwardConfig
    seed_manifest: SeedManifest
    retrain_events: list[dict[str, Any]] = field(default_factory=list)
    quality_gate: PredictionQualityGateResult | None = None

    def headline_predictions(self) -> pd.DataFrame:
        """Predictions eligible for headline metrics (excludes continuity)."""
        if self.predictions.empty:
            return self.predictions.copy()
        return self.predictions.loc[~self.predictions["exclude_from_headline"]].copy()

    def store_predictions(self, path: Path | str) -> Path:
        """Write the metrics-ready predictions table to Parquet."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        frame = self.predictions.copy()
        # Stable column order + row order for byte-identical reruns.
        cols = [c for c in PREDICTION_COLUMNS if c in frame.columns]
        extra = [c for c in frame.columns if c not in cols]
        ordered = (
            frame[cols + extra]
            .sort_values(["season", "week", "game_id"], kind="mergesort")
            .reset_index(drop=True)
        )
        ordered.to_parquet(target, index=False)
        return target


@dataclass
class InformationSetAuditMismatch:
    """One (season, week, game) where harness ≠ recomputed features."""

    season: int
    week: int
    game_id: int
    feature: str
    harness_value: Any
    recomputed_value: Any


@dataclass
class InformationSetAuditResult:
    """Summary of the information-set audit."""

    n_week_points: int
    n_games_checked: int
    mismatches: list[InformationSetAuditMismatch] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.mismatches


@dataclass
class ShiftedLabelResult:
    """Outcome of the retired shifted-label hook — **diagnostic only**.

    .. warning::

       The null this measures is wrong, and §14 as amended deletes it (audit A-8).
       "Future features must not predict past games above chance" is false: team
       strength persists, so a November rating is legitimately informative about a
       September game. A leak-free system fails this test, and the only ways to
       make it pass are to loosen the threshold until it means nothing or to break
       the feature pipeline.

       ``passed`` is therefore **not** evidence of anything and must not gate
       promotion. Use :mod:`ncaa_quant.evaluation.leakage` — within-week label
       permutation, planted prophecy, and as-of sensitivity — which are
       falsifiable for the right reason.

       The one still-valid use is as a *cheater detector*: a model that reads
       leaked outcomes will beat chance here, so a large improvement remains
       informative even though scoring at chance proves nothing.
    """

    model_score: float
    chance_score: float
    metric: str
    n: int
    tolerance: float
    passed: bool
    detail: str = ""
    #: Marks that ``passed`` came from a null the spec deleted.
    null_is_invalid: bool = True


# ---------------------------------------------------------------------------
# Core harness
# ---------------------------------------------------------------------------


class WalkForwardHarness:
    """§7.2 rolling-origin replay engine.

    Parameters
    ----------
    config:
        Replay configuration.
    predictor:
        Mapping-layer predictor (placeholder or real).
    feature_provider:
        As-of feature computer.
    rating_engine:
        Stage-1 state manager.
    """

    def __init__(
        self,
        config: WalkForwardConfig,
        predictor: Predictor,
        feature_provider: FeatureProvider,
        rating_engine: RatingEngine,
    ) -> None:
        self.config = config
        self.predictor = predictor
        self.feature_provider = feature_provider
        self.rating_engine = rating_engine

    def run(
        self,
        games: pd.DataFrame,
        *,
        snapshots: pd.DataFrame | None = None,
        cfbd_lines: pd.DataFrame | None = None,
        train_labels_seed: pd.DataFrame | None = None,
        train_features_seed: pd.DataFrame | None = None,
    ) -> WalkForwardResult:
        """Replay all configured seasons week-by-week.

        ``games`` must carry :data:`REQUIRED_GAME_COLS`. Completed games need
        non-null ``home_points`` / ``away_points`` for reveal + label joins.
        ``train_labels_seed`` / ``train_features_seed`` optionally supply
        pre-test-season labels and as-of feature rows for the initial fit.
        At every retrain gate the harness passes the accumulated as-of feature
        frame for revealed games — never an empty frame.
        """
        self.config.validate_ablations()
        _validate_games(games)
        manifest = set_global_seed(self.config.seed)

        work = games.copy()
        work["event_time"] = [to_utc(pd.Timestamp(ts).to_pydatetime()) for ts in work["event_time"]]
        if "game_key" not in work.columns:
            work["game_key"] = [game_key_for_row(r) for r in work.itertuples(index=False)]
        work["realized_margin"] = work["home_points"].astype(float) - work["away_points"].astype(
            float
        )
        work["realized_total"] = work["home_points"].astype(float) + work["away_points"].astype(
            float
        )

        prediction_rows: list[dict[str, Any]] = []
        feature_log_rows: list[dict[str, Any]] = []
        retrain_events: list[dict[str, Any]] = []
        revealed_labels = (
            train_labels_seed.copy()
            if train_labels_seed is not None
            else pd.DataFrame(
                columns=["game_id", "season", "week", "realized_margin", "realized_total"]
            )
        )
        # game_id → as-of feature row accumulated at prediction time (Task 17 seam).
        feature_bank: dict[int, dict[str, Any]] = {}
        if train_features_seed is not None and not train_features_seed.empty:
            for row in train_features_seed.to_dict(orient="records"):
                gid = int(row["game_id"])
                feature_bank[gid] = {k: v for k, v in row.items() if k != "game_id"}

        def _features_for_labels(labels: pd.DataFrame) -> pd.DataFrame:
            if labels.empty:
                return pd.DataFrame()
            rows: list[dict[str, Any]] = []
            for gid in labels["game_id"].astype(int):
                cached = feature_bank.get(int(gid))
                if cached is None:
                    continue
                rows.append({"game_id": int(gid), **cached})
            if not rows:
                return pd.DataFrame()
            return pd.DataFrame(rows)

        # Initial fit on any seed labels (pre-test history).
        retrain_epoch = 0
        if not revealed_labels.empty:
            seed_feats = _features_for_labels(revealed_labels)
            self._retrain(
                seed_feats,
                revealed_labels,
                retrain_epoch=retrain_epoch,
                season=0,
                week=0,
            )
            retrain_events.append(
                {
                    "season": None,
                    "week": None,
                    "retrain_epoch": retrain_epoch,
                    "n": len(revealed_labels),
                }
            )

        for season in self.config.all_replay_seasons():
            season_games = work.loc[work["season"] == season]
            if season_games.empty:
                continue
            weeks = sorted(int(w) for w in season_games["week"].unique())
            # Pre-Week-1 prior init: as_of just before the first week's decision.
            first_as_of = week_decision_as_of(season, weeks[0], self.config)
            prior_as_of = first_as_of - timedelta(seconds=1)
            self.rating_engine.initialize_season(season, prior_as_of)

            # Offseason mapping retrain on all revealed labels (seasons < Y).
            retrain_epoch += 1
            self._retrain(
                _features_for_labels(revealed_labels),
                revealed_labels,
                retrain_epoch=retrain_epoch,
                season=int(season),
                week=0,
            )
            retrain_events.append(
                {
                    "season": season,
                    "week": 0,
                    "retrain_epoch": retrain_epoch,
                    "n": len(revealed_labels),
                }
            )

            for week in weeks:
                as_of = week_decision_as_of(season, week, self.config)
                week_games = season_games.loc[season_games["week"] == week].copy()
                week_games = week_games.sort_values("game_id", kind="mergesort")

                if week in self.config.retrain_weeks:
                    retrain_epoch += 1
                    self._retrain(
                        _features_for_labels(revealed_labels),
                        revealed_labels,
                        retrain_epoch=retrain_epoch,
                        season=int(season),
                        week=int(week),
                    )
                    retrain_events.append(
                        {
                            "season": season,
                            "week": week,
                            "retrain_epoch": retrain_epoch,
                            "n": len(revealed_labels),
                        }
                    )

                rating_state = self.rating_engine.state_snapshot()
                features = self.feature_provider.compute_game_features(
                    week_games,
                    as_of,
                    rating_state=rating_state,
                    market_features=self.config.market_features_available,
                )
                features = _align_features_to_games(features, week_games)
                feat_cols = _feature_columns(features)

                # Bank as-of feature rows for subsequent retrain gates.
                for row in features.to_dict(orient="records"):
                    gid = int(row["game_id"])
                    feature_bank[gid] = {c: row[c] for c in feat_cols}

                n_train_games = int(len(revealed_labels))
                is_warmup = self.config.is_warmup_season(season)
                predictor_fitted = bool(getattr(self.predictor, "is_fitted", True))
                if not predictor_fitted:
                    # Bank + reveal only until the mapping layer has a real fit.
                    # Never emit a silent zero-μ row (D2 / NotFittedError contract).
                    self.rating_engine.update_after_games(week_games)
                    new_labels = week_games[
                        [
                            "game_id",
                            "season",
                            "week",
                            "realized_margin",
                            "realized_total",
                        ]
                    ].copy()
                    if revealed_labels.empty:
                        revealed_labels = new_labels.reset_index(drop=True)
                    else:
                        revealed_labels = pd.concat(
                            [revealed_labels, new_labels],
                            ignore_index=True,
                        )
                    # Opportunistic fit once enough labels exist (covers configs
                    # with empty retrain_weeks). Thin cold-start fits may fail
                    # for some heads (e.g. CatBoost on constant features); leave
                    # unfitted and keep banking rather than emitting zeros.
                    if len(revealed_labels) >= 2:
                        try:
                            retrain_epoch += 1
                            self._retrain(
                                _features_for_labels(revealed_labels),
                                revealed_labels,
                                retrain_epoch=retrain_epoch,
                                season=int(season),
                                week=int(week),
                            )
                            retrain_events.append(
                                {
                                    "season": season,
                                    "week": week,
                                    "retrain_epoch": retrain_epoch,
                                    "n": len(revealed_labels),
                                    "reason": "cold_start_catchup",
                                }
                            )
                        except Exception:  # noqa: BLE001 — thin cold-start fit may fail per-head
                            pass
                    continue

                preds = self.predictor.predict(features)
                if "game_id" not in preds.columns:
                    msg = "predictor.predict must return game_id"
                    raise WalkForwardError(msg)
                pred_map = preds.set_index("game_id")

                asof_lines = resolve_lines_for_games(
                    week_games,
                    as_of,
                    snapshots=snapshots,
                    cfbd_lines=cfbd_lines,
                    config=self.config,
                    closing=False,
                ).set_index("game_id")
                close_lines = resolve_lines_for_games(
                    week_games,
                    as_of,
                    snapshots=snapshots,
                    cfbd_lines=cfbd_lines,
                    config=self.config,
                    closing=True,
                ).set_index("game_id")

                nnls_fallback = getattr(self.predictor, "nnls_fallback", None)

                for grow in week_games.itertuples(index=False):
                    gid = int(grow.game_id)
                    gkey = str(grow.game_key)
                    feat_row = features.loc[features["game_id"] == gid]
                    if feat_row.empty:
                        msg = f"missing features for game_id={gid}"
                        raise WalkForwardError(msg)
                    feat_dict = {c: feat_row.iloc[0][c] for c in feat_cols}
                    fhash = feature_hash_row(feat_dict, feat_cols)
                    model_version = getattr(
                        self.predictor, "model_version", self.config.model_version
                    )
                    pid = make_prediction_id(
                        game_key=gkey,
                        as_of=as_of,
                        model_version=str(model_version),
                        feature_hash=fhash,
                    )
                    pred_margin = float(pred_map.loc[gid, "pred_margin"])
                    pred_total = (
                        float(pred_map.loc[gid, "pred_total"])
                        if "pred_total" in pred_map.columns
                        and pd.notna(pred_map.loc[gid, "pred_total"])
                        else float("nan")
                    )
                    a_line = asof_lines.loc[gid] if gid in asof_lines.index else None
                    c_line = close_lines.loc[gid] if gid in close_lines.index else None

                    row_dict: dict[str, Any] = {
                        "prediction_id": pid,
                        "run_id": self.config.run_id,
                        "ablation_id": self.config.ablation_id,
                        "run_kind": self.config.run_kind,
                        "n_train_games": n_train_games,
                        "game_key": gkey,
                        "game_id": gid,
                        "season": season,
                        "week": week,
                        "as_of": as_of,
                        "model_version": str(model_version),
                        "feature_hash": fhash,
                        "pred_margin": pred_margin,
                        "pred_total": pred_total,
                        "spread_asof": (
                            float(a_line["spread"]) if a_line is not None else float("nan")
                        ),
                        "total_asof": (
                            float(a_line["total"]) if a_line is not None else float("nan")
                        ),
                        "line_source_asof": (
                            str(a_line["line_source"]) if a_line is not None else "null"
                        ),
                        "n_books_asof": (int(a_line["n_books"]) if a_line is not None else 0),
                        "spread_close": (
                            float(c_line["spread"]) if c_line is not None else float("nan")
                        ),
                        "total_close": (
                            float(c_line["total"]) if c_line is not None else float("nan")
                        ),
                        "line_source_close": (
                            str(c_line["line_source"]) if c_line is not None else "null"
                        ),
                        "n_books_close": (int(c_line["n_books"]) if c_line is not None else 0),
                        "home_points": (
                            int(grow.home_points) if pd.notna(grow.home_points) else pd.NA
                        ),
                        "away_points": (
                            int(grow.away_points) if pd.notna(grow.away_points) else pd.NA
                        ),
                        "realized_margin": float(grow.realized_margin),
                        "realized_total": float(grow.realized_total),
                        "exclude_from_headline": self.config.exclude_from_headline(season),
                        "is_week1": week == weeks[0] or week == 1,
                        "retrain_epoch": retrain_epoch,
                        "continuity_season": self.config.is_continuity_season(season),
                        "warmup_season": is_warmup,
                        "nnls_fallback": (
                            str(nnls_fallback) if nnls_fallback is not None else None
                        ),
                    }
                    row_dict.update(_distributional_row_fields(pred_map, gid))
                    prediction_rows.append(row_dict)
                    feature_log_rows.append(
                        {
                            "season": season,
                            "week": week,
                            "as_of": as_of,
                            "game_id": gid,
                            "game_key": gkey,
                            "feature_hash": fhash,
                            **{f"feat__{k}": v for k, v in feat_dict.items()},
                        }
                    )

                # Reveal: update ratings, append labels.
                self.rating_engine.update_after_games(week_games)
                new_labels = week_games[
                    [
                        "game_id",
                        "season",
                        "week",
                        "realized_margin",
                        "realized_total",
                    ]
                ].copy()
                if revealed_labels.empty:
                    revealed_labels = new_labels.reset_index(drop=True)
                else:
                    revealed_labels = pd.concat(
                        [revealed_labels, new_labels],
                        ignore_index=True,
                    )

        predictions = (
            pd.DataFrame(prediction_rows)
            if prediction_rows
            else pd.DataFrame(columns=list(PREDICTION_COLUMNS))
        )
        if not predictions.empty:
            predictions = predictions.sort_values(
                ["season", "week", "game_id"], kind="mergesort"
            ).reset_index(drop=True)
        feature_log = (
            pd.DataFrame(feature_log_rows)
            if feature_log_rows
            else pd.DataFrame(
                columns=[
                    "season",
                    "week",
                    "as_of",
                    "game_id",
                    "game_key",
                    "feature_hash",
                ]
            )
        )
        gate: PredictionQualityGateResult | None = None
        if self.config.enforce_prediction_quality_gate:
            # Smoke runs must still pass the structural gate; they are refused
            # later by the reporter when emitting headline metrics.
            gate = assert_prediction_quality_gate(
                predictions,
                max_zero_mu_rate=self.config.max_zero_mu_rate,
                min_train_games=self.config.min_train_games,
                raise_on_fail=True,
            )
        return WalkForwardResult(
            predictions=predictions,
            feature_log=feature_log,
            config=self.config,
            seed_manifest=manifest,
            retrain_events=retrain_events,
            quality_gate=gate,
        )

    def _retrain(
        self,
        features: pd.DataFrame,
        labels: pd.DataFrame,
        *,
        retrain_epoch: int,
        season: int | None = None,
        week: int | None = None,
    ) -> None:
        del retrain_epoch
        # Point-in-time expected-possessions refit (DESIGN §4.5 / Task 11).
        # Never loads a globally-fitted live artifact inside the walk-forward.
        fit_poss = getattr(self.feature_provider, "fit_possessions_at_retrain", None)
        if callable(fit_poss) and season is not None and week is not None:
            fit_poss(int(season), int(week))
        self.predictor.fit(features, labels)


# ---------------------------------------------------------------------------
# Information-set audit
# ---------------------------------------------------------------------------


def audit_information_set(
    feature_log: pd.DataFrame,
    feature_provider: FeatureProvider,
    games: pd.DataFrame,
    *,
    rating_snapshots: Mapping[tuple[int, int], Mapping[str, Any]] | None = None,
    market_features: bool = True,
    sample_week_points: Sequence[tuple[int, int]] | None = None,
    rtol: float = 1e-9,
    atol: float = 1e-9,
) -> InformationSetAuditResult:
    """Recompute features for sampled ``(season, week)`` points; assert equality.

    For each week-point the audit rebuilds the game subset, calls
    ``feature_provider.compute_game_features`` with the logged ``as_of``, and
    compares every ``feat__*`` column in ``feature_log`` to the recompute.

    ``rating_snapshots`` optionally supplies the rating state that was live at
    that week-point (default: empty dict — providers that ignore ratings still
    audit cleanly).
    """
    if feature_log.empty:
        return InformationSetAuditResult(n_week_points=0, n_games_checked=0)

    points = sample_week_points
    if points is None:
        pairs = (
            feature_log[["season", "week"]]
            .drop_duplicates()
            .sort_values(["season", "week"], kind="mergesort")
        )
        points = [(int(r.season), int(r.week)) for r in pairs.itertuples(index=False)]

    mismatches: list[InformationSetAuditMismatch] = []
    n_games = 0
    snapshots = rating_snapshots or {}

    for season, week in points:
        log_sub = feature_log.loc[(feature_log["season"] == season) & (feature_log["week"] == week)]
        if log_sub.empty:
            continue
        as_of = to_utc(pd.Timestamp(log_sub.iloc[0]["as_of"]).to_pydatetime())
        week_games = games.loc[(games["season"] == season) & (games["week"] == week)].copy()
        if week_games.empty:
            continue
        rating_state = dict(snapshots.get((season, week), {}))
        recomputed = feature_provider.compute_game_features(
            week_games,
            as_of,
            rating_state=rating_state,
            market_features=market_features,
        )
        recomputed = _align_features_to_games(recomputed, week_games)
        feat_cols = [c[len("feat__") :] for c in log_sub.columns if c.startswith("feat__")]
        for _, log_row in log_sub.iterrows():
            gid = int(log_row["game_id"])
            rec_row = recomputed.loc[recomputed["game_id"] == gid]
            if rec_row.empty:
                mismatches.append(
                    InformationSetAuditMismatch(
                        season=season,
                        week=week,
                        game_id=gid,
                        feature="__missing_row__",
                        harness_value=None,
                        recomputed_value=None,
                    )
                )
                continue
            n_games += 1
            for col in feat_cols:
                h_val = log_row[f"feat__{col}"]
                r_val = rec_row.iloc[0][col] if col in rec_row.columns else None
                if not _values_equal(h_val, r_val, rtol=rtol, atol=atol):
                    mismatches.append(
                        InformationSetAuditMismatch(
                            season=season,
                            week=week,
                            game_id=gid,
                            feature=col,
                            harness_value=h_val,
                            recomputed_value=r_val,
                        )
                    )
    return InformationSetAuditResult(
        n_week_points=len(points),
        n_games_checked=n_games,
        mismatches=mismatches,
    )


def assert_information_set_clean(result: InformationSetAuditResult) -> None:
    """Raise :class:`InformationSetAuditError` when the audit failed."""
    if result.passed:
        return
    sample = result.mismatches[:5]
    detail = "; ".join(
        f"(s={m.season} w={m.week} g={m.game_id} {m.feature}: "
        f"{m.harness_value!r} vs {m.recomputed_value!r})"
        for m in sample
    )
    msg = (
        f"information-set audit failed: {len(result.mismatches)} mismatches "
        f"across {result.n_week_points} week-points — {detail}"
    )
    raise InformationSetAuditError(msg)


# ---------------------------------------------------------------------------
# Shifted-label hook (§14)
# ---------------------------------------------------------------------------


def chance_mae_constant(labels: pd.Series, constant: float = 0.0) -> float:
    """MAE of predicting a constant — the chance baseline for margin regression."""
    y = labels.astype(float).dropna()
    if y.empty:
        return float("nan")
    return float(np.mean(np.abs(y.to_numpy() - constant)))


def build_shifted_feature_frame(
    games: pd.DataFrame,
    feature_provider: FeatureProvider,
    shifted_as_of: datetime,
    *,
    rating_state: Mapping[str, Any] | None = None,
    market_features: bool = True,
) -> pd.DataFrame:
    """Compute features for ``games`` at a *future* ``shifted_as_of``.

    This is the leakage-side input for :func:`run_shifted_label_test`: the
    provider is asked for information that production would not have had at
    the original decision time. Honest providers that cut on
    ``event_time < as_of`` will still emit post-decision facts when
    ``shifted_as_of`` is moved forward — that is intentional for the test.
    """
    assert_tz_aware(shifted_as_of)
    work = games.copy()
    if "realized_margin" not in work.columns:
        work["realized_margin"] = work["home_points"].astype(float) - work["away_points"].astype(
            float
        )
    features = feature_provider.compute_game_features(
        work,
        to_utc(shifted_as_of),
        rating_state=dict(rating_state or {}),
        market_features=market_features,
    )
    features = _align_features_to_games(features, work)
    labels = work[["game_id", "realized_margin"]].drop_duplicates("game_id")
    out = features.merge(labels, on="game_id", how="left")
    return out.reset_index(drop=True)


def run_shifted_label_test(
    predictor: Predictor,
    games: pd.DataFrame,
    feature_provider: FeatureProvider,
    shifted_as_of: datetime,
    *,
    rating_state: Mapping[str, Any] | None = None,
    market_features: bool = True,
    metric: str = "mae",
    chance_constant: float | None = None,
    tolerance: float = 0.05,
    relative: bool = True,
) -> ShiftedLabelResult:
    """Score ``predictor`` on future-features → past-labels. **Diagnostic only.**

    Retained as a cheater detector, not as a gate: see
    :class:`ShiftedLabelResult` for why the underlying null was deleted from §14
    (audit A-8). A model that beats chance here is worth investigating; one that
    scores at chance has demonstrated nothing.

    The hook builds features at ``shifted_as_of`` (after the games), predicts, and
    reports whether the model beat the constant-baseline MAE by more than
    ``tolerance``.

    Chance baseline defaults to predicting the sample mean of the shifted
    labels (best constant predictor). The placeholder (ignores features)
    therefore sits at chance; a cheater that reads leaked outcomes beats it.
    """
    if metric != "mae":
        msg = f"unsupported shifted-label metric {metric!r}"
        raise WalkForwardError(msg)
    if games.empty:
        return ShiftedLabelResult(
            model_score=float("nan"),
            chance_score=float("nan"),
            metric=metric,
            n=0,
            tolerance=tolerance,
            passed=False,
            detail="empty games",
        )

    shifted = build_shifted_feature_frame(
        games,
        feature_provider,
        shifted_as_of,
        rating_state=rating_state,
        market_features=market_features,
    )
    preds = predictor.predict(shifted)
    merged = shifted[["game_id", "realized_margin"]].merge(preds, on="game_id", how="inner")
    if merged.empty:
        return ShiftedLabelResult(
            model_score=float("nan"),
            chance_score=float("nan"),
            metric=metric,
            n=0,
            tolerance=tolerance,
            passed=False,
            detail="no overlapping predictions",
        )
    y = merged["realized_margin"].astype(float)
    yhat = merged["pred_margin"].astype(float)
    model_score = float(np.mean(np.abs(y.to_numpy() - yhat.to_numpy())))
    const = float(y.mean()) if chance_constant is None else float(chance_constant)
    chance_score = chance_mae_constant(y, constant=const)
    if relative:
        threshold = chance_score * (1.0 - tolerance)
        passed = model_score >= threshold or math.isnan(chance_score)
        detail = (
            f"model_mae={model_score:.6f} chance_mae={chance_score:.6f} "
            f"threshold(>=)={threshold:.6f}"
        )
    else:
        passed = model_score >= chance_score - tolerance
        detail = (
            f"model_mae={model_score:.6f} chance_mae={chance_score:.6f} abs_tolerance={tolerance}"
        )
    return ShiftedLabelResult(
        model_score=model_score,
        chance_score=chance_score,
        metric=metric,
        n=len(merged),
        tolerance=tolerance,
        passed=passed,
        detail=detail,
    )


# ---------------------------------------------------------------------------
# Determinism helper
# ---------------------------------------------------------------------------


def predictions_bytes(predictions: pd.DataFrame) -> bytes:
    """Canonical byte representation for determinism comparisons."""
    if predictions.empty:
        return b""
    cols = [c for c in PREDICTION_COLUMNS if c in predictions.columns]
    extra = sorted(c for c in predictions.columns if c not in cols)
    frame = (
        predictions[cols + extra]
        .sort_values(["season", "week", "game_id"], kind="mergesort")
        .reset_index(drop=True)
    )
    # Normalize timestamps to ISO strings for stable hashing across engines.
    out = frame.copy()
    if "as_of" in out.columns:
        out["as_of"] = [to_utc(pd.Timestamp(ts).to_pydatetime()).isoformat() for ts in out["as_of"]]
    records = out.to_dict(orient="records")
    return json.dumps(records, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def config_fingerprint(config: WalkForwardConfig) -> str:
    """Stable hash of the walk-forward config."""
    payload = asdict(config)
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return digest[:16]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _validate_games(games: pd.DataFrame) -> None:
    missing = [c for c in REQUIRED_GAME_COLS if c not in games.columns]
    if missing:
        msg = f"games missing required columns: {missing}"
        raise WalkForwardError(msg)
    if games.empty:
        return
    sample = games["event_time"].iloc[0]
    ts = pd.Timestamp(sample).to_pydatetime()
    if ts.tzinfo is None or ts.tzinfo.utcoffset(ts) is None:
        msg = "games.event_time must be timezone-aware (NAIVE-DATETIME-FORBIDDEN)"
        raise WalkForwardError(msg)


def _align_features_to_games(
    features: pd.DataFrame,
    games: pd.DataFrame,
) -> pd.DataFrame:
    if features.empty:
        msg = "feature provider returned no rows"
        raise WalkForwardError(msg)
    if "game_id" not in features.columns:
        msg = "feature provider output missing game_id"
        raise WalkForwardError(msg)
    known = set(features["game_id"].tolist())
    needed = [int(g) for g in games["game_id"].tolist()]
    missing = [g for g in needed if g not in known]
    if missing:
        msg = f"feature provider missing game_ids: {missing[:5]}"
        raise WalkForwardError(msg)
    indexed = features.drop_duplicates(subset=["game_id"], keep="last").set_index("game_id")
    ordered = indexed.reindex(needed)
    return ordered.reset_index()


def _values_equal(a: Any, b: Any, *, rtol: float, atol: float) -> bool:
    if a is None and b is None:
        return True
    if isinstance(a, float) and isinstance(b, float):
        if math.isnan(a) and math.isnan(b):
            return True
        return math.isclose(a, b, rel_tol=rtol, abs_tol=atol)
    try:
        fa, fb = float(a), float(b)
        if math.isnan(fa) and math.isnan(fb):
            return True
        return math.isclose(fa, fb, rel_tol=rtol, abs_tol=atol)
    except (TypeError, ValueError):
        return bool(a == b)
