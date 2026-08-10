"""Tempo / possession feature builders (DESIGN §4.5 / §15 item 11).

Team-level pace (adjusted plays/game, situation-neutral seconds/play, run/pass
rate over expectation) plus the matchup-level expected-possessions regression
artifact used for totals.

Point-in-time: builders only use ``history`` rows with ``event_time < as_of``
(via :meth:`FeatureBuilder.filter_event_time`).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Literal

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
from sklearn.linear_model import LinearRegression  # type: ignore[import-untyped]

from ncaa_quant.features.builder import (
    AS_OF_COL,
    ENTITY_COL,
    VALUE_COL,
    FeatureBuilder,
    FeatureBuildError,
)
from ncaa_quant.features.builders.efficiency import (
    bayesian_shrink,
    ewma_final,
    fbs_team_id_set,
    game_adjusted_values,
    last_n_delta,
    pool_entity_id,
    resolve_priors,
    ridge_opponent_adjust,
)
from ncaa_quant.features.epa import filter_garbage_time
from ncaa_quant.features.registry import FeatureSpec

# ---------------------------------------------------------------------------
# Constants / exclusion rules (situation-neutral seconds/play)
# ---------------------------------------------------------------------------

# Documented exclusion rules for situation-neutral seconds/play (Task 11):
# 1. Garbage time — same WP / Connelly filter as efficiency (``filter_garbage_time``).
# 2. End-of-half — period ∈ {2, 4} with clock ≤ ``END_OF_HALF_CLOCK_S`` remaining.
# 3. Kneel / spike — play_type in :data:`KNEEL_SPIKE_TYPES` (or label contains kneel/spike).
# 4. Clear hurry-up — inter-snap clock elapsed < ``HURRY_UP_MAX_ELAPSED_S``.
# 5. Non-snaps — special teams and penalties excluded (rush/pass only).

END_OF_HALF_CLOCK_S: Final[int] = 120
HURRY_UP_MAX_ELAPSED_S: Final[float] = 12.0
PERIOD_SECONDS: Final[int] = 900

KNEEL_SPIKE_TYPES: Final[frozenset[str]] = frozenset(
    {
        "Kneel",
        "Spike",
        "QB Kneel",
        "Quarterback Kneel",
        "Kneel Down",
    }
)

DEFAULT_EWMA_HALF_LIFE_TEMPO: Final[float] = 10.0
DEFAULT_SHRINKAGE_K_TEMPO: Final[float] = 8.0
DEFAULT_RIDGE_LAMBDA_TEMPO: Final[float] = 5.0

# Empirical pass-rate bins (down, distance, score, clock).
_DIST_BINS: Final[tuple[int, ...]] = (0, 3, 6, 10, 100)
_MARGIN_BINS: Final[tuple[int, ...]] = (-100, -14, -7, -3, 3, 7, 14, 100)
_CLOCK_BINS: Final[tuple[int, ...]] = (0, 120, 300, 600, 901)

TempoMetric = Literal["plays_per_game", "sec_per_play", "pass_rate_oe", "rush_rate_oe"]
TempoForm = Literal["std", "ewma", "l3d"]

_TEMPO_FEATURE_RE = re.compile(
    r"^adj_(?P<metric>plays_per_game|sec_per_play|pass_rate_oe|rush_rate_oe)"
    r"_(?P<form>std|ewma|l3d)$"
)

_EXPECTED_POSSESSIONS_NAME: Final[str] = "expected_possessions"
EXPECTED_POSSESSIONS_FEATURE_NAMES: Final[tuple[str, ...]] = (
    "home_pace",
    "away_pace",
    "home_pass_rate",
    "away_pass_rate",
)
_EXP_POS_FEATURES: Final[tuple[str, ...]] = EXPECTED_POSSESSIONS_FEATURE_NAMES


@dataclass(frozen=True, slots=True)
class TempoConfig:
    """Tunable knobs for tempo builders (DESIGN §4.4 tempo half-life)."""

    ridge_lambda: float = DEFAULT_RIDGE_LAMBDA_TEMPO
    shrinkage_k: float = DEFAULT_SHRINKAGE_K_TEMPO
    ewma_half_life_tempo: float = DEFAULT_EWMA_HALF_LIFE_TEMPO


@dataclass(frozen=True, slots=True)
class TempoFeatureCard:
    """Parsed ``adj_{metric}_{form}`` identity."""

    metric: TempoMetric
    form: TempoForm


@dataclass(frozen=True, slots=True)
class ExpectedPossessionsArtifact:
    """Fitted expected-possessions regression (DESIGN §4.5).

    Predicts total game possessions from both teams' season-to-date pace
    (plays/game) and pass rates. Serialized as JSON for PIT application.
    """

    intercept: float
    coefficients: tuple[float, ...]
    feature_names: tuple[str, ...]
    train_seasons: tuple[int, ...]
    oos_mae: float | None = None
    n_train: int = 0
    target_mean: float = 0.0

    def predict_row(self, features: Mapping[str, float]) -> float:
        """Predict possessions for one matchup feature map."""
        total = float(self.intercept)
        for name, coef in zip(self.feature_names, self.coefficients, strict=True):
            total += float(coef) * float(features[name])
        return total

    def predict_frame(self, frame: pd.DataFrame) -> np.ndarray:
        """Vectorized predictions; ``frame`` must contain ``feature_names``."""
        x = frame.loc[:, list(self.feature_names)].to_numpy(dtype=np.float64)
        coef = np.asarray(self.coefficients, dtype=np.float64)
        return np.asarray(self.intercept + x @ coef, dtype=np.float64)

    def to_json(self) -> dict[str, Any]:
        """JSON-serializable dict."""
        return asdict(self)

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> ExpectedPossessionsArtifact:
        """Load from :meth:`to_json` payload."""
        return cls(
            intercept=float(payload["intercept"]),
            coefficients=tuple(float(c) for c in payload["coefficients"]),
            feature_names=tuple(str(n) for n in payload["feature_names"]),
            train_seasons=tuple(int(s) for s in payload.get("train_seasons", ())),
            oos_mae=(None if payload.get("oos_mae") is None else float(payload["oos_mae"])),
            n_train=int(payload.get("n_train", 0)),
            target_mean=float(payload.get("target_mean", 0.0)),
        )


# ---------------------------------------------------------------------------
# Situation-neutral helpers
# ---------------------------------------------------------------------------


def _as_int(value: object) -> int | None:
    """Best-effort int coerce; None on failure / NA."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def is_kneel_or_spike(play_type: object) -> bool:
    """True for kneel / spike play types (clock-killing / spike situations)."""
    if play_type is None or (isinstance(play_type, float) and math.isnan(play_type)):
        return False
    label = str(play_type)
    if label in KNEEL_SPIKE_TYPES:
        return True
    lowered = label.casefold()
    return "kneel" in lowered or "spike" in lowered


