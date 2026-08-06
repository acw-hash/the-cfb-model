"""Roster / prior-input feature builders (DESIGN §3.1, §3.3, §3.4, §4.5 / §15 item 12).

Preseason-dated features (``event_time`` = Aug 1 of the season UTC) for returning
production, talent, recruiting, portal net, and coaching staff. QB status is
game-team grain with ``event_time`` = manual-entry instant.

Null discipline (non-negotiable)
--------------------------------
Missing values are **null with an ``is_missing`` indicator**, never zero-filled.
Portal features are always null before :data:`PORTAL_ERA_START` (2021), with
``portal_era == 0``; from 2021 onward ``portal_era == 1`` and net rating is null
when no rated transfers are visible as-of (wide-uncertainty regime, §3.4).

OL returning starts
-------------------
Skipped: staged ``rosters`` carries position/year but not career or season
starts, so OL returning starts cannot be derived without a new data source.
Documented here and in ``docs/notes/12.md``.

Point-in-time: builders only use ``history`` rows with ``event_time < as_of``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal

import pandas as pd  # type: ignore[import-untyped]
from omegaconf import OmegaConf

from ncaa_quant.features.builder import (
    AS_OF_COL,
    ENTITY_COL,
    VALUE_COL,
    FeatureBuilder,
    FeatureBuildError,
)
from ncaa_quant.features.registry import FeatureSpec
from ncaa_quant.utils.timeutils import assert_tz_aware, to_utc

PORTAL_ERA_START: Final[int] = 2021
PRESEASON_MONTH: Final[int] = 8
PRESEASON_DAY: Final[int] = 1

# Weights for recruiting seasons [S-3, S-2, S-1, S] (oldest → newest).
DEFAULT_RECRUITING_WEIGHTS: Final[tuple[float, float, float, float]] = (0.1, 0.2, 0.3, 0.4)

DEFAULT_COORDINATORS_PATH: Final[str] = "configs/coordinators.yaml"
QB_STATUS_TABLE: Final[str] = "qb_status"
QB_STATUSES: Final[frozenset[str]] = frozenset({"starter", "backup", "unknown"})

# Numeric encoding for trees: starter=1, backup=0, unknown/missing → null.
QB_STATUS_VALUE: Final[Mapping[str, float | None]] = {
    "starter": 1.0,
    "backup": 0.0,
    "unknown": None,
}

RosterFeature = Literal[
    "returning_offense_pct",
    "returning_defense_pct",
    "talent_composite",
    "blue_chip_ratio",
    "recruiting_4yr_weighted",
    "portal_net_rating",
    "portal_era",
    "hc_tenure_years",
    "new_hc_flag",
    "oc_tenure_years",
    "dc_tenure_years",
    "oc_change_flag",
    "dc_change_flag",
    "qb_status",
]

_TEAM_SEASON_FEATURES: Final[frozenset[str]] = frozenset(
    {
        "returning_offense_pct",
        "returning_defense_pct",
        "talent_composite",
        "blue_chip_ratio",
        "recruiting_4yr_weighted",
        "portal_net_rating",
        "portal_era",
        "hc_tenure_years",
        "new_hc_flag",
        "oc_tenure_years",
        "dc_tenure_years",
        "oc_change_flag",
        "dc_change_flag",
    }
)

_ALL_ROSTER_FEATURES: Final[frozenset[str]] = _TEAM_SEASON_FEATURES | {"qb_status"}

_FEATURE_NAME_RE = re.compile(r"^(?P<name>" + "|".join(sorted(_ALL_ROSTER_FEATURES)) + r")$")


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def preseason_event_time(season: int) -> datetime:
    """Documented preseason instant: Aug 1 of ``season`` at 00:00 UTC."""
    return datetime(season, PRESEASON_MONTH, PRESEASON_DAY, tzinfo=UTC)


def is_portal_era(season: int) -> bool:
    """True when transfer-portal data is in-regime (``season >= 2021``)."""
    return season >= PORTAL_ERA_START


def weighted_recruiting_composite(
    points_by_season: Mapping[int, float | None],
    season: int,
    weights: Sequence[float] = DEFAULT_RECRUITING_WEIGHTS,
) -> float:
    """4-year weighted recruiting points for ``season``.

    Uses class years ``[season-3, season-2, season-1, season]`` with
    ``weights`` (oldest → newest). Missing years are dropped and remaining
    weights are renormalized. Returns NaN when no year has points.

    Units: CFBD recruiting ``points`` (same scale as staged ``recruiting``).
    """
    if len(weights) != 4:
        msg = f"recruiting weights must have length 4, got {len(weights)}"
        raise ValueError(msg)
    years = [season - 3, season - 2, season - 1, season]
    num = 0.0
    den = 0.0
    for year, weight in zip(years, weights, strict=True):
        raw = points_by_season.get(year)
        if raw is None:
            continue
        if isinstance(raw, float) and raw != raw:  # NaN
            continue
        w = float(weight)
        num += w * float(raw)
        den += w
    if den <= 0.0:
        return float("nan")
    return float(num / den)


def portal_net_rating(
    portal: pd.DataFrame,
    *,
    team_id: int,
    season: int,
    as_of: datetime,
) -> float:
    """Inbound minus outbound portal rating sum for ``team_id`` in ``season``.

    Pre-2021 always returns NaN (caller sets ``portal_era``). Within the portal
    era, returns NaN when no rated transfer touching the team is visible with
    ``event_time < as_of`` — never coerced to 0.0.
    """
    assert_tz_aware(as_of)
    if not is_portal_era(season):
        return float("nan")
    if portal.empty:
        return float("nan")

    bound = pd.Timestamp(to_utc(as_of))
    frame = portal.copy()
    if "event_time" in frame.columns:
        ts = pd.to_datetime(frame["event_time"], utc=True)
        frame = frame.loc[ts < bound]
    if "season" in frame.columns:
        frame = frame.loc[frame["season"] == season]
    if frame.empty or "rating" not in frame.columns:
        return float("nan")

    rated = frame.loc[frame["rating"].notna()]
    if rated.empty:
        return float("nan")

    inbound = rated.loc[rated["dest_team_id"] == team_id, "rating"].sum()
    outbound = rated.loc[rated["origin_team_id"] == team_id, "rating"].sum()
    touched = (rated["dest_team_id"] == team_id) | (rated["origin_team_id"] == team_id)
    if not bool(touched.any()):
        return float("nan")
    return float(inbound) - float(outbound)


def hc_tenure_years(coaches: pd.DataFrame, *, team_id: int, season: int) -> float:
    """Consecutive seasons ending at ``season`` with the same HC name.

    Identity is ``(first_name, last_name)`` on ``team_id``. Returns NaN when
    no coach row exists for ``(team_id, season)``.
    """
    if coaches.empty:
        return float("nan")
    frame = coaches.loc[coaches["team_id"] == team_id].copy()
    if frame.empty:
        return float("nan")
    frame = frame.sort_values("season")
    current = frame.loc[frame["season"] == season]
    if current.empty:
        return float("nan")
    row = current.iloc[-1]
    name = (_norm_name(row["first_name"]), _norm_name(row["last_name"]))
    tenure = 0
    for year in range(season, season - 50, -1):
        year_rows = frame.loc[frame["season"] == year]
        if year_rows.empty:
            break
        y = year_rows.iloc[-1]
        if (_norm_name(y["first_name"]), _norm_name(y["last_name"])) != name:
            break
        tenure += 1
    return float(tenure) if tenure > 0 else float("nan")


def _norm_name(value: Any) -> str:
    if value is None or (isinstance(value, float) and value != value):
        return ""
    return str(value).strip().casefold()


# ---------------------------------------------------------------------------
# Coordinators config
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CoordinatorSeason:
    """One school-season OC/DC staff row."""

    school: str
    season: int
    oc: str | None
    dc: str | None


def load_coordinators(path: Path | str | None = None) -> tuple[CoordinatorSeason, ...]:
    """Load hand-maintained OC/DC rows from YAML.

    Expected shape::

        coordinators:
          Alabama:
            2023: {oc: Tommy Rees, dc: Kevin Steele}
            2024: {oc: Glenn Schumann, dc: Kane Wommack}
    """
    cfg_path = Path(path) if path is not None else Path(DEFAULT_COORDINATORS_PATH)
    if not cfg_path.is_file():
        msg = f"coordinators config not found: {cfg_path}"
        raise FileNotFoundError(msg)
    loaded = OmegaConf.to_container(OmegaConf.load(cfg_path), resolve=True) or {}
    if not isinstance(loaded, dict):
        msg = "coordinators YAML root must be a mapping"
        raise ValueError(msg)
    raw = loaded.get("coordinators", loaded)
    if not isinstance(raw, dict):
        msg = "coordinators YAML must contain a mapping under 'coordinators'"
        raise ValueError(msg)
    rows: list[CoordinatorSeason] = []
    for school, seasons in raw.items():
        if not isinstance(seasons, dict):
            msg = f"coordinators[{school!r}] must be a season mapping"
            raise ValueError(msg)
        for season_key, staff in seasons.items():
            season = int(season_key)
            if not isinstance(staff, dict):
                msg = f"coordinators[{school!r}][{season}] must be a mapping"
                raise ValueError(msg)
            oc_raw = staff.get("oc")
            dc_raw = staff.get("dc")
            oc = str(oc_raw).strip() if oc_raw is not None else None
            dc = str(dc_raw).strip() if dc_raw is not None else None
            if oc == "":
                oc = None
            if dc == "":
                dc = None
            rows.append(CoordinatorSeason(school=str(school), season=season, oc=oc, dc=dc))
    return tuple(rows)


def coordinator_tenure_and_change(
    rows: Sequence[CoordinatorSeason],
    *,
    school: str,
    season: int,
    role: Literal["oc", "dc"],
) -> tuple[float, float]:
    """Return ``(tenure_years, change_flag)`` for ``role`` at ``school``/``season``.

    Both are NaN when the school-season role is missing from the config
    (null-with-indicator downstream — never zero-filled).
    """
    by_season: dict[int, str | None] = {}
    for row in rows:
        if row.school != school:
            continue
        by_season[row.season] = row.oc if role == "oc" else row.dc
    if season not in by_season:
        return float("nan"), float("nan")
    current = by_season[season]
    if current is None:
        return float("nan"), float("nan")
    tenure = 0
    for year in range(season, season - 50, -1):
        if year not in by_season or by_season[year] != current:
            break
        tenure += 1
    prev = by_season.get(season - 1)
    # First observed season counts as a change/new coordinator.
    change = 1.0 if prev is None or prev != current else 0.0
    return float(tenure), change


# ---------------------------------------------------------------------------
# QB status table + CLI helpers
# ---------------------------------------------------------------------------


def qb_entity_id(game_id: int, team_id: int) -> str:
    """Stable entity key for game-team QB status features."""
    return f"{int(game_id)}:{int(team_id)}"


def parse_qb_entity_id(entity_id: str) -> tuple[int, int]:
    """Parse ``game_id:team_id`` entity key."""
    parts = str(entity_id).split(":")
    if len(parts) != 2:
        msg = f"qb entity_id must be 'game_id:team_id', got {entity_id!r}"
        raise ValueError(msg)
    return int(parts[0]), int(parts[1])


def encode_qb_status(status: str) -> float:
    """Map status label to float; ``unknown`` → NaN."""
    key = status.strip().casefold()
    if key not in QB_STATUSES:
        msg = f"status must be one of {sorted(QB_STATUSES)}, got {status!r}"
        raise ValueError(msg)
    value = QB_STATUS_VALUE[key]
    return float("nan") if value is None else float(value)


def set_qb_status(
    store: Any,
    *,
    game_id: int,
    team_id: int,
    status: str,
    event_time: datetime | None = None,
    ingested_at: datetime | None = None,
    source_version: str = "manual_v1",
) -> pd.DataFrame:
    """Append a versioned QB-status row to the staged ``qb_status`` table.

    ``event_time`` defaults to now (UTC) — the instant the status became known.
    Multiple rows per (game, team) are retained; as-of reads take the latest
    with ``event_time < as_of``.
    """
    key = status.strip().casefold()
    if key not in QB_STATUSES:
        msg = f"status must be one of {sorted(QB_STATUSES)}, got {status!r}"
        raise ValueError(msg)
    now = datetime.now(tz=UTC)
    event = to_utc(event_time) if event_time is not None else now
    ingested = to_utc(ingested_at) if ingested_at is not None else now
    assert_tz_aware(event)
    assert_tz_aware(ingested)

    games = store.read("games", filters={"game_id": game_id})
    if games.empty:
        msg = f"game_id {game_id} not found in staged games"
        raise ValueError(msg)
    season = int(games.iloc[0]["season"])
    home = int(games.iloc[0]["home_team_id"])
    away = int(games.iloc[0]["away_team_id"])
    if int(team_id) not in {home, away}:
        msg = f"team_id {team_id} is not a participant in game {game_id}"
        raise ValueError(msg)

    row = pd.DataFrame(
        [
            {
                "game_id": int(game_id),
                "team_id": int(team_id),
                "season": season,
                "status": key,
                "source_version": source_version,
                "event_time": event,
                "ingested_at": ingested,
            }
        ]
    )
    store.write_partition(
        QB_STATUS_TABLE,
        row,
        {"season": season},
        mode="append",
    )
    return row


def scrape_depth_chart_qb_status(
    *,
    season: int,
    week: int | None = None,
) -> pd.DataFrame:
    """Depth-chart scrape stub for prospective QB starter detection.

    v1 does **not** scrape. A future implementation would:

    1. Pull weekly depth charts from a fragile public source (e.g. ESPN
       unofficial endpoints or school athletics pages).
    2. Identify the listed QB1 per FBS team.
    3. Write ``starter`` / ``backup`` / ``unknown`` rows into ``qb_status``
       with ``event_time`` = scrape instant (UTC), never backfilling historical
       injury lists that do not exist (§3.4).

    Historical CFB injury feeds are incomplete and loss-biased; QB status is
    collected prospectively only.

    Raises
    ------
    NotImplementedError
        Always — manual ``ncaa-quant roster set-qb`` is the v1 path.
    """
    msg = (
        f"depth-chart scrape is not implemented (season={season}, week={week}); "
        "use `ncaa-quant roster set-qb` for prospective QB status"
    )
    raise NotImplementedError(msg)


def resolve_team_id(
    team: str,
    teams: pd.DataFrame,
    *,
    season: int | None = None,
) -> int:
    """Resolve ``team`` as numeric id or school name within ``teams``."""
    stripped = team.strip()
    if stripped.isdigit():
        return int(stripped)
    frame = teams
    if season is not None and "season" in frame.columns:
        frame = frame.loc[frame["season"] == season]
    matches = frame.loc[frame["school"].astype(str).str.casefold() == stripped.casefold()]
    if matches.empty:
        msg = f"team {team!r} not found in staged teams"
        raise ValueError(msg)
    return int(matches.iloc[0]["team_id"])


# ---------------------------------------------------------------------------
# History frame builders
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RosterConfig:
    """Injectable knobs for roster / prior builders."""

    recruiting_weights: tuple[float, float, float, float] = DEFAULT_RECRUITING_WEIGHTS
    coordinators_path: str = DEFAULT_COORDINATORS_PATH


def build_roster_frame(
    *,
    teams: pd.DataFrame,
    returning: pd.DataFrame,
    talent: pd.DataFrame,
    recruiting: pd.DataFrame,
    portal: pd.DataFrame,
    coaches: pd.DataFrame,
    seasons: Sequence[int],
    config: RosterConfig | None = None,
    coordinators: Sequence[CoordinatorSeason] | None = None,
) -> pd.DataFrame:
    """Build team-season roster/prior feature rows (preseason-dated).

    Each row has ``team_id``, ``season``, ``event_time`` (Aug 1 UTC), and one
    column per team-season roster feature. Missing source facts stay as NaN —
    never zero-filled.
    """
    cfg = config or RosterConfig()
    coord_rows = (
        tuple(coordinators)
        if coordinators is not None
        else load_coordinators(cfg.coordinators_path)
    )
    school_by_team: dict[tuple[int, int], str] = {}
    if not teams.empty:
        for r in teams.itertuples(index=False):
            school_by_team[(int(r.team_id), int(r.season))] = str(r.school)

    team_ids = sorted({int(t) for t in teams["team_id"].unique()}) if not teams.empty else []
    rows: list[dict[str, Any]] = []

    recruiting_points: dict[tuple[int, int], float] = {}
    blue_chip_by_team_season: dict[tuple[int, int], float] = {}
    if not recruiting.empty:
        for r in recruiting.itertuples(index=False):
            tid, yr = int(r.team_id), int(r.season)
            pts = getattr(r, "points", None)
            if pts is not None and not (isinstance(pts, float) and pts != pts):
                recruiting_points[(tid, yr)] = float(pts)
            blue_raw = getattr(r, "blue_chip_ratio", None)
            if blue_raw is not None and not (isinstance(blue_raw, float) and blue_raw != blue_raw):
                blue_chip_by_team_season[(tid, yr)] = float(blue_raw)

    returning_idx = _index_team_season(returning)
    talent_idx = _index_team_season(talent)
    coaches_by_team = coaches if not coaches.empty else pd.DataFrame()

    for season in seasons:
        event = preseason_event_time(int(season))
        # Portal as-of for preseason features: include transfers known by Aug 1.
        portal_as_of = event
        for team_id in team_ids:
            key = (team_id, int(season))
            ret = returning_idx.get(key, {})
            tal = talent_idx.get(key, {})

            points_map = {
                y: recruiting_points.get((team_id, y))
                for y in (int(season) - 3, int(season) - 2, int(season) - 1, int(season))
            }
            rec4 = weighted_recruiting_composite(
                points_map, int(season), weights=cfg.recruiting_weights
            )
            blue = _optional_float(blue_chip_by_team_season.get(key))
            talent_val = _optional_float(tal.get("talent"))
            off_pct = _optional_float(ret.get("offense_pct"))
            def_pct = _optional_float(ret.get("defense_pct"))

            portal_era = 1.0 if is_portal_era(int(season)) else 0.0
            portal_net = portal_net_rating(
                portal, team_id=team_id, season=int(season), as_of=portal_as_of
            )

            tenure = hc_tenure_years(coaches_by_team, team_id=team_id, season=int(season))
            new_hc = float("nan") if tenure != tenure else float(tenure <= 1.0)

            school = school_by_team.get(key) or school_by_team.get((team_id, int(season)))
            if school is None:
                # Fall back to any season's school label for this team_id.
                for (tid, _), name in school_by_team.items():
                    if tid == team_id:
                        school = name
                        break
            oc_ten, oc_chg = (float("nan"), float("nan"))
            dc_ten, dc_chg = (float("nan"), float("nan"))
            if school is not None:
                oc_ten, oc_chg = coordinator_tenure_and_change(
                    coord_rows, school=school, season=int(season), role="oc"
                )
                dc_ten, dc_chg = coordinator_tenure_and_change(
                    coord_rows, school=school, season=int(season), role="dc"
                )

            rows.append(
                {
                    "team_id": team_id,
                    "season": int(season),
                    "event_time": event,
                    "returning_offense_pct": off_pct,
                    "returning_defense_pct": def_pct,
                    "talent_composite": talent_val,
                    "blue_chip_ratio": blue,
                    "recruiting_4yr_weighted": rec4,
                    "portal_net_rating": portal_net,
                    "portal_era": portal_era,
                    "hc_tenure_years": tenure,
                    "new_hc_flag": new_hc,
                    "oc_tenure_years": oc_ten,
                    "dc_tenure_years": dc_ten,
                    "oc_change_flag": oc_chg,
                    "dc_change_flag": dc_chg,
                }
            )
    return pd.DataFrame(rows)


def build_qb_status_frame(qb_status: pd.DataFrame) -> pd.DataFrame:
    """Normalize QB-status history for the feature builder.

    Output columns: ``entity_id`` (``game_id:team_id``), ``event_time``,
    ``qb_status`` (float encoding).
    """
    if qb_status.empty:
        return pd.DataFrame(columns=["entity_id", "event_time", "qb_status"])
    rows: list[dict[str, Any]] = []
    for r in qb_status.itertuples(index=False):
        rows.append(
            {
                "entity_id": qb_entity_id(int(r.game_id), int(r.team_id)),
                "event_time": pd.Timestamp(r.event_time).to_pydatetime(),
                "qb_status": encode_qb_status(str(r.status)),
            }
        )
    return pd.DataFrame(rows)


def _index_team_season(frame: pd.DataFrame) -> dict[tuple[int, int], dict[str, Any]]:
    out: dict[tuple[int, int], dict[str, Any]] = {}
    if frame.empty:
        return out
    for r in frame.itertuples(index=False):
        out[(int(r.team_id), int(r.season))] = r._asdict()
    return out


def _optional_float(value: Any) -> float:
    if value is None:
        return float("nan")
    if isinstance(value, float) and value != value:
        return float("nan")
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def assert_no_zero_fill(value: float, *, is_missing_source: bool) -> None:
    """Raise if a missing source was coerced to 0.0 (test / audit helper)."""
    if is_missing_source and value == 0.0:
        msg = "zero-fill forbidden: missing roster/prior source became 0.0"
        raise AssertionError(msg)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RosterFeatureCard:
    """Parsed roster feature name."""

    name: RosterFeature


def parse_roster_feature_name(name: str) -> RosterFeatureCard:
    """Parse a registered roster / prior feature name."""
    match = _FEATURE_NAME_RE.match(name)
    if match is None:
        msg = f"unsupported roster feature name: {name!r}"
        raise FeatureBuildError(msg)
    return RosterFeatureCard(name=match.group("name"))  # type: ignore[arg-type]


class RosterFeatureBuilder(FeatureBuilder):
    """Team-season or game-team roster/prior feature; PIT via ``history``.

    For team-season features, ``entity_id`` is ``team_id`` and ``history`` is
    the frame from :func:`build_roster_frame`. For ``qb_status``, ``entity_id``
    is ``game_id:team_id`` and ``history`` is from :func:`build_qb_status_frame`.
    """

    def __init__(self, spec: FeatureSpec, history: pd.DataFrame) -> None:
        super().__init__(spec)
        self.history = history
        self.card = parse_roster_feature_name(spec.name)

    def compute(self, entity_ids: Sequence[Any], as_of: datetime) -> pd.DataFrame:
        eligible = self.filter_event_time(self.history, as_of)
        col = self.card.name
        rows: list[dict[str, Any]] = []

        if col == "qb_status":
            by_entity = _latest_by_entity(eligible, col)
            for eid in entity_ids:
                key = str(eid)
                raw = by_entity.get(key, float("nan"))
                missing = raw != raw
                row: dict[str, Any] = {
                    ENTITY_COL: key,
                    AS_OF_COL: as_of,
                    VALUE_COL: raw,
                }
                if self.spec.null_policy == "indicator":
                    row["is_missing"] = bool(missing)
                rows.append(row)
            return pd.DataFrame(rows)

        # Team-season: take latest eligible row per team_id (preseason-dated).
        by_team: dict[Any, float] = {}
        if not eligible.empty and col in eligible.columns and "team_id" in eligible.columns:
            ordered = eligible.sort_values("event_time")
            for r in ordered.itertuples(index=False):
                by_team[int(r.team_id)] = float(getattr(r, col))

        for eid in entity_ids:
            tid = int(eid)
            raw = by_team.get(tid, float("nan"))
            missing = raw != raw
            # portal_era is always defined when any season row exists; if the
            # team has no history row at all, still emit 0/1 from as_of year?
            # Prefer history; if absent, null only when indicator policy.
            out_row: dict[str, Any] = {
                ENTITY_COL: tid,
                AS_OF_COL: as_of,
                VALUE_COL: raw,
            }
            if self.spec.null_policy == "indicator":
                out_row["is_missing"] = bool(missing)
            rows.append(out_row)
        return pd.DataFrame(rows)


def _latest_by_entity(eligible: pd.DataFrame, col: str) -> dict[str, float]:
    out: dict[str, float] = {}
    if eligible.empty or col not in eligible.columns:
        return out
    ordered = eligible.sort_values("event_time")
    for r in ordered.itertuples(index=False):
        out[str(r.entity_id)] = float(getattr(r, col))
    return out
