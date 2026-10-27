"""Situational-angle research harness — pre-registered definitions and the
team-game frame (DESIGN §13 item 1, §16 item 1; §7.2 items 5/8/9).

One row per FBS-vs-FBS regular-season game **per side** (so two rows per
game), built from the angle team's perspective:

- conditions (``team_rest_days``, ``opp_consecutive_weeks``, ``opp_luck_last``,
  …) use only information knowable before the game's Tuesday 06:00 ET decision
  point (``as_of``); for a game kicking off before that Tuesday the as-of is
  the kickoff itself;
- outcomes (``ats_residual_*``, ``cover_*``, ``ridge_residual``) are attached
  in a separate step (:func:`attach_outcomes`) so a run can print qualifying
  counts before any outcome is touched;
- ``qualifies`` / ``baseline`` are evaluated from the committed
  ``configs/angles.yaml`` vocabulary only (unknown condition keys raise);
- games where **both** sides qualify are dropped and counted.

Sign conventions (angle team's side): ``spread_*`` negative when the angle
team is favored; ``ats_residual = margin − (−spread_close)``; ``cover =
ats_residual > 0`` with pushes null; ``ridge_residual = margin −
ridge_pred_margin``. Units are points; all timestamps UTC.

This module is research tooling. It changes no production feature, model, or
publish path.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
import yaml  # type: ignore[import-untyped]

from ncaa_quant.evaluation.walkforward import WeekDecisionCalendar
from ncaa_quant.features.builders.situational import (
    BYE_DAYS,
    build_situational_frame,
    load_rivalry_pairs,
)
from ncaa_quant.utils.timeutils import to_utc

DEFAULT_ANGLES_CONFIG: Final[Path] = Path("configs/angles.yaml")

#: Rest threshold (days) that defines a "long rest" / bye for streak logic.
LONG_REST_DAYS: Final[float] = float(BYE_DAYS)

#: Luck definitions understood by the condition vocabulary.
LUCK_DEFINITIONS: Final[tuple[str, ...]] = ("default", "ats_component", "neg_epa_component")

_LUCK_COLUMN: Final[Mapping[str, str]] = {
    "default": "opp_luck_last",
    "ats_component": "opp_luck_ats_last",
    "neg_epa_component": "opp_luck_negepa_last",
}

#: Condition keys allowed in ``configs/angles.yaml`` (pre-registration vocabulary).
CONDITION_KEYS: Final[frozenset[str]] = frozenset(
    {
        "team_rest_days_min",
        "team_rest_days_max",
        "team_exclude_opener",
        "opp_rest_days_min",
        "opp_rest_days_max",
        "opp_exclude_opener",
        "team_prior_long_rest_count_min",
        "team_prior_long_rest_count_max",
        "opp_prior_long_rest_count_min",
        "opp_prior_long_rest_count_max",
        "opp_consecutive_weeks_min",
        "opp_consecutive_weeks_max",
        "team_consecutive_weeks_min",
        "team_consecutive_weeks_max",
        "rivalry_flag",
        "conference_game",
        "opp_luck_last_q_min",
        "opp_luck_last_q_max",
        "luck_quantile_min_n",
        "opp_last_game_completed_before_as_of",
        "team_last_game_completed_before_as_of",
        "spread_close_min",
        "spread_close_max",
        "venue_home",
        "venue_away",
        "team_ridge_lt_opp",
        "team_last_won",
        "team_last_lost",
        "opp_last_won",
        "opp_last_lost",
        "team_last_spread_min",
        "team_last_spread_max",
        "opp_last_spread_min",
        "opp_last_spread_max",
        "team_last_margin_min",
        "team_last_margin_max",
        "opp_last_margin_min",
        "opp_last_margin_max",
        "team_last_wounded",
        "opp_last_upset_win",
        "team_last_ridge_rank_max",
        "opp_last_ridge_rank_max",
        "team_last_was_home",
        "team_last_was_away",
        "opp_last_was_home",
        "opp_last_was_away",
        "team_last_neutral",
        "opp_last_neutral",
        "opp_last_to_margin_min",
        "opp_last_to_margin_max",
        "opp_last_fumble_margin_min",
        "opp_last_int_margin_min",
        "opp_last_opp_ap_rank_max",
        "opp_last_opp_ap_unranked",
        "opp_last_opp_ridge_rank_max",
        "opp_last_opp_fbs",
        "ats_momentum_abs_q_min",
        "ats_momentum_abs_q_max",
        "momentum_window_min_n",
        "team_last_ats_gt_opp",
        # Batch 5
        "week_min",
        "week_max",
        "weeks_from_season_end_max",
        "opp_net_epa_l3d_q_max",
        "opp_off_epa_l3d_q_max",
        "opp_def_epa_l3d_q_min",
        "opp_body_clock_east_early",
        "opp_eastern_pacific_late",
        "team_last_opp_service_academy",
        "opp_last_opp_service_academy",
        "opp_is_service_academy",
        "team_lookahead",
        "opp_lookahead",
        "team_last_lookahead",
        "opp_last_lookahead",
        "team_next_ridge_rank_max",
        "opp_next_ridge_rank_max",
        "team_revenge_loss_14",
        "team_rematch_prior_season",
        "team_prior_season_vs_opp_margin_min",
        "team_prior_season_vs_opp_margin_max",
        "team_season_wins_min",
        "team_season_wins_max",
        "team_season_wins_eq",
        "opp_season_wins_min",
        "opp_season_losses_min",
        "opp_bowl_or_spoiler",
        "team_interim_coach_first_game",
        "team_ap_ranked",
        "team_ap_unranked",
        "opp_ap_ranked",
        "opp_ap_unranked",
        "team_ap_rank_max",
        "opp_ap_rank_max",
        "wind_speed_ms_min",
        "wind_speed_ms_max",
        "outdoor",
        "either_last_total_over_21",
        "rest_same_bucket_6_8",
    }
)

#: Columns that are outcomes (never present before :func:`attach_outcomes`).
OUTCOME_COLUMNS: Final[tuple[str, ...]] = (
    "team_points",
    "opp_points",
    "margin",
    "spread_close_cfbd",
    "spread_close_odds",
    "spread_tue_odds",
    "line_source_close_cfbd",
    "line_source_close_odds",
    "line_source_tue_odds",
    "n_books_close_odds",
    "n_books_tue_odds",
    "ridge_pred_margin",
    "ats_residual_cfbd",
    "ats_residual_odds",
    "cover_cfbd",
    "cover_odds",
    "ridge_residual",
    "line_move_toward_angle",
    "total_close_cfbd",
    "actual_total",
    "total_residual_cfbd",
    "cover_under_cfbd",
)


class AnglesConfigError(ValueError):
    """Malformed ``configs/angles.yaml``."""


STATUS_RETIRED_UNOPENED: Final[str] = "retired_unopened"
STATUS_NOT_RUN_DATA_MISSING: Final[str] = "not_run_data_missing"


class RetiredUnopenedError(RuntimeError):
    """Raised when a retired-unopened hypothesis is asked to run.

    Holdout stays closed; the definition is kept for history only.
    """


class NotRunDataMissingError(RuntimeError):
    """Hypothesis cannot run because required staged data is absent."""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConditionSet:
    """One named definition (primary, baseline, or variant)."""

    id: str
    description: str
    conditions: dict[str, Any]
    statistic: str
    direction: str | None = None
    trend: dict[str, Any] | None = None
    regression: dict[str, Any] | None = None
    luck_definition: str = "default"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "conditions": dict(sorted(self.conditions.items())),
            "statistic": self.statistic,
            "direction": self.direction,
            "trend": self.trend,
            "regression": self.regression,
            "luck_definition": self.luck_definition,
        }


@dataclass(frozen=True)
class Hypothesis:
    """A pre-registered hypothesis block."""

    id: str
    name: str
    side: str
    market: str
    primary: ConditionSet
    baseline: ConditionSet
    variants: tuple[ConditionSet, ...] = ()
    include_in_bh: bool = True
    status: str = "active"
    retired_reason: str = ""
    skip_both_qualify: bool = False
    one_row_per_game: bool = False

    def all_definitions(self) -> tuple[ConditionSet, ...]:
        return (self.primary, self.baseline, *self.variants)


@dataclass(frozen=True)
class AnglesConfig:
    """Loaded ``configs/angles.yaml``."""

    raw: dict[str, Any]
    hypotheses: dict[str, Hypothesis]
    source_path: Path | None = None
    source_sha256: str | None = None

    @property
    def universe(self) -> dict[str, Any]:
        return dict(self.raw.get("universe") or {})

    @property
    def splits(self) -> dict[str, Any]:
        return dict(self.raw.get("splits") or {})

    @property
    def inference(self) -> dict[str, Any]:
        return dict(self.raw.get("inference") or {})

    @property
    def multiple_testing(self) -> dict[str, Any]:
        return dict(self.raw.get("multiple_testing") or {})

    @property
    def sources(self) -> dict[str, Any]:
        return dict(self.raw.get("sources") or {})

    @property
    def features(self) -> dict[str, Any]:
        return dict(self.raw.get("features") or {})

    def split_seasons(self, split: str) -> tuple[int, ...]:
        block = self.splits.get(split)
        if not isinstance(block, Mapping):
            msg = f"unknown split {split!r}; known: {sorted(self.splits)}"
            raise AnglesConfigError(msg)
        return tuple(int(s) for s in block.get("seasons") or ())

    def split_sensitivity_seasons(self, split: str) -> tuple[int, ...]:
        block = self.splits.get(split) or {}
        return tuple(int(s) for s in (block.get("sensitivity_seasons") or ()))

    def split_lockbox_seasons(self, split: str) -> tuple[int, ...]:
        block = self.splits.get(split) or {}
        return tuple(int(s) for s in (block.get("lockbox_seasons") or ()))

    def hypothesis_hash(self, hypothesis_id: str) -> str:
        """Content hash of one hypothesis's locked definition.

        Covers the hypothesis ``id``/``side``/``market``/``primary``/``baseline``
        plus the ``splits``, ``multiple_testing`` and ``inference`` blocks.
        Variants are exploratory and excluded (changing them does not re-lock).
        """
        hyp = self.hypotheses[hypothesis_id]
        payload = {
            "id": hyp.id,
            "side": hyp.side,
            "market": hyp.market,
            "primary": hyp.primary.as_dict(),
            "baseline": hyp.baseline.as_dict(),
            "splits": self.splits,
            "multiple_testing": self.multiple_testing,
            "inference": self.inference,
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        return hashlib.sha256(blob).hexdigest()[:16]


def _parse_condition_set(
    block: Mapping[str, Any],
    *,
    default_id: str,
    default_statistic: str,
) -> ConditionSet:
    conditions = dict(block.get("conditions") or {})
    unknown = set(conditions) - CONDITION_KEYS
    if unknown:
        msg = (
            f"{default_id}: unknown condition key(s) {sorted(unknown)}; "
            f"allowed: {sorted(CONDITION_KEYS)}"
        )
        raise AnglesConfigError(msg)
    for key, value in conditions.items():
        if isinstance(value, bool):
            continue
        if not isinstance(value, (int, float)):
            msg = (
                f"{default_id}: condition {key!r} must be an explicit number or bool, got {value!r}"
            )
            raise AnglesConfigError(msg)
    luck_def = str(block.get("luck_definition") or "default")
    if luck_def not in LUCK_DEFINITIONS:
        msg = f"{default_id}: luck_definition {luck_def!r} not in {LUCK_DEFINITIONS}"
        raise AnglesConfigError(msg)
    trend = block.get("trend")
    explicit = block.get("statistic")
    if explicit is not None:
        statistic = str(explicit)
    elif isinstance(trend, Mapping):
        statistic = "trend_slope_ats_residual_on_" + str(trend.get("regressor"))
    else:
        statistic = default_statistic
    return ConditionSet(
        id=str(block.get("id") or default_id),
        description=str(block.get("description") or ""),
        conditions=conditions,
        statistic=statistic,
        direction=(str(block["direction"]) if block.get("direction") is not None else None),
        trend=dict(trend) if isinstance(trend, Mapping) else None,
        regression=(
            dict(block["regression"]) if isinstance(block.get("regression"), Mapping) else None
        ),
        luck_definition=luck_def,
    )


def load_angles_config(path: Path | str | None = None) -> AnglesConfig:
    """Load and validate the pre-registered hypotheses file."""
    cfg_path = Path(path) if path is not None else DEFAULT_ANGLES_CONFIG
    if not cfg_path.is_file():
        msg = f"angles config not found: {cfg_path}"
        raise AnglesConfigError(msg)
    text = cfg_path.read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    if not isinstance(raw, Mapping):
        msg = "angles config root must be a mapping"
        raise AnglesConfigError(msg)
    hyps_raw = raw.get("hypotheses")
    if not isinstance(hyps_raw, list) or not hyps_raw:
        msg = "angles config must list at least one hypothesis"
        raise AnglesConfigError(msg)
    hypotheses: dict[str, Hypothesis] = {}
    for entry in hyps_raw:
        if not isinstance(entry, Mapping):
            msg = "each hypothesis must be a mapping"
            raise AnglesConfigError(msg)
        hid = str(entry.get("id") or "").strip()
        if not hid:
            msg = "hypothesis missing id"
            raise AnglesConfigError(msg)
        if hid in hypotheses:
            msg = f"duplicate hypothesis id {hid!r}"
            raise AnglesConfigError(msg)
        market = str(entry.get("market") or "")
        if market not in {"spread", "total"}:
            msg = f"{hid}: market must be spread|total, got {market!r}"
            raise AnglesConfigError(msg)
        primary_raw = entry.get("primary")
        baseline_raw = entry.get("baseline")
        if not isinstance(primary_raw, Mapping) or not isinstance(baseline_raw, Mapping):
            msg = f"{hid}: primary and baseline blocks are required"
            raise AnglesConfigError(msg)
        primary = _parse_condition_set(
            primary_raw, default_id=f"{hid}_primary", default_statistic="mean_ats_residual"
        )
        baseline = _parse_condition_set(
            baseline_raw, default_id=f"{hid}_baseline", default_statistic=primary.statistic
        )
        variants: list[ConditionSet] = []
        for i, vraw in enumerate(entry.get("variants") or []):
            if not isinstance(vraw, Mapping):
                msg = f"{hid}: variant {i} must be a mapping"
                raise AnglesConfigError(msg)
            variants.append(
                _parse_condition_set(
                    vraw, default_id=f"{hid}v{i}", default_statistic="mean_ats_residual"
                )
            )
        hypotheses[hid] = Hypothesis(
            id=hid,
            name=str(entry.get("name") or hid),
            side=str(entry.get("side") or ""),
            market=market,
            primary=primary,
            baseline=baseline,
            variants=tuple(variants),
            include_in_bh=bool(entry.get("include_in_bh", True)),
            status=str(entry.get("status") or "active"),
            retired_reason=str(entry.get("retired_reason") or ""),
            skip_both_qualify=bool(entry.get("skip_both_qualify", False)),
            one_row_per_game=bool(
                entry.get("one_row_per_game", market == "total")
            ),
        )
    return AnglesConfig(
        raw=dict(raw),
        hypotheses=hypotheses,
        source_path=cfg_path,
        source_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
    )


# ---------------------------------------------------------------------------
# Schedule conditions (rest, prior long rest, consecutive weeks)
# ---------------------------------------------------------------------------


def _kickoff_utc(games: pd.DataFrame) -> pd.Series:
    col = "start_date" if "start_date" in games.columns else "event_time"
    return pd.to_datetime(games[col], utc=True)


def kickoff_utc(games: pd.DataFrame) -> pd.Series:
    """Kickoff timestamps (UTC) from ``start_date`` else ``event_time``."""
    return _kickoff_utc(games)


def _game_end_utc(games: pd.DataFrame) -> pd.Series:
    """Completion timestamp: staged ``event_time`` (kickoff + duration) else kickoff + 4h."""
    if "event_time" in games.columns:
        return pd.to_datetime(games["event_time"], utc=True)
    return _kickoff_utc(games) + pd.Timedelta(hours=4)


def game_end_utc(games: pd.DataFrame) -> pd.Series:
    """Public alias of the completion timestamp helper."""
    return _game_end_utc(games)


def fbs_ids_by_season(teams: pd.DataFrame) -> dict[int, set[int]]:
    """``season -> set(team_id)`` for ``classification == 'fbs'``."""
    out: dict[int, set[int]] = {}
    if teams.empty or "classification" not in teams.columns:
        return out
    fbs = teams.loc[teams["classification"].astype(str).str.casefold() == "fbs"]
    for season, grp in fbs.groupby("season"):
        out[int(season)] = {int(t) for t in grp["team_id"].dropna()}
    return out


def build_schedule_conditions(games: pd.DataFrame) -> pd.DataFrame:
    """Per-(game, team) rest / prior-long-rest / consecutive-weeks from the full schedule.

    ``games`` is the **complete** schedule for the seasons of interest (FCS
    opponents and postseason included — a game vs FCS is a game played, not a
    bye). Only prior same-season games are read for each row (point-in-time by
    construction). Columns: ``game_id``, ``team_id``, ``rest_days``,
    ``opener``, ``prior_long_rest_count``, ``consecutive_weeks``,
    ``prev_game_id``, ``prev_game_end``.
    """
    cols = [
        "game_id",
        "team_id",
        "rest_days",
        "opener",
        "prior_long_rest_count",
        "consecutive_weeks",
        "prev_game_id",
        "prev_game_end",
    ]
    if games.empty:
        return pd.DataFrame(columns=cols)
    work = games[["game_id", "season", "home_team_id", "away_team_id"]].copy()
    work["kickoff"] = _kickoff_utc(games).to_numpy()
    work["game_end"] = _game_end_utc(games).to_numpy()
    long_rows = pd.concat(
        [
            work.rename(columns={"home_team_id": "team_id"}).drop(columns=["away_team_id"]),
            work.rename(columns={"away_team_id": "team_id"}).drop(columns=["home_team_id"]),
        ],
        ignore_index=True,
    )
    long_rows = long_rows.sort_values(["team_id", "season", "kickoff", "game_id"], kind="mergesort")
    rows: list[dict[str, Any]] = []
    for (_team, _season), grp in long_rows.groupby(["team_id", "season"], sort=False):
        prev_kick: pd.Timestamp | None = None
        prev_gid: int | None = None
        prev_end: pd.Timestamp | None = None
        long_count = 0
        streak = 0
        for r in grp.itertuples(index=False):
            kick = pd.Timestamp(r.kickoff)
            if prev_kick is None:
                rest = float("nan")
                opener = True
                streak = 1
            else:
                rest = float((kick - prev_kick).total_seconds() / 86400.0)
                opener = False
                streak = 1 if rest >= LONG_REST_DAYS else streak + 1
            rows.append(
                {
                    "game_id": int(r.game_id),
                    "team_id": int(r.team_id),
                    "rest_days": rest,
                    "opener": opener,
                    "prior_long_rest_count": int(long_count),
                    "consecutive_weeks": int(streak),
                    "prev_game_id": prev_gid,
                    "prev_game_end": prev_end,
                }
            )
            if not opener and rest >= LONG_REST_DAYS:
                long_count += 1
            prev_kick = kick
            prev_gid = int(r.game_id)
            prev_end = pd.Timestamp(r.game_end)
    out = pd.DataFrame(rows, columns=cols)
    out["prev_game_id"] = out["prev_game_id"].astype("Int64")
    out["prev_game_end"] = pd.to_datetime(out["prev_game_end"], utc=True)
    return out


def game_as_of(kickoff: pd.Series, tuesday: pd.Series) -> pd.Series:
    """Per-game decision instant: Tuesday 06:00 ET of the CFBD week, or kickoff if earlier."""
    kick = pd.to_datetime(kickoff, utc=True)
    tue = pd.to_datetime(tuesday, utc=True)
    return tue.where(tue < kick, kick).where(tue.notna(), kick)


def tuesday_as_of_by_week(games: pd.DataFrame) -> dict[tuple[int, int], datetime]:
    """``(season, week) -> Tuesday 06:00 ET (UTC)`` from the kickoff calendar."""
    if games.empty:
        return {}
    work = games[["season", "week"]].copy()
    work["event_time"] = _kickoff_utc(games).to_numpy()
    cal = WeekDecisionCalendar.from_games(work)
    out: dict[tuple[int, int], datetime] = {}
    for (season, week), pts in cal.items():
        out[(int(season), int(week))] = pts.tuesday_0600_et
    return out


# ---------------------------------------------------------------------------
# Team-game frame (conditions only)
# ---------------------------------------------------------------------------


@dataclass
class TeamGameFrame:
    """Two rows per game (one per side) with condition columns; outcomes optional."""

    rows: pd.DataFrame
    n_games_universe: int
    seasons: tuple[int, ...]
    has_outcomes: bool = False
    notes: dict[str, Any] = field(default_factory=dict)


def build_team_game_frame(
    games: pd.DataFrame,
    teams: pd.DataFrame,
    *,
    venues: pd.DataFrame | None = None,
    rivalry_pairs: frozenset[frozenset[str]] | None = None,
    rivalries_path: Path | str | None = None,
    season_type: str = "regular",
) -> TeamGameFrame:
    """Build the condition frame (no outcomes) for FBS-vs-FBS ``season_type`` games.

    Rest / bye / rivalry / conference / neutral columns come from
    :func:`build_situational_frame` (existing feature builder); prior-long-rest
    and consecutive-week streaks from :func:`build_schedule_conditions`. Both
    read the **full** schedule so FCS games count as played weeks.
    """
    if games.empty:
        return TeamGameFrame(rows=pd.DataFrame(), n_games_universe=0, seasons=())
    sched = games.copy()
    sched["kickoff"] = _kickoff_utc(sched).to_numpy()
    sched["game_end"] = _game_end_utc(sched).to_numpy()

    # Existing situational builder: rest days / bye / rivalry / conference / neutral.
    sit_games = sched.drop(columns=["event_time"], errors="ignore").rename(
        columns={"kickoff": "event_time"}
    )
    venues_frame = (
        venues
        if venues is not None and not venues.empty
        else pd.DataFrame(
            columns=[
                "venue_id",
                "latitude",
                "longitude",
                "elevation_m",
                "timezone",
                "surface",
            ]
        )
    )
    pairs = rivalry_pairs if rivalry_pairs is not None else load_rivalry_pairs(rivalries_path)
    situational = build_situational_frame(sit_games, venues_frame, teams, rivalry_pairs=pairs)
    # conference_game / neutral_site come straight from the games frame; the
    # situational builder only contributes rest / bye / rivalry here.
    sit_cols = ["game_id", "rest_days_home", "rest_days_away", "bye_flag", "rivalry_flag"]
    situational = situational[sit_cols].drop_duplicates("game_id")

    sched_cond = build_schedule_conditions(sched)
    tuesdays = tuesday_as_of_by_week(sched)

    fbs_by_season = fbs_ids_by_season(teams)
    schools = (
        {int(r.team_id): str(r.school) for r in teams.itertuples(index=False)}
        if not teams.empty and "school" in teams.columns
        else {}
    )

    if "season_type" in sched.columns:
        universe = sched.loc[sched["season_type"].astype(str) == season_type].copy()
    else:
        universe = sched.copy()
    if "completed" not in universe.columns:
        universe["completed"] = universe["home_points"].notna() & universe["away_points"].notna()
    if fbs_by_season:
        keep = [
            int(r.home_team_id) in fbs_by_season.get(int(r.season), set())
            and int(r.away_team_id) in fbs_by_season.get(int(r.season), set())
            for r in universe.itertuples(index=False)
        ]
        universe = universe.loc[keep]
    universe = universe.merge(situational, on="game_id", how="left")

    sides: list[pd.DataFrame] = []
    for is_home in (True, False):
        side = pd.DataFrame(
            {
                "game_id": universe["game_id"].astype(int).to_numpy(),
                "season": universe["season"].astype(int).to_numpy(),
                "week": universe["week"].astype(int).to_numpy(),
                "kickoff": universe["kickoff"].to_numpy(),
                "game_end": universe["game_end"].to_numpy(),
                "completed": universe["completed"].astype(bool).to_numpy(),
                "team_id": (universe["home_team_id"] if is_home else universe["away_team_id"])
                .astype(int)
                .to_numpy(),
                "opp_id": (universe["away_team_id"] if is_home else universe["home_team_id"])
                .astype(int)
                .to_numpy(),
                "is_home": is_home,
                "neutral_site": universe["neutral_site"].fillna(0).astype(bool).to_numpy(),
                "conference_game": universe["conference_game"].fillna(0).astype(bool).to_numpy(),
                "rivalry_flag": universe["rivalry_flag"].fillna(0).astype(bool).to_numpy(),
                "bye_flag": universe["bye_flag"].fillna(0).astype(bool).to_numpy(),
                "team_rest_days_sit": (
                    universe["rest_days_home"] if is_home else universe["rest_days_away"]
                ).to_numpy(dtype=float),
                "opp_rest_days_sit": (
                    universe["rest_days_away"] if is_home else universe["rest_days_home"]
                ).to_numpy(dtype=float),
            }
        )
        sides.append(side)
    rows = pd.concat(sides, ignore_index=True)
    rows["venue"] = np.where(
        rows["neutral_site"], "neutral", np.where(rows["is_home"], "home", "away")
    )
    rows["team_school"] = rows["team_id"].map(lambda t: schools.get(int(t), str(t)))
    rows["opp_school"] = rows["opp_id"].map(lambda t: schools.get(int(t), str(t)))

    team_cond = sched_cond.rename(
        columns={
            "rest_days": "team_rest_days",
            "opener": "team_opener",
            "prior_long_rest_count": "team_prior_long_rest_count",
            "consecutive_weeks": "team_consecutive_weeks",
            "prev_game_id": "team_prev_game_id",
            "prev_game_end": "team_prev_game_end",
        }
    )
    opp_cond = sched_cond.rename(
        columns={
            "team_id": "opp_id",
            "rest_days": "opp_rest_days",
            "opener": "opp_opener",
            "prior_long_rest_count": "opp_prior_long_rest_count",
            "consecutive_weeks": "opp_consecutive_weeks",
            "prev_game_id": "opp_prev_game_id",
            "prev_game_end": "opp_prev_game_end",
        }
    )
    rows = rows.merge(team_cond, on=["game_id", "team_id"], how="left")
    rows = rows.merge(opp_cond, on=["game_id", "opp_id"], how="left")

    # Cross-check: situational rest must agree with the schedule-derived rest.
    for a, b in (("team_rest_days", "team_rest_days_sit"), ("opp_rest_days", "opp_rest_days_sit")):
        both = rows[a].notna() & rows[b].notna()
        if both.any() and not np.allclose(
            rows.loc[both, a].to_numpy(dtype=float),
            rows.loc[both, b].to_numpy(dtype=float),
            atol=1e-6,
        ):
            msg = f"{a} disagrees with situational builder {b}"
            raise RuntimeError(msg)
    rows = rows.drop(columns=["team_rest_days_sit", "opp_rest_days_sit"])

    tue = [
        tuesdays.get((int(s), int(w))) for s, w in zip(rows["season"], rows["week"], strict=True)
    ]
    rows["tuesday_as_of"] = pd.to_datetime(pd.Series(tue, index=rows.index), utc=True)
    rows["as_of"] = game_as_of(rows["kickoff"], rows["tuesday_as_of"])
    rows["opp_last_game_completed_before_as_of"] = rows["opp_prev_game_end"].notna() & (
        rows["opp_prev_game_end"] < rows["as_of"]
    )
    rows["team_last_game_completed_before_as_of"] = rows["team_prev_game_end"].notna() & (
        rows["team_prev_game_end"] < rows["as_of"]
    )
    rows["season_week"] = rows["season"].astype(str) + "-" + rows["week"].astype(str).str.zfill(2)
    rows = rows.sort_values(["season", "week", "kickoff", "game_id", "is_home"], kind="mergesort")
    rows = rows.reset_index(drop=True)
    return TeamGameFrame(
        rows=rows,
        n_games_universe=int(universe["game_id"].nunique()),
        seasons=tuple(sorted({int(s) for s in universe["season"].unique()})),
    )


# ---------------------------------------------------------------------------
# Luck (H14) — last-game surprise decomposition
# ---------------------------------------------------------------------------


def compute_game_luck(
    games: pd.DataFrame,
    team_game_epa: pd.DataFrame,
    close_spread_home: Mapping[int, float] | pd.Series,
    filter_history: pd.DataFrame,
    *,
    hfa_epa_per_play: float,
) -> pd.DataFrame:
    """Home-perspective luck per game (points).

    ``luck_home = (margin_home + spread_close_home)
    − (epa_margin_home − expected_epa_margin_home)`` where
    ``epa_margin_home = home_epa·home_plays − away_epa·away_plays`` (GT-filtered,
    points) and ``expected_epa_margin_home = (off_h − def_a + hfa_h)·home_plays
    − (off_a − def_h)·away_plays`` from the latest same-season Stage-1 postgame
    posteriors with ``event_time < kickoff``. ``hfa_h`` is
    ``hfa_epa_per_play`` for non-neutral games else 0. Away luck is the
    negative. Rows with any missing input are null.

    ``team_game_epa`` is :func:`ratings.state_space.build_game_observations_from_plays`
    output (``game_id, home_team_id, away_team_id, home_epa, away_epa,
    home_plays, away_plays``).
    """
    cols = [
        "game_id",
        "luck_home",
        "luck_ats_home",
        "luck_epa_home",
        "epa_margin_home",
        "expected_epa_margin_home",
    ]
    if games.empty:
        return pd.DataFrame(columns=cols)
    g = games[["game_id", "season", "home_team_id", "away_team_id", "neutral_site"]].copy()
    g["kickoff"] = _kickoff_utc(games).to_numpy()
    hp = pd.to_numeric(games["home_points"], errors="coerce")
    ap = pd.to_numeric(games["away_points"], errors="coerce")
    g["margin_home"] = (hp - ap).to_numpy(dtype=float)
    spread_map = (
        close_spread_home
        if isinstance(close_spread_home, pd.Series)
        else pd.Series(dict(close_spread_home), dtype=float)
    )
    g["spread_close_home"] = g["game_id"].map(spread_map).astype(float)

    if team_game_epa is None or team_game_epa.empty:
        g["home_epa"] = np.nan
        g["away_epa"] = np.nan
        g["home_plays"] = np.nan
        g["away_plays"] = np.nan
    else:
        epa_cols = ["game_id", "home_epa", "away_epa", "home_plays", "away_plays"]
        g = g.merge(team_game_epa[epa_cols].drop_duplicates("game_id"), on="game_id", how="left")

    # Pre-game Stage-1 posteriors: latest postgame row strictly before kickoff, same season.
    rat = pd.DataFrame(columns=["team_id", "season", "event_time", "off_epa", "def_epa"])
    if filter_history is not None and not filter_history.empty:
        rat = filter_history.copy()
        if "kind" in rat.columns:
            rat = rat.loc[rat["kind"].astype(str) == "postgame"]
        rat = rat[["team_id", "season", "event_time", "off_epa", "def_epa"]].copy()
        rat["event_time"] = pd.to_datetime(rat["event_time"], utc=True)
        rat["team_id"] = rat["team_id"].astype(int)
        rat = rat.sort_values("event_time", kind="mergesort")

    def _pregame(side: str) -> pd.DataFrame:
        left = g[["game_id", "season", f"{side}_team_id", "kickoff"]].rename(
            columns={f"{side}_team_id": "team_id"}
        )
        left["team_id"] = left["team_id"].astype(int)
        left = left.sort_values("kickoff", kind="mergesort")
        if rat.empty:
            out = left.copy()
            out[f"{side}_off_pre"] = np.nan
            out[f"{side}_def_pre"] = np.nan
            return out[["game_id", f"{side}_off_pre", f"{side}_def_pre"]]
        merged = pd.merge_asof(
            left,
            rat.rename(columns={"season": "rat_season"}),
            left_on="kickoff",
            right_on="event_time",
            by="team_id",
            allow_exact_matches=False,
            direction="backward",
        )
        same_season = merged["rat_season"].notna() & (
            merged["rat_season"].astype(float) == merged["season"].astype(float)
        )
        merged[f"{side}_off_pre"] = merged["off_epa"].where(same_season)
        merged[f"{side}_def_pre"] = merged["def_epa"].where(same_season)
        return merged[["game_id", f"{side}_off_pre", f"{side}_def_pre"]]

    g = g.merge(_pregame("home"), on="game_id", how="left")
    g = g.merge(_pregame("away"), on="game_id", how="left")

    neutral = g["neutral_site"].fillna(False).astype(bool).to_numpy()
    hfa_h = np.where(neutral, 0.0, float(hfa_epa_per_play))
    home_plays = g["home_plays"].astype(float).to_numpy()
    away_plays = g["away_plays"].astype(float).to_numpy()
    epa_margin = (
        g["home_epa"].astype(float).to_numpy() * home_plays
        - g["away_epa"].astype(float).to_numpy() * away_plays
    )
    expected = (
        g["home_off_pre"].astype(float).to_numpy()
        - g["away_def_pre"].astype(float).to_numpy()
        + hfa_h
    ) * home_plays - (
        g["away_off_pre"].astype(float).to_numpy() - g["home_def_pre"].astype(float).to_numpy()
    ) * away_plays
    ats = g["margin_home"].to_numpy(dtype=float) + g["spread_close_home"].to_numpy(dtype=float)
    epa_surprise = epa_margin - expected
    out = pd.DataFrame(
        {
            "game_id": g["game_id"].astype(int).to_numpy(),
            "luck_home": ats - epa_surprise,
            "luck_ats_home": ats,
            "luck_epa_home": epa_surprise,
            "epa_margin_home": epa_margin,
            "expected_epa_margin_home": expected,
        }
    )
    return out


def attach_last_game_luck(
    rows: pd.DataFrame,
    game_luck: pd.DataFrame,
    games: pd.DataFrame,
) -> pd.DataFrame:
    """Attach ``opp_luck_last`` / ``team_luck_last`` (+ component variants) from each
    side's previous same-season game (any opponent). Sign is from that team's side.
    """
    out = rows.copy()
    if game_luck.empty:
        for c in (
            "opp_luck_last",
            "opp_luck_ats_last",
            "opp_luck_negepa_last",
            "team_luck_last",
        ):
            out[c] = np.nan
        return out
    home_of_game = games.drop_duplicates("game_id").set_index("game_id")["home_team_id"].astype(int)
    luck = game_luck.drop_duplicates("game_id").set_index("game_id")

    def _side_luck(prev_gid: pd.Series, team: pd.Series, col: str, *, negate: bool) -> np.ndarray:
        vals = np.full(len(prev_gid), np.nan)
        pg = prev_gid.to_numpy()
        tm = team.to_numpy()
        for i in range(len(vals)):
            gid = pg[i]
            if pd.isna(gid):
                continue
            gid_i = int(gid)
            if gid_i not in luck.index:
                continue
            v = float(luck.at[gid_i, col])
            if not math.isfinite(v):
                continue
            is_home_prev = int(home_of_game.get(gid_i, -1)) == int(tm[i])
            v = v if is_home_prev else -v
            vals[i] = -v if negate else v
        return vals

    out["opp_luck_last"] = _side_luck(
        out["opp_prev_game_id"], out["opp_id"], "luck_home", negate=False
    )
    out["opp_luck_ats_last"] = _side_luck(
        out["opp_prev_game_id"], out["opp_id"], "luck_ats_home", negate=False
    )
    out["opp_luck_negepa_last"] = _side_luck(
        out["opp_prev_game_id"], out["opp_id"], "luck_epa_home", negate=True
    )
    out["team_luck_last"] = _side_luck(
        out["team_prev_game_id"], out["team_id"], "luck_home", negate=False
    )
    return out


def attach_trailing_luck_quantiles(
    rows: pd.DataFrame,
    game_luck: pd.DataFrame,
    games: pd.DataFrame,
) -> pd.DataFrame:
    """Attach ``*_q`` trailing-quantile columns and ``luck_window_n``.

    The reference distribution at a row's ``as_of`` is every finite team-game
    luck value (both sides, so symmetric) from games with ``game_end < as_of``
    in ``game_luck`` (which may span earlier seasons than ``rows``). Quantile is
    the empirical CDF rank ``#(v <= x) / n``.
    """
    out = rows.copy()
    qcols = {
        "opp_luck_last": ("opp_luck_last_q", "luck_home"),
        "opp_luck_ats_last": ("opp_luck_ats_last_q", "luck_ats_home"),
        "opp_luck_negepa_last": ("opp_luck_negepa_last_q", "luck_epa_home"),
    }
    for _src, (qcol, _lcol) in qcols.items():
        out[qcol] = np.nan
    out["luck_window_n"] = 0
    if game_luck.empty or out.empty:
        return out
    ends = games.drop_duplicates("game_id").set_index("game_id")
    ends_series = _game_end_utc(ends.reset_index()).to_numpy()
    end_map = pd.Series(ends_series, index=ends.index)
    lk = game_luck.drop_duplicates("game_id").copy()
    lk["game_end"] = lk["game_id"].map(end_map)
    lk = lk.dropna(subset=["game_end"]).sort_values("game_end", kind="mergesort")
    end_arr = pd.to_datetime(lk["game_end"], utc=True).to_numpy()

    values_by_col = {lcol: lk[lcol].to_numpy(dtype=float) for _s, (_q, lcol) in qcols.items()}
    as_of_arr = pd.to_datetime(out["as_of"], utc=True).to_numpy()
    for as_of in np.unique(as_of_arr):
        k = int(np.searchsorted(end_arr, as_of, side="left"))  # games with game_end < as_of
        mask = as_of_arr == as_of
        n_window = 0
        for src, (qcol, lcol) in qcols.items():
            vk = values_by_col[lcol][:k]
            # Both perspectives → symmetric reference distribution.
            pool = np.sort(np.concatenate([vk, -vk]))
            pool = pool[np.isfinite(pool)]
            n = int(pool.size)
            x = out.loc[mask, src].to_numpy(dtype=float)
            if n == 0:
                q = np.full(x.shape, np.nan)
            else:
                q = np.searchsorted(pool, x, side="right") / float(n)
                q = np.where(np.isfinite(x), q, np.nan)
            out.loc[mask, qcol] = q
            if lcol == "luck_home":
                n_window = n
        out.loc[mask, "luck_window_n"] = n_window
    out["luck_window_n"] = out["luck_window_n"].astype(int)
    return out


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------


def _flip_for_side(home_values: pd.Series, is_home: pd.Series) -> np.ndarray:
    v = home_values.to_numpy(dtype=float)
    flipped: np.ndarray = np.where(is_home.to_numpy(dtype=bool), v, -v)
    return flipped


def apply_home_spread_to_rows(
    rows: pd.DataFrame,
    df: pd.DataFrame | None,
    prefix: str,
    *,
    with_books: bool,
) -> pd.DataFrame:
    """Write home-perspective spreads onto team-game rows, flipped to the angle side."""
    out = rows
    gid = out["game_id"]
    is_home = out["is_home"]
    if df is None or df.empty:
        out[f"spread_{prefix}"] = np.nan
        out[f"line_source_{prefix}"] = "null"
        if with_books:
            out[f"n_books_{prefix}"] = 0
        return out
    d = df.drop_duplicates("game_id").set_index("game_id")
    sp = gid.map(pd.to_numeric(d["spread"], errors="coerce")).astype(float)
    out[f"spread_{prefix}"] = _flip_for_side(sp, is_home)
    src = gid.map(d["line_source"]).fillna("null").astype(str)
    out[f"line_source_{prefix}"] = np.where(np.isfinite(out[f"spread_{prefix}"]), src, "null")
    if with_books:
        nb = gid.map(pd.to_numeric(d["n_books"], errors="coerce")).fillna(0).astype(int)
        out[f"n_books_{prefix}"] = nb.to_numpy()
    return out


def attach_market_lines(
    rows: pd.DataFrame,
    *,
    close_cfbd: pd.DataFrame | None,
    close_odds: pd.DataFrame | None = None,
    tuesday_odds: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Attach closing / Tuesday spreads (angle side) without scores or residuals.

    Used as a *condition* (H7 / H24 underdog-at-the-close) before this-game
    outcomes are attached. CFBD and Odds columns stay separate (never pooled).
    """
    out = rows.copy()
    apply_home_spread_to_rows(out, close_cfbd, "close_cfbd", with_books=False)
    apply_home_spread_to_rows(out, close_odds, "close_odds", with_books=True)
    apply_home_spread_to_rows(out, tuesday_odds, "tue_odds", with_books=True)
    out["line_source_close_cfbd"] = np.where(
        out["line_source_close_cfbd"].astype(str) == "null", "null", "cfbd_close"
    )
    return out


