"""Bayesian state-space (Kalman) team rating engine (DESIGN §9.2–§9.5 / §15 item 14).

Stage-1 dynamic state layer: per-team latent strength with full covariance,
updated after every completed game. This is the production rating engine;
Elo (:mod:`ncaa_quant.ratings.elo_baseline`) remains the benchmark only.

State (DESIGN §9.2)
-------------------
Per team ``i`` the latent state is a configurable vector. v1-minimal::

    x_i = [off_epa, def_epa, st_value, pace]

v1.1 adds ``off_rush_bias``, ``off_explos``, ``def_explos`` by extending
``StateSpaceConfig.state_dims`` — measurement equations for those dims are
not yet wired (priors + Q only). League-level: ``hfa_global`` and a heavily
shrunk per-team ``hfa_deviation``.

Measurement (DESIGN §9.3)
-------------------------
For a home/away game (neutral drops HFA)::

    epa_h = off_h − def_a + hfa_global + hfa_dev_h + ε
    epa_a = off_a − def_h + ε
    st    = st_h − st_a + ε_st
    pace  = 0.5·(pace_h + pace_a) + ε_pace   # obs = mean plays / ref_plays − 1
    margin = margin_scale · (off_h − def_a − off_a + def_h + hfa) + ε_m

Observation noise on EPA scales with informativeness::

    σ²_epa = r_epa_base² · (ref_plays / max(n_plays, 1))

FCS opponents are replaced by a pooled FCS prior with large variance for the
duration of the update (their state is not written back as a named school).

Process noise (DESIGN §9.4)
---------------------------
Weekly ``x ← x + w``, ``w ~ N(0, Q)`` with diagonal ``Q`` keyed by state dim.
Event-triggered inflation multiplies selected diagonal entries (QB change,
coordinator change, or a manual multiplier hook).

Robustness (DESIGN §9.5)
------------------------
Standardized innovations are winsorized at ±``residual_winsor_sigma`` (default
2.5) before the gain is applied.

Time semantics
--------------
Each posterior row's ``event_time`` is the game's result-knowable time.
As-of queries keep rows with ``event_time < as_of`` only.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Final, Literal

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from ncaa_quant.utils.seeding import set_global_seed
from ncaa_quant.utils.timeutils import as_of_bound, to_utc

# ---------------------------------------------------------------------------
# Dimension catalogs
# ---------------------------------------------------------------------------

V1_STATE_DIMS: Final[tuple[str, ...]] = ("off_epa", "def_epa", "st_value", "pace")
V11_EXTRA_DIMS: Final[tuple[str, ...]] = ("off_rush_bias", "off_explos", "def_explos")
V11_STATE_DIMS: Final[tuple[str, ...]] = V1_STATE_DIMS + V11_EXTRA_DIMS

Q_INFLATION_EVENTS: Final[Mapping[str, float]] = {
    "qb_change": 5.0,
    "coordinator_change": 3.0,
    "manual": 1.0,  # multiplier supplied separately
}

FCS_TEAM_KEY: Final[str] = "__FCS_TIER__"

HISTORY_KIND = Literal["postgame", "weekly", "preseason"]


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _default_q_diag(dims: Sequence[str]) -> dict[str, float]:
    """Default weekly process-noise variances (pre-tune placeholders).

    Efficiency dims drift more than pace (DESIGN §9.4).
    """
    base = {
        "off_epa": 0.0025,
        "def_epa": 0.0025,
        "st_value": 0.0015,
        "pace": 0.0004,
        "off_rush_bias": 0.001,
        "off_explos": 0.001,
        "def_explos": 0.001,
    }
    return {d: float(base.get(d, 0.001)) for d in dims}


@dataclass(frozen=True)
class StateSpaceConfig:
    """Hyperparameters for the Kalman rating filter.

    ``state_dims`` selects the latent vector; extending to v1.1 is a config
    change (append dims from :data:`V11_EXTRA_DIMS`), not a rewrite.
    """

    state_dims: tuple[str, ...] = V1_STATE_DIMS
    residual_winsor_sigma: float = 2.5
    q_diag: Mapping[str, float] = field(default_factory=dict)
    prior_var: float = 0.04
    prior_mean: float = 0.0
    # Observation noise (EPA/play units unless noted).
    r_epa_base: float = 0.12
    r_st_base: float = 0.20
    r_pace_base: float = 0.15
    r_margin: float = 18.0  # points; deliberately high (secondary obs)
    ref_plays: float = 70.0
    margin_scale: float = 80.0  # points per unit net EPA differential
    # League HFA (EPA/play additive to home offense expectation).
    hfa_prior_mean: float = 0.025
    hfa_prior_var: float = 0.0004
    hfa_q: float = 1.0e-6
    hfa_team_prior_var: float = 1.0e-4  # heavily shrunk
    hfa_team_q: float = 1.0e-7
    # FCS pooled prior.
    fcs_prior_mean: float = -0.05
    fcs_prior_var: float = 0.25
    # Between-season soft regression toward prior_mean (Task 15 owns real priors).
    season_regression: float = 0.30
    season_var_inflation: float = 0.02
    # Q-inflation multipliers (DESIGN §9.4).
    q_inflation: Mapping[str, float] = field(default_factory=lambda: dict(Q_INFLATION_EVENTS))

    def __post_init__(self) -> None:
        if not self.state_dims:
            msg = "state_dims must be non-empty"
            raise ValueError(msg)
        if self.residual_winsor_sigma <= 0:
            msg = f"residual_winsor_sigma must be > 0, got {self.residual_winsor_sigma}"
            raise ValueError(msg)
        object.__setattr__(
            self,
            "q_diag",
            dict(self.q_diag) if self.q_diag else _default_q_diag(self.state_dims),
        )

    @property
    def n_dims(self) -> int:
        return len(self.state_dims)

    def dim_index(self, name: str) -> int:
        try:
            return self.state_dims.index(name)
        except ValueError as exc:
            msg = f"unknown state dim {name!r}; configured={self.state_dims}"
            raise KeyError(msg) from exc

    def q_matrix(self, *, scale: float = 1.0) -> np.ndarray:
        """Return diagonal weekly process-noise covariance ``Q``."""
        d = self.n_dims
        q = np.zeros((d, d), dtype=float)
        for i, name in enumerate(self.state_dims):
            q[i, i] = float(self.q_diag.get(name, 0.001)) * scale
        return q


# ---------------------------------------------------------------------------
# Gaussian state containers
# ---------------------------------------------------------------------------


@dataclass
class GaussianState:
    """Mean vector and full covariance for one latent block."""

    mean: np.ndarray
    cov: np.ndarray

    def copy(self) -> GaussianState:
        return GaussianState(mean=self.mean.copy(), cov=self.cov.copy())

    def sd(self) -> np.ndarray:
        diag = np.asarray(np.diag(self.cov), dtype=float)
        out: np.ndarray = np.sqrt(np.clip(diag, 0.0, None))
        return out


@dataclass
class InnovationRecord:
    """One observation residual after a game update (for diagnostics)."""

    game_id: int
    team_id: Any
    season: int
    week: int
    event_time: datetime
    obs_name: str
    innovation: float
    pred_sd: float
    z: float
    winsorized: bool


@dataclass
class GameUpdateResult:
    """Result of a single game measurement update."""

    home: GaussianState
    away: GaussianState
    hfa_global: GaussianState
    hfa_dev_home: GaussianState
    innovations: list[InnovationRecord]
    log_likelihood: float


# ---------------------------------------------------------------------------
# Core Kalman primitives
# ---------------------------------------------------------------------------


def kalman_predict(state: GaussianState, q: np.ndarray) -> GaussianState:
    """Time update: ``x ← x``, ``P ← P + Q`` (identity dynamics)."""
    return GaussianState(mean=state.mean.copy(), cov=state.cov + q)


def winsorize_innovation(
    innovation: np.ndarray,
    pred_var: np.ndarray,
    *,
    sigma: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Clip standardized innovations at ±``sigma``.

    Parameters
    ----------
    innovation:
        Residual vector ``y − Hμ`` (shape ``(m,)``).
    pred_var:
        Marginal predictive variances ``diag(S)`` (shape ``(m,)``).
    sigma:
        Winsorization threshold in standardized units.

    Returns
    -------
    clipped, z, was_clipped
        Clipped innovation, standardized z, and boolean mask of clips.
    """
    sd = np.sqrt(np.clip(np.asarray(pred_var, dtype=float), 1e-18, None))
    z = np.asarray(innovation, dtype=float) / sd
    z_clip = np.clip(z, -sigma, sigma)
    clipped = z_clip * sd
    was_clipped = np.abs(z) > sigma + 1e-15
    return clipped, z, was_clipped