def is_end_of_half(period: object, clock: object) -> bool:
    """True when play is in the final ``END_OF_HALF_CLOCK_S`` of a half."""
    p = _as_int(period)
    c = _as_int(clock)
    if p is None or c is None:
        return False
    return p in (2, 4) and c <= END_OF_HALF_CLOCK_S


def inter_snap_elapsed_seconds(prev_period: int, prev_clock: int, period: int, clock: int) -> float:
    """Seconds of game clock between two consecutive snaps.

    Same period: ``prev_clock - clock``. Period advance assumes a
    ``PERIOD_SECONDS`` quarter (college). Returns NaN when clocks are missing
    or the delta is non-positive / implausible.
    """
    if period == prev_period:
        elapsed = float(prev_clock - clock)
    elif period == prev_period + 1:
        elapsed = float(prev_clock) + float(PERIOD_SECONDS - clock)
    else:
        return float("nan")
    if elapsed <= 0 or elapsed > PERIOD_SECONDS + 60:
        return float("nan")
    return elapsed


def annotate_tempo_exclusions(plays: pd.DataFrame) -> pd.DataFrame:
    """Add exclusion / elapsed columns used by situation-neutral seconds/play.

    Adds ``is_kneel_spike``, ``is_end_of_half``, ``elapsed_s``, ``is_hurry_up``,
    and ``neutral_eligible`` (rush/pass, not garbage, not kneel/spike, not
    end-of-half, not hurry-up).
    """
    if plays.empty:
        out = plays.copy()
        for col in (
            "is_kneel_spike",
            "is_end_of_half",
            "elapsed_s",
            "is_hurry_up",
            "neutral_eligible",
        ):
            out[col] = pd.Series(dtype="boolean")
        return out

    work = plays.copy()
    if "garbage_time" not in work.columns:
        work = filter_garbage_time(work)

    work["is_kneel_spike"] = work["play_type"].map(is_kneel_or_spike)
    work["is_end_of_half"] = [
        is_end_of_half(p, c) for p, c in zip(work["period"], work["clock"], strict=True)
    ]

    sort_cols = ["game_id"]
    if "drive_id" in work.columns:
        sort_cols.append("drive_id")
    sort_cols.extend(["period"])
    # Higher clock = earlier in period; play_id as tiebreak when present.
    work = work.sort_values(
        sort_cols + (["play_id"] if "play_id" in work.columns else []),
        ascending=[True] * len(sort_cols) + ([True] if "play_id" in work.columns else []),
        kind="mergesort",
    ).reset_index(drop=True)

    elapsed = np.full(len(work), np.nan, dtype=np.float64)
    hurry = np.zeros(len(work), dtype=bool)
    group_keys = ["game_id", "drive_id"] if "drive_id" in work.columns else ["game_id"]
    for _, idx in work.groupby(group_keys, sort=False).groups.items():
        positions = list(idx)
        for j, pos in enumerate(positions):
            if j == 0:
                continue
            prev = positions[j - 1]
            try:
                pp = int(work.at[prev, "period"])
                pc = int(work.at[prev, "clock"])
                p = int(work.at[pos, "period"])
                c = int(work.at[pos, "clock"])
            except (TypeError, ValueError):
                continue
            e = inter_snap_elapsed_seconds(pp, pc, p, c)
            elapsed[pos] = e
            if not math.isnan(e) and e < HURRY_UP_MAX_ELAPSED_S:
                hurry[pos] = True

    work["elapsed_s"] = elapsed
    work["is_hurry_up"] = hurry

    rush_pass = (
        work["is_rush"].astype(bool) | work["is_pass"].astype(bool)
        if "is_rush" in work.columns and "is_pass" in work.columns
        else pd.Series(True, index=work.index)
    )
    not_garbage = ~work["garbage_time"].astype(bool)
    work["neutral_eligible"] = (
        rush_pass
        & not_garbage
        & ~work["is_kneel_spike"].astype(bool)
        & ~work["is_end_of_half"].astype(bool)
        & ~work["is_hurry_up"].astype(bool)
        & work["elapsed_s"].notna()
    )
    return work