def attach_outcomes(
    frame: TeamGameFrame,
    games: pd.DataFrame,
    *,
    close_cfbd: pd.DataFrame | None,
    close_odds: pd.DataFrame | None,
    tuesday_odds: pd.DataFrame | None,
    ridge_predictions: pd.DataFrame | None,
) -> TeamGameFrame:
    """Attach scores, closing lines, Ridge predictions, and residuals (angle side).

    Line frames carry home-perspective ``game_id, spread, line_source, n_books``
    (``resolve_lines_for_games`` output). ``ridge_predictions`` carries
    ``game_id, pred_margin`` (home perspective).
    """
    rows = frame.rows.copy()
    gi = games.drop_duplicates("game_id").set_index("game_id")
    hp = pd.to_numeric(gi["home_points"], errors="coerce")
    ap = pd.to_numeric(gi["away_points"], errors="coerce")
    gid = rows["game_id"]
    is_home = rows["is_home"]
    home_pts = gid.map(hp).astype(float)
    away_pts = gid.map(ap).astype(float)
    rows["team_points"] = np.where(is_home, home_pts, away_pts)
    rows["opp_points"] = np.where(is_home, away_pts, home_pts)
    rows["margin"] = rows["team_points"] - rows["opp_points"]
    rows.loc[~rows["completed"].astype(bool), ["team_points", "opp_points", "margin"]] = np.nan
    rows = attach_market_lines(
        rows, close_cfbd=close_cfbd, close_odds=close_odds, tuesday_odds=tuesday_odds
    )
    gid = rows["game_id"]
    is_home = rows["is_home"]

    if ridge_predictions is None or ridge_predictions.empty:
        rows["ridge_pred_margin"] = np.nan
    else:
        rp = ridge_predictions.drop_duplicates("game_id").set_index("game_id")
        pm = gid.map(pd.to_numeric(rp["pred_margin"], errors="coerce")).astype(float)
        rows["ridge_pred_margin"] = _flip_for_side(pm, is_home)

    for src in ("cfbd", "odds"):
        spread = rows[f"spread_close_{src}"].to_numpy(dtype=float)
        resid = rows["margin"].to_numpy(dtype=float) - (-spread)
        rows[f"ats_residual_{src}"] = resid
        cover = np.where(resid > 0, 1.0, np.where(resid < 0, 0.0, np.nan))
        cover = np.where(np.isfinite(resid), cover, np.nan)
        rows[f"cover_{src}"] = cover
    rows["ridge_residual"] = rows["margin"] - rows["ridge_pred_margin"]
    # Positive = the close moved toward the angle team (its spread got more negative).
    rows["line_move_toward_angle"] = rows["spread_tue_odds"] - rows["spread_close_odds"]
    # Totals: residual = closing total − actual total (positive ⇒ under cashes).
    if "total_close_cfbd" not in rows.columns:
        rows["total_close_cfbd"] = np.nan
    actual_total = rows["team_points"] + rows["opp_points"]
    rows["actual_total"] = actual_total
    rows["total_residual_cfbd"] = rows["total_close_cfbd"] - actual_total
    tres = rows["total_residual_cfbd"].to_numpy(dtype=float)
    cover_u = np.where(tres > 0, 1.0, np.where(tres < 0, 0.0, np.nan))
    cover_u = np.where(np.isfinite(tres), cover_u, np.nan)
    rows["cover_under_cfbd"] = cover_u
    return TeamGameFrame(
        rows=rows,
        n_games_universe=frame.n_games_universe,
        seasons=frame.seasons,
        has_outcomes=True,
        notes=dict(frame.notes),
    )