def effective_obs_noise(
    r: np.ndarray,
    z: np.ndarray,
    was_clipped: np.ndarray,
    *,
    sigma: float,
) -> np.ndarray:
    """Inflate observation noise for winsorized components (DESIGN §9.4).

    ``R_eff = R · (|z| / sigma)²`` on clipped rows. A clipped residual carries
    less information than the raw tail implies, but the Joseph covariance update
    does not know the residual was dampened — it shrinks ``P`` as if the whole
    tail had been observed. The visible symptom is overconfident November ratings
    after early-season blowouts: the filter treats a 60-point margin as a
    high-precision measurement while only acting on 2.5σ of it.

    Rows *and* columns are scaled by ``sqrt`` of the factor so a correlated ``R``
    stays positive semi-definite; for the diagonal ``R`` used here this reduces
    to scaling each variance by ``(|z| / sigma)²``.
    """
    r_arr = np.asarray(r, dtype=float)
    if r_arr.ndim == 1:
        r_arr = np.diag(r_arr)
    clipped = np.asarray(was_clipped, dtype=bool)
    if not np.any(clipped):
        return r_arr.copy()

    factor = np.ones(r_arr.shape[0], dtype=float)
    z_arr = np.abs(np.asarray(z, dtype=float))
    factor[clipped] = (z_arr[clipped] / float(sigma)) ** 2
    root = np.sqrt(factor)
    return np.asarray(r_arr * np.outer(root, root), dtype=float)


def kalman_update(
    state: GaussianState,
    h: np.ndarray,
    y: np.ndarray,
    r: np.ndarray,
    *,
    winsor_sigma: float = 2.5,
) -> tuple[GaussianState, np.ndarray, np.ndarray, np.ndarray, float]:
    """Measurement update with Joseph covariance and innovation winsorization.

    Returns
    -------
    posterior, innovation_raw, innovation_used, z, log_likelihood
    """
    h = np.atleast_2d(np.asarray(h, dtype=float))
    y = np.atleast_1d(np.asarray(y, dtype=float))
    r = np.asarray(r, dtype=float)
    if r.ndim == 1:
        r = np.diag(r)
    m = y.shape[0]
    if h.shape != (m, state.mean.shape[0]):
        msg = f"H shape {h.shape} incompatible with state {state.mean.shape} / y {y.shape}"
        raise ValueError(msg)

    s = h @ state.cov @ h.T + r
    innov = y - h @ state.mean
    pred_var = np.diag(s).copy()
    innov_used, z, was_clipped = winsorize_innovation(innov, pred_var, sigma=winsor_sigma)

    r_eff = effective_obs_noise(r, z, was_clipped, sigma=winsor_sigma)
    s_eff = h @ state.cov @ h.T + r_eff

    # Solve K = P H^T S_eff^{-1} without forming the inverse explicitly.
    try:
        gain_t = np.linalg.solve(s_eff, h @ state.cov.T)
        k = gain_t.T
    except np.linalg.LinAlgError:
        k = state.cov @ h.T @ np.linalg.pinv(s_eff)

    mean_new = state.mean + k @ innov_used
    i_kh = np.eye(state.mean.shape[0]) - k @ h
    cov_new = i_kh @ state.cov @ i_kh.T + k @ r_eff @ k.T
    # Numerical hygiene.
    cov_new = 0.5 * (cov_new + cov_new.T)

    # Predictive log-likelihood uses the *raw* innovation (pre-winsor).
    sign, logdet = np.linalg.slogdet(s)
    if sign <= 0:
        log_lik = -1.0e6
    else:
        quad = float(innov @ np.linalg.solve(s, innov))
        log_lik = -0.5 * (m * math.log(2.0 * math.pi) + logdet + quad)

    return (
        GaussianState(mean=mean_new, cov=cov_new),
        innov,
        innov_used,
        z,
        log_lik,
    )


def mean_centering_operator(n: int, index_sets: Sequence[Sequence[int]]) -> np.ndarray:
    """Build ``I − M``, the projection that zeroes the mean of each index set.

    ``index_sets`` lists the position groups to centre independently — typically
    the offensive block across all FBS teams, and the defensive block. Groups must
    not overlap: a position centred twice would be projected onto the intersection
    of two constraints, which is not what §9.3 asks for.
    """
    seen: set[int] = set()
    proj = np.eye(int(n), dtype=float)
    for group in index_sets:
        idx = np.asarray(sorted(set(int(i) for i in group)), dtype=int)
        if idx.size == 0:
            continue
        overlap = seen.intersection(int(i) for i in idx)
        if overlap:
            msg = f"index sets overlap at positions {sorted(overlap)}; centring must be disjoint"
            raise ValueError(msg)
        seen.update(int(i) for i in idx)
        block = np.ix_(idx, idx)
        proj[block] = np.eye(idx.size) - np.full((idx.size, idx.size), 1.0 / idx.size)
    return proj


