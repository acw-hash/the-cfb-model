"""EPA/WP normalization, garbage-time filtering, and efficiency aggregates.

DESIGN §3.6 / §4.2 / Task 8. Uses CFBD-shipped EPA (``ppa``) for v1. Primary
garbage-time rule is WP-threshold; Connelly score-margin-by-quarter is the
fallback when WP is missing.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, runtime_checkable

import pandas as pd  # type: ignore[import-untyped]
import pandera.pandas as pa
from pandera.typing import Series

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WP_GT_LOW: Final[float] = 0.02
WP_GT_HIGH: Final[float] = 0.98

# Bill Connelly / Football Outsiders published thresholds (SB Nation):
# garbage when scoring margin is *greater than* these values by quarter.
CONNELLY_MARGIN_BY_PERIOD: Final[Mapping[int, int]] = {
    1: 28,
    2: 24,
    3: 21,
    4: 16,
}

# Success-rate yardage fractions of distance needed (Football Outsiders / Connelly).
SUCCESS_FRAC_BY_DOWN: Final[Mapping[int, float]] = {
    1: 0.50,
    2: 0.70,
    3: 1.00,
    4: 1.00,
}

_RUSH_TYPES: Final[frozenset[str]] = frozenset(
    {
        "Rush",
        "Rushing Touchdown",
    }
)

_PASS_TYPES: Final[frozenset[str]] = frozenset(
    {
        "Pass Reception",
        "Pass Incompletion",
        "Passing Touchdown",
        "Sack",
        "Interception",
        "Interception Return Touchdown",
        "Pass Interception Return",
    }
)

_SPECIAL_TEAMS_TYPES: Final[frozenset[str]] = frozenset(
    {
        "Kickoff",
        "Kickoff Return (Offense)",
        "Kickoff Return Touchdown",
        "Punt",
        "Punt Return Touchdown",
        "Blocked Punt",
        "Blocked Punt Touchdown",
        "Field Goal Good",
        "Field Goal Missed",
        "Blocked Field Goal",
        "Blocked Field Goal Touchdown",
        "Missed Field Goal Return",
        "Missed Field Goal Return Touchdown",
        "Safety",
    }
)

_PENALTY_TYPES: Final[frozenset[str]] = frozenset({"Penalty"})

# Defensive havoc events (Connelly: TFL/sack + FF + INT). Approximated from
# CFBD play_type labels — no separate TFL type is emitted for non-sack TFLs.
_HAVOC_TYPES: Final[frozenset[str]] = frozenset(
    {
        "Sack",
        "Interception",
        "Interception Return Touchdown",
        "Pass Interception Return",
        "Fumble Recovery (Opponent)",
        "Fumble Return Touchdown",
    }
)

_OUTPUT_COLUMNS: Final[tuple[str, ...]] = (
    "game_key",
    "play_id",
    "game_id",
    "offense_team",
    "defense_team",
    "play_type",
    "down",
    "distance",
    "yardline",
    "period",
    "clock",
    "yards_gained",
    "epa",
    "wp_before",
    "wp_after",
    "is_rush",
    "is_pass",
    "is_special_teams",
    "is_penalty",
    "is_havoc",
    "is_success",
    "score_margin",
    "garbage_time",
    "gt_rule",
    "gt_fallback_used",
)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class EpaPlaysSchema(pa.DataFrameModel):
    """Normalized per-play EPA table (Task 8 deliverable columns + helpers)."""

    game_key: Series[str]
    play_id: Series[pa.Int64] = pa.Field(nullable=True)
    game_id: Series[pa.Int64] = pa.Field(ge=0, nullable=True)
    offense_team: Series[str] = pa.Field(nullable=True)
    defense_team: Series[str] = pa.Field(nullable=True)
    play_type: Series[str] = pa.Field(nullable=True)
    down: Series[pa.Int32] = pa.Field(ge=0, le=4, nullable=True)
    distance: Series[pa.Int32] = pa.Field(nullable=True)
    yardline: Series[pa.Int32] = pa.Field(ge=0, le=100, nullable=True)
    period: Series[pa.Int32] = pa.Field(ge=1, le=8, nullable=True)
    clock: Series[pa.Int32] = pa.Field(ge=0, le=900, nullable=True)
    yards_gained: Series[pa.Int32] = pa.Field(nullable=True)
    epa: Series[pa.Float64] = pa.Field(nullable=True)
    wp_before: Series[pa.Float64] = pa.Field(ge=0.0, le=1.0, nullable=True)
    wp_after: Series[pa.Float64] = pa.Field(ge=0.0, le=1.0, nullable=True)
    is_rush: Series[pa.Bool]
    is_pass: Series[pa.Bool]
    is_special_teams: Series[pa.Bool]
    is_penalty: Series[pa.Bool]
    is_havoc: Series[pa.Bool]
    is_success: Series[pa.Bool] = pa.Field(nullable=True)
    score_margin: Series[pa.Int32] = pa.Field(nullable=True)
    garbage_time: Series[pa.Bool]
    gt_rule: Series[str] = pa.Field(isin=["wp", "connelly_fallback", "none"])
    gt_fallback_used: Series[pa.Bool]

    class Config:
        strict = True
        coerce = True


# ---------------------------------------------------------------------------
# Play-type / success helpers
# ---------------------------------------------------------------------------


def classify_play_type(play_type: str | None) -> tuple[bool, bool, bool, bool]:
    """Return ``(is_rush, is_pass, is_special_teams, is_penalty)`` for a CFBD type."""
    if play_type is None or (isinstance(play_type, float) and pd.isna(play_type)):
        return False, False, False, False
    label = str(play_type)
    is_penalty = label in _PENALTY_TYPES
    is_rush = label in _RUSH_TYPES
    is_pass = label in _PASS_TYPES
    is_st = label in _SPECIAL_TEAMS_TYPES
    return is_rush, is_pass, is_st, is_penalty


def is_havoc_play(play_type: str | None) -> bool:
    """True when the play type is a defensive havoc event (sack / INT / FF recovery)."""
    if play_type is None or (isinstance(play_type, float) and pd.isna(play_type)):
        return False
    return str(play_type) in _HAVOC_TYPES


def is_successful_play(
    down: int | None,
    distance: int | None,
    yards_gained: int | None,
) -> bool | None:
    """Standard FO/Connelly success: 50%/70%/100% of needed yards on 1st/2nd/3rd–4th.

    Returns ``None`` when down/distance/yards are missing or down is not 1–4
    (e.g. kickoffs with down 0).
    """
    if down is None or distance is None or yards_gained is None:
        return None
    if isinstance(down, float) and pd.isna(down):
        return None
    if isinstance(distance, float) and pd.isna(distance):
        return None
    if isinstance(yards_gained, float) and pd.isna(yards_gained):
        return None
    d = int(down)
    if d not in SUCCESS_FRAC_BY_DOWN:
        return None
    needed = float(distance) * SUCCESS_FRAC_BY_DOWN[d]
    return float(yards_gained) >= needed


def connelly_garbage_time(period: int | None, score_margin: int | None) -> bool:
    """Connelly score-margin-by-quarter garbage-time rule.

    Periods ``>= 5`` (OT) use the Q4 threshold. Returns False when inputs are
    missing (cannot classify).
    """
    if period is None or score_margin is None:
        return False
    if isinstance(period, float) and pd.isna(period):
        return False
    if isinstance(score_margin, float) and pd.isna(score_margin):
        return False
    q = int(period)
    threshold = CONNELLY_MARGIN_BY_PERIOD.get(q, CONNELLY_MARGIN_BY_PERIOD[4])
    return abs(int(score_margin)) > threshold


def wp_garbage_time(wp_before: float | None) -> bool:
    """Primary §4.2 rule: WP outside ``(0.02, 0.98)``."""
    if wp_before is None:
        return False
    if isinstance(wp_before, float) and pd.isna(wp_before):
        return False
    wp = float(wp_before)
    return wp > WP_GT_HIGH or wp < WP_GT_LOW


# ---------------------------------------------------------------------------
# Garbage-time filter
# ---------------------------------------------------------------------------


def apply_garbage_time(
    plays: pd.DataFrame,
    *,
    wp_col: str = "wp_before",
    period_col: str = "period",
    margin_col: str = "score_margin",
) -> pd.DataFrame:
    """Attach ``garbage_time``, ``gt_rule``, and ``gt_fallback_used`` columns.

    Primary rule uses ``wp_col`` when non-null. When WP is null, falls back to
    Connelly score-margin-by-quarter and sets ``gt_fallback_used=True``.
    """
    out = plays.copy()
    n = len(out)
    if n == 0:
        out["garbage_time"] = pd.Series(dtype=bool)
        out["gt_rule"] = pd.Series(dtype="string")
        out["gt_fallback_used"] = pd.Series(dtype=bool)
        return out

    wp = (
        out[wp_col] if wp_col in out.columns else pd.Series(pd.NA, index=out.index, dtype="Float64")
    )
    period = (
        out[period_col]
        if period_col in out.columns
        else pd.Series(pd.NA, index=out.index, dtype="Int64")
    )
    margin = (
        out[margin_col]
        if margin_col in out.columns
        else pd.Series(pd.NA, index=out.index, dtype="Int64")
    )

    wp_present = wp.notna()
    wp_gt = pd.Series(False, index=out.index, dtype=bool)
    if bool(wp_present.any()):
        wp_vals = wp.loc[wp_present].astype(float)
        wp_gt.loc[wp_present] = (wp_vals > WP_GT_HIGH) | (wp_vals < WP_GT_LOW)

    # Connelly fallback for rows without WP
    period_num = pd.to_numeric(period, errors="coerce")
    margin_num = pd.to_numeric(margin, errors="coerce")
    q = period_num.clip(upper=4).fillna(4).astype(int)
    thresholds = q.map(lambda p: CONNELLY_MARGIN_BY_PERIOD.get(int(p), 16))
    connelly_ready = period_num.notna() & margin_num.notna()
    connelly_gt = pd.Series(False, index=out.index, dtype=bool)
    connelly_gt.loc[connelly_ready] = (
        margin_num.loc[connelly_ready].abs() > thresholds.loc[connelly_ready]
    )

    fallback = ~wp_present
    garbage = wp_gt.where(wp_present, connelly_gt)
    rules = pd.Series("none", index=out.index, dtype="string")
    rules.loc[wp_present] = "wp"
    rules.loc[fallback] = "connelly_fallback"

    out["garbage_time"] = garbage.astype(bool)
    out["gt_rule"] = rules
    out["gt_fallback_used"] = fallback.astype(bool)
    return out


def filter_garbage_time(plays: pd.DataFrame) -> pd.DataFrame:
    """Return non-garbage rows. Ensures GT flags exist (via :func:`apply_garbage_time`)."""
    flagged = apply_garbage_time(plays) if "garbage_time" not in plays.columns else plays
    return flagged.loc[~flagged["garbage_time"].astype(bool)].copy()


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def _as_int(value: object) -> int | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        try:
            return int(cleaned)
        except ValueError:
            return None
    return None


def _as_float(value: object) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _down(value: object) -> int | None:
    """CFBD occasionally emits down > 4; coerce to null (same as Task 5 ingest)."""
    down = _as_int(value)
    if down is None:
        return None
    if down < 0 or down > 4:
        return None
    return down


def _period(value: object) -> int | None:
    period = _as_int(value)
    if period is None:
        return None
    if period < 1 or period > 8:
        return None
    return period


def _yardline(value: object) -> int | None:
    yards = _as_int(value)
    if yards is None:
        return None
    if yards < 0 or yards > 100:
        return None
    return yards


def _clock_seconds(value: object) -> int | None:
    """Parse CFBD clock dict ``{minutes, seconds}`` or numeric seconds → seconds left."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, dict):
        minutes = _as_int(value.get("minutes"))
        seconds = _as_int(value.get("seconds"))
        if minutes is None and seconds is None:
            return None
        return (minutes or 0) * 60 + (seconds or 0)
    return _as_int(value)