# ---------------------------------------------------------------------------
# Conditions
# ---------------------------------------------------------------------------


def _ge(series: pd.Series, v: float) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    return (s >= float(v)) & s.notna()


def _le(series: pd.Series, v: float) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    return (s <= float(v)) & s.notna()


def _as_bool_mask(series: pd.Series, value: bool) -> pd.Series:
    """Nulls never match a bool condition (unlike fillna-False flags)."""
    present = series.notna()
    coerced = series.where(present, False).astype(bool)
    return present & (coerced == bool(value))


def _series_or_missing(rows: pd.DataFrame, column: str) -> pd.Series:
    if column not in rows.columns:
        return pd.Series(np.nan, index=rows.index)
    return rows[column]


_NUMERIC_MIN_COLUMNS: Final[Mapping[str, str]] = {
    "spread_close_min": "spread_close_cfbd",
    "team_last_spread_min": "team_last_spread",
    "opp_last_spread_min": "opp_last_spread",
    "team_last_margin_min": "team_last_margin",
    "opp_last_margin_min": "opp_last_margin",
    "opp_last_to_margin_min": "opp_last_to_margin",
    "opp_last_fumble_margin_min": "opp_last_fumble_margin",
    "opp_last_int_margin_min": "opp_last_int_margin",
    "ats_momentum_abs_q_min": "ats_momentum_abs_q",
    "week_min": "week",
    "team_season_wins_min": "team_season_wins",
    "opp_season_wins_min": "opp_season_wins",
    "opp_season_losses_min": "opp_season_losses",
    "team_prior_season_vs_opp_margin_min": "team_prior_season_vs_opp_margin",
    "wind_speed_ms_min": "wind_speed_ms",
    "opp_def_epa_l3d_q_min": "opp_def_epa_l3d_q",
}
_NUMERIC_MAX_COLUMNS: Final[Mapping[str, str]] = {
    "spread_close_max": "spread_close_cfbd",
    "team_last_spread_max": "team_last_spread",
    "opp_last_spread_max": "opp_last_spread",
    "team_last_margin_max": "team_last_margin",
    "opp_last_margin_max": "opp_last_margin",
    "opp_last_to_margin_max": "opp_last_to_margin",
    "team_last_ridge_rank_max": "team_last_ridge_rank",
    "opp_last_ridge_rank_max": "opp_last_ridge_rank",
    "opp_last_opp_ridge_rank_max": "opp_last_opp_ridge_rank",
    "opp_last_opp_ap_rank_max": "opp_last_opp_ap_rank",
    "ats_momentum_abs_q_max": "ats_momentum_abs_q",
    "week_max": "week",
    "weeks_from_season_end_max": "weeks_from_season_end",
    "opp_net_epa_l3d_q_max": "opp_net_epa_l3d_q",
    "opp_off_epa_l3d_q_max": "opp_off_epa_l3d_q",
    "team_next_ridge_rank_max": "team_next_ridge_rank",
    "opp_next_ridge_rank_max": "opp_next_ridge_rank",
    "team_season_wins_max": "team_season_wins",
    "team_prior_season_vs_opp_margin_max": "team_prior_season_vs_opp_margin",
    "team_ap_rank_max": "team_ap_rank",
    "opp_ap_rank_max": "opp_ap_rank",
    "wind_speed_ms_max": "wind_speed_ms",
}
_NUMERIC_EQ_COLUMNS: Final[Mapping[str, str]] = {
    "team_season_wins_eq": "team_season_wins",
}
_BOOL_COLUMNS: Final[Mapping[str, str]] = {
    "team_ridge_lt_opp": "team_ridge_lt_opp",
    "team_last_won": "team_last_won",
    "team_last_lost": "team_last_lost",
    "opp_last_won": "opp_last_won",
    "opp_last_lost": "opp_last_lost",
    "team_last_wounded": "team_last_wounded",
    "opp_last_upset_win": "opp_last_upset_win",
    "team_last_was_home": "team_last_was_home",
    "team_last_was_away": "team_last_was_away",
    "opp_last_was_home": "opp_last_was_home",
    "opp_last_was_away": "opp_last_was_away",
    "team_last_neutral": "team_last_neutral",
    "opp_last_neutral": "opp_last_neutral",
    "opp_last_opp_ap_unranked": "opp_last_opp_ap_unranked",
    "opp_last_opp_fbs": "opp_last_opp_fbs",
    "team_last_game_completed_before_as_of": "team_last_game_completed_before_as_of",
    "team_last_ats_gt_opp": "team_last_ats_gt_opp",
    "opp_body_clock_east_early": "opp_body_clock_east_early",
    "opp_eastern_pacific_late": "opp_eastern_pacific_late",
    "team_last_opp_service_academy": "team_last_opp_service_academy",
    "opp_last_opp_service_academy": "opp_last_opp_service_academy",
    "opp_is_service_academy": "opp_is_service_academy",
    "team_lookahead": "team_lookahead",
    "opp_lookahead": "opp_lookahead",
    "team_last_lookahead": "team_last_lookahead",
    "opp_last_lookahead": "opp_last_lookahead",
    "team_revenge_loss_14": "team_revenge_loss_14",
    "team_rematch_prior_season": "team_rematch_prior_season",
    "opp_bowl_or_spoiler": "opp_bowl_or_spoiler",
    "team_interim_coach_first_game": "team_interim_coach_first_game",
    "team_ap_ranked": "team_ap_ranked",
    "team_ap_unranked": "team_ap_unranked",
    "opp_ap_ranked": "opp_ap_ranked",
    "opp_ap_unranked": "opp_ap_unranked",
    "outdoor": "outdoor",
    "either_last_total_over_21": "either_last_total_over_21",
    "rest_same_bucket_6_8": "rest_same_bucket_6_8",
}
_VENUE_COLUMNS: Final[Mapping[str, str]] = {
    "venue_home": "home",
    "venue_away": "away",
}