def project_league_mean_zero(
    state: GaussianState,
    index_sets: Sequence[Sequence[int]],
) -> GaussianState:
    """Project a state onto the league-mean-zero subspace (DESIGN §9.3, A-3).

    The measurement contrast ``off_h − def_a`` identifies only *differences*:
    adding a constant to every team's offense and every team's defense leaves
    every measurement unchanged. That null direction is collinear with the league
    scoring-environment state, so without a hard constraint the filter has a ridge
    it can wander along — team ratings drift jointly while fitting the data
    equally well, and the absolute level is arbitrary.

    The constraint is applied as an explicit projection, ``x ← (I−M)x`` and
    ``P ← (I−M) P (I−M)ᵀ``, rather than as a zero-noise pseudo-observation, so it
    holds *exactly* after every update rather than approximately.
    """
    proj = mean_centering_operator(state.mean.shape[0], index_sets)
    mean = proj @ state.mean
    cov = proj @ state.cov @ proj.T
    return GaussianState(mean=mean, cov=0.5 * (cov + cov.T))


def analytic_1d_kalman_update(
    prior_mean: float,
    prior_var: float,
    *,
    observation: float,
    obs_var: float,
    process_var: float = 0.0,
) -> tuple[float, float, float]:
    """Closed-form 1-D Kalman predict+update (identity dynamics, H=1).

    Returns ``(posterior_mean, posterior_var, kalman_gain)``.
    """
    pred_var = prior_var + process_var
    gain = pred_var / (pred_var + obs_var)
    post_mean = prior_mean + gain * (observation - prior_mean)
    post_var = (1.0 - gain) * pred_var
    return post_mean, post_var, gain


# ---------------------------------------------------------------------------
# Q inflation
# ---------------------------------------------------------------------------


def inflate_q(
    q: np.ndarray,
    event: str,
    *,
    config: StateSpaceConfig | None = None,
    manual_multiplier: float | None = None,
    dims: Sequence[str] | None = None,
    dim_names: Sequence[str] | None = None,
) -> np.ndarray:
    """Return ``Q`` with event-triggered diagonal inflation (DESIGN §9.4).

    Parameters
    ----------
    event:
        ``qb_change``, ``coordinator_change``, or ``manual``.
    manual_multiplier:
        Required when ``event='manual'``; otherwise ignored.
    dims:
        Optional subset of dimension *names* to inflate; default all.
    dim_names:
        Ordered names matching ``q``'s diagonal (defaults to v1 dims).
    """
    cfg = config or StateSpaceConfig()
    names = tuple(dim_names) if dim_names is not None else cfg.state_dims
    if q.shape[0] != len(names):
        msg = f"q shape {q.shape} does not match dim_names length {len(names)}"
        raise ValueError(msg)

    if event == "manual":
        if manual_multiplier is None:
            msg = "manual_multiplier is required when event='manual'"
            raise ValueError(msg)
        mult = float(manual_multiplier)
    else:
        table = cfg.q_inflation
        if event not in table:
            msg = f"unknown Q-inflation event {event!r}; known={sorted(table)}"
            raise KeyError(msg)
        mult = float(table[event])

    out = q.copy()
    target = set(dims) if dims is not None else set(names)
    for i, name in enumerate(names):
        if name in target:
            out[i, i] *= mult
    return out


# ---------------------------------------------------------------------------
# Initialization / season boundary
# ---------------------------------------------------------------------------


def initial_team_state(config: StateSpaceConfig) -> GaussianState:
    """Zero-centered prior with diagonal ``prior_var``."""
    d = config.n_dims
    mean = np.full(d, config.prior_mean, dtype=float)
    cov = np.eye(d, dtype=float) * config.prior_var
    return GaussianState(mean=mean, cov=cov)


def initial_hfa_global(config: StateSpaceConfig) -> GaussianState:
    return GaussianState(
        mean=np.array([config.hfa_prior_mean], dtype=float),
        cov=np.array([[config.hfa_prior_var]], dtype=float),
    )


def initial_hfa_team(config: StateSpaceConfig) -> GaussianState:
    return GaussianState(
        mean=np.array([0.0], dtype=float),
        cov=np.array([[config.hfa_team_prior_var]], dtype=float),
    )


def apply_season_regression(state: GaussianState, config: StateSpaceConfig) -> GaussianState:
    """Soft mean reversion + variance inflation between seasons."""
    alpha = config.season_regression
    mean = (1.0 - alpha) * state.mean + alpha * config.prior_mean
    cov = state.cov + np.eye(config.n_dims) * config.season_var_inflation
    return GaussianState(mean=mean, cov=cov)


def fcs_pinned_state(config: StateSpaceConfig) -> GaussianState:
    """Pooled FCS prior with large variance (DESIGN §9.3)."""
    d = config.n_dims
    mean = np.full(d, config.fcs_prior_mean, dtype=float)
    # Offense slightly worse, defense slightly worse (higher allowed EPA).
    if "off_epa" in config.state_dims:
        mean[config.dim_index("off_epa")] = config.fcs_prior_mean
    if "def_epa" in config.state_dims:
        # Higher def_epa means worse defense under off − def measurement.
        mean[config.dim_index("def_epa")] = -config.fcs_prior_mean
    cov = np.eye(d, dtype=float) * config.fcs_prior_var
    return GaussianState(mean=mean, cov=cov)


# ---------------------------------------------------------------------------
# Observation helpers
# ---------------------------------------------------------------------------


def observation_epa_variance(n_plays: float, config: StateSpaceConfig) -> float:
    """Informativeness-scaled EPA observation variance."""
    n = max(float(n_plays), 1.0)
    return (config.r_epa_base**2) * (config.ref_plays / n)