# ---------------------------------------------------------------------------
# Pass-rate over expectation
# ---------------------------------------------------------------------------


def _bin_distance(distance: object) -> int:
    d = _as_int(distance)
    if d is None:
        return -1
    for i in range(len(_DIST_BINS) - 1):
        if _DIST_BINS[i] < d <= _DIST_BINS[i + 1]:
            return i
    return -1


def _bin_margin(margin: object) -> int:
    m = _as_int(margin)
    if m is None:
        return -1
    for i in range(len(_MARGIN_BINS) - 1):
        if _MARGIN_BINS[i] < m <= _MARGIN_BINS[i + 1]:
            return i
    return -1


def _bin_clock(clock: object) -> int:
    c = _as_int(clock)
    if c is None:
        return -1
    for i in range(len(_CLOCK_BINS) - 1):
        if _CLOCK_BINS[i] <= c < _CLOCK_BINS[i + 1]:
            return i
    return -1


def situation_key(
    down: object,
    distance: object,
    score_margin: object,
    clock: object,
    period: object,
) -> tuple[int, int, int, int, int]:
    """Discrete situation key for empirical pass-rate expectation."""
    dwn = _as_int(down)
    per = _as_int(period)
    if dwn is None or per is None:
        return (-1, -1, -1, -1, -1)
    return (dwn, _bin_distance(distance), _bin_margin(score_margin), _bin_clock(clock), per)