def _first_present(row: Mapping[str, object], *keys: str) -> object:
    for key in keys:
        if key in row and row[key] is not None:
            val = row[key]
            if isinstance(val, float) and pd.isna(val):
                continue
            return val
    return None


def normalize_epa_plays(
    plays: pd.DataFrame,
    *,
    validate: bool = True,
) -> pd.DataFrame:
    """Normalize CFBD/staged play rows into the clean per-play EPA table.

    Accepted input columns (any subset; aliases in parentheses):

    - ``game_key`` (else ``str(game_id)``)
    - ``play_id``, ``game_id``
    - ``offense_team`` (``offense``) / ``defense_team`` (``defense``);
      falls back to stringified ``offense_id`` / ``defense_id``
    - ``play_type``, ``down``, ``distance``
    - ``yardline`` (``yards_to_goal``, ``yardsToGoal``)
    - ``period``, ``clock`` (seconds or CFBD ``{minutes,seconds}``)
    - ``yards_gained`` (``yardsGained``)
    - ``epa`` (``ppa``)
    - ``wp_before`` (``wp``, ``homeWinProb``) / ``wp_after``
    - ``offense_score`` / ``defense_score`` (for Connelly margin) or
      ``score_margin`` directly

    Time semantics: this function does not assign ``event_time``; callers that
    persist features must supply PIT timestamps separately (Task 9+).
    """
    if plays.empty:
        empty = pd.DataFrame(columns=list(_OUTPUT_COLUMNS))
        return EpaPlaysSchema.validate(empty) if validate else empty

    rows: list[dict[str, object]] = []
    records = plays.to_dict(orient="records")
    for raw in records:
        play_type_val = _first_present(raw, "play_type", "playType")
        play_type = None if play_type_val is None else str(play_type_val)
        is_rush, is_pass, is_st, is_penalty = classify_play_type(play_type)

        down = _down(_first_present(raw, "down"))
        distance = _as_int(_first_present(raw, "distance"))
        yards_gained = _as_int(_first_present(raw, "yards_gained", "yardsGained"))
        period = _period(_first_present(raw, "period"))

        offense_score = _as_int(
            _first_present(raw, "offense_score", "offenseScore", "offense_score_before")
        )
        defense_score = _as_int(
            _first_present(raw, "defense_score", "defenseScore", "defense_score_before")
        )
        score_margin = _as_int(_first_present(raw, "score_margin"))
        if score_margin is None and offense_score is not None and defense_score is not None:
            score_margin = offense_score - defense_score

        wp_before = _as_float(_first_present(raw, "wp_before", "wp", "homeWinProb"))
        wp_after = _as_float(_first_present(raw, "wp_after"))

        game_id = _as_int(_first_present(raw, "game_id", "gameId"))
        game_key_val = _first_present(raw, "game_key")
        if game_key_val is None:
            game_key = str(game_id) if game_id is not None else ""
        else:
            game_key = str(game_key_val)

        offense = _first_present(raw, "offense_team", "offense")
        defense = _first_present(raw, "defense_team", "defense")
        if offense is None:
            oid = _as_int(_first_present(raw, "offense_id", "offenseId"))
            offense = str(oid) if oid is not None else None
        else:
            offense = str(offense)
        if defense is None:
            did = _as_int(_first_present(raw, "defense_id", "defenseId"))
            defense = str(did) if did is not None else None
        else:
            defense = str(defense)

        yardline = _yardline(
            _first_present(raw, "yardline", "yards_to_goal", "yardsToGoal", "yardLine")
        )
        epa = _as_float(_first_present(raw, "epa", "ppa"))
        clock = _clock_seconds(_first_present(raw, "clock", "clock_seconds"))

        rows.append(
            {
                "game_key": game_key,
                "play_id": _as_int(_first_present(raw, "play_id", "id")),
                "game_id": game_id,
                "offense_team": offense,
                "defense_team": defense,
                "play_type": play_type,
                "down": down,
                "distance": distance,
                "yardline": yardline,
                "period": period,
                "clock": clock,
                "yards_gained": yards_gained,
                "epa": epa,
                "wp_before": wp_before,
                "wp_after": wp_after,
                "is_rush": is_rush,
                "is_pass": is_pass,
                "is_special_teams": is_st,
                "is_penalty": is_penalty,
                "is_havoc": is_havoc_play(play_type),
                "is_success": is_successful_play(down, distance, yards_gained),
                "score_margin": score_margin,
                # placeholders; apply_garbage_time fills these
                "garbage_time": False,
                "gt_rule": "none",
                "gt_fallback_used": False,
            }
        )

    frame = pd.DataFrame(rows)
    frame = apply_garbage_time(frame)
    if validate:
        return EpaPlaysSchema.validate(frame)
    return frame