def evaluate_conditions(
    rows: pd.DataFrame,
    conditions: Mapping[str, Any],
    *,
    luck_definition: str = "default",
) -> pd.Series:
    """Boolean mask: every condition in ``conditions`` holds (nulls never qualify)."""
    unknown = set(conditions) - CONDITION_KEYS
    if unknown:
        msg = f"unknown condition key(s) {sorted(unknown)}"
        raise AnglesConfigError(msg)
    if luck_definition not in _LUCK_COLUMN:
        msg = f"unknown luck_definition {luck_definition!r}"
        raise AnglesConfigError(msg)
    luck_q_col = _LUCK_COLUMN[luck_definition] + "_q"
    mask = pd.Series(True, index=rows.index, dtype=bool)
    for key, value in conditions.items():
        if key == "team_rest_days_min":
            mask &= _ge(rows["team_rest_days"], value)
        elif key == "team_rest_days_max":
            mask &= _le(rows["team_rest_days"], value)
        elif key == "opp_rest_days_min":
            mask &= _ge(rows["opp_rest_days"], value)
        elif key == "opp_rest_days_max":
            mask &= _le(rows["opp_rest_days"], value)
        elif key == "team_exclude_opener":
            if bool(value):
                mask &= ~rows["team_opener"].fillna(True).astype(bool)
        elif key == "opp_exclude_opener":
            if bool(value):
                mask &= ~rows["opp_opener"].fillna(True).astype(bool)
        elif key == "team_prior_long_rest_count_min":
            mask &= _ge(rows["team_prior_long_rest_count"], value)
        elif key == "team_prior_long_rest_count_max":
            mask &= _le(rows["team_prior_long_rest_count"], value)
        elif key == "opp_prior_long_rest_count_min":
            mask &= _ge(rows["opp_prior_long_rest_count"], value)
        elif key == "opp_prior_long_rest_count_max":
            mask &= _le(rows["opp_prior_long_rest_count"], value)
        elif key == "opp_consecutive_weeks_min":
            mask &= _ge(rows["opp_consecutive_weeks"], value)
        elif key == "opp_consecutive_weeks_max":
            mask &= _le(rows["opp_consecutive_weeks"], value)
        elif key == "team_consecutive_weeks_min":
            mask &= _ge(rows["team_consecutive_weeks"], value)
        elif key == "team_consecutive_weeks_max":
            mask &= _le(rows["team_consecutive_weeks"], value)
        elif key == "rivalry_flag":
            mask &= rows["rivalry_flag"].fillna(False).astype(bool) == bool(value)
        elif key == "conference_game":
            mask &= rows["conference_game"].fillna(False).astype(bool) == bool(value)
        elif key == "opp_luck_last_q_min":
            mask &= _ge(rows[luck_q_col], value)
        elif key == "opp_luck_last_q_max":
            mask &= _le(rows[luck_q_col], value)
        elif key == "luck_quantile_min_n":
            mask &= _ge(rows["luck_window_n"], value)
        elif key == "momentum_window_min_n":
            mask &= _ge(_series_or_missing(rows, "momentum_window_n"), value)
        elif key == "opp_last_game_completed_before_as_of":
            if bool(value):
                mask &= rows["opp_last_game_completed_before_as_of"].fillna(False).astype(bool)
        elif key in _NUMERIC_MIN_COLUMNS:
            mask &= _ge(_series_or_missing(rows, _NUMERIC_MIN_COLUMNS[key]), value)
        elif key in _NUMERIC_MAX_COLUMNS:
            mask &= _le(_series_or_missing(rows, _NUMERIC_MAX_COLUMNS[key]), value)
        elif key in _NUMERIC_EQ_COLUMNS:
            s = pd.to_numeric(
                _series_or_missing(rows, _NUMERIC_EQ_COLUMNS[key]), errors="coerce"
            )
            mask &= s.notna() & (s == float(value))
        elif key == "rest_same_bucket_6_8":
            if bool(value):
                tr = pd.to_numeric(rows["team_rest_days"], errors="coerce")
                orest = pd.to_numeric(rows["opp_rest_days"], errors="coerce")
                mask &= tr.ge(6.0) & tr.le(8.0) & orest.ge(6.0) & orest.le(8.0)
        elif key in _VENUE_COLUMNS:
            mask &= rows["venue"].astype(str) == _VENUE_COLUMNS[key]
        elif key in _BOOL_COLUMNS:
            mask &= _as_bool_mask(_series_or_missing(rows, _BOOL_COLUMNS[key]), bool(value))
        else:  # pragma: no cover — guarded by CONDITION_KEYS
            msg = f"unhandled condition {key!r}"
            raise AnglesConfigError(msg)
    return mask