def build_game_observations_from_plays(
    plays: pd.DataFrame,
    games: pd.DataFrame,
    *,
    drop_garbage: bool = True,
) -> pd.DataFrame:
    """Aggregate garbage-filtered rush/pass EPA into per-game filter inputs.

    Uses :func:`ncaa_quant.features.epa.filter_garbage_time` when
    ``drop_garbage`` is true. Special-teams EPA is mean EPA on ST plays
    (offense perspective) when present; otherwise left null (skipped in update).

    Returns columns consumed by :func:`run_filter`.
    """
    from ncaa_quant.features.epa import filter_garbage_time

    if plays.empty or games.empty:
        return pd.DataFrame()

    work = plays.copy()
    if drop_garbage:
        work = filter_garbage_time(work)

    if "is_rush" in work.columns and "is_pass" in work.columns:
        snaps = work.loc[work["is_rush"].astype(bool) | work["is_pass"].astype(bool)].copy()
    else:
        snaps = work

    snaps = snaps.dropna(subset=["game_id", "offense_id", "epa"])
    if snaps.empty:
        return pd.DataFrame()

    g = (
        snaps.groupby(["game_id", "offense_id"], sort=False)
        .agg(epa_per_play=("epa", "mean"), n_plays=("epa", "count"))
        .reset_index()
    )

    st_rows = pd.DataFrame()
    if "is_special_teams" in work.columns:
        st = work.loc[work["is_special_teams"].astype(bool)].dropna(subset=["epa"])
        if not st.empty:
            st_rows = (
                st.groupby(["game_id", "offense_id"], sort=False)["epa"]
                .mean()
                .rename("st_epa")
                .reset_index()
            )

    game_cols = [
        "game_id",
        "season",
        "week",
        "home_team_id",
        "away_team_id",
        "home_points",
        "away_points",
        "neutral_site",
        "completed",
    ]
    meta_cols = [c for c in game_cols if c in games.columns]
    meta = games[meta_cols].drop_duplicates(subset=["game_id"]).copy()
    if "event_time" in games.columns:
        meta["event_time"] = games.drop_duplicates(subset=["game_id"])["event_time"].to_numpy()
    elif "start_date" in games.columns:
        from ncaa_quant.ingestion.cfbd import game_event_time

        meta["event_time"] = [
            game_event_time(pd.Timestamp(ts).to_pydatetime())
            for ts in games.drop_duplicates(subset=["game_id"])["start_date"]
        ]

    home = g.rename(
        columns={
            "offense_id": "home_team_id",
            "epa_per_play": "home_epa",
            "n_plays": "home_plays",
        }
    )
    away = g.rename(
        columns={
            "offense_id": "away_team_id",
            "epa_per_play": "away_epa",
            "n_plays": "away_plays",
        }
    )
    out = meta.merge(home, on=["game_id", "home_team_id"], how="inner")
    out = out.merge(away, on=["game_id", "away_team_id"], how="inner")

    if not st_rows.empty:
        st_h = st_rows.rename(columns={"offense_id": "home_team_id", "st_epa": "home_st_epa"})
        st_a = st_rows.rename(columns={"offense_id": "away_team_id", "st_epa": "away_st_epa"})
        out = out.merge(st_h, on=["game_id", "home_team_id"], how="left")
        out = out.merge(st_a, on=["game_id", "away_team_id"], how="left")
    else:
        out["home_st_epa"] = np.nan
        out["away_st_epa"] = np.nan

    out["margin"] = pd.to_numeric(out["home_points"], errors="coerce") - pd.to_numeric(
        out["away_points"], errors="coerce"
    )
    # Pace obs: mean plays relative to ref, centered at 0.
    out["pace_obs"] = 0.5 * (
        pd.to_numeric(out["home_plays"], errors="coerce")
        + pd.to_numeric(out["away_plays"], errors="coerce")
    )
    return out.reset_index(drop=True)


def build_game_observations_from_advanced(
    advanced: pd.DataFrame,
    games: pd.DataFrame,
    *,
    default_plays: float = 70.0,
) -> pd.DataFrame:
    """Build filter observations from advanced-box EPA + game margins.

    Used when full PBP is unavailable. ``offense_epa`` is treated as the
    garbage-filtered EPA/play observation (CFBD PPA). Play counts default to
    ``default_plays`` when not present on the advanced frame.
    """
    if advanced.empty or games.empty:
        return pd.DataFrame()

    adv = advanced.copy()
    if "n_plays" not in adv.columns:
        adv["n_plays"] = default_plays

    meta_cols = [
        c
        for c in (
            "game_id",
            "season",
            "week",
            "home_team_id",
            "away_team_id",
            "home_points",
            "away_points",
            "neutral_site",
            "completed",
            "event_time",
            "start_date",
        )
        if c in games.columns
    ]
    meta = games[meta_cols].drop_duplicates(subset=["game_id"]).copy()
    if "event_time" not in meta.columns and "start_date" in meta.columns:
        from ncaa_quant.ingestion.cfbd import game_event_time

        meta["event_time"] = [
            game_event_time(pd.Timestamp(ts).to_pydatetime()) for ts in meta["start_date"]
        ]

    home = adv.rename(
        columns={
            "team_id": "home_team_id",
            "offense_epa": "home_epa",
            "n_plays": "home_plays",
        }
    )
    away = adv.rename(
        columns={
            "team_id": "away_team_id",
            "offense_epa": "away_epa",
            "n_plays": "away_plays",
        }
    )
    keep_h = ["game_id", "home_team_id", "home_epa", "home_plays"]
    keep_a = ["game_id", "away_team_id", "away_epa", "away_plays"]
    out = meta.merge(home[keep_h], on=["game_id", "home_team_id"], how="inner")
    out = out.merge(away[keep_a], on=["game_id", "away_team_id"], how="inner")
    out["home_st_epa"] = np.nan
    out["away_st_epa"] = np.nan
    out["margin"] = pd.to_numeric(out["home_points"], errors="coerce") - pd.to_numeric(
        out["away_points"], errors="coerce"
    )
    out["pace_obs"] = 0.5 * (
        pd.to_numeric(out["home_plays"], errors="coerce")
        + pd.to_numeric(out["away_plays"], errors="coerce")
    )
    return out.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Game update
# ---------------------------------------------------------------------------


def _team_key(team_id: Any, *, is_fcs: bool) -> str:
    if is_fcs:
        return FCS_TEAM_KEY
    return str(team_id)