def fit_pass_rate_expectation(plays: pd.DataFrame) -> dict[tuple[int, ...], float]:
    """Empirical P(pass | down, distance-bin, margin-bin, clock-bin, period)."""
    if plays.empty:
        return {}
    work = plays.copy()
    if "is_pass" not in work.columns or "is_rush" not in work.columns:
        return {}
    snaps = work.loc[work["is_rush"].astype(bool) | work["is_pass"].astype(bool)].copy()
    if snaps.empty:
        return {}
    if "score_margin" not in snaps.columns:
        snaps["score_margin"] = 0
    keys = [
        situation_key(r.down, r.distance, r.score_margin, r.clock, r.period)
        for r in snaps.itertuples(index=False)
    ]
    snaps = snaps.assign(_key=keys, _pass=snaps["is_pass"].astype(bool).astype(float))
    snaps = snaps.loc[snaps["_key"].map(lambda k: k[0] >= 1)]
    if snaps.empty:
        return {}
    rates = snaps.groupby("_key", sort=False)["_pass"].mean()
    return {tuple(k): float(v) for k, v in rates.items()}


def pass_rate_over_expectation(
    plays: pd.DataFrame,
    expectation: Mapping[tuple[int, ...], float],
    *,
    league_mean: float = 0.45,
) -> pd.DataFrame:
    """Per-(game, offense) mean (is_pass − E[pass|situation])."""
    if plays.empty:
        return pd.DataFrame(columns=["game_id", "offense_id", "pass_rate_oe", "pass_rate"])

    work = plays.copy()
    snaps = work.loc[work["is_rush"].astype(bool) | work["is_pass"].astype(bool)].copy()
    if snaps.empty:
        return pd.DataFrame(columns=["game_id", "offense_id", "pass_rate_oe", "pass_rate"])
    if "score_margin" not in snaps.columns:
        snaps["score_margin"] = 0

    expected = []
    for r in snaps.itertuples(index=False):
        key = situation_key(r.down, r.distance, r.score_margin, r.clock, r.period)
        expected.append(float(expectation.get(tuple(key), league_mean)))
    snaps["_exp"] = expected
    snaps["_pass"] = snaps["is_pass"].astype(bool).astype(float)
    snaps["_oe"] = snaps["_pass"] - snaps["_exp"]

    grouped = snaps.groupby(["game_id", "offense_id"], dropna=False, sort=False)
    out = grouped.agg(pass_rate_oe=("_oe", "mean"), pass_rate=("_pass", "mean")).reset_index()
    return out


# ---------------------------------------------------------------------------
# Observation construction
# ---------------------------------------------------------------------------


def _game_event_time(games: pd.DataFrame) -> pd.DataFrame:
    out = games.copy()
    if "event_time" not in out.columns and "start_date" in out.columns:
        out["event_time"] = pd.to_datetime(out["start_date"], utc=True)
    else:
        out["event_time"] = pd.to_datetime(out["event_time"], utc=True)
    cols = ["game_id", "home_team_id", "neutral_site", "event_time"]
    for optional in ("season", "week", "away_team_id"):
        if optional in out.columns:
            cols.append(optional)
    return out[cols].drop_duplicates(subset=["game_id"])


