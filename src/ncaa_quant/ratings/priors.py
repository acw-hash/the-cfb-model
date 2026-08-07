"""Preseason prior builder (DESIGN §9.6 / §15 item 15).

Builds per-team, per-dimension prior means and variances that initialize the
Stage-1 Kalman filter and feed the Task 10 efficiency shrinkage seam.

Prior mean (DESIGN §9.6)
------------------------
Weighted blend of six predictors; weights are **fit** by OLS of next-season
early ratings on the predictors over historical seasons (default 2015–2024)::

    prior = w1·(last-season posterior, regressed α to conference mean)
          + w2·returning-production adjustment
          + w3·recruiting / talent
          + w4·portal net (2021+; missing → variance inflation, not silent fill)
          + w5·coaching-change discontinuity (new-HC pull toward talent-implied)
          + w6·QB carryover (returning starter vs new)

``α`` defaults to 0.30 (DESIGN 25–35%; ``RatingsConfig.prior_regression_to_conf_mean``).

Prior variance
--------------
``base_var`` inflated by roster turnover and by each missing input. High-
continuity teams start tighter; low-continuity / missing-data teams start
wider — this is what makes prior-vs-evidence decay self-adjusting per team.

Wiring (scope: this module only)
--------------------------------
* State-space: :func:`gaussian_state_from_priors` / :func:`build_preseason_states`
  produce :class:`~ncaa_quant.ratings.state_space.GaussianState` values for
  filter initialization. Callers pass them in place of
  :func:`~ncaa_quant.ratings.state_space.initial_team_state` /
  :func:`~ncaa_quant.ratings.state_space.apply_season_regression`.
* Task 10 seam: :func:`efficiency_prior_lookup` builds the ``prior_lookup``
  dict accepted by :func:`ncaa_quant.features.builders.efficiency.resolve_priors`.

Point-in-time: all roster / rating inputs for season ``S`` must be knowable
before Week 1 of ``S`` (preseason Aug 1; last season = ``S-1`` finals only).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from ncaa_quant.ratings.state_space import (
    V1_STATE_DIMS,
    GaussianState,
    StateSpaceConfig,
    end_of_season_ratings,
    run_filter,
)
from ncaa_quant.utils.seeding import set_global_seed
from ncaa_quant.utils.timeutils import to_utc

PREDICTOR_NAMES: Final[tuple[str, ...]] = (
    "last_regressed",
    "returning_adj",
    "talent",
    "portal_net",
    "coaching_adj",
    "qb_carryover",
)

# Columns required on a roster/prior feature frame (Task 12 ``build_roster_frame``).
_ROSTER_COLS: Final[tuple[str, ...]] = (
    "team_id",
    "season",
    "returning_offense_pct",
    "returning_defense_pct",
    "talent_composite",
    "blue_chip_ratio",
    "recruiting_4yr_weighted",
    "portal_net_rating",
    "portal_era",
    "new_hc_flag",
)

MissingPolicy = Literal["widen"]  # only honest policy per §9.6 / Task 15

#: The non-circular fitting target: late-season posterior from a diffuse run.
LATE_TARGET_COLUMN: Final[str] = "late_rating"
#: Targets the preseason prior helped produce, so they cannot validate it (A-2).
CIRCULAR_TARGET_COLUMNS: Final[frozenset[str]] = frozenset({"early_rating"})


@dataclass(frozen=True)
class PriorConfig:
    """Knobs for prior construction and weight fitting.

    Units
    -----
    Rating means are in the same units as the Kalman state dims (EPA/play for
    ``off_epa`` / ``def_epa``, etc.). Variances are squared rating units.
    """

    conf_regression: float = 0.30
    base_var: float = 0.02
    turnover_scale: float = 2.5
    missing_var_penalty: float = 0.015
    # Effective per-game observation variance for prior-weight crossover
    # (calibrated so typical continuity lands near games 5–7; see notes).
    obs_var_eff: float = 0.20
    early_n_games: int = 3
    portal_era_start: int = 2021
    state_dims: tuple[str, ...] = V1_STATE_DIMS
    # Talent → rating scale (fit jointly would be ideal; fixed slope maps
    # z-scored talent into EPA-ish units as a starting elasticity).
    talent_rating_scale: float = 0.08
    returning_elasticity: float = 1.0
    fit_intercept: bool = True
    # Late-season cutoff for the non-circular fitting target (audit A-2).
    late_n_games: int = 8
    # Initial variance for the diffuse filter run that produces that target.
    # Large enough that the preseason prior mean is effectively ignored, so the
    # late posterior reflects observed games rather than the prior being fit.
    diffuse_prior_var: float = 100.0


@dataclass(frozen=True)
class FittedPriorWeights:
    """OLS weights for one state dimension, with analytic SEs."""

    dim: str
    weights: dict[str, float]
    std_errors: dict[str, float]
    r_squared: float
    n_obs: int
    seasons_train: tuple[int, ...]
    predictor_names: tuple[str, ...] = PREDICTOR_NAMES
    intercept: float = 0.0
    intercept_se: float = 0.0
    #: Which target these weights were fit against. Recorded because an R² is
    #: uninterpretable without it: a high R² against a prior-dominated target
    #: means the fit is circular, not that the priors forecast well (audit A-2).
    target_column: str = LATE_TARGET_COLUMN

    @property
    def target_is_circular(self) -> bool:
        return self.target_column in CIRCULAR_TARGET_COLUMNS

    def weight_vector(self) -> np.ndarray:
        return np.array([self.weights[n] for n in self.predictor_names], dtype=float)


@dataclass(frozen=True)
class TeamSeasonPrior:
    """Prior mean + variance for one team-season-dimension."""

    team_id: int
    season: int
    dim: str
    mean: float
    variance: float
    returning_pct: float
    n_missing: int
    predictors: dict[str, float] = field(default_factory=dict)

    @property
    def sd(self) -> float:
        return float(math.sqrt(max(self.variance, 0.0)))


@dataclass(frozen=True)
class PriorFitResult:
    """Multi-dimension fit plus the design rows used."""

    by_dim: dict[str, FittedPriorWeights]
    design: pd.DataFrame


# ---------------------------------------------------------------------------
# Basic transforms
# ---------------------------------------------------------------------------


def regress_to_conference_mean(
    value: float,
    conference_mean: float,
    *,
    alpha: float = 0.30,
) -> float:
    """Blend ``value`` toward ``conference_mean`` by fraction ``alpha``.

    DESIGN §9.6: last-season posterior regressed 25–35% to conference mean.
    """
    if not (0.0 <= alpha <= 1.0):
        msg = f"alpha must be in [0, 1], got {alpha}"
        raise ValueError(msg)
    return float((1.0 - alpha) * value + alpha * conference_mean)


def returning_pct_for_dim(row: Mapping[str, Any], dim: str) -> float:
    """Select the continuity percentage relevant to ``dim`` (NaN if missing)."""
    if dim == "off_epa":
        return _as_float(row.get("returning_offense_pct"))
    if dim == "def_epa":
        return _as_float(row.get("returning_defense_pct"))
    off = _as_float(row.get("returning_offense_pct"))
    de = _as_float(row.get("returning_defense_pct"))
    if _is_nan(off) and _is_nan(de):
        return float("nan")
    if _is_nan(off):
        return de
    if _is_nan(de):
        return off
    return 0.5 * (off + de)


def talent_implied_rating(
    talent_z: float,
    *,
    scale: float = 0.08,
    missing: bool = False,
) -> float:
    """Map z-scored talent composite into rating units.

    Missing talent returns 0.0 as a *neutral contribution* — callers must still
    widen prior variance via :func:`count_missing_inputs` / :func:`prior_variance`.
    """
    if missing or _is_nan(talent_z):
        return 0.0
    return float(scale * talent_z)


def prior_variance(
    returning_pct: float,
    *,
    n_missing: int = 0,
    config: PriorConfig | None = None,
) -> float:
    """Base variance × turnover inflation + per-missing penalty.

    A team returning 40% of production gets a materially wider prior than one
    returning 85%. Missing inputs add variance; they never silently restore
    false confidence.
    """
    cfg = config or PriorConfig()
    ret = returning_pct
    if _is_nan(ret):
        # Treat fully-unknown continuity as worst-case turnover + one missing.
        ret = 0.0
        n_missing = max(n_missing, 1)
    ret = float(np.clip(ret, 0.0, 1.0))
    turnover = 1.0 - ret
    var = cfg.base_var * (1.0 + cfg.turnover_scale * turnover)
    var += cfg.missing_var_penalty * float(max(n_missing, 0))
    return float(var)


def count_missing_inputs(
    *,
    returning_pct: float,
    talent: float,
    portal_net: float,
    portal_era: float,
    new_hc_flag: float,
    qb_carryover: float,
    last_posterior: float,
    config: PriorConfig | None = None,
) -> int:
    """Count predictors that are unavailable (each widens prior variance)."""
    cfg = config or PriorConfig()
    n = 0
    if _is_nan(last_posterior):
        n += 1
    if _is_nan(returning_pct):
        n += 1
    if _is_nan(talent):
        n += 1
    # Portal: pre-era is *structurally* absent (not a data hole for that season);
    # in-era nulls are real holes and must widen variance.
    if float(portal_era) >= 1.0 and _is_nan(portal_net):
        n += 1
    elif float(portal_era) < 1.0 and not _is_nan(portal_era):
        pass  # pre-portal regime: expected absence, no penalty beyond era flag
    if _is_nan(new_hc_flag):
        n += 1
    if _is_nan(qb_carryover):
        n += 1
    _ = cfg  # reserved for future per-feature penalties
    return n


def prior_evidence_weight(
    prior_var: float,
    n_games: int,
    *,
    obs_var_eff: float | None = None,
    config: PriorConfig | None = None,
) -> float:
    """Posterior weight on the prior mean after ``n_games`` independent obs.

    Under a 1-D Gaussian conjugate update, ``w = R / (R + n·P0)``. Equals 0.5
    at ``n = R / P0`` (the prior-vs-evidence crossover).
    """
    cfg = config or PriorConfig()
    r = cfg.obs_var_eff if obs_var_eff is None else float(obs_var_eff)
    p0 = float(prior_var)
    if p0 <= 0.0 or r <= 0.0:
        msg = f"prior_var and obs_var_eff must be > 0, got {p0}, {r}"
        raise ValueError(msg)
    n = max(int(n_games), 0)
    return float(r / (r + n * p0))


def prior_evidence_crossover_games(
    prior_var: float,
    *,
    obs_var_eff: float | None = None,
    config: PriorConfig | None = None,
) -> float:
    """Game count at which prior and evidence weights are equal (50/50)."""
    cfg = config or PriorConfig()
    r = cfg.obs_var_eff if obs_var_eff is None else float(obs_var_eff)
    p0 = float(prior_var)
    if p0 <= 0.0:
        msg = f"prior_var must be > 0, got {p0}"
        raise ValueError(msg)
    return float(r / p0)


# ---------------------------------------------------------------------------
# Predictor construction
# ---------------------------------------------------------------------------


def build_predictors(
    *,
    last_posterior: float,
    conference_mean: float,
    returning_pct: float,
    talent_z: float,
    portal_net: float,
    portal_era: float,
    new_hc_flag: float,
    qb_carryover: float,
    config: PriorConfig | None = None,
) -> dict[str, float]:
    """Build the six §9.6 predictor values (missing → 0 contribution).

    Variance inflation for missingness is handled separately via
    :func:`count_missing_inputs` / :func:`prior_variance` — predictors themselves
    never invent a confident league-mean substitute.
    """
    cfg = config or PriorConfig()
    last = 0.0 if _is_nan(last_posterior) else float(last_posterior)
    conf = 0.0 if _is_nan(conference_mean) else float(conference_mean)
    x_last = regress_to_conference_mean(last, conf, alpha=cfg.conf_regression)

    ret = 0.0 if _is_nan(returning_pct) else float(np.clip(returning_pct, 0.0, 1.0))
    # Returning adjustment: continuity-weighted last rating (elasticity refit via w2).
    x_ret = cfg.returning_elasticity * ret * last

    talent_missing = _is_nan(talent_z)
    x_talent = talent_implied_rating(
        0.0 if talent_missing else float(talent_z),
        scale=cfg.talent_rating_scale,
        missing=talent_missing,
    )

    x_portal = float(portal_net) if float(portal_era) >= 1.0 and not _is_nan(portal_net) else 0.0

    hc = 0.0 if _is_nan(new_hc_flag) else float(new_hc_flag)
    # New-HC discontinuity: pull from last-regressed toward talent-implied.
    x_coach = hc * (x_talent - x_last)

    x_qb = 0.0 if _is_nan(qb_carryover) else float(qb_carryover)

    return {
        "last_regressed": float(x_last),
        "returning_adj": float(x_ret),
        "talent": float(x_talent),
        "portal_net": float(x_portal),
        "coaching_adj": float(x_coach),
        "qb_carryover": float(x_qb),
    }


def blend_prior_mean(
    predictors: Mapping[str, float],
    weights: FittedPriorWeights | Mapping[str, float],
    *,
    intercept: float | None = None,
) -> float:
    """``intercept + Σ w_i x_i`` for the six predictors."""
    if isinstance(weights, FittedPriorWeights):
        wmap = weights.weights
        b0 = weights.intercept if intercept is None else float(intercept)
        names = weights.predictor_names
    else:
        wmap = dict(weights)
        b0 = 0.0 if intercept is None else float(intercept)
        names = PREDICTOR_NAMES
    total = b0
    for name in names:
        total += float(wmap.get(name, 0.0)) * float(predictors.get(name, 0.0))
    return float(total)


# ---------------------------------------------------------------------------
# Design matrix / early ratings from filter history
# ---------------------------------------------------------------------------


def early_season_ratings(
    history: pd.DataFrame,
    season: int,
    *,
    dim: str,
    n_games: int = 3,
    kind: str = "postgame",
) -> pd.DataFrame:
    """Per-team rating after the first ``n_games`` observations of ``season``.

    Uses chronological ``event_time`` (not CFBD week labels — bowls reuse week
    numbers). Returns columns ``team_id``, ``season``, ``early_rating``,
    ``n_games_used``.
    """
    if history.empty:
        return pd.DataFrame(columns=["team_id", "season", "early_rating", "n_games_used"])
    sub = history.loc[(history["season"] == season) & (history["kind"] == kind)].copy()
    if sub.empty:
        sub = history.loc[history["season"] == season].copy()
    if sub.empty or dim not in sub.columns:
        return pd.DataFrame(columns=["team_id", "season", "early_rating", "n_games_used"])

    sub["event_time"] = [to_utc(pd.Timestamp(ts).to_pydatetime()) for ts in sub["event_time"]]
    sub = sub.sort_values(["team_id", "event_time", "game_id"], kind="mergesort")
    rows: list[dict[str, Any]] = []
    for tid, grp in sub.groupby("team_id", sort=False):
        head = grp.head(int(n_games))
        if head.empty:
            continue
        rows.append(
            {
                "team_id": int(tid) if _can_int(tid) else tid,
                "season": int(season),
                "early_rating": float(head.iloc[-1][dim]),
                "n_games_used": int(len(head)),
            }
        )
    return pd.DataFrame(rows)


def late_season_ratings(
    history: pd.DataFrame,
    season: int,
    *,
    dim: str,
    n_games: int = 8,
    kind: str = "postgame",
) -> pd.DataFrame:
    """Per-team rating after at least ``n_games`` observations of ``season``.

    This is the **non-circular** fitting target (audit A-2), and it is only valid
    when ``history`` comes from a diffuse-initialization filter run — see
    :func:`diffuse_late_ratings`. Teams with fewer than ``n_games`` games are
    dropped rather than being scored on a still-prior-dominated posterior.

    Returns ``team_id``, ``season``, ``late_rating``, ``n_games_used``.
    """
    empty = pd.DataFrame(columns=["team_id", "season", "late_rating", "n_games_used"])
    if history.empty:
        return empty
    sub = history.loc[(history["season"] == season) & (history["kind"] == kind)].copy()
    if sub.empty:
        sub = history.loc[history["season"] == season].copy()
    if sub.empty or dim not in sub.columns:
        return empty

    sub["event_time"] = [to_utc(pd.Timestamp(ts).to_pydatetime()) for ts in sub["event_time"]]
    sub = sub.sort_values(["team_id", "event_time", "game_id"], kind="mergesort")
    rows: list[dict[str, Any]] = []
    for tid, grp in sub.groupby("team_id", sort=False):
        if len(grp) < int(n_games):
            continue
        rows.append(
            {
                "team_id": int(tid) if _can_int(tid) else tid,
                "season": int(season),
                "late_rating": float(grp.iloc[-1][dim]),
                "n_games_used": int(len(grp)),
            }
        )
    return pd.DataFrame(rows) if rows else empty


def diffuse_filter_config(config: PriorConfig | None = None) -> StateSpaceConfig:
    """A :class:`StateSpaceConfig` whose initial state carries almost no information.

    The point of the diffuse run (audit A-2) is to produce a target the preseason
    prior had no hand in. With ``prior_var`` set very wide the filter's opening
    position is essentially "I know nothing", so by game 8 the posterior reflects
    the season's observations rather than the prior weights being estimated.
    """
    cfg = config or PriorConfig()
    return StateSpaceConfig(state_dims=cfg.state_dims, prior_var=float(cfg.diffuse_prior_var))


def diffuse_late_ratings(
    observations: pd.DataFrame,
    *,
    seasons: Sequence[int],
    dim: str,
    config: PriorConfig | None = None,
    fbs_team_ids: set[Any] | None = None,
) -> pd.DataFrame:
    """Late-season ratings from a diffuse-initialization filter run (audit A-2).

    Runs the filter with **no preseason states** and a diffuse initial covariance,
    then takes each team's posterior after at least ``late_n_games`` games. Because
    the prior never entered this run, regressing these ratings on the preseason
    predictors is an honest test of whether those predictors forecast anything.

    Fitting against *early* ratings from a prior-initialized run cannot do that:
    the early posterior is dominated by the prior, so the regression largely
    recovers the weights that were assumed. See
    :func:`fit_prior_weights` and the circularity demonstration test.
    """
    cfg = config or PriorConfig()
    if observations.empty:
        return pd.DataFrame(columns=["team_id", "season", "late_rating", "n_games_used"])

    result = run_filter(
        observations,
        config=diffuse_filter_config(cfg),
        fbs_team_ids=fbs_team_ids,
        preseason_states=None,
    )
    frames = [
        late_season_ratings(
            result.history,
            int(season),
            dim=dim,
            n_games=cfg.late_n_games,
        )
        for season in seasons
    ]
    keep = [f for f in frames if not f.empty]
    if not keep:
        return pd.DataFrame(columns=["team_id", "season", "late_rating", "n_games_used"])
    return pd.concat(keep, ignore_index=True)


def attach_late_target(
    design: pd.DataFrame,
    late: pd.DataFrame,
) -> pd.DataFrame:
    """Join diffuse-run late ratings onto a design frame as the fitting target.

    Left join on ``(team_id, season)``; team-seasons without a late rating get
    ``NaN`` and are dropped at fit time rather than being silently filled.
    """
    if design.empty:
        return design.copy()
    out = design.copy()
    if late.empty:
        out[LATE_TARGET_COLUMN] = float("nan")
        return out
    cols = ["team_id", "season", LATE_TARGET_COLUMN]
    right = late.loc[:, cols].drop_duplicates(subset=["team_id", "season"], keep="last")
    return out.merge(right, on=["team_id", "season"], how="left")


def conference_means_from_ratings(
    ratings: pd.DataFrame,
    teams: pd.DataFrame,
    *,
    dim: str,
    season: int,
) -> dict[int, float]:
    """Map ``team_id → conference mean`` of ``dim`` for ``season`` ratings."""
    if ratings.empty or teams.empty or dim not in ratings.columns:
        return {}
    tsub = teams.loc[teams["season"] == season, ["team_id", "conference"]].drop_duplicates(
        "team_id"
    )
    merged = ratings.merge(tsub, on="team_id", how="left")
    conf_avg = merged.groupby("conference", dropna=False)[dim].mean().to_dict()
    out: dict[int, float] = {}
    for r in merged.itertuples(index=False):
        conf = getattr(r, "conference", None)
        mu = conf_avg.get(conf, float(merged[dim].mean()) if len(merged) else 0.0)
        out[int(r.team_id)] = float(mu) if not _is_nan(mu) else 0.0
    return out


def _talent_z_by_team(roster: pd.DataFrame, season: int) -> dict[int, float]:
    """Z-score ``talent_composite`` within season (NaN stays NaN)."""
    sub = roster.loc[roster["season"] == season]
    if sub.empty or "talent_composite" not in sub.columns:
        return {}
    vals = pd.to_numeric(sub["talent_composite"], errors="coerce")
    mu = float(vals.mean(skipna=True)) if vals.notna().any() else 0.0
    sd = float(vals.std(skipna=True)) if vals.notna().sum() > 1 else 1.0
    if sd <= 0.0 or _is_nan(sd):
        sd = 1.0
    out: dict[int, float] = {}
    for tid, v in zip(sub["team_id"].tolist(), vals.tolist(), strict=True):
        if v is None or (isinstance(v, float) and _is_nan(float(v))):
            out[int(tid)] = float("nan")
        else:
            out[int(tid)] = (float(v) - mu) / sd
    return out


def build_design_frame(
    *,
    history: pd.DataFrame,
    roster: pd.DataFrame,
    teams: pd.DataFrame,
    seasons: Sequence[int],
    dim: str,
    qb_carryover: pd.DataFrame | None = None,
    config: PriorConfig | None = None,
) -> pd.DataFrame:
    """One row per team-season: predictors + ``early_rating`` target for ``dim``.

    Target season ``S`` uses last-season (``S-1``) finals and preseason roster
    features dated for ``S``. Rows lacking an early rating or last posterior
    are dropped (cannot supervise the fit).
    """
    cfg = config or PriorConfig()
    qb_map = _qb_carryover_map(qb_carryover)
    rows: list[dict[str, Any]] = []

    for season in seasons:
        early = early_season_ratings(history, int(season), dim=dim, n_games=cfg.early_n_games)
        if early.empty:
            continue
        prev = end_of_season_ratings(history, int(season) - 1, kind="weekly")
        if prev.empty or dim not in prev.columns:
            prev = end_of_season_ratings(history, int(season) - 1, kind="postgame")
        if prev.empty or dim not in prev.columns:
            continue
        conf_means = conference_means_from_ratings(prev, teams, dim=dim, season=int(season) - 1)
        talent_z = _talent_z_by_team(roster, int(season))
        ros = roster.loc[roster["season"] == int(season)]
        ros_by_team = {int(r.team_id): r._asdict() for r in ros.itertuples(index=False)}
        last_by_team = {
            int(r.team_id): float(getattr(r, dim)) for r in prev.itertuples(index=False)
        }

        for er in early.itertuples(index=False):
            tid = int(er.team_id)
            if tid not in last_by_team:
                continue
            rdict = ros_by_team.get(tid, {})
            last = last_by_team[tid]
            conf_mu = conf_means.get(tid, 0.0)
            ret = returning_pct_for_dim(rdict, dim)
            tz = talent_z.get(tid, float("nan"))
            portal_net = _as_float(rdict.get("portal_net_rating"))
            portal_era = _as_float(rdict.get("portal_era"))
            if _is_nan(portal_era):
                portal_era = 1.0 if int(season) >= cfg.portal_era_start else 0.0
            new_hc = _as_float(rdict.get("new_hc_flag"))
            qb = qb_map.get((tid, int(season)), float("nan"))

            preds = build_predictors(
                last_posterior=last,
                conference_mean=conf_mu,
                returning_pct=ret,
                talent_z=tz,
                portal_net=portal_net,
                portal_era=portal_era,
                new_hc_flag=new_hc,
                qb_carryover=qb,
                config=cfg,
            )
            n_miss = count_missing_inputs(
                returning_pct=ret,
                talent=tz,
                portal_net=portal_net,
                portal_era=portal_era,
                new_hc_flag=new_hc,
                qb_carryover=qb,
                last_posterior=last,
                config=cfg,
            )
            rows.append(
                {
                    "team_id": tid,
                    "season": int(season),
                    "dim": dim,
                    "early_rating": float(er.early_rating),
                    "last_posterior": last,
                    "conference_mean": conf_mu,
                    "returning_pct": ret,
                    "n_missing": n_miss,
                    **preds,
                }
            )
    return pd.DataFrame(rows)


def _qb_carryover_map(frame: pd.DataFrame | None) -> dict[tuple[int, int], float]:
    """Optional ``(team_id, season) → {1 returning starter, 0 new}``."""
    if frame is None or frame.empty:
        return {}
    out: dict[tuple[int, int], float] = {}
    for r in frame.itertuples(index=False):
        out[(int(r.team_id), int(r.season))] = float(r.qb_carryover)
    return out


# ---------------------------------------------------------------------------
# Weight fitting
# ---------------------------------------------------------------------------


def fit_prior_weights(
    design: pd.DataFrame,
    *,
    dim: str,
    seasons_train: Sequence[int] | None = None,
    config: PriorConfig | None = None,
    seed: int = 42,
    target_column: str = LATE_TARGET_COLUMN,
    allow_circular_target: bool = False,
) -> FittedPriorWeights:
    """OLS of the target on the six predictors (optionally + intercept).

    The target must be **prior-free** (audit A-2). By default that is
    ``late_rating`` from a diffuse-initialization filter run — see
    :func:`diffuse_late_ratings`.

    Fitting against ``early_rating`` is refused unless ``allow_circular_target``
    is set, because early posteriors are prior-dominated: the regression then
    largely recovers the weights that were assumed rather than testing them, and
    the fit looks excellent for exactly the wrong reason. The escape hatch exists
    so the circularity demonstration test can show the failure explicitly.

    Reproducible under ``seed`` via :func:`set_global_seed` (numpy LSTSQ is
    deterministic; seed is recorded for the run manifest).
    """
    cfg = config or PriorConfig()
    set_global_seed(seed)
    if design.empty:
        msg = "design frame is empty; cannot fit prior weights"
        raise ValueError(msg)
    if target_column in CIRCULAR_TARGET_COLUMNS and not allow_circular_target:
        msg = (
            f"target_column={target_column!r} is prior-dominated, so fitting against it "
            "recovers the assumed weights instead of testing them (audit A-2). Use "
            f"{LATE_TARGET_COLUMN!r} from diffuse_late_ratings(), or pass "
            "allow_circular_target=True only to demonstrate the circularity."
        )
        raise ValueError(msg)

    frame = design.loc[design["dim"] == dim].copy() if "dim" in design.columns else design.copy()
    if seasons_train is not None:
        train_set = {int(s) for s in seasons_train}
        frame = frame.loc[frame["season"].isin(train_set)]
    if frame.empty:
        msg = f"no design rows for dim={dim!r} in seasons_train={seasons_train}"
        raise ValueError(msg)
    if target_column not in frame.columns:
        msg = (
            f"target column {target_column!r} missing from the design frame. "
            "Attach it with attach_late_target(design, diffuse_late_ratings(...))."
        )
        raise ValueError(msg)
    frame = frame.loc[np.isfinite(frame[target_column].to_numpy(dtype=float))]
    if frame.empty:
        msg = f"no finite {target_column!r} rows for dim={dim!r}"
        raise ValueError(msg)

    y = frame[target_column].to_numpy(dtype=float)
    x_cols = list(PREDICTOR_NAMES)
    x = frame[x_cols].to_numpy(dtype=float)
    if cfg.fit_intercept:
        x = np.column_stack([np.ones(len(frame)), x])

    beta, _, _, _ = np.linalg.lstsq(x, y, rcond=None)
    y_hat = x @ beta
    resid = y - y_hat
    ss_res = float(np.sum(resid**2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    n, p = x.shape
    dof = max(n - p, 1)
    sigma2 = ss_res / dof
    # Use pseudoinverse so collinear / zero-variance predictors (e.g. always-missing
    # QB carryover) still yield finite SEs where the column has support.
    xtx_pinv = np.linalg.pinv(x.T @ x)
    cov_beta = sigma2 * xtx_pinv
    se = np.sqrt(np.clip(np.diag(cov_beta), 0.0, None))
    se = np.where(np.isfinite(se), se, np.nan)

    if cfg.fit_intercept:
        intercept = float(beta[0])
        intercept_se = float(se[0])
        w = {name: float(beta[i + 1]) for i, name in enumerate(x_cols)}
        wse = {name: float(se[i + 1]) for i, name in enumerate(x_cols)}
    else:
        intercept = 0.0
        intercept_se = 0.0
        w = {name: float(beta[i]) for i, name in enumerate(x_cols)}
        wse = {name: float(se[i]) for i, name in enumerate(x_cols)}

    seasons = tuple(sorted({int(s) for s in frame["season"].tolist()}))
    return FittedPriorWeights(
        dim=dim,
        weights=w,
        std_errors=wse,
        r_squared=float(r2),
        n_obs=int(n),
        seasons_train=seasons,
        intercept=intercept,
        intercept_se=intercept_se,
        target_column=str(target_column),
    )


def fit_all_dims(
    design: pd.DataFrame,
    *,
    dims: Sequence[str] | None = None,
    seasons_train: Sequence[int] | None = None,
    config: PriorConfig | None = None,
    seed: int = 42,
    target_column: str = LATE_TARGET_COLUMN,
    allow_circular_target: bool = False,
) -> PriorFitResult:
    """Fit :func:`fit_prior_weights` for each requested state dimension."""
    cfg = config or PriorConfig()
    use_dims = tuple(dims) if dims is not None else cfg.state_dims
    by_dim: dict[str, FittedPriorWeights] = {}
    for i, dim in enumerate(use_dims):
        sub = design.loc[design["dim"] == dim] if "dim" in design.columns else design
        if sub.empty:
            continue
        by_dim[dim] = fit_prior_weights(
            design,
            dim=dim,
            seasons_train=seasons_train,
            config=cfg,
            seed=seed + i,
            target_column=target_column,
            allow_circular_target=allow_circular_target,
        )
    return PriorFitResult(by_dim=by_dim, design=design)


def out_of_sample_r2(
    design: pd.DataFrame,
    fitted: FittedPriorWeights,
    *,
    seasons_test: Sequence[int],
    target_column: str | None = None,
) -> float:
    """R² of the fitted prior mean against the held-out seasons' target.

    Defaults to the same target the weights were fit on, which for a valid fit is
    the diffuse-run ``late_rating``. Task 15's acceptance criterion scores priors
    against prior-free ratings, never against prior-initialized early posteriors
    (audit A-2).
    """
    column = target_column or fitted.target_column
    test_set = {int(s) for s in seasons_test}
    frame = design.loc[design["season"].isin(test_set)].copy()
    if "dim" in frame.columns:
        frame = frame.loc[frame["dim"] == fitted.dim]
    if frame.empty or column not in frame.columns:
        return float("nan")
    frame = frame.loc[np.isfinite(frame[column].to_numpy(dtype=float))]
    if frame.empty:
        return float("nan")
    y = frame[column].to_numpy(dtype=float)
    preds = [
        blend_prior_mean({n: float(row[n]) for n in PREDICTOR_NAMES}, fitted)
        for row in frame.to_dict(orient="records")
    ]
    y_hat = np.asarray(preds, dtype=float)
    ss_res = float(np.sum((y - y_hat) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    if ss_tot <= 0:
        return float("nan")
    return float(1.0 - ss_res / ss_tot)


# ---------------------------------------------------------------------------
# Team-season prior + state-space / efficiency wiring
# ---------------------------------------------------------------------------


def build_team_season_prior(
    *,
    team_id: int,
    season: int,
    dim: str,
    last_posterior: float,
    conference_mean: float,
    roster_row: Mapping[str, Any],
    fitted: FittedPriorWeights | Mapping[str, float],
    qb_carryover: float = float("nan"),
    config: PriorConfig | None = None,
    talent_z: float | None = None,
) -> TeamSeasonPrior:
    """Point prior (mean, variance) for one team-season-dimension."""
    cfg = config or PriorConfig()
    ret = returning_pct_for_dim(roster_row, dim)
    tz = float("nan") if talent_z is None else float(talent_z)
    if talent_z is None:
        # Fall back to raw composite scaled as if already z-ish mid / 100.
        raw = _as_float(roster_row.get("talent_composite"))
        tz = float("nan") if _is_nan(raw) else float(raw) / 100.0
    portal_net = _as_float(roster_row.get("portal_net_rating"))
    portal_era = _as_float(roster_row.get("portal_era"))
    if _is_nan(portal_era):
        portal_era = 1.0 if int(season) >= cfg.portal_era_start else 0.0
    new_hc = _as_float(roster_row.get("new_hc_flag"))

    preds = build_predictors(
        last_posterior=last_posterior,
        conference_mean=conference_mean,
        returning_pct=ret,
        talent_z=tz,
        portal_net=portal_net,
        portal_era=portal_era,
        new_hc_flag=new_hc,
        qb_carryover=qb_carryover,
        config=cfg,
    )
    mean = blend_prior_mean(preds, fitted)
    n_miss = count_missing_inputs(
        returning_pct=ret,
        talent=tz,
        portal_net=portal_net,
        portal_era=portal_era,
        new_hc_flag=new_hc,
        qb_carryover=qb_carryover,
        last_posterior=last_posterior,
        config=cfg,
    )
    var = prior_variance(ret, n_missing=n_miss, config=cfg)
    return TeamSeasonPrior(
        team_id=int(team_id),
        season=int(season),
        dim=dim,
        mean=mean,
        variance=var,
        returning_pct=ret if not _is_nan(ret) else float("nan"),
        n_missing=n_miss,
        predictors=preds,
    )


def build_preseason_priors_frame(
    *,
    history: pd.DataFrame,
    roster: pd.DataFrame,
    teams: pd.DataFrame,
    season: int,
    fitted_by_dim: Mapping[str, FittedPriorWeights | Mapping[str, float]],
    qb_carryover: pd.DataFrame | None = None,
    config: PriorConfig | None = None,
    dims: Sequence[str] | None = None,
) -> pd.DataFrame:
    """All team priors for ``season`` Week-1 initialization."""
    cfg = config or PriorConfig()
    use_dims = tuple(dims) if dims is not None else tuple(fitted_by_dim.keys())
    qb_map = _qb_carryover_map(qb_carryover)
    prev = end_of_season_ratings(history, int(season) - 1, kind="weekly")
    if prev.empty:
        prev = end_of_season_ratings(history, int(season) - 1, kind="postgame")

    ros = roster.loc[roster["season"] == int(season)]
    if ros.empty:
        return pd.DataFrame()

    talent_z = _talent_z_by_team(roster, int(season))
    rows: list[dict[str, Any]] = []
    for dim in use_dims:
        if dim not in fitted_by_dim:
            continue
        fitted = fitted_by_dim[dim]
        conf_means: dict[int, float] = {}
        last_by: dict[int, float] = {}
        if not prev.empty and dim in prev.columns:
            conf_means = conference_means_from_ratings(prev, teams, dim=dim, season=int(season) - 1)
            last_by = {int(r.team_id): float(getattr(r, dim)) for r in prev.itertuples(index=False)}
        for r in ros.itertuples(index=False):
            tid = int(r.team_id)
            last = last_by.get(tid, float("nan"))
            prior = build_team_season_prior(
                team_id=tid,
                season=int(season),
                dim=dim,
                last_posterior=last,
                conference_mean=conf_means.get(tid, 0.0),
                roster_row=r._asdict(),
                fitted=fitted,
                qb_carryover=qb_map.get((tid, int(season)), float("nan")),
                config=cfg,
                talent_z=talent_z.get(tid),
            )
            rows.append(
                {
                    "team_id": prior.team_id,
                    "season": prior.season,
                    "dim": prior.dim,
                    "prior_mean": prior.mean,
                    "prior_var": prior.variance,
                    "prior_sd": prior.sd,
                    "returning_pct": prior.returning_pct,
                    "n_missing": prior.n_missing,
                    "crossover_games": prior_evidence_crossover_games(prior.variance, config=cfg),
                    **{f"x_{k}": v for k, v in prior.predictors.items()},
                }
            )
    return pd.DataFrame(rows)


def gaussian_state_from_priors(
    priors_by_dim: Mapping[str, TeamSeasonPrior | tuple[float, float]],
    *,
    config: StateSpaceConfig | None = None,
    prior_config: PriorConfig | None = None,
) -> GaussianState:
    """Build a diagonal-covariance :class:`GaussianState` from per-dim priors.

    This is the state-space initialization hook (Task 15 → Task 14). Dims
    present in ``StateSpaceConfig.state_dims`` but absent from ``priors_by_dim``
    fall back to ``PriorConfig`` / ``StateSpaceConfig`` defaults with widened
    variance (missing-input honesty).
    """
    ss = config or StateSpaceConfig()
    pc = prior_config or PriorConfig()
    d = ss.n_dims
    mean = np.zeros(d, dtype=float)
    cov = np.eye(d, dtype=float) * pc.base_var
    for i, dim in enumerate(ss.state_dims):
        if dim not in priors_by_dim:
            cov[i, i] = prior_variance(float("nan"), n_missing=1, config=pc)
            mean[i] = ss.prior_mean
            continue
        spec = priors_by_dim[dim]
        if isinstance(spec, TeamSeasonPrior):
            mean[i] = spec.mean
            cov[i, i] = spec.variance
        else:
            mean[i] = float(spec[0])
            cov[i, i] = float(spec[1])
    return GaussianState(mean=mean, cov=cov)


def build_preseason_states(
    priors_frame: pd.DataFrame,
    *,
    season: int,
    config: StateSpaceConfig | None = None,
    prior_config: PriorConfig | None = None,
) -> dict[int, GaussianState]:
    """Map ``team_id → GaussianState`` for Week-1 filter initialization."""
    ss = config or StateSpaceConfig()
    pc = prior_config or PriorConfig()
    sub = priors_frame.loc[priors_frame["season"] == int(season)]
    out: dict[int, GaussianState] = {}
    if sub.empty:
        return out
    for tid, grp in sub.groupby("team_id", sort=False):
        by_dim: dict[str, tuple[float, float]] = {}
        for r in grp.itertuples(index=False):
            by_dim[str(r.dim)] = (float(r.prior_mean), float(r.prior_var))
        out[int(tid)] = gaussian_state_from_priors(by_dim, config=ss, prior_config=pc)
    return out


def league_mean_preseason_states(
    team_ids: Sequence[Any],
    *,
    config: StateSpaceConfig | None = None,
    prior_config: PriorConfig | None = None,
    league_mean: float | None = None,
    pooled_var: float | None = None,
) -> dict[int, GaussianState]:
    """A1 ablation: every team gets the same league-mean / pooled-variance prior.

    Replaces BOTH location and uncertainty. Using a fitted variance with a
    league-mean location would confound the ablation (Task 22B).
    """
    ss = config or StateSpaceConfig()
    pc = prior_config or PriorConfig()
    mean = float(ss.prior_mean if league_mean is None else league_mean)
    var = float(pc.base_var if pooled_var is None else pooled_var)
    by_dim = {dim: (mean, var) for dim in ss.state_dims}
    state = gaussian_state_from_priors(by_dim, config=ss, prior_config=pc)
    return {int(tid): state for tid in team_ids}


def efficiency_prior_lookup(
    priors_frame: pd.DataFrame,
    *,
    dim: str,
    season: int | None = None,
) -> dict[str, float]:
    """Build ``prior_lookup`` for :func:`resolve_priors` (Task 10 seam).

    Keys are stringified ``team_id`` (efficiency builders key entities as
    strings). Pass the result as ``EfficiencyFeatureBuilder(..., prior_lookup=...)``.
    """
    frame = priors_frame
    if season is not None:
        frame = frame.loc[frame["season"] == int(season)]
    if "dim" in frame.columns:
        frame = frame.loc[frame["dim"] == dim]
    if frame.empty:
        return {}
    return {
        str(int(r.team_id)): float(r.prior_mean)
        for r in frame.itertuples(index=False)
        if hasattr(r, "prior_mean")
    }


def store_week1_predictions(
    priors_frame: pd.DataFrame,
    path: Path | str,
    *,
    seasons: Sequence[int],
) -> Path:
    """Persist Week-1 prior predictions for later evaluation harness use."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    sub = priors_frame.loc[priors_frame["season"].isin({int(s) for s in seasons})].copy()
    sub.to_parquet(target, index=False)
    return target


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _as_float(value: Any) -> float:
    if value is None:
        return float("nan")
    if isinstance(value, float) and value != value:
        return float("nan")
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _is_nan(value: float) -> bool:
    return value != value


def _can_int(value: Any) -> bool:
    try:
        int(value)
    except (TypeError, ValueError):
        return False
    return True