def update_game(
    home_state: GaussianState,
    away_state: GaussianState,
    hfa_global: GaussianState,
    hfa_dev_home: GaussianState,
    *,
    home_epa: float | None,
    away_epa: float | None,
    home_plays: float,
    away_plays: float,
    home_st_epa: float | None = None,
    away_st_epa: float | None = None,
    pace_obs: float | None = None,
    margin: float | None = None,
    neutral_site: bool = False,
    config: StateSpaceConfig | None = None,
    game_id: int = 0,
    home_team_id: Any = "home",
    away_team_id: Any = "away",
    season: int = 0,
    week: int = 0,
    event_time: datetime | None = None,
) -> GameUpdateResult:
    """Joint Kalman update for one game's measurement vector.

    Team states are stacked with league HFA into a joint Gaussian; after the
    update the home/away/HFA marginals are extracted (cross-covariance is
    discarded for storage — standard independent-team approximation).
    """
    cfg = config or StateSpaceConfig()
    d = cfg.n_dims
    # Joint: [home(d), away(d), hfa_global(1), hfa_dev(1)]
    joint_mean = np.concatenate(
        [home_state.mean, away_state.mean, hfa_global.mean, hfa_dev_home.mean]
    )
    joint_cov = np.zeros((2 * d + 2, 2 * d + 2), dtype=float)
    joint_cov[:d, :d] = home_state.cov
    joint_cov[d : 2 * d, d : 2 * d] = away_state.cov
    joint_cov[2 * d, 2 * d] = float(hfa_global.cov[0, 0])
    joint_cov[2 * d + 1, 2 * d + 1] = float(hfa_dev_home.cov[0, 0])
    joint = GaussianState(mean=joint_mean, cov=joint_cov)

    i_off = cfg.dim_index("off_epa") if "off_epa" in cfg.state_dims else None
    i_def = cfg.dim_index("def_epa") if "def_epa" in cfg.state_dims else None
    i_st = cfg.dim_index("st_value") if "st_value" in cfg.state_dims else None
    i_pace = cfg.dim_index("pace") if "pace" in cfg.state_dims else None

    rows: list[np.ndarray] = []
    ys: list[float] = []
    rs: list[float] = []
    names: list[str] = []
    team_for_obs: list[Any] = []

    def _h_row() -> np.ndarray:
        return np.zeros(2 * d + 2, dtype=float)

    if home_epa is not None and i_off is not None and i_def is not None:
        h = _h_row()
        h[i_off] = 1.0
        h[d + i_def] = -1.0
        if not neutral_site:
            h[2 * d] = 1.0
            h[2 * d + 1] = 1.0
        rows.append(h)
        ys.append(float(home_epa))
        rs.append(observation_epa_variance(home_plays, cfg))
        names.append("home_epa")
        team_for_obs.append(home_team_id)

    if away_epa is not None and i_off is not None and i_def is not None:
        h = _h_row()
        h[d + i_off] = 1.0
        h[i_def] = -1.0
        rows.append(h)
        ys.append(float(away_epa))
        rs.append(observation_epa_variance(away_plays, cfg))
        names.append("away_epa")
        team_for_obs.append(away_team_id)

    if (
        home_st_epa is not None
        and away_st_epa is not None
        and i_st is not None
        and not (math.isnan(home_st_epa) or math.isnan(away_st_epa))
    ):
        h = _h_row()
        h[i_st] = 1.0
        h[d + i_st] = -1.0
        rows.append(h)
        ys.append(float(home_st_epa) - float(away_st_epa))
        rs.append(cfg.r_st_base**2)
        names.append("st_diff")
        team_for_obs.append(home_team_id)

    if pace_obs is not None and i_pace is not None and not math.isnan(pace_obs):
        # Center plays at ref_plays → obs in roughly ±1 units.
        y_pace = float(pace_obs) / cfg.ref_plays - 1.0
        h = _h_row()
        h[i_pace] = 0.5
        h[d + i_pace] = 0.5
        rows.append(h)
        ys.append(y_pace)
        rs.append(cfg.r_pace_base**2)
        names.append("pace")
        team_for_obs.append(home_team_id)

    if margin is not None and i_off is not None and i_def is not None and not math.isnan(margin):
        h = _h_row()
        # margin ≈ scale * (off_h - def_a - off_a + def_h + hfa)
        h[i_off] = cfg.margin_scale
        h[d + i_def] = -cfg.margin_scale
        h[d + i_off] = -cfg.margin_scale
        h[i_def] = cfg.margin_scale
        if not neutral_site:
            h[2 * d] = cfg.margin_scale
            h[2 * d + 1] = cfg.margin_scale
        rows.append(h)
        ys.append(float(margin))
        rs.append(cfg.r_margin**2)
        names.append("margin")
        team_for_obs.append(home_team_id)

    innovations: list[InnovationRecord] = []
    ts = event_time if event_time is not None else datetime(1970, 1, 1, tzinfo=UTC)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    ts = to_utc(ts)

    if not rows:
        return GameUpdateResult(
            home=home_state.copy(),
            away=away_state.copy(),
            hfa_global=hfa_global.copy(),
            hfa_dev_home=hfa_dev_home.copy(),
            innovations=[],
            log_likelihood=0.0,
        )

    h_mat = np.vstack(rows)
    y_vec = np.asarray(ys, dtype=float)
    r_vec = np.asarray(rs, dtype=float)
    posterior, innov_raw, _innov_used, z, ll = kalman_update(
        joint,
        h_mat,
        y_vec,
        r_vec,
        winsor_sigma=cfg.residual_winsor_sigma,
    )

    # Predictive S diagonal for recording (recompute from prior joint).
    s = h_mat @ joint.cov @ h_mat.T + np.diag(r_vec)
    pred_sd = np.sqrt(np.clip(np.diag(s), 1e-18, None))
    for i, name in enumerate(names):
        innovations.append(
            InnovationRecord(
                game_id=int(game_id),
                team_id=team_for_obs[i],
                season=int(season),
                week=int(week),
                event_time=ts,
                obs_name=name,
                innovation=float(innov_raw[i]),
                pred_sd=float(pred_sd[i]),
                z=float(z[i]),
                winsorized=bool(abs(float(z[i])) > cfg.residual_winsor_sigma),
            )
        )

    home_post = GaussianState(mean=posterior.mean[:d].copy(), cov=posterior.cov[:d, :d].copy())
    away_post = GaussianState(
        mean=posterior.mean[d : 2 * d].copy(),
        cov=posterior.cov[d : 2 * d, d : 2 * d].copy(),
    )
    hfa_post = GaussianState(
        mean=posterior.mean[2 * d : 2 * d + 1].copy(),
        cov=posterior.cov[2 * d : 2 * d + 1, 2 * d : 2 * d + 1].copy(),
    )
    hfa_dev_post = GaussianState(
        mean=posterior.mean[2 * d + 1 : 2 * d + 2].copy(),
        cov=posterior.cov[2 * d + 1 : 2 * d + 2, 2 * d + 1 : 2 * d + 2].copy(),
    )
    return GameUpdateResult(
        home=home_post,
        away=away_post,
        hfa_global=hfa_post,
        hfa_dev_home=hfa_dev_post,
        innovations=innovations,
        log_likelihood=ll,
    )


# ---------------------------------------------------------------------------
# History packing / as-of
# ---------------------------------------------------------------------------


def _state_to_row(
    team_id: Any,
    state: GaussianState,
    *,
    season: int,
    week: int,
    game_id: int | None,
    event_time: datetime,
    kind: HISTORY_KIND,
    config: StateSpaceConfig,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "team_id": team_id,
        "season": int(season),
        "week": int(week),
        "game_id": game_id,
        "event_time": event_time,
        "kind": kind,
    }
    for i, name in enumerate(config.state_dims):
        row[name] = float(state.mean[i])
        row[f"sd_{name}"] = float(math.sqrt(max(state.cov[i, i], 0.0)))
    # Full covariance as nested list for consumers that need it.
    row["cov"] = state.cov.tolist()
    return row