@dataclass
class FlaggedFrame:
    """Rows with ``qualifies`` / ``baseline`` flags and both-qualify accounting."""

    rows: pd.DataFrame
    n_both_qualify_games: int
    n_both_baseline_games: int
    both_qualify_game_ids: tuple[int, ...]


def flag_definition(
    rows: pd.DataFrame,
    definition: ConditionSet,
    baseline: ConditionSet | None = None,
    *,
    skip_both_qualify: bool = False,
    one_row_per_game: bool = False,
) -> FlaggedFrame:
    """Evaluate ``qualifies`` (and ``baseline``) and drop both-sides-qualify games.

    A game where both sides qualify is dropped (both rows) and counted — the
    angle cannot back both teams. Rows that satisfy both the definition and the
    baseline are kept as ``qualifies`` only (baseline is the comparison group).

    ``skip_both_qualify`` (totals) keeps both-qualify games and does not drop
    them. ``one_row_per_game`` keeps the designated-home row only (totals /
    game-level angles).
    """
    out = rows.copy()
    if one_row_per_game and "is_home" in out.columns:
        out = out.loc[out["is_home"].astype(bool)].copy()
    q = evaluate_conditions(out, definition.conditions, luck_definition=definition.luck_definition)
    if skip_both_qualify or one_row_per_game:
        both_q = pd.Series(False, index=out.index)
        both_ids: tuple[int, ...] = ()
        out["qualifies"] = q.to_numpy(dtype=bool)
    else:
        per_game = q.groupby(out["game_id"]).transform("sum")
        both_q = per_game >= 2
        both_ids = tuple(sorted({int(g) for g in out.loc[both_q, "game_id"].unique()}))
        out["qualifies"] = (q & ~both_q).to_numpy(dtype=bool)
    n_both_b = 0
    if baseline is not None:
        b = evaluate_conditions(out, baseline.conditions, luck_definition=baseline.luck_definition)
        if skip_both_qualify or one_row_per_game:
            out["baseline"] = (b & ~out["qualifies"]).to_numpy(dtype=bool)
        else:
            per_game_b = b.groupby(out["game_id"]).transform("sum")
            both_b = per_game_b >= 2
            n_both_b = int(out.loc[both_b, "game_id"].nunique())
            out["baseline"] = (b & ~both_b & ~out["qualifies"]).to_numpy(dtype=bool)
    else:
        out["baseline"] = False
    if not (skip_both_qualify or one_row_per_game):
        out = out.loc[~both_q].reset_index(drop=True)
    else:
        out = out.reset_index(drop=True)
    return FlaggedFrame(
        rows=out,
        n_both_qualify_games=len(both_ids),
        n_both_baseline_games=n_both_b,
        both_qualify_game_ids=both_ids,
    )