def build_tempo_observations(
    plays: pd.DataFrame,
    games: pd.DataFrame,
    teams: pd.DataFrame,
    *,
    drop_garbage: bool = True,
    pass_expectation: Mapping[tuple[int, ...], float] | None = None,
) -> pd.DataFrame:
    """Per-(game, offense) tempo rows for ridge / OE / seconds-per-play.

    Columns include ``plays_per_game`` (offensive rush+pass snaps),
    ``sec_per_play`` (situation-neutral mean elapsed), ``pass_rate``,
    ``pass_rate_oe``, ``rush_rate_oe`` (= −pass_rate_oe), plus ridge keys.
    """
    if plays.empty:
        return pd.DataFrame()

    work = plays.copy()
    if drop_garbage:
        work = filter_garbage_time(work)

    annotated = annotate_tempo_exclusions(work)
    rush_pass = annotated.loc[
        annotated["is_rush"].astype(bool) | annotated["is_pass"].astype(bool)
    ].copy()
    if rush_pass.empty:
        return pd.DataFrame()

    # Plays per game (offense snaps).
    n_plays = (
        rush_pass.groupby(["game_id", "offense_id", "defense_id"], dropna=False, sort=False)
        .size()
        .astype(float)
        .rename("plays_per_game")
        .reset_index()
    )

    # Situation-neutral seconds / play.
    neutral = annotated.loc[annotated["neutral_eligible"].astype(bool)]
    if neutral.empty:
        spp = pd.DataFrame(columns=["game_id", "offense_id", "sec_per_play"])
    else:
        spp = (
            neutral.groupby(["game_id", "offense_id"], dropna=False, sort=False)["elapsed_s"]
            .mean()
            .rename("sec_per_play")
            .reset_index()
        )

    exp = pass_expectation if pass_expectation is not None else fit_pass_rate_expectation(rush_pass)
    oe = pass_rate_over_expectation(rush_pass, exp)

    agg = n_plays.merge(spp, on=["game_id", "offense_id"], how="left")
    agg = agg.merge(oe, on=["game_id", "offense_id"], how="left")
    agg["rush_rate_oe"] = -agg["pass_rate_oe"]

    game_ctx = _game_event_time(games)
    agg = agg.merge(game_ctx, on="game_id", how="left")
    agg["is_home"] = (agg["offense_id"] == agg["home_team_id"]) & (
        ~agg["neutral_site"].fillna(False)
    )

    fbs_ids = fbs_team_id_set(teams)
    agg["offense_id"] = [pool_entity_id(v, fbs_team_ids=fbs_ids) for v in agg["offense_id"]]
    agg["defense_id"] = [pool_entity_id(v, fbs_team_ids=fbs_ids) for v in agg["defense_id"]]
    return agg.reset_index(drop=True)


def game_possessions(drives: pd.DataFrame) -> pd.DataFrame:
    """Total possessions (drive count) per ``game_id``."""
    if drives.empty:
        return pd.DataFrame(columns=["game_id", "possessions"])
    out = (
        drives.dropna(subset=["game_id"])
        .groupby("game_id", sort=False)
        .size()
        .astype(float)
        .rename("possessions")
        .reset_index()
    )
    return out


def team_season_to_date_rates(
    tempo_obs: pd.DataFrame,
    *,
    as_of: datetime | None = None,
) -> pd.DataFrame:
    """Season-to-date mean ``plays_per_game`` and ``pass_rate`` per team-game.

    For each (team, game) row, aggregates prior games for that offense with
    ``event_time <`` the game's event_time (and ``< as_of`` when given).
    """
    if tempo_obs.empty:
        return pd.DataFrame(
            columns=["game_id", "team_id", "event_time", "pace", "pass_rate", "n_prior"]
        )

    work = tempo_obs.dropna(subset=["offense_id", "event_time", "game_id"]).copy()
    work["event_time"] = pd.to_datetime(work["event_time"], utc=True)
    if as_of is not None:
        bound = pd.Timestamp(as_of)
        if bound.tzinfo is None:
            msg = "as_of must be tz-aware"
            raise ValueError(msg)
        work = work.loc[work["event_time"] < bound]
    work = work.sort_values("event_time", kind="mergesort")

    rows: list[dict[str, Any]] = []
    for team, grp in work.groupby("offense_id", sort=False):
        g = grp.reset_index(drop=True)
        pace_vals: list[float] = []
        pass_vals: list[float] = []
        for i in range(len(g)):
            n_prior = len(pace_vals)
            pace = float(sum(pace_vals) / n_prior) if n_prior else float("nan")
            pr = float(sum(pass_vals) / n_prior) if n_prior else float("nan")
            rows.append(
                {
                    "game_id": g.at[i, "game_id"],
                    "team_id": str(team),
                    "event_time": g.at[i, "event_time"],
                    "pace": pace,
                    "pass_rate": pr,
                    "n_prior": float(n_prior),
                }
            )
            if pd.notna(g.at[i, "plays_per_game"]):
                pace_vals.append(float(g.at[i, "plays_per_game"]))
            if "pass_rate" in g.columns and pd.notna(g.at[i, "pass_rate"]):
                pass_vals.append(float(g.at[i, "pass_rate"]))
            elif "pass_rate_oe" not in g.columns:
                pass
    return pd.DataFrame(rows)