def posterior_asof(
    history: pd.DataFrame,
    team_id: Any,
    as_of: datetime,
    *,
    kind: HISTORY_KIND | None = "postgame",
    config: StateSpaceConfig | None = None,
) -> GaussianState | None:
    """Return the latest posterior for ``team_id`` with ``event_time < as_of``.

    Point-in-time: never returns a posterior computed from a future game.
    """
    cfg = config or StateSpaceConfig()
    bound = as_of_bound(as_of)
    if history.empty:
        return None
    mask = (history["team_id"].astype(str) == str(team_id)) & (
        pd.to_datetime(history["event_time"], utc=True) < bound
    )
    if kind is not None and "kind" in history.columns:
        mask = mask & (history["kind"] == kind)
    sub = history.loc[mask]
    if sub.empty:
        return None
    # Latest by event_time.
    idx = pd.to_datetime(sub["event_time"], utc=True).idxmax()
    row = sub.loc[idx]
    if "cov" in sub.columns and row["cov"] is not None:
        cov = np.asarray(row["cov"], dtype=float)
    else:
        cov = np.eye(cfg.n_dims, dtype=float)
        for i, name in enumerate(cfg.state_dims):
            sd_col = f"sd_{name}"
            if sd_col in sub.columns and pd.notna(row[sd_col]):
                cov[i, i] = float(row[sd_col]) ** 2
    mean = np.array([float(row[name]) for name in cfg.state_dims], dtype=float)
    return GaussianState(mean=mean, cov=cov)


# ---------------------------------------------------------------------------
# Filter runner
# ---------------------------------------------------------------------------


@dataclass
class FilterResult:
    """Full filter output: history table, innovations, league HFA path."""

    history: pd.DataFrame
    innovations: pd.DataFrame
    hfa_history: pd.DataFrame
    log_likelihood: float
    config: StateSpaceConfig


def run_filter(
    observations: pd.DataFrame,
    *,
    config: StateSpaceConfig | None = None,
    fbs_team_ids: set[Any] | None = None,
    q_events: Mapping[tuple[Any, int, int], Sequence[tuple[str, float | None]]] | None = None,
    record_weekly: bool = True,
    preseason_states: Mapping[int, Mapping[Any, GaussianState]] | None = None,
) -> FilterResult:
    """Run the Kalman filter over a chronologically sorted observation frame.

    Required columns: ``game_id``, ``season``, ``week``, ``event_time``,
    ``home_team_id``, ``away_team_id``, ``home_epa``, ``away_epa``,
    ``home_plays``, ``away_plays``, ``margin``, ``neutral_site``.

    Optional: ``home_st_epa``, ``away_st_epa``, ``pace_obs``,
    ``home_is_fcs``, ``away_is_fcs``.

    ``q_events`` maps ``(team_id, season, week)`` to a list of
    ``(event_name, manual_multiplier|None)`` applied before that team's
    process-noise step for the game.

    ``preseason_states`` maps ``season -> {team_id -> GaussianState}``. When a
    team first appears in a new season (or crosses a season boundary), the
    matching preseason state is injected instead of soft
    :func:`apply_season_regression`. Missing teams still soft-regress; callers
    that want widened missing-input variance must supply those priors via
    :func:`ncaa_quant.ratings.priors.build_preseason_states`.
    """
    cfg = config or StateSpaceConfig()
    if observations.empty:
        return FilterResult(
            history=pd.DataFrame(),
            innovations=pd.DataFrame(),
            hfa_history=pd.DataFrame(),
            log_likelihood=0.0,
            config=cfg,
        )

    obs = observations.copy()
    obs["event_time"] = [to_utc(pd.Timestamp(ts).to_pydatetime()) for ts in obs["event_time"]]
    obs = obs.sort_values(["event_time", "game_id"], kind="mergesort").reset_index(drop=True)

    teams: dict[str, GaussianState] = {}
    hfa_devs: dict[str, GaussianState] = {}
    hfa = initial_hfa_global(cfg)
    last_week: dict[str, tuple[int, int]] = {}
    history_rows: list[dict[str, Any]] = []
    innov_rows: list[InnovationRecord] = []
    hfa_rows: list[dict[str, Any]] = []
    total_ll = 0.0
    events = q_events or {}
    priors_by_season = preseason_states or {}

    def _lookup_prior(tid: str, season: int) -> GaussianState | None:
        season_map = priors_by_season.get(int(season))
        if not season_map:
            return None
        if tid in season_map:
            return season_map[tid]
        parsed = _parse_tid(tid)
        if parsed in season_map:
            return season_map[parsed]
        return None

    def _ensure(tid: str, *, is_fcs: bool, season: int) -> GaussianState:
        if is_fcs:
            return fcs_pinned_state(cfg)
        if tid not in teams:
            prior = _lookup_prior(tid, season)
            teams[tid] = prior if prior is not None else initial_team_state(cfg)
            hfa_devs[tid] = initial_hfa_team(cfg)
            last_week[tid] = (-1, -1)
        return teams[tid]

    def _predict_team(
        tid: str,
        state: GaussianState,
        *,
        season: int,
        week: int,
        is_fcs: bool,
    ) -> GaussianState:
        if is_fcs:
            return fcs_pinned_state(cfg)
        prev = last_week.get(tid, (-1, -1))
        q = cfg.q_matrix()
        # Season boundary.
        if prev[0] != -1 and season > prev[0]:
            prior = _lookup_prior(tid, season)
            state = prior if prior is not None else apply_season_regression(state, cfg)
            # Soft-reset team HFA deviation toward zero with inflated variance.
            prev_dev = hfa_devs.get(tid, initial_hfa_team(cfg))
            hfa_devs[tid] = GaussianState(
                mean=(1.0 - cfg.season_regression) * prev_dev.mean,
                cov=prev_dev.cov + np.array([[cfg.season_var_inflation * 0.05]]),
            )
            weeks_elapsed = max(week, 1)
        elif prev[0] == season:
            weeks_elapsed = max(int(week) - int(prev[1]), 1)
        else:
            weeks_elapsed = 1

        # Event-triggered inflation for this team-week.
        seen: set[tuple[Any, int, int]] = set()
        for key in ((tid, season, week), (_parse_tid(tid), season, week)):
            if key in seen:
                continue
            seen.add(key)
            for ev_name, manual in events.get(key, ()):
                q = inflate_q(
                    q,
                    ev_name,
                    config=cfg,
                    manual_multiplier=manual,
                    dim_names=cfg.state_dims,
                )

        return kalman_predict(state, q * float(weeks_elapsed))

    prev_season = None
    for row in obs.itertuples(index=False):
        season = int(row.season)
        week = int(row.week)
        if prev_season is not None and season > prev_season:
            # League HFA soft persistence across seasons.
            hfa = GaussianState(
                mean=(1.0 - cfg.season_regression) * hfa.mean
                + cfg.season_regression * cfg.hfa_prior_mean,
                cov=hfa.cov + np.array([[cfg.season_var_inflation * 0.1]]),
            )
        prev_season = season

        home_id = row.home_team_id
        away_id = row.away_team_id
        home_fcs = bool(getattr(row, "home_is_fcs", False))
        away_fcs = bool(getattr(row, "away_is_fcs", False))
        if fbs_team_ids is not None:
            home_fcs = home_fcs or (home_id not in fbs_team_ids)
            away_fcs = away_fcs or (away_id not in fbs_team_ids)

        h_key = _team_key(home_id, is_fcs=home_fcs)
        a_key = _team_key(away_id, is_fcs=away_fcs)

        h_state = _predict_team(
            h_key,
            _ensure(h_key, is_fcs=home_fcs, season=season),
            season=season,
            week=week,
            is_fcs=home_fcs,
        )
        a_state = _predict_team(
            a_key,
            _ensure(a_key, is_fcs=away_fcs, season=season),
            season=season,
            week=week,
            is_fcs=away_fcs,
        )
        hfa = kalman_predict(hfa, np.array([[cfg.hfa_q]]))
        h_dev = hfa_devs.get(h_key, initial_hfa_team(cfg))
        if not home_fcs:
            h_dev = kalman_predict(h_dev, np.array([[cfg.hfa_team_q]]))

        et = to_utc(pd.Timestamp(row.event_time).to_pydatetime())
        home_st = getattr(row, "home_st_epa", None)
        away_st = getattr(row, "away_st_epa", None)
        pace = getattr(row, "pace_obs", None)
        if pace is None or (isinstance(pace, float) and math.isnan(pace)):
            hp = float(row.home_plays) if pd.notna(row.home_plays) else cfg.ref_plays
            ap = float(row.away_plays) if pd.notna(row.away_plays) else cfg.ref_plays
            pace = 0.5 * (hp + ap)

        result = update_game(
            h_state,
            a_state,
            hfa,
            h_dev,
            home_epa=float(row.home_epa) if pd.notna(row.home_epa) else None,
            away_epa=float(row.away_epa) if pd.notna(row.away_epa) else None,
            home_plays=float(row.home_plays) if pd.notna(row.home_plays) else cfg.ref_plays,
            away_plays=float(row.away_plays) if pd.notna(row.away_plays) else cfg.ref_plays,
            home_st_epa=float(home_st) if home_st is not None and pd.notna(home_st) else None,
            away_st_epa=float(away_st) if away_st is not None and pd.notna(away_st) else None,
            pace_obs=float(pace) if pace is not None and pd.notna(pace) else None,
            margin=float(row.margin) if pd.notna(row.margin) else None,
            neutral_site=bool(row.neutral_site),
            config=cfg,
            game_id=int(row.game_id),
            home_team_id=home_id,
            away_team_id=away_id,
            season=season,
            week=week,
            event_time=et,
        )
        total_ll += result.log_likelihood
        innov_rows.extend(result.innovations)
        hfa = result.hfa_global

        if not home_fcs:
            teams[h_key] = result.home
            hfa_devs[h_key] = result.hfa_dev_home
            last_week[h_key] = (season, week)
            history_rows.append(
                _state_to_row(
                    home_id,
                    result.home,
                    season=season,
                    week=week,
                    game_id=int(row.game_id),
                    event_time=et,
                    kind="postgame",
                    config=cfg,
                )
            )
        if not away_fcs:
            teams[a_key] = result.away
            last_week[a_key] = (season, week)
            history_rows.append(
                _state_to_row(
                    away_id,
                    result.away,
                    season=season,
                    week=week,
                    game_id=int(row.game_id),
                    event_time=et,
                    kind="postgame",
                    config=cfg,
                )
            )

        hfa_rows.append(
            {
                "season": season,
                "week": week,
                "game_id": int(row.game_id),
                "event_time": et,
                "hfa_global": float(hfa.mean[0]),
                "sd_hfa_global": float(math.sqrt(max(hfa.cov[0, 0], 0.0))),
            }
        )

    history = pd.DataFrame(history_rows)
    if record_weekly and not history.empty:
        weekly = (
            history.sort_values("event_time")
            .groupby(["team_id", "season", "week"], sort=False)
            .tail(1)
            .copy()
        )
        weekly["kind"] = "weekly"
        weekly["game_id"] = None
        history = pd.concat([history, weekly], ignore_index=True)

    innov_df = pd.DataFrame(
        [
            {
                "game_id": r.game_id,
                "team_id": r.team_id,
                "season": r.season,
                "week": r.week,
                "event_time": r.event_time,
                "obs_name": r.obs_name,
                "innovation": r.innovation,
                "pred_sd": r.pred_sd,
                "z": r.z,
                "winsorized": r.winsorized,
            }
            for r in innov_rows
        ]
    )
    return FilterResult(
        history=history,
        innovations=innov_df,
        hfa_history=pd.DataFrame(hfa_rows),
        log_likelihood=total_ll,
        config=cfg,
    )