def qualifying_counts_by_season(flagged: FlaggedFrame, seasons: Iterable[int]) -> pd.DataFrame:
    """``season, n_qualifies, n_baseline, n_rows`` for the requested seasons."""
    season_list = [int(s) for s in seasons]
    rows = flagged.rows
    out: list[dict[str, Any]] = []
    for s in season_list:
        sub = rows.loc[rows["season"] == s]
        out.append(
            {
                "season": s,
                "n_qualifies": int(sub["qualifies"].sum()),
                "n_baseline": int(sub["baseline"].sum()),
                "n_rows": int(len(sub)),
                "n_games": int(sub["game_id"].nunique()),
            }
        )
    return pd.DataFrame(out, columns=["season", "n_qualifies", "n_baseline", "n_rows", "n_games"])


# ---------------------------------------------------------------------------
# Helpers used by the runner / tests
# ---------------------------------------------------------------------------


def truncate_games_after(games: pd.DataFrame, kickoff: datetime) -> pd.DataFrame:
    """Delete every game whose kickoff is after ``kickoff`` (PIT test helper)."""
    bound = pd.Timestamp(to_utc(kickoff))
    kick = _kickoff_utc(games)
    return games.loc[kick <= bound].copy()


def condition_columns(rows: pd.DataFrame) -> list[str]:
    """Columns that describe conditions (everything that is not an outcome)."""
    return [c for c in rows.columns if c not in OUTCOME_COLUMNS]