def plays_from_cfbd_raw_json(
    path: Path | str,
    *,
    validate: bool = True,
) -> pd.DataFrame:
    """Load one CFBD ``/plays`` raw JSON archive into the normalized EPA table.

    Used when staged ``plays`` lack score/clock/WP fields needed for GT
    fallback (see docs/notes/08.md). Does not modify the ingestion layer.
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        msg = f"expected JSON list in {path}"
        raise TypeError(msg)
    return normalize_epa_plays(pd.DataFrame(payload), validate=validate)


def load_season_plays_from_cfbd_raw(
    raw_root: Path | str,
    season: int,
    *,
    validate: bool = True,
) -> pd.DataFrame:
    """Concatenate all ``plays_s{season}_*.json`` under a CFBD raw root into EPA plays."""
    root = Path(raw_root)
    paths = sorted(root.glob(f"**/plays_s{season}_*.json"))
    if not paths:
        msg = f"no CFBD plays archives for season {season} under {root}"
        raise FileNotFoundError(msg)
    frames = [plays_from_cfbd_raw_json(p, validate=False) for p in paths]
    combined = pd.concat(frames, ignore_index=True)
    if validate:
        return EpaPlaysSchema.validate(combined)
    return combined


# ---------------------------------------------------------------------------
# Play weighting
# ---------------------------------------------------------------------------


@runtime_checkable
class PlayWeighting(Protocol):
    """Interface for per-play weights used in efficiency aggregation.

    v1 ships :class:`UniformWeighting`. Leverage-weighted EPA (§3.6 research
    option) plugs in here later without changing aggregators.
    """

    def weights(self, plays: pd.DataFrame) -> pd.Series:
        """Return a non-negative weight per row, indexed like ``plays``."""
        ...


@dataclass(frozen=True, slots=True)
class UniformWeighting:
    """Every play weight = 1.0."""

    name: str = "uniform"

    def weights(self, plays: pd.DataFrame) -> pd.Series:
        return pd.Series(1.0, index=plays.index, dtype="float64")


DEFAULT_WEIGHTING: Final[PlayWeighting] = UniformWeighting()


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------


def _weighted_mean(values: pd.Series, weights: pd.Series) -> float:
    mask = values.notna() & weights.notna()
    if not bool(mask.any()):
        return float("nan")
    w = weights.loc[mask].astype(float)
    v = values.loc[mask].astype(float)
    total = float(w.sum())
    if total <= 0.0:
        return float("nan")
    return float((v * w).sum() / total)


def _subset_metrics(
    plays: pd.DataFrame,
    weights: pd.Series,
    *,
    prefix: str,
) -> dict[str, float]:
    epa = _weighted_mean(plays["epa"], weights)
    success = plays["is_success"]
    # success rate over plays where success is defined
    defined = success.notna()
    if bool(defined.any()):
        sr = _weighted_mean(success.loc[defined].astype(float), weights.loc[defined])
    else:
        sr = float("nan")
    successful = plays.loc[success.fillna(False).astype(bool)]
    if successful.empty:
        explosiveness = float("nan")
    else:
        explosiveness = _weighted_mean(successful["epa"], weights.loc[successful.index])
    havoc = _weighted_mean(plays["is_havoc"].astype(float), weights)
    return {
        f"{prefix}epa_per_play": epa,
        f"{prefix}success_rate": sr,
        f"{prefix}explosiveness": explosiveness,
        f"{prefix}havoc_rate": havoc,
        f"{prefix}n_plays": float(len(plays)),
    }


def aggregate_efficiency(
    plays: pd.DataFrame,
    group_cols: Sequence[str],
    *,
    weighting: PlayWeighting | None = None,
    drop_garbage: bool = True,
) -> pd.DataFrame:
    """Aggregate EPA/play, success rate, explosiveness, and havoc by ``group_cols``.

    Success rate uses :func:`is_successful_play` (50/70/100 by down).
    Explosiveness is mean EPA on successful plays (IsoPPP-style).
    Havoc rate is the fraction of plays with a havoc play_type.
    All four are also reported for rush and pass subsets (``rush_`` / ``pass_``
    prefixes). Special-teams and penalty-only rows remain in the overall
    numbers unless the caller filters first.
    """
    if drop_garbage:
        work = apply_garbage_time(plays) if "garbage_time" not in plays.columns else plays
        work = work.loc[~work["garbage_time"].astype(bool)].copy()
    else:
        work = plays.copy()

    if work.empty:
        cols = list(group_cols) + [
            "epa_per_play",
            "success_rate",
            "explosiveness",
            "havoc_rate",
            "n_plays",
            "rush_epa_per_play",
            "rush_success_rate",
            "rush_explosiveness",
            "rush_havoc_rate",
            "rush_n_plays",
            "pass_epa_per_play",
            "pass_success_rate",
            "pass_explosiveness",
            "pass_havoc_rate",
            "pass_n_plays",
        ]
        return pd.DataFrame(columns=cols)

    weighting = weighting or DEFAULT_WEIGHTING
    w_all = weighting.weights(work)

    rows: list[dict[str, object]] = []
    grouped = work.groupby(list(group_cols), dropna=False, sort=False)
    for keys, group in grouped:
        if not isinstance(keys, tuple):
            keys = (keys,)
        row: dict[str, object] = dict(zip(group_cols, keys, strict=True))
        gw = w_all.loc[group.index]
        row.update(_subset_metrics(group, gw, prefix=""))
        rush = group.loc[group["is_rush"].astype(bool)]
        row.update(_subset_metrics(rush, w_all.loc[rush.index], prefix="rush_"))
        pas = group.loc[group["is_pass"].astype(bool)]
        row.update(_subset_metrics(pas, w_all.loc[pas.index], prefix="pass_"))
        rows.append(row)
    return pd.DataFrame(rows)


def garbage_time_summary(plays: pd.DataFrame) -> dict[str, float]:
    """Return GT fraction and fallback-fire rate for acceptance reporting."""
    if plays.empty:
        return {
            "n_plays": 0.0,
            "n_garbage": 0.0,
            "garbage_frac": float("nan"),
            "n_fallback": 0.0,
            "fallback_frac": float("nan"),
        }
    flagged = plays if "garbage_time" in plays.columns else apply_garbage_time(plays)
    n = float(len(flagged))
    n_gt = float(flagged["garbage_time"].astype(bool).sum())
    n_fb = float(flagged["gt_fallback_used"].astype(bool).sum())
    return {
        "n_plays": n,
        "n_garbage": n_gt,
        "garbage_frac": n_gt / n if n else float("nan"),
        "n_fallback": n_fb,
        "fallback_frac": n_fb / n if n else float("nan"),
    }