def _parse_tid(tid: str) -> Any:
    try:
        return int(tid)
    except (TypeError, ValueError):
        return tid


# ---------------------------------------------------------------------------
# Q tuning
# ---------------------------------------------------------------------------


def tune_process_noise(
    observations: pd.DataFrame,
    *,
    q_scales: Sequence[float] | None = None,
    config: StateSpaceConfig | None = None,
    fbs_team_ids: set[Any] | None = None,
    dims: Sequence[str] | None = None,
) -> tuple[StateSpaceConfig, dict[str, float], float]:
    """Scan diagonal Q scales maximizing one-step-ahead predictive log-lik.

    A simple per-dimension scale grid (shared scale applied to the default
    ``q_diag`` shape, then optional per-dim refinement) — no Optuna.
    """
    base = config or StateSpaceConfig()
    scales = list(q_scales) if q_scales is not None else [0.25, 0.5, 1.0, 2.0, 4.0]
    target_dims = list(dims) if dims is not None else list(base.state_dims)

    best_cfg = base
    best_ll = -float("inf")
    best_q = dict(base.q_diag)

    # First: global scale on all dims.
    for scale in scales:
        q = {k: float(v) * scale for k, v in base.q_diag.items()}
        cfg = replace(base, q_diag=q)
        result = run_filter(observations, config=cfg, fbs_team_ids=fbs_team_ids)
        if result.log_likelihood > best_ll:
            best_ll = result.log_likelihood
            best_cfg = cfg
            best_q = q

    # Second: per-dim absolute scale vs base, holding other dims at best_q.
    for dim in target_dims:
        dim_best_ll = best_ll
        dim_best_q = dict(best_q)
        for scale in scales:
            trial_q = dict(best_q)
            trial_q[dim] = float(base.q_diag[dim]) * float(scale)
            cfg = replace(base, q_diag=trial_q)
            result = run_filter(observations, config=cfg, fbs_team_ids=fbs_team_ids)
            if result.log_likelihood > dim_best_ll:
                dim_best_ll = result.log_likelihood
                dim_best_q = trial_q
        best_q = dim_best_q
        best_ll = dim_best_ll
        best_cfg = replace(base, q_diag=best_q)

    return best_cfg, best_q, best_ll


# ---------------------------------------------------------------------------
# Simulation (parameter recovery)
# ---------------------------------------------------------------------------