def week_bucket_label(week: int, buckets: Sequence[Sequence[int]]) -> str:
    for lo, hi in buckets:
        if int(lo) <= int(week) <= int(hi):
            return f"w{int(lo):02d}-{int(hi):02d}"
    return "w_other"


def spread_bucket_label(spread: float, buckets: Mapping[str, Mapping[str, float]]) -> str | None:
    """Label a spread (angle side) with the first matching configured bucket."""
    if spread is None or not math.isfinite(float(spread)):
        return None
    s = float(spread)
    for name, bounds in buckets.items():
        lo = bounds.get("min")
        hi = bounds.get("max")
        ok_lo = lo is None or s >= float(lo)
        if hi is None:
            ok_hi = True
        elif lo is None:
            ok_hi = s <= float(hi)
        else:
            ok_hi = s < float(hi)
        if ok_lo and ok_hi:
            return str(name)
    return None


def as_of_minus(kickoff: datetime, hours: float) -> datetime:
    """``kickoff − hours`` (UTC); convenience for fixtures."""
    return to_utc(kickoff) - timedelta(hours=float(hours))


def definition_needs_luck(definition: ConditionSet) -> bool:
    """True when the definition reads a luck condition or luck_definition variant."""
    keys = set(definition.conditions)
    return bool(keys & {"opp_luck_last_q_min", "opp_luck_last_q_max", "luck_quantile_min_n"}) or (
        definition.luck_definition != "default"
    )


def hypothesis_needs_luck(hypothesis: Hypothesis) -> bool:
    return any(definition_needs_luck(d) for d in hypothesis.all_definitions())


def _definition_keys(definition: ConditionSet) -> set[str]:
    return set(definition.conditions)


def definition_needs_lines(definition: ConditionSet) -> bool:
    return bool(_definition_keys(definition) & {"spread_close_min", "spread_close_max"})