def build_expected_possessions_training_frame(
    tempo_obs: pd.DataFrame,
    games: pd.DataFrame,
    drives: pd.DataFrame,
) -> pd.DataFrame:
    """Rows of (home/away pace & pass rate → possessions) for regression fit.

    Uses season-to-date rates *before* each game (strict PIT). Drops rows with
    missing features or targets.
    """
    if tempo_obs.empty or games.empty or drives.empty:
        return pd.DataFrame()

    rates = team_season_to_date_rates(tempo_obs)
    poss = game_possessions(drives)
    g = _game_event_time(games)
    if "away_team_id" not in g.columns:
        msg = "games frame requires away_team_id for expected-possessions training"
        raise ValueError(msg)

    g = g.merge(poss, on="game_id", how="inner")
    home_rates = rates.rename(
        columns={
            "team_id": "home_team_id_str",
            "pace": "home_pace",
            "pass_rate": "home_pass_rate",
            "n_prior": "home_n_prior",
        }
    )
    away_rates = rates.rename(
        columns={
            "team_id": "away_team_id_str",
            "pace": "away_pace",
            "pass_rate": "away_pass_rate",
            "n_prior": "away_n_prior",
        }
    )
    g["home_team_id_str"] = g["home_team_id"].map(str)
    g["away_team_id_str"] = g["away_team_id"].map(str)
    # Pooling: FBS ids stringify; training uses pooled ids from tempo_obs.
    merged = g.merge(
        home_rates[["game_id", "home_team_id_str", "home_pace", "home_pass_rate", "home_n_prior"]],
        on=["game_id", "home_team_id_str"],
        how="left",
    )
    merged = merged.merge(
        away_rates[["game_id", "away_team_id_str", "away_pace", "away_pass_rate", "away_n_prior"]],
        on=["game_id", "away_team_id_str"],
        how="left",
    )
    keep = merged.dropna(
        subset=["home_pace", "away_pace", "home_pass_rate", "away_pass_rate", "possessions"]
    )
    # Require at least one prior game each side.
    keep = keep.loc[(keep["home_n_prior"] >= 1) & (keep["away_n_prior"] >= 1)]
    return keep.reset_index(drop=True)


def fit_expected_possessions(
    training: pd.DataFrame,
    *,
    train_seasons: Sequence[int] | None = None,
    oos_mae: float | None = None,
) -> ExpectedPossessionsArtifact:
    """Fit linear regression: possessions ~ home/away pace + pass rates."""
    if training.empty:
        msg = "cannot fit expected possessions on empty training frame"
        raise ValueError(msg)
    required = set(_EXP_POS_FEATURES) | {"possessions"}
    missing = required - set(training.columns)
    if missing:
        msg = f"training frame missing columns: {sorted(missing)}"
        raise ValueError(msg)

    x = training.loc[:, list(_EXP_POS_FEATURES)].to_numpy(dtype=np.float64)
    y = training["possessions"].to_numpy(dtype=np.float64)
    model = LinearRegression(fit_intercept=True)
    model.fit(x, y)
    seasons: tuple[int, ...]
    if train_seasons is not None:
        seasons = tuple(int(s) for s in train_seasons)
    elif "season" in training.columns:
        seasons = tuple(sorted({int(s) for s in training["season"].dropna().tolist()}))
    else:
        seasons = ()
    return ExpectedPossessionsArtifact(
        intercept=float(model.intercept_),
        coefficients=tuple(float(c) for c in model.coef_),
        feature_names=_EXP_POS_FEATURES,
        train_seasons=seasons,
        oos_mae=oos_mae,
        n_train=int(len(training)),
        target_mean=float(np.mean(y)),
    )


def expected_possessions_oos_mae(
    frame: pd.DataFrame,
    *,
    train_mask: pd.Series,
    test_mask: pd.Series,
) -> tuple[ExpectedPossessionsArtifact, float]:
    """Fit on ``train_mask`` rows; return artifact + MAE on ``test_mask`` rows."""
    train = frame.loc[train_mask]
    test = frame.loc[test_mask]
    if train.empty or test.empty:
        msg = "train and test splits must be non-empty"
        raise ValueError(msg)
    seasons = (
        tuple(sorted({int(s) for s in train["season"].dropna().tolist()}))
        if "season" in train.columns
        else ()
    )
    artifact = fit_expected_possessions(train, train_seasons=seasons)
    preds = artifact.predict_frame(test)
    mae = float(np.mean(np.abs(preds - test["possessions"].to_numpy(dtype=np.float64))))
    return (
        ExpectedPossessionsArtifact(
            intercept=artifact.intercept,
            coefficients=artifact.coefficients,
            feature_names=artifact.feature_names,
            train_seasons=artifact.train_seasons,
            oos_mae=mae,
            n_train=artifact.n_train,
            target_mean=artifact.target_mean,
        ),
        mae,
    )


