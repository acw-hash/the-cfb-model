"""Efficiency feature builders (DESIGN §4.3 / §4.4 / §4.5 / §15 item 10).

Ridge opponent adjustment, Bayesian shrinkage, EWMA / last-3 deltas, and FCS
pooling for the production efficiency feature family.

Point-in-time: builders only use ``history`` rows with ``event_time < as_of``
(via :meth:`FeatureBuilder.filter_event_time`). Materializers inject the full
history frame; :mod:`ncaa_quant.features.pit_audit` re-injects a restricted
frame on audit.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from ncaa_quant.features.builder import (
    AS_OF_COL,
    ENTITY_COL,
    VALUE_COL,
    FeatureBuilder,
    FeatureBuildError,
)
from ncaa_quant.features.epa import filter_garbage_time
from ncaa_quant.features.registry import FeatureSpec

# ---------------------------------------------------------------------------
# Constants / config
# ---------------------------------------------------------------------------

# Pooled entity for every non-FBS opponent (DESIGN §1.5 / Task 10). Individual
# FCS / II / III schools lack enough games for stable ratings; pooling them
# into one FCS-tier entity is the intended model. FBS teams keep their own ids.
FCS_TIER_ENTITY: Final[str] = "FCS_TIER"

# Untuned PLACEHOLDER — walk-forward CV lands in a later task (DESIGN §4.3).
DEFAULT_RIDGE_LAMBDA: Final[float] = 5.0
DEFAULT_SHRINKAGE_K: Final[float] = 8.0
DEFAULT_EWMA_HALF_LIFE: Final[float] = 6.5
DEFAULT_EWMA_HALF_LIFE_EXPLOSIVENESS: Final[float] = 10.0

# Trips "inside the 40" use yards-to-goal ≤ this threshold (finishing drives).
FINISHING_YARDS_TO_GOAL: Final[int] = 40

MetricName = Literal[
    "epa",
    "rush_epa",
    "pass_epa",
    "success_rate",
    "rush_success_rate",
    "pass_success_rate",
    "explosiveness",
    "rush_explosiveness",
    "pass_explosiveness",
    "havoc",
    "finishing_drives",
    "field_position",
]
Side = Literal["off", "def"]
Form = Literal["std", "ewma", "l3d"]

_METRIC_Y_COL: Final[Mapping[MetricName, str]] = {
    "epa": "epa_per_play",
    "rush_epa": "rush_epa_per_play",
    "pass_epa": "pass_epa_per_play",
    "success_rate": "success_rate",
    "rush_success_rate": "rush_success_rate",
    "pass_success_rate": "pass_success_rate",
    "explosiveness": "explosiveness",
    "rush_explosiveness": "rush_explosiveness",
    "pass_explosiveness": "pass_explosiveness",
    "havoc": "havoc_rate",
    "finishing_drives": "finishing_drives",
    "field_position": "field_position",
}

# Feature names: adj_{off|def}_{metric}_{std|ewma|l3d}
_FEATURE_NAME_RE = re.compile(
    r"^adj_(?P<side>off|def)_(?P<metric>"
    r"epa|rush_epa|pass_epa|"
    r"success_rate|rush_success_rate|pass_success_rate|"
    r"explosiveness|rush_explosiveness|pass_explosiveness|"
    r"havoc|finishing_drives|field_position"
    r")_(?P<form>std|ewma|l3d)$"
)


@dataclass(frozen=True, slots=True)
class EfficiencyConfig:
    """Tunable knobs for efficiency builders (DESIGN §4.3 / §4.4).

    ``ridge_lambda`` is L2 strength toward zero; **not tuned here** (later
    walk-forward CV). Shrinkage ``k`` and EWMA half-lives match ``configs/data.yaml``
    midpoints until those are retuned.
    """

    ridge_lambda: float = DEFAULT_RIDGE_LAMBDA
    shrinkage_k: float = DEFAULT_SHRINKAGE_K
    ewma_half_life_efficiency: float = DEFAULT_EWMA_HALF_LIFE
    ewma_half_life_explosiveness: float = DEFAULT_EWMA_HALF_LIFE_EXPLOSIVENESS

    def half_life_for(self, metric: MetricName) -> float:
        """Return the EWMA half-life (games) for ``metric``."""
        if "explosiveness" in metric:
            return float(self.ewma_half_life_explosiveness)
        return float(self.ewma_half_life_efficiency)


@dataclass(frozen=True, slots=True)
class RidgeResult:
    """Fitted opponent-adjusted ratings for one metric."""

    off_ratings: dict[str, float]
    def_ratings: dict[str, float]
    hfa: float
    entities: tuple[str, ...]
    ridge_lambda: float
    n_obs: int


@dataclass(frozen=True, slots=True)
class FeatureCard:
    """Parsed efficiency feature identity from a registry name."""

    side: Side
    metric: MetricName
    form: Form


# ---------------------------------------------------------------------------
# Shrinkage / EWMA (pure math)
# ---------------------------------------------------------------------------


def bayesian_shrink(
    observed_mean: float,
    prior: float,
    n: float,
    k: float,
) -> float:
    """Shrink ``observed_mean`` toward ``prior`` with weight ``n/(n+k)``.

    Units follow the metric. ``n`` is the effective sample size (games count).
    At ``n=0`` the result is ``prior``; at ``n=k`` the midpoint; as ``n→∞``
    the result approaches ``observed_mean``.
    """
    if k < 0:
        msg = f"shrinkage k must be >= 0, got {k}"
        raise ValueError(msg)
    if n < 0:
        msg = f"sample size n must be >= 0, got {n}"
        raise ValueError(msg)
    if n == 0 and k == 0:
        return float(prior)
    weight = n / (n + k)
    return float(weight * observed_mean + (1.0 - weight) * prior)


def ewma_alpha(half_life: float) -> float:
    """EWMA smoothing factor for a half-life measured in games.

    Weight of an observation ``age`` games ago is ``0.5 ** (age / half_life)``.
    Equivalent one-step recursion uses ``alpha = 1 - 0.5 ** (1 / half_life)``.
    """
    if half_life <= 0:
        msg = f"half_life must be > 0, got {half_life}"
        raise ValueError(msg)
    return float(1.0 - math.pow(0.5, 1.0 / half_life))


def ewma_sequence(values: Sequence[float], half_life: float) -> list[float]:
    """Forward EWMA over a chronologically ordered sequence (oldest first).

    First point initializes the state; subsequent points use
    ``s_t = alpha * x_t + (1 - alpha) * s_{t-1}``.
    """
    alpha = ewma_alpha(half_life)
    out: list[float] = []
    state: float | None = None
    for value in values:
        x = float(value)
        state = x if state is None else alpha * x + (1.0 - alpha) * state
        out.append(float(state))
    return out


def ewma_final(values: Sequence[float], half_life: float) -> float:
    """Return the terminal EWMA value, or NaN when ``values`` is empty."""
    if not values:
        return float("nan")
    return ewma_sequence(values, half_life)[-1]


def last_n_delta(
    values: Sequence[float],
    *,
    n: int = 3,
    season_mean: float | None = None,
) -> float:
    """Last-``n`` mean minus season mean (recent-form delta).

    When fewer than ``n`` values exist, uses all of them. Empty → NaN.
    """
    if not values:
        return float("nan")
    recent = list(values)[-n:]
    recent_mean = float(sum(recent) / len(recent))
    base = float(sum(values) / len(values)) if season_mean is None else float(season_mean)
    return recent_mean - base


# ---------------------------------------------------------------------------
# FCS pooling
# ---------------------------------------------------------------------------


def pool_entity_id(
    team_id: Any,
    *,
    fbs_team_ids: set[Any],
) -> str:
    """Map a team id to its ridge entity; non-FBS → :data:`FCS_TIER_ENTITY`."""
    if team_id in fbs_team_ids:
        return str(team_id)
    return FCS_TIER_ENTITY


def fbs_team_id_set(teams: pd.DataFrame) -> set[Any]:
    """Return the set of FBS ``team_id`` values from a teams frame."""
    if teams.empty or "team_id" not in teams.columns:
        return set()
    if "classification" in teams.columns:
        mask = teams["classification"].astype(str).str.casefold() == "fbs"
        return set(teams.loc[mask, "team_id"].tolist())
    return set(teams["team_id"].tolist())


# ---------------------------------------------------------------------------
# Ridge opponent adjustment
# ---------------------------------------------------------------------------


def ridge_opponent_adjust(
    observations: pd.DataFrame,
    *,
    y_col: str = "y",
    offense_col: str = "offense_id",
    defense_col: str = "defense_id",
    is_home_col: str = "is_home",
    ridge_lambda: float = DEFAULT_RIDGE_LAMBDA,
    fbs_team_ids: set[Any] | None = None,
) -> RidgeResult:
    """Solve ``y = off_i − def_j + hfa + ε`` with L2 shrinkage toward zero.

    Each row is one offensive observation (play- or game-level). Non-FBS
    entities in ``offense_id`` / ``defense_id`` are pooled into
    :data:`FCS_TIER_ENTITY` when ``fbs_team_ids`` is provided; if ids are
    already pooled strings, pass ``fbs_team_ids=None`` and leave them as-is.

    Parameters
    ----------
    ridge_lambda:
        L2 / Tikhonov strength toward zero (added to the diagonal of X'X).
        Default :data:`DEFAULT_RIDGE_LAMBDA` is an untuned placeholder.

    Returns
    -------
    RidgeResult
        Per-entity offense/defense ratings and the league HFA coefficient.
        Higher offense = more of ``y`` generated; higher defense = more of
        ``y`` suppressed.
    """
    if ridge_lambda < 0:
        msg = f"ridge_lambda must be >= 0, got {ridge_lambda}"
        raise ValueError(msg)
    required = {y_col, offense_col, defense_col, is_home_col}
    missing = required - set(observations.columns)
    if missing:
        msg = f"ridge observations missing columns: {sorted(missing)}"
        raise ValueError(msg)

    work = observations.loc[observations[y_col].notna()].copy()
    if work.empty:
        return RidgeResult(
            off_ratings={},
            def_ratings={},
            hfa=0.0,
            entities=(),
            ridge_lambda=float(ridge_lambda),
            n_obs=0,
        )

    if fbs_team_ids is not None:
        work[offense_col] = [
            pool_entity_id(v, fbs_team_ids=fbs_team_ids) for v in work[offense_col]
        ]
        work[defense_col] = [
            pool_entity_id(v, fbs_team_ids=fbs_team_ids) for v in work[defense_col]
        ]
    else:
        work[offense_col] = work[offense_col].map(str)
        work[defense_col] = work[defense_col].map(str)

    entities = tuple(sorted(set(work[offense_col]) | set(work[defense_col])))
    index = {entity: i for i, entity in enumerate(entities)}
    n_ent = len(entities)
    # Columns: off_0..off_{n-1}, def_0..def_{n-1}, hfa
    n_params = 2 * n_ent + 1
    n_obs = len(work)
    design = np.zeros((n_obs, n_params), dtype=np.float64)
    y = work[y_col].to_numpy(dtype=np.float64)

    off_ids = work[offense_col].tolist()
    def_ids = work[defense_col].tolist()
    home = work[is_home_col].astype(bool).to_numpy()
    for row_i, (off_e, def_e, is_home) in enumerate(zip(off_ids, def_ids, home, strict=True)):
        design[row_i, index[off_e]] = 1.0
        design[row_i, n_ent + index[def_e]] = -1.0
        design[row_i, -1] = 1.0 if is_home else 0.0

    # Tikhonov: (X'X + λI) β = X'y  (equivalent to sklearn Ridge fit_intercept=False)
    gram = design.T @ design
    gram.flat[:: n_params + 1] += float(ridge_lambda)
    rhs = design.T @ y
    coef = np.linalg.solve(gram, rhs)

    off_ratings = {entities[i]: float(coef[i]) for i in range(n_ent)}
    def_ratings = {entities[i]: float(coef[n_ent + i]) for i in range(n_ent)}
    hfa = float(coef[-1])
    return RidgeResult(
        off_ratings=off_ratings,
        def_ratings=def_ratings,
        hfa=hfa,
        entities=entities,
        ridge_lambda=float(ridge_lambda),
        n_obs=n_obs,
    )


def game_adjusted_values(
    observations: pd.DataFrame,
    result: RidgeResult,
    *,
    y_col: str = "y",
    offense_col: str = "offense_id",
    defense_col: str = "defense_id",
    is_home_col: str = "is_home",
) -> pd.DataFrame:
    """Back out per-game adjusted off/def contributions using fitted ratings.

    For ``y ≈ off_i − def_j + hfa``:
    - ``adj_off = y + def_j − hfa·home``
    - ``adj_def = off_i − y + hfa·home``
    """
    if observations.empty:
        return pd.DataFrame(
            columns=[
                offense_col,
                defense_col,
                "event_time",
                "adj_off",
                "adj_def",
                "game_id",
            ]
        )

    off_ids = observations[offense_col].map(str)
    def_ids = observations[defense_col].map(str)
    y = observations[y_col].astype(float)
    home = observations[is_home_col].astype(bool).astype(float)
    def_hat = off_ids.map(lambda e: result.def_ratings.get(str(e), 0.0)).astype(float)
    # Map via offense id string for off_hat of the offense on this row; for
    # adj_def we need the offense's off rating.
    off_hat = off_ids.map(lambda e: result.off_ratings.get(str(e), 0.0)).astype(float)
    hfa = float(result.hfa)

    out = observations.copy()
    out[offense_col] = off_ids
    out[defense_col] = def_ids
    out["adj_off"] = y + def_hat - hfa * home
    out["adj_def"] = off_hat - y + hfa * home
    return out


# ---------------------------------------------------------------------------
# Observation construction
# ---------------------------------------------------------------------------


def _team_name_to_id(teams: pd.DataFrame) -> dict[str, Any]:
    if teams.empty:
        return {}
    name_col = "school" if "school" in teams.columns else None
    if name_col is None:
        return {}
    return {str(row[name_col]): row["team_id"] for _, row in teams.iterrows()}


def build_play_game_observations(
    plays: pd.DataFrame,
    games: pd.DataFrame,
    teams: pd.DataFrame,
    *,
    drives: pd.DataFrame | None = None,
    drop_garbage: bool = True,
) -> pd.DataFrame:
    """Aggregate garbage-filtered plays into per-(game, offense) ridge rows.

    Efficiency rates use rush+pass snaps only (special teams / penalties
    excluded). Adds finishing-drives and field-position when ``drives`` is
    supplied. ``event_time`` comes from the game ``start_date`` / staged
    ``event_time``. ``field_position`` is mean starting yards-from-own-goal
    (``100 - start_yards_to_goal``) so higher is better for the offense.
    ``finishing_drives`` is mean points on drives starting at
    yards-to-goal ≤ :data:`FINISHING_YARDS_TO_GOAL`.
    """
    if plays.empty:
        return pd.DataFrame()

    work = plays.copy()
    if "offense_team" not in work.columns and "offense_id" in work.columns:
        work["offense_team"] = work["offense_id"].map(str)
    if "defense_team" not in work.columns and "defense_id" in work.columns:
        work["defense_team"] = work["defense_id"].map(str)

    if drop_garbage:
        work = filter_garbage_time(work)

    name_map = _team_name_to_id(teams)
    if "offense_id" not in work.columns:
        work["offense_id"] = work["offense_team"].map(name_map)
    if "defense_id" not in work.columns:
        work["defense_id"] = work["defense_team"].map(name_map)

    work = work.dropna(subset=["game_id", "offense_id", "defense_id"])
    if work.empty:
        return pd.DataFrame()

    # Snap filter: efficiency metrics are rush/pass only.
    if "is_rush" in work.columns and "is_pass" in work.columns:
        snap = work.loc[work["is_rush"].astype(bool) | work["is_pass"].astype(bool)].copy()
    else:
        snap = work

    agg = _vectorized_efficiency_by_game(snap)
    if agg.empty:
        return pd.DataFrame()

    game_ctx = _game_context(games)
    agg = agg.merge(game_ctx, on="game_id", how="left")
    home_ids = agg["home_team_id"]
    # Compare before FCS pooling (still raw ids).
    agg["is_home"] = (agg["offense_id"] == home_ids) & (~agg["neutral_site"].fillna(False))

    if drives is not None and not drives.empty:
        drive_metrics = _drive_metrics(drives)
        agg = agg.merge(drive_metrics, on=["game_id", "offense_id"], how="left")
    else:
        agg["finishing_drives"] = float("nan")
        agg["field_position"] = float("nan")

    fbs_ids = fbs_team_id_set(teams)
    agg["offense_id"] = [pool_entity_id(v, fbs_team_ids=fbs_ids) for v in agg["offense_id"]]
    agg["defense_id"] = [pool_entity_id(v, fbs_team_ids=fbs_ids) for v in agg["defense_id"]]
    return agg.reset_index(drop=True)


def _vectorized_efficiency_by_game(plays: pd.DataFrame) -> pd.DataFrame:
    """Fast game-level EPA / SR / explosiveness / havoc (+ rush/pass splits)."""
    if plays.empty:
        return pd.DataFrame()

    keys = ["game_id", "offense_id", "defense_id"]
    frame = plays.copy()
    if "is_success" in frame.columns:
        frame["_success"] = pd.to_numeric(frame["is_success"], errors="coerce")
    else:
        frame["_success"] = pd.NA
    frame["_havoc"] = (
        frame["is_havoc"].astype(bool).astype(float) if "is_havoc" in frame.columns else 0.0
    )
    frame["_epa"] = pd.to_numeric(frame["epa"], errors="coerce")

    def _block(subset: pd.DataFrame, prefix: str) -> pd.DataFrame:
        if subset.empty:
            cols = keys + [
                f"{prefix}epa_per_play",
                f"{prefix}success_rate",
                f"{prefix}explosiveness",
                f"{prefix}havoc_rate",
                f"{prefix}n_plays",
            ]
            return pd.DataFrame(columns=cols)
        grouped = subset.groupby(keys, dropna=False, sort=False)
        epa = grouped["_epa"].mean().rename(f"{prefix}epa_per_play")
        sr = grouped["_success"].mean().rename(f"{prefix}success_rate")
        havoc = grouped["_havoc"].mean().rename(f"{prefix}havoc_rate")
        n_plays = grouped.size().astype(float).rename(f"{prefix}n_plays")
        succ_mask = subset["_success"].fillna(0).astype(float) > 0
        succ = subset.loc[succ_mask]
        if succ.empty:
            expl = pd.Series(float("nan"), index=epa.index, name=f"{prefix}explosiveness")
        else:
            expl = (
                succ.groupby(keys, dropna=False, sort=False)["_epa"]
                .mean()
                .reindex(epa.index)
                .rename(f"{prefix}explosiveness")
            )
        return pd.concat([epa, sr, expl, havoc, n_plays], axis=1).reset_index()

    out = _block(frame, prefix="")
    if "is_rush" in frame.columns:
        rush_block = _block(frame.loc[frame["is_rush"].astype(bool)], "rush_")
        out = out.merge(rush_block, on=keys, how="left")
    if "is_pass" in frame.columns:
        pass_block = _block(frame.loc[frame["is_pass"].astype(bool)], "pass_")
        out = out.merge(pass_block, on=keys, how="left")
    return out


def _game_context(games: pd.DataFrame) -> pd.DataFrame:
    if games.empty:
        return pd.DataFrame(
            columns=["game_id", "home_team_id", "neutral_site", "event_time", "season", "week"]
        )
    out = games.copy()
    if "event_time" not in out.columns and "start_date" in out.columns:
        out["event_time"] = pd.to_datetime(out["start_date"], utc=True)
    else:
        out["event_time"] = pd.to_datetime(out["event_time"], utc=True)
    cols = ["game_id", "home_team_id", "neutral_site", "event_time"]
    for optional in ("season", "week"):
        if optional in out.columns:
            cols.append(optional)
    return out[cols].drop_duplicates(subset=["game_id"])


def _drive_metrics(drives: pd.DataFrame) -> pd.DataFrame:
    """Per-(game, offense) finishing drives + field position."""
    work = drives.dropna(subset=["game_id", "offense_id"]).copy()
    if work.empty:
        return pd.DataFrame(columns=["game_id", "offense_id", "finishing_drives", "field_position"])

    ytg = pd.to_numeric(work["start_yards_to_goal"], errors="coerce")
    points = pd.to_numeric(work["points"], errors="coerce")
    work = work.assign(_ytg=ytg, _points=points)
    # Field position: yards from own goal (higher = better).
    work["_fp"] = 100.0 - work["_ytg"]

    finishing = (
        work.loc[work["_ytg"] <= FINISHING_YARDS_TO_GOAL]
        .groupby(["game_id", "offense_id"], dropna=False)["_points"]
        .mean()
        .rename("finishing_drives")
    )
    field_pos = (
        work.groupby(["game_id", "offense_id"], dropna=False)["_fp"].mean().rename("field_position")
    )
    out = pd.concat([finishing, field_pos], axis=1).reset_index()
    return out


# ---------------------------------------------------------------------------
# Priors seam (Task 15 swaps real preseason priors in here)
# ---------------------------------------------------------------------------


def resolve_priors(
    entity_ids: Sequence[str],
    *,
    league_mean: float,
    prior_lookup: Mapping[str, float] | None = None,
) -> dict[str, float]:
    """Return per-entity priors.

    Until Task 15 builds real preseason priors, ``prior_lookup`` is ``None`` and
    every entity shrinks toward ``league_mean``. Pass a lookup to swap in
    preseason priors without changing shrinkage math.
    """
    if prior_lookup is None:
        return {str(e): float(league_mean) for e in entity_ids}
    return {str(e): float(prior_lookup.get(str(e), league_mean)) for e in entity_ids}


# ---------------------------------------------------------------------------
# Feature name parsing / builder
# ---------------------------------------------------------------------------


def parse_feature_name(name: str) -> FeatureCard:
    """Parse ``adj_{side}_{metric}_{form}`` registry names."""
    match = _FEATURE_NAME_RE.match(name)
    if match is None:
        msg = f"unsupported efficiency feature name: {name!r}"
        raise FeatureBuildError(msg)
    return FeatureCard(
        side=match.group("side"),  # type: ignore[arg-type]
        metric=match.group("metric"),  # type: ignore[arg-type]
        form=match.group("form"),  # type: ignore[arg-type]
    )


class EfficiencyFeatureBuilder(FeatureBuilder):
    """One scalar efficiency feature; behavior driven by ``spec.name``.

    Constructor injects the observation ``history`` frame (game-level rows from
    :func:`build_play_game_observations`). ``history`` is mutable so
    :func:`pit_audit.audit_partition` can replace it with an as-of-restricted
    copy.
    """

    def __init__(
        self,
        spec: FeatureSpec,
        history: pd.DataFrame,
        *,
        config: EfficiencyConfig | None = None,
        prior_lookup: Mapping[str, float] | None = None,
    ) -> None:
        super().__init__(spec)
        self.history = history
        self.config = config or EfficiencyConfig()
        self.prior_lookup = prior_lookup
        self.card = parse_feature_name(spec.name)

    def compute(self, entity_ids: Sequence[Any], as_of: datetime) -> pd.DataFrame:
        eligible = self.filter_event_time(self.history, as_of)
        card = self.card
        y_col = _METRIC_Y_COL[card.metric]

        if eligible.empty or y_col not in eligible.columns:
            return self._empty_frame(entity_ids, as_of)

        obs = eligible.dropna(subset=[y_col, "offense_id", "defense_id", "is_home"]).copy()
        if "event_time" in obs.columns:
            obs = obs.sort_values("event_time", kind="mergesort")
        if obs.empty:
            return self._empty_frame(entity_ids, as_of)

        # Ids are already pooled strings from build_play_game_observations.
        ridge = ridge_opponent_adjust(
            obs.rename(columns={y_col: "y"}),
            y_col="y",
            ridge_lambda=self.config.ridge_lambda,
            fbs_team_ids=None,
        )
        adjusted = game_adjusted_values(
            obs.rename(columns={y_col: "y"}),
            ridge,
            y_col="y",
        )

        values = self._entity_values(adjusted, ridge, entity_ids, card)
        rows = [
            {
                ENTITY_COL: entity_id,
                AS_OF_COL: as_of,
                VALUE_COL: values.get(str(entity_id), float("nan")),
            }
            for entity_id in entity_ids
        ]
        frame = pd.DataFrame(rows)
        if self.spec.null_policy == "indicator":
            frame["is_missing"] = frame[VALUE_COL].isna()
        return frame

    def _entity_values(
        self,
        adjusted: pd.DataFrame,
        ridge: RidgeResult,
        entity_ids: Sequence[Any],
        card: FeatureCard,
    ) -> dict[str, float]:
        value_col = "adj_off" if card.side == "off" else "adj_def"
        id_col = "offense_id" if card.side == "off" else "defense_id"
        half_life = self.config.half_life_for(card.metric)

        # League mean of the ridge rating on this side — prior seam target.
        rating_map = ridge.off_ratings if card.side == "off" else ridge.def_ratings
        league_mean = float(sum(rating_map.values()) / len(rating_map)) if rating_map else 0.0
        priors = resolve_priors(
            [str(e) for e in entity_ids],
            league_mean=league_mean,
            prior_lookup=self.prior_lookup,
        )

        out: dict[str, float] = {}
        for entity_id in entity_ids:
            key = str(entity_id)
            series = adjusted.loc[adjusted[id_col] == key, value_col].astype(float).tolist()
            n = float(len(series))
            if n == 0:
                # Fall back to ridge coefficient when the entity appears only
                # on the opposite side of the design, else prior.
                ridge_val = rating_map.get(key)
                if ridge_val is None:
                    out[key] = float("nan") if self.spec.null_policy != "forbid" else priors[key]
                elif card.form == "std":
                    out[key] = bayesian_shrink(
                        float(ridge_val),
                        priors.get(key, league_mean),
                        n=0.0,
                        k=self.config.shrinkage_k,
                    )
                else:
                    out[key] = float("nan")
                continue

            season_mean = float(sum(series) / n)
            if card.form == "std":
                out[key] = bayesian_shrink(
                    season_mean,
                    priors.get(key, league_mean),
                    n=n,
                    k=self.config.shrinkage_k,
                )
            elif card.form == "ewma":
                out[key] = ewma_final(series, half_life)
            else:  # l3d
                out[key] = last_n_delta(series, n=3, season_mean=season_mean)
        return out

    def _empty_frame(self, entity_ids: Sequence[Any], as_of: datetime) -> pd.DataFrame:
        rows = [{ENTITY_COL: eid, AS_OF_COL: as_of, VALUE_COL: float("nan")} for eid in entity_ids]
        frame = pd.DataFrame(rows)
        if self.spec.null_policy == "indicator":
            frame["is_missing"] = True
        return frame


def efficiency_config_from_data(data_cfg: Any) -> EfficiencyConfig:
    """Build :class:`EfficiencyConfig` from a ``DataConfig``-like object."""
    return EfficiencyConfig(
        ridge_lambda=float(getattr(data_cfg, "ridge_lambda_efficiency", DEFAULT_RIDGE_LAMBDA)),
        shrinkage_k=float(getattr(data_cfg, "shrinkage_k_efficiency", DEFAULT_SHRINKAGE_K)),
        ewma_half_life_efficiency=float(
            getattr(data_cfg, "ewma_half_life_efficiency", DEFAULT_EWMA_HALF_LIFE)
        ),
        ewma_half_life_explosiveness=float(
            getattr(
                data_cfg,
                "ewma_half_life_explosiveness",
                DEFAULT_EWMA_HALF_LIFE_EXPLOSIVENESS,
            )
        ),
    )


def season_end_adjusted_epa(
    observations: pd.DataFrame,
    *,
    config: EfficiencyConfig | None = None,
    y_col: str = "epa_per_play",
) -> pd.DataFrame:
    """Fit season-to-date ridge on ``observations`` and return off/def EPA table.

    Used by the Task 10 acceptance dump (top-15 lists). Columns:
    ``entity_id``, ``off_epa``, ``def_epa``. Excludes :data:`FCS_TIER_ENTITY`.
    """
    cfg = config or EfficiencyConfig()
    work = observations.dropna(subset=[y_col, "offense_id", "defense_id", "is_home"])
    ridge = ridge_opponent_adjust(
        work.rename(columns={y_col: "y"}),
        y_col="y",
        ridge_lambda=cfg.ridge_lambda,
        fbs_team_ids=None,
    )
    entities = [e for e in ridge.entities if e != FCS_TIER_ENTITY]
    rows = [
        {
            "entity_id": e,
            "off_epa": ridge.off_ratings.get(e, float("nan")),
            "def_epa": ridge.def_ratings.get(e, float("nan")),
        }
        for e in entities
    ]
    return pd.DataFrame(rows)