def simulate_league(
    *,
    n_teams: int = 20,
    n_weeks: int = 12,
    games_per_week: int = 10,
    state_dims: Sequence[str] = V1_STATE_DIMS,
    q_diag: Mapping[str, float] | None = None,
    seed: int = 0,
    obs_noise: float = 0.08,
    schedule_seed: int | None = None,
) -> tuple[pd.DataFrame, dict[int, np.ndarray], StateSpaceConfig]:
    """Simulate a league with known latent random-walk strengths + schedule.

    Returns ``(observations, true_paths, config)`` where ``true_paths[team]``
    has shape ``(n_weeks+1, n_dims)`` (preseason + after each week).
    """
    set_global_seed(seed)
    rng = np.random.default_rng(seed)
    sched_rng = np.random.default_rng(schedule_seed if schedule_seed is not None else seed + 1)
    dims = tuple(state_dims)
    cfg = StateSpaceConfig(
        state_dims=dims,
        q_diag=dict(q_diag) if q_diag is not None else _default_q_diag(dims),
        r_epa_base=obs_noise,
        residual_winsor_sigma=2.5,
    )
    d = len(dims)
    q = cfg.q_matrix()
    i_off = dims.index("off_epa") if "off_epa" in dims else 0
    i_def = dims.index("def_epa") if "def_epa" in dims else min(1, d - 1)

    truths: dict[int, np.ndarray] = {
        t: np.zeros((n_weeks + 1, d), dtype=float) for t in range(n_teams)
    }
    for t in range(n_teams):
        truths[t][0] = rng.normal(0.0, 0.15, size=d)

    from datetime import UTC

    rows: list[dict[str, Any]] = []
    game_id = 0
    for week in range(1, n_weeks + 1):
        # Drift all teams.
        for t in range(n_teams):
            truths[t][week] = truths[t][week - 1] + rng.multivariate_normal(np.zeros(d), q)
        # Random matching.
        order = sched_rng.permutation(n_teams)
        pairs = [
            (int(order[i]), int(order[i + 1]))
            for i in range(0, min(2 * games_per_week, n_teams - n_teams % 2), 2)
        ]
        for home, away in pairs:
            game_id += 1
            th = truths[home][week]
            ta = truths[away][week]
            hfa = cfg.hfa_prior_mean
            epa_h = th[i_off] - ta[i_def] + hfa + rng.normal(0.0, obs_noise)
            epa_a = ta[i_off] - th[i_def] + rng.normal(0.0, obs_noise)
            net = (th[i_off] - ta[i_def]) - (ta[i_off] - th[i_def]) + hfa
            margin = cfg.margin_scale * net + rng.normal(0.0, cfg.r_margin * 0.3)
            et = datetime(2020, 9, 1, tzinfo=UTC) + pd.Timedelta(weeks=week)
            rows.append(
                {
                    "game_id": game_id,
                    "season": 2020,
                    "week": week,
                    "event_time": et.to_pydatetime() if hasattr(et, "to_pydatetime") else et,
                    "home_team_id": home,
                    "away_team_id": away,
                    "home_epa": float(epa_h),
                    "away_epa": float(epa_a),
                    "home_plays": cfg.ref_plays,
                    "away_plays": cfg.ref_plays,
                    "home_st_epa": np.nan,
                    "away_st_epa": np.nan,
                    "pace_obs": cfg.ref_plays,
                    "margin": float(margin),
                    "neutral_site": False,
                    "home_is_fcs": False,
                    "away_is_fcs": False,
                }
            )

    return pd.DataFrame(rows), truths, cfg


def parameter_recovery_coverage(
    *,
    n_teams: int = 16,
    n_weeks: int = 10,
    seed: int = 42,
    z_band: float = 1.96,
) -> dict[str, float]:
    """Simulate, filter, and report empirical coverage of the ``z_band`` interval.

    Returns a dict with ``coverage``, ``mean_abs_error_off``, ``mean_abs_error_def``,
    and ``n_points``.
    """
    obs, truths, cfg = simulate_league(
        n_teams=n_teams, n_weeks=n_weeks, games_per_week=n_teams // 2, seed=seed
    )
    # Give the filter the true Q (recovery of paths, not of Q).
    result = run_filter(obs, config=cfg, record_weekly=True)
    hist = result.history
    hist = hist.loc[hist["kind"] == "weekly"].copy()

    i_off = cfg.dim_index("off_epa")
    i_def = cfg.dim_index("def_epa")
    covered = 0
    total = 0
    abs_off = 0.0
    abs_def = 0.0

    for row in hist.itertuples(index=False):
        tid = int(row.team_id)
        week = int(row.week)
        true = truths[tid][week]
        mean = np.array([getattr(row, name) for name in cfg.state_dims], dtype=float)
        sd = np.array([getattr(row, f"sd_{name}") for name in cfg.state_dims], dtype=float)
        for j in range(cfg.n_dims):
            lo = mean[j] - z_band * sd[j]
            hi = mean[j] + z_band * sd[j]
            if lo <= true[j] <= hi:
                covered += 1
            total += 1
        abs_off += abs(float(mean[i_off] - true[i_off]))
        abs_def += abs(float(mean[i_def] - true[i_def]))

    n_rows = max(len(hist), 1)
    return {
        "coverage": covered / max(total, 1),
        "mean_abs_error_off": abs_off / n_rows,
        "mean_abs_error_def": abs_def / n_rows,
        "n_points": float(total),
    }


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------


def end_of_season_ratings(
    history: pd.DataFrame,
    season: int,
    *,
    config: StateSpaceConfig | None = None,
    kind: HISTORY_KIND = "weekly",
) -> pd.DataFrame:
    """Latest ratings per team for a season (off/def means + SDs)."""
    cfg = config or StateSpaceConfig()
    if history.empty:
        return pd.DataFrame()
    sub = history.loc[(history["season"] == season) & (history["kind"] == kind)]
    if sub.empty:
        sub = history.loc[history["season"] == season]
    if sub.empty:
        return pd.DataFrame()
    sub = sub.sort_values("event_time")
    latest = sub.groupby("team_id", sort=False).tail(1).copy()
    cols = ["team_id", "season", "week", "event_time", *cfg.state_dims]
    cols += [f"sd_{d}" for d in cfg.state_dims]
    return latest[[c for c in cols if c in latest.columns]].reset_index(drop=True)


def team_sd_trajectory(
    history: pd.DataFrame,
    team_id: Any,
    season: int,
    *,
    dim: str = "off_epa",
    kind: HISTORY_KIND = "postgame",
) -> pd.DataFrame:
    """Posterior SD path for one team-season (must shrink as games accumulate)."""
    mask = (
        (history["team_id"].astype(str) == str(team_id))
        & (history["season"] == season)
        & (history["kind"] == kind)
    )
    sub = history.loc[mask, ["week", "event_time", dim, f"sd_{dim}"]].copy()
    return sub.sort_values("event_time").reset_index(drop=True)