def save_expected_possessions_artifact(
    artifact: ExpectedPossessionsArtifact,
    path: Path | str,
) -> Path:
    """Write artifact JSON to ``path``."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(artifact.to_json(), indent=2, sort_keys=True) + "\n"
    out.write_text(payload, encoding="utf-8")
    return out


def load_expected_possessions_artifact(path: Path | str) -> ExpectedPossessionsArtifact:
    """Load artifact JSON from ``path``."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return ExpectedPossessionsArtifact.from_json(payload)


# ---------------------------------------------------------------------------
# Feature builders
# ---------------------------------------------------------------------------


def parse_tempo_feature_name(name: str) -> TempoFeatureCard:
    """Parse ``adj_{metric}_{form}`` registry names."""
    match = _TEMPO_FEATURE_RE.match(name)
    if match is None:
        msg = f"unsupported tempo feature name: {name!r}"
        raise FeatureBuildError(msg)
    return TempoFeatureCard(
        metric=match.group("metric"),  # type: ignore[arg-type]
        form=match.group("form"),  # type: ignore[arg-type]
    )


_METRIC_Y: Final[Mapping[TempoMetric, str]] = {
    "plays_per_game": "plays_per_game",
    "sec_per_play": "sec_per_play",
    "pass_rate_oe": "pass_rate_oe",
    "rush_rate_oe": "rush_rate_oe",
}


class TempoFeatureBuilder(FeatureBuilder):
    """Team-level tempo feature; behavior driven by ``spec.name``.

    ``history`` is the game-level observation frame from
    :func:`build_tempo_observations` (mutable for pit_audit injection).
    """

    def __init__(
        self,
        spec: FeatureSpec,
        history: pd.DataFrame,
        *,
        config: TempoConfig | None = None,
        prior_lookup: Mapping[str, float] | None = None,
    ) -> None:
        super().__init__(spec)
        self.history = history
        self.config = config or TempoConfig()
        self.prior_lookup = prior_lookup
        self.card = parse_tempo_feature_name(spec.name)

    def compute(self, entity_ids: Sequence[Any], as_of: datetime) -> pd.DataFrame:
        eligible = self.filter_event_time(self.history, as_of)
        y_col = _METRIC_Y[self.card.metric]
        if eligible.empty or y_col not in eligible.columns:
            return self._empty_frame(entity_ids, as_of)

        obs = eligible.dropna(subset=[y_col, "offense_id", "defense_id", "is_home"]).copy()
        if "event_time" in obs.columns:
            obs = obs.sort_values("event_time", kind="mergesort")
        if obs.empty:
            return self._empty_frame(entity_ids, as_of)

        # Opponent-adjust plays/game and seconds/play; OE metrics are already
        # situation-residualized — still ridge-adjust for schedule tempo.
        ridge = ridge_opponent_adjust(
            obs.rename(columns={y_col: "y"}),
            y_col="y",
            ridge_lambda=self.config.ridge_lambda,
            fbs_team_ids=None,
        )
        adjusted = game_adjusted_values(obs.rename(columns={y_col: "y"}), ridge, y_col="y")
        values = self._entity_values(adjusted, ridge, entity_ids)
        rows = [
            {
                ENTITY_COL: eid,
                AS_OF_COL: as_of,
                VALUE_COL: values.get(str(eid), float("nan")),
            }
            for eid in entity_ids
        ]
        frame = pd.DataFrame(rows)
        if self.spec.null_policy == "indicator":
            frame["is_missing"] = frame[VALUE_COL].isna()
        return frame

    def _entity_values(
        self,
        adjusted: pd.DataFrame,
        ridge: Any,
        entity_ids: Sequence[Any],
    ) -> dict[str, float]:
        half_life = float(self.config.ewma_half_life_tempo)
        rating_map = ridge.off_ratings
        league_mean = float(sum(rating_map.values()) / len(rating_map)) if rating_map else 0.0
        priors = resolve_priors(
            [str(e) for e in entity_ids],
            league_mean=league_mean,
            prior_lookup=self.prior_lookup,
        )
        out: dict[str, float] = {}
        for entity_id in entity_ids:
            key = str(entity_id)
            series = adjusted.loc[adjusted["offense_id"] == key, "adj_off"].astype(float).tolist()
            n = float(len(series))
            if n == 0:
                out[key] = float("nan")
                continue
            season_mean = float(sum(series) / n)
            if self.card.form == "std":
                out[key] = bayesian_shrink(
                    season_mean,
                    priors.get(key, league_mean),
                    n=n,
                    k=self.config.shrinkage_k,
                )
            elif self.card.form == "ewma":
                out[key] = ewma_final(series, half_life)
            else:
                out[key] = last_n_delta(series, n=3, season_mean=season_mean)
        return out

    def _empty_frame(self, entity_ids: Sequence[Any], as_of: datetime) -> pd.DataFrame:
        rows = [{ENTITY_COL: eid, AS_OF_COL: as_of, VALUE_COL: float("nan")} for eid in entity_ids]
        frame = pd.DataFrame(rows)
        if self.spec.null_policy == "indicator":
            frame["is_missing"] = True
        return frame