def definition_needs_last_game(definition: ConditionSet) -> bool:
    keys = _definition_keys(definition)
    return any(k.startswith("team_last_") or k.startswith("opp_last_") for k in keys)


def definition_needs_ridge(definition: ConditionSet) -> bool:
    keys = _definition_keys(definition)
    return "team_ridge_lt_opp" in keys or any("ridge_rank" in k for k in keys)


def definition_needs_turnovers(definition: ConditionSet) -> bool:
    keys = _definition_keys(definition)
    return any("to_margin" in k or "fumble_margin" in k or "int_margin" in k for k in keys)


def definition_needs_ap(definition: ConditionSet) -> bool:
    keys = _definition_keys(definition)
    return any("ap_rank" in k or "ap_unranked" in k for k in keys)


def hypothesis_needs_lines(hypothesis: Hypothesis) -> bool:
    return any(definition_needs_lines(d) for d in hypothesis.all_definitions())


def hypothesis_needs_last_game(hypothesis: Hypothesis) -> bool:
    return any(definition_needs_last_game(d) for d in hypothesis.all_definitions())


def hypothesis_needs_ridge(hypothesis: Hypothesis) -> bool:
    return any(definition_needs_ridge(d) for d in hypothesis.all_definitions())


def hypothesis_needs_turnovers(hypothesis: Hypothesis) -> bool:
    return any(definition_needs_turnovers(d) for d in hypothesis.all_definitions())


def hypothesis_needs_ap(hypothesis: Hypothesis) -> bool:
    return any(definition_needs_ap(d) for d in hypothesis.all_definitions())


_BATCH5_BODY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "opp_body_clock_east_early",
        "opp_eastern_pacific_late",
        "outdoor",
        "wind_speed_ms_min",
    }
)
_BATCH5_SA_KEYS: Final[frozenset[str]] = frozenset(
    {
        "team_last_opp_service_academy",
        "opp_last_opp_service_academy",
        "opp_is_service_academy",
    }
)
_BATCH5_LOOKAHEAD_KEYS: Final[frozenset[str]] = frozenset(
    {
        "team_lookahead",
        "opp_lookahead",
        "team_last_lookahead",
        "opp_last_lookahead",
        "team_next_ridge_rank_max",
        "opp_next_ridge_rank_max",
    }
)
_BATCH5_REVENGE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "team_revenge_loss_14",
        "team_rematch_prior_season",
        "team_prior_season_vs_opp_margin_min",
        "team_prior_season_vs_opp_margin_max",
    }
)
_BATCH5_RECORD_KEYS: Final[frozenset[str]] = frozenset(
    {
        "team_season_wins_min",
        "team_season_wins_max",
        "team_season_wins_eq",
        "opp_season_wins_min",
        "opp_season_losses_min",
        "opp_bowl_or_spoiler",
        "weeks_from_season_end_max",
    }
)
_BATCH5_EPA_Q_KEYS: Final[frozenset[str]] = frozenset(
    {"opp_net_epa_l3d_q_max", "opp_off_epa_l3d_q_max", "opp_def_epa_l3d_q_min"}
)
_BATCH5_TOTALS_KEYS: Final[frozenset[str]] = frozenset({"either_last_total_over_21"})
_BATCH5_RETURNING_KEYS: Final[frozenset[str]] = frozenset({"week_min", "week_max"})


def _hyp_keys(hypothesis: Hypothesis) -> set[str]:
    keys: set[str] = set()
    for d in hypothesis.all_definitions():
        keys |= set(d.conditions)
        if d.regression and d.regression.get("regressor"):
            keys.add(str(d.regression["regressor"]))
    return keys


def hypothesis_needs_body_clock(hypothesis: Hypothesis) -> bool:
    return bool(_hyp_keys(hypothesis) & _BATCH5_BODY_KEYS)


def hypothesis_needs_service_academy(hypothesis: Hypothesis) -> bool:
    return bool(_hyp_keys(hypothesis) & _BATCH5_SA_KEYS)


def hypothesis_needs_lookahead(hypothesis: Hypothesis) -> bool:
    return bool(_hyp_keys(hypothesis) & _BATCH5_LOOKAHEAD_KEYS)


def hypothesis_needs_revenge(hypothesis: Hypothesis) -> bool:
    return bool(_hyp_keys(hypothesis) & _BATCH5_REVENGE_KEYS)


def hypothesis_needs_season_records(hypothesis: Hypothesis) -> bool:
    return bool(_hyp_keys(hypothesis) & _BATCH5_RECORD_KEYS)


def hypothesis_needs_epa_quantiles(hypothesis: Hypothesis) -> bool:
    return bool(_hyp_keys(hypothesis) & _BATCH5_EPA_Q_KEYS)


def hypothesis_needs_totals(hypothesis: Hypothesis) -> bool:
    if hypothesis.market == "total":
        return True
    return bool(_hyp_keys(hypothesis) & _BATCH5_TOTALS_KEYS) or any(
        d.statistic in {"mean_total_residual", "mean_under_residual"}
        for d in hypothesis.all_definitions()
    )


def hypothesis_needs_returning(hypothesis: Hypothesis) -> bool:
    keys = _hyp_keys(hypothesis)
    if "team_returning_pctile" in keys or "team_returning_pct" in keys:
        return True
    return any(
        (d.regression or {}).get("regressor") in {"team_returning_pctile", "team_returning_pct"}
        for d in hypothesis.all_definitions()
    )


def hypothesis_needs_this_game_ap(hypothesis: Hypothesis) -> bool:
    keys = _hyp_keys(hypothesis)
    return bool(
        keys
        & {
            "team_ap_ranked",
            "team_ap_unranked",
            "opp_ap_ranked",
            "opp_ap_unranked",
            "team_ap_rank_max",
            "opp_ap_rank_max",
        }
    )


def attach_efficiency_l3d(
    rows: pd.DataFrame,
    features: Mapping[str, pd.DataFrame],
) -> pd.DataFrame:
    """Left-join materialized ``adj_*_epa_l3d`` onto team-game rows (no recompute).

    ``features`` maps feature name → frame with ``entity_id`` (team), ``season``,
    ``week``, ``value``. Joined as ``team_<name>`` / ``opp_<name>`` on
    ``(team_id, season, week)``. Missing store → columns stay absent.
    """
    out = rows.copy()
    if not features:
        return out
    for name, feat in features.items():
        if feat is None or feat.empty:
            continue
        work = feat.copy()
        if "entity_id" in work.columns:
            work = work.rename(columns={"entity_id": "team_id"})
        if "value" in work.columns:
            work = work.rename(columns={"value": name})
        keep = [c for c in ("team_id", "season", "week", name) if c in work.columns]
        if name not in keep:
            continue
        work = work[keep].drop_duplicates(["team_id", "season", "week"], keep="last")
        work["team_id"] = work["team_id"].astype(int)
        work["season"] = work["season"].astype(int)
        work["week"] = work["week"].astype(int)
        team_col = f"team_{name}"
        opp_col = f"opp_{name}"
        team_part = work.rename(columns={name: team_col})
        out = out.merge(team_part, on=["team_id", "season", "week"], how="left")
        opp_part = work.rename(columns={"team_id": "opp_id", name: opp_col})
        out = out.merge(opp_part, on=["opp_id", "season", "week"], how="left")
    return out


__all__ = [
    "CONDITION_KEYS",
    "DEFAULT_ANGLES_CONFIG",
    "LONG_REST_DAYS",
    "LUCK_DEFINITIONS",
    "OUTCOME_COLUMNS",
    "AnglesConfig",
    "AnglesConfigError",
    "ConditionSet",
    "FlaggedFrame",
    "Hypothesis",
    "TeamGameFrame",
    "attach_efficiency_l3d",
    "attach_last_game_luck",
    "attach_market_lines",
    "attach_outcomes",
    "attach_trailing_luck_quantiles",
    "build_schedule_conditions",
    "build_team_game_frame",
    "compute_game_luck",
    "condition_columns",
    "definition_needs_ap",
    "definition_needs_last_game",
    "definition_needs_lines",
    "definition_needs_luck",
    "definition_needs_ridge",
    "definition_needs_turnovers",
    "evaluate_conditions",
    "fbs_ids_by_season",
    "flag_definition",
    "game_as_of",
    "game_end_utc",
    "hypothesis_needs_ap",
    "hypothesis_needs_last_game",
    "hypothesis_needs_lines",
    "hypothesis_needs_luck",
    "hypothesis_needs_ridge",
    "hypothesis_needs_turnovers",
    "kickoff_utc",
    "load_angles_config",
    "qualifying_counts_by_season",
    "RetiredUnopenedError",
    "STATUS_RETIRED_UNOPENED",
    "spread_bucket_label",
    "truncate_games_after",
    "tuesday_as_of_by_week",
    "week_bucket_label",
]