class ExpectedPossessionsFeatureBuilder(FeatureBuilder):
    """Matchup-level expected possessions; ``entity_id`` is ``game_id``.

    ``history`` must contain one row per game with columns
    ``game_id``, ``event_time``, and the four pace/pass features (already
    computed PIT by the materializer). The fitted ``artifact`` is applied
    without refitting at build time.
    """

    def __init__(
        self,
        spec: FeatureSpec,
        history: pd.DataFrame,
        *,
        artifact: ExpectedPossessionsArtifact,
    ) -> None:
        super().__init__(spec)
        if spec.name != _EXPECTED_POSSESSIONS_NAME:
            msg = f"ExpectedPossessionsFeatureBuilder only serves {_EXPECTED_POSSESSIONS_NAME!r}"
            raise FeatureBuildError(msg)
        self.history = history
        self.artifact = artifact

    def compute(self, entity_ids: Sequence[Any], as_of: datetime) -> pd.DataFrame:
        eligible = self.filter_event_time(self.history, as_of)
        by_game = {
            int(r.game_id) if not isinstance(r.game_id, str) else r.game_id: r
            for r in eligible.itertuples(index=False)
        }
        rows: list[dict[str, Any]] = []
        for eid in entity_ids:
            key = int(eid) if not isinstance(eid, str) else eid
            # Also try string/int cross.
            row = by_game.get(key)
            if row is None:
                try:
                    row = by_game.get(int(eid))
                except (TypeError, ValueError):
                    row = None
            if row is None:
                value = float("nan")
            else:
                feats = {name: float(getattr(row, name)) for name in self.artifact.feature_names}
                if any(math.isnan(v) for v in feats.values()):
                    value = float("nan")
                else:
                    value = float(self.artifact.predict_row(feats))
            rows.append({ENTITY_COL: eid, AS_OF_COL: as_of, VALUE_COL: value})
        frame = pd.DataFrame(rows)
        if self.spec.null_policy == "indicator":
            frame["is_missing"] = frame[VALUE_COL].isna()
        return frame


def tempo_config_from_data(data_cfg: Any) -> TempoConfig:
    """Build :class:`TempoConfig` from a ``DataConfig``-like object."""
    return TempoConfig(
        ridge_lambda=float(
            getattr(data_cfg, "ridge_lambda_efficiency", DEFAULT_RIDGE_LAMBDA_TEMPO)
        ),
        shrinkage_k=float(getattr(data_cfg, "shrinkage_k_efficiency", DEFAULT_SHRINKAGE_K_TEMPO)),
        ewma_half_life_tempo=float(
            getattr(data_cfg, "ewma_half_life_tempo", DEFAULT_EWMA_HALF_LIFE_TEMPO)
        ),
    )
