"""Situational feature builders (DESIGN §4.5 / §15 item 11).

Rest, travel, timezone, altitude, surface, calendar, conference, and rivalry
flags. Entity id is ``game_id`` (matchup-level features).

Point-in-time: builders only use ``history`` rows with ``event_time < as_of``.
Schedule facts for the *target* game may equal the decision ``as_of`` only when
the materializer sets ``event_time`` to kickoff and queries with ``as_of`` after
the feature is knowable (pre-kickoff). For rest/rivalry lookahead, prior and
future games are read from the injected schedule with the same strict cut on
the history used for *outcomes*; schedule rows for computing rest use games
with ``event_time <`` target kickoff for prior games and the full season
schedule frame for lookahead (lookahead is a known scheduled future contest,
not an outcome).
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final, Literal
from zoneinfo import ZoneInfo

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

# Earth mean radius (km) for haversine.
EARTH_RADIUS_KM: Final[float] = 6371.0

# Rest thresholds (calendar days between kickoffs).
SHORT_WEEK_DAYS: Final[float] = 6.0
BYE_DAYS: Final[float] = 13.0

DEFAULT_RIVALRIES_PATH: Final[str] = "configs/rivalries.yaml"

SituationalFeature = Literal[
    "rest_days_diff",
    "rest_days_home",
    "rest_days_away",
    "short_week_flag",
    "bye_flag",
    "travel_km",
    "tz_crossed",
    "altitude_delta_m",
    "surface_turf",
    "surface_change_flag",
    "neutral_site",
    "week_number",
    "month",
    "conference_game",
    "rivalry_flag",
    "post_rivalry_flag",
    "rivalry_lookahead_flag",
]

_SITUATIONAL_NAMES: Final[frozenset[str]] = frozenset(
    {
        "rest_days_diff",
        "rest_days_home",
        "rest_days_away",
        "short_week_flag",
        "bye_flag",
        "travel_km",
        "tz_crossed",
        "altitude_delta_m",
        "surface_turf",
        "surface_change_flag",
        "neutral_site",
        "week_number",
        "month",
        "conference_game",
        "rivalry_flag",
        "post_rivalry_flag",
        "rivalry_lookahead_flag",
    }
)

_FEATURE_NAME_RE = re.compile(r"^(?P<name>" + "|".join(sorted(_SITUATIONAL_NAMES)) + r")$")


# ---------------------------------------------------------------------------
# Geo / timezone pure helpers
# ---------------------------------------------------------------------------


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres between two WGS84 points.

    Units: degrees in, kilometres out. Uses mean Earth radius
    :data:`EARTH_RADIUS_KM`.
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    return float(2.0 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a))))


def timezone_offset_hours(tz_name: str, at: datetime) -> float:
    """UTC offset of ``tz_name`` at ``at`` in hours (e.g. America/New_York → −4/−5)."""
    if at.tzinfo is None:
        msg = "NAIVE-DATETIME-FORBIDDEN: timezone_offset_hours requires tz-aware at"
        raise ValueError(msg)
    aware = at.astimezone(ZoneInfo(tz_name))
    offset = aware.utcoffset()
    if offset is None:
        return 0.0
    return float(offset.total_seconds() / 3600.0)


def tz_crossed_signed(
    origin_tz: str,
    destination_tz: str,
    *,
    at: datetime,
) -> float:
    """Signed timezone hours crossed traveling origin → destination.

    Positive means clocks jump ahead (eastward travel); negative is westward.
    Example: Pacific → Eastern at a November kickoff ≈ +3.0.
    """
    return timezone_offset_hours(destination_tz, at) - timezone_offset_hours(origin_tz, at)


def rest_days_between(previous_kickoff: datetime, kickoff: datetime) -> float:
    """Days of rest between consecutive kickoffs (fractional, 24h basis).

    ``rest_days = (kickoff − previous_kickoff).total_seconds() / 86400``.
    First game of season → NaN (caller treats as bye-equivalent only when
    explicitly flagged via week gap; we leave NaN).
    """
    if previous_kickoff.tzinfo is None or kickoff.tzinfo is None:
        msg = "NAIVE-DATETIME-FORBIDDEN: rest_days_between requires tz-aware datetimes"
        raise ValueError(msg)
    delta = (kickoff - previous_kickoff).total_seconds() / 86400.0
    return float(delta)


def is_short_week(rest_days: float) -> bool:
    """True when rest is strictly less than :data:`SHORT_WEEK_DAYS`."""
    return not math.isnan(rest_days) and rest_days < SHORT_WEEK_DAYS


def is_bye(rest_days: float) -> bool:
    """True when rest is at least :data:`BYE_DAYS` (≈ week+ off)."""
    return not math.isnan(rest_days) and rest_days >= BYE_DAYS


# ---------------------------------------------------------------------------
# Rivalry config
# ---------------------------------------------------------------------------


def load_rivalry_pairs(path: Path | str | None = None) -> frozenset[frozenset[str]]:
    """Load unordered school-name rivalry pairs from YAML.

    Expected shape::

        rivalries:
          - [Michigan, Ohio State]
          - [Alabama, Auburn]
    """
    rivalries_path = Path(path) if path is not None else Path(DEFAULT_RIVALRIES_PATH)
    if not rivalries_path.is_file():
        msg = f"rivalries config not found: {rivalries_path}"
        raise FileNotFoundError(msg)
    loaded = OmegaConf.to_container(OmegaConf.load(rivalries_path), resolve=True) or {}
    if not isinstance(loaded, dict):
        msg = "rivalries YAML root must be a mapping"
        raise ValueError(msg)
    raw = loaded.get("rivalries", loaded)
    if not isinstance(raw, list):
        msg = "rivalries YAML must contain a list under 'rivalries'"
        raise ValueError(msg)
    pairs: set[frozenset[str]] = set()
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            msg = f"each rivalry must be a 2-item list, got {item!r}"
            raise ValueError(msg)
        a, b = str(item[0]).strip(), str(item[1]).strip()
        if not a or not b or a == b:
            msg = f"invalid rivalry pair: {item!r}"
            raise ValueError(msg)
        pairs.add(frozenset({a, b}))
    return frozenset(pairs)


def is_rivalry_pair(
    school_a: str,
    school_b: str,
    pairs: Mapping[frozenset[str], Any] | frozenset[frozenset[str]],
) -> bool:
    """True when ``{school_a, school_b}`` is a configured rivalry."""
    key = frozenset({school_a, school_b})
    return key in pairs


# ---------------------------------------------------------------------------
# Venue / schedule helpers
# ---------------------------------------------------------------------------


def _finite_float(value: object) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        out = float(value)
        return None if math.isnan(out) else out
    if isinstance(value, str):
        try:
            out = float(value)
        except ValueError:
            return None
        return None if math.isnan(out) else out
    return None


def team_home_venues(games: pd.DataFrame, venues: pd.DataFrame) -> pd.DataFrame:
    """Modal non-neutral home venue per ``team_id`` with lat/lon/elev/tz/surface.

    Uses the most frequent ``venue_id`` among completed home (non-neutral) games.
    """
    if games.empty:
        return pd.DataFrame(
            columns=[
                "team_id",
                "venue_id",
                "latitude",
                "longitude",
                "elevation_m",
                "timezone",
                "surface",
            ]
        )
    home = games.loc[~games["neutral_site"].fillna(False)].dropna(subset=["venue_id"]).copy()
    if home.empty:
        return pd.DataFrame(
            columns=[
                "team_id",
                "venue_id",
                "latitude",
                "longitude",
                "elevation_m",
                "timezone",
                "surface",
            ]
        )
    home["team_id"] = home["home_team_id"]
    mode = (
        home.groupby(["team_id", "venue_id"], sort=False)
        .size()
        .rename("n")
        .reset_index()
        .sort_values(["team_id", "n"], ascending=[True, False], kind="mergesort")
        .drop_duplicates("team_id", keep="first")
    )
    vcols = ["venue_id", "latitude", "longitude", "elevation_m", "timezone", "surface"]
    present = [c for c in vcols if c in venues.columns]
    v = venues[present].drop_duplicates("venue_id")
    return mode.merge(v, on="venue_id", how="left")


def _kickoff_series(games: pd.DataFrame) -> pd.Series:
    if "event_time" in games.columns:
        return pd.to_datetime(games["event_time"], utc=True)
    return pd.to_datetime(games["start_date"], utc=True)


def _school_map(teams: pd.DataFrame) -> dict[Any, str]:
    if teams.empty or "school" not in teams.columns:
        return {}
    return {row["team_id"]: str(row["school"]) for _, row in teams.iterrows()}


def build_situational_frame(
    games: pd.DataFrame,
    venues: pd.DataFrame,
    teams: pd.DataFrame,
    *,
    rivalry_pairs: frozenset[frozenset[str]] | None = None,
    rivalries_path: Path | str | None = None,
) -> pd.DataFrame:
    """Materialize one situational feature row per game.

    Output columns include every :data:`SituationalFeature` name plus
    ``game_id`` / ``event_time``. Rest uses prior kickoffs for the same team;
    post-rivalry looks at the immediately previous contest; lookahead at the
    next scheduled contest in the same season (schedule knowledge, not outcome).
    """
    if games.empty:
        cols = ["game_id", "event_time", *sorted(_SITUATIONAL_NAMES)]
        return pd.DataFrame(columns=cols)

    pairs = rivalry_pairs if rivalry_pairs is not None else load_rivalry_pairs(rivalries_path)
    schools = _school_map(teams)
    home_venues = team_home_venues(games, venues)
    home_by_team = {int(r.team_id): r for r in home_venues.itertuples(index=False)}

    venue_by_id: dict[int, Any] = {}
    if not venues.empty:
        for r in venues.drop_duplicates("venue_id").itertuples(index=False):
            venue_by_id[int(r.venue_id)] = r

    work = games.copy()
    work["event_time"] = _kickoff_series(work)
    work = work.sort_values("event_time", kind="mergesort").reset_index(drop=True)

    # Per-team chronological game lists for rest / rivalry flags.
    team_games: dict[int, list[dict[str, Any]]] = {}
    for r in work.itertuples(index=False):
        gid = int(r.game_id)
        kick = pd.Timestamp(r.event_time).to_pydatetime()
        home_id = int(r.home_team_id)
        away_id = int(r.away_team_id)
        home_school = schools.get(home_id, "")
        away_school = schools.get(away_id, "")
        riv = bool(home_school and away_school and is_rivalry_pair(home_school, away_school, pairs))
        entry = {
            "game_id": gid,
            "event_time": kick,
            "home_team_id": home_id,
            "away_team_id": away_id,
            "rivalry": riv,
            "home_school": home_school,
            "away_school": away_school,
        }
        for tid in (home_id, away_id):
            team_games.setdefault(tid, []).append(entry)

    rows: list[dict[str, Any]] = []
    for r in work.itertuples(index=False):
        gid = int(r.game_id)
        kick = pd.Timestamp(r.event_time).to_pydatetime()
        home_id = int(r.home_team_id)
        away_id = int(r.away_team_id)
        neutral = bool(r.neutral_site) if pd.notna(r.neutral_site) else False
        conf = bool(r.conference_game) if pd.notna(getattr(r, "conference_game", None)) else False
        week = int(r.week) if pd.notna(getattr(r, "week", None)) else float("nan")
        month = float(kick.month)

        rest_home = _prior_rest(team_games.get(home_id, []), gid, kick)
        rest_away = _prior_rest(team_games.get(away_id, []), gid, kick)
        rest_diff = (
            rest_home - rest_away
            if not math.isnan(rest_home) and not math.isnan(rest_away)
            else float("nan")
        )

        short = float(is_short_week(rest_home) or is_short_week(rest_away))
        bye = float(is_bye(rest_home) or is_bye(rest_away))

        game_venue = None
        if pd.notna(getattr(r, "venue_id", None)):
            game_venue = venue_by_id.get(int(r.venue_id))

        away_home = home_by_team.get(away_id)
        home_home = home_by_team.get(home_id)

        travel = float("nan")
        tz_cross = float("nan")
        alt_delta = float("nan")
        surface_turf = float("nan")
        surface_change = float("nan")

        if game_venue is not None:
            g_lat = getattr(game_venue, "latitude", None)
            g_lon = getattr(game_venue, "longitude", None)
            g_elev = getattr(game_venue, "elevation_m", None)
            g_tz = getattr(game_venue, "timezone", None)
            g_surf = getattr(game_venue, "surface", None)
            if g_surf is not None and not (isinstance(g_surf, float) and math.isnan(g_surf)):
                surface_turf = 1.0 if str(g_surf).casefold() in {"turf", "artificial"} else 0.0

            # Travel / tz: away team from its home venue → game venue.
            # Neutral site: average of both teams' travel from home venues.
            if neutral:
                dists: list[float] = []
                tzs: list[float] = []
                alts: list[float] = []
                for th in (home_home, away_home):
                    if th is None:
                        continue
                    has_coords = (
                        pd.notna(th.latitude)
                        and pd.notna(th.longitude)
                        and pd.notna(g_lat)
                        and pd.notna(g_lon)
                    )
                    if has_coords:
                        lat1 = _finite_float(th.latitude)
                        lon1 = _finite_float(th.longitude)
                        lat2 = _finite_float(g_lat)
                        lon2 = _finite_float(g_lon)
                        if (
                            lat1 is not None
                            and lon1 is not None
                            and lat2 is not None
                            and lon2 is not None
                        ):
                            dists.append(haversine_km(lat1, lon1, lat2, lon2))
                    if (
                        th.timezone
                        and g_tz
                        and not (isinstance(th.timezone, float) and math.isnan(th.timezone))
                    ):
                        tzs.append(tz_crossed_signed(str(th.timezone), str(g_tz), at=kick))
                    elev_home = _finite_float(th.elevation_m)
                    elev_game = _finite_float(g_elev)
                    if elev_home is not None and elev_game is not None:
                        alts.append(elev_game - elev_home)
                if dists:
                    travel = float(sum(dists) / len(dists))
                if tzs:
                    tz_cross = float(sum(tzs) / len(tzs))
                if alts:
                    alt_delta = float(sum(alts) / len(alts))
            elif away_home is not None:
                lat1 = _finite_float(away_home.latitude)
                lon1 = _finite_float(away_home.longitude)
                lat2 = _finite_float(g_lat)
                lon2 = _finite_float(g_lon)
                if lat1 is not None and lon1 is not None and lat2 is not None and lon2 is not None:
                    travel = haversine_km(lat1, lon1, lat2, lon2)
                if (
                    away_home.timezone
                    and g_tz
                    and not (
                        isinstance(away_home.timezone, float) and math.isnan(away_home.timezone)
                    )
                ):
                    tz_cross = tz_crossed_signed(str(away_home.timezone), str(g_tz), at=kick)
                elev_home = _finite_float(away_home.elevation_m)
                elev_game = _finite_float(g_elev)
                if elev_home is not None and elev_game is not None:
                    alt_delta = elev_game - elev_home
                if (
                    away_home.surface is not None
                    and g_surf is not None
                    and not (isinstance(g_surf, float) and math.isnan(g_surf))
                ):
                    surface_change = float(
                        str(away_home.surface).casefold() != str(g_surf).casefold()
                    )

        home_school = schools.get(home_id, "")
        away_school = schools.get(away_id, "")
        rivalry = float(
            bool(home_school and away_school and is_rivalry_pair(home_school, away_school, pairs))
        )
        post_riv = float(
            _prior_was_rivalry(team_games.get(home_id, []), gid)
            or _prior_was_rivalry(team_games.get(away_id, []), gid)
        )
        look_riv = float(
            _next_is_rivalry(team_games.get(home_id, []), gid)
            or _next_is_rivalry(team_games.get(away_id, []), gid)
        )

        # Knowable time: after both teams' prior kickoffs (rest/post-rivalry
        # known); week-1 / no prior → two weeks before kickoff (schedule freeze).
        priors = [
            p
            for p in (
                _prior_kickoff(team_games.get(home_id, []), gid),
                _prior_kickoff(team_games.get(away_id, []), gid),
            )
            if p is not None
        ]
        knowable = max(priors) if priors else kick - timedelta(days=14)
        if knowable >= kick:
            knowable = kick - timedelta(hours=1)

        rows.append(
            {
                "game_id": gid,
                "event_time": knowable,
                "rest_days_home": rest_home,
                "rest_days_away": rest_away,
                "rest_days_diff": rest_diff,
                "short_week_flag": short,
                "bye_flag": bye,
                "travel_km": travel,
                "tz_crossed": tz_cross,
                "altitude_delta_m": alt_delta,
                "surface_turf": surface_turf,
                "surface_change_flag": surface_change,
                "neutral_site": float(neutral),
                "week_number": float(week),
                "month": month,
                "conference_game": float(conf),
                "rivalry_flag": rivalry,
                "post_rivalry_flag": post_riv,
                "rivalry_lookahead_flag": look_riv,
            }
        )
    return pd.DataFrame(rows)


def _prior_kickoff(
    timeline: Sequence[Mapping[str, Any]],
    game_id: int,
) -> datetime | None:
    prior: datetime | None = None
    for entry in timeline:
        if int(entry["game_id"]) == game_id:
            break
        prior = entry["event_time"]
    return prior


def _prior_rest(
    timeline: Sequence[Mapping[str, Any]],
    game_id: int,
    kickoff: datetime,
) -> float:
    prior = _prior_kickoff(timeline, game_id)
    if prior is None:
        return float("nan")
    return rest_days_between(prior, kickoff)


def _prior_was_rivalry(timeline: Sequence[Mapping[str, Any]], game_id: int) -> bool:
    prev: Mapping[str, Any] | None = None
    for entry in timeline:
        if int(entry["game_id"]) == game_id:
            return bool(prev["rivalry"]) if prev is not None else False
        prev = entry
    return False


def _next_is_rivalry(timeline: Sequence[Mapping[str, Any]], game_id: int) -> bool:
    found = False
    for entry in timeline:
        if found:
            return bool(entry["rivalry"])
        if int(entry["game_id"]) == game_id:
            found = True
    return False


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SituationalFeatureCard:
    """Parsed situational feature name."""

    name: SituationalFeature


def parse_situational_feature_name(name: str) -> SituationalFeatureCard:
    """Parse a registered situational feature name."""
    match = _FEATURE_NAME_RE.match(name)
    if match is None:
        msg = f"unsupported situational feature name: {name!r}"
        raise FeatureBuildError(msg)
    return SituationalFeatureCard(name=match.group("name"))  # type: ignore[arg-type]


class SituationalFeatureBuilder(FeatureBuilder):
    """One scalar situational feature per game; ``entity_id`` is ``game_id``.

    ``history`` is the frame from :func:`build_situational_frame` (mutable for
    pit_audit injection).
    """

    def __init__(self, spec: FeatureSpec, history: pd.DataFrame) -> None:
        super().__init__(spec)
        self.history = history
        self.card = parse_situational_feature_name(spec.name)

    def compute(self, entity_ids: Sequence[Any], as_of: datetime) -> pd.DataFrame:
        eligible = self.filter_event_time(self.history, as_of)
        col = self.card.name
        by_game: dict[Any, float] = {}
        if not eligible.empty and col in eligible.columns:
            for r in eligible.itertuples(index=False):
                by_game[int(r.game_id)] = float(getattr(r, col))

        rows: list[dict[str, Any]] = []
        for eid in entity_ids:
            try:
                key = int(eid)
            except (TypeError, ValueError):
                key = eid
            value = by_game.get(key, float("nan"))
            rows.append({ENTITY_COL: eid, AS_OF_COL: as_of, VALUE_COL: value})
        frame = pd.DataFrame(rows)
        if self.spec.null_policy == "indicator":
            frame["is_missing"] = frame[VALUE_COL].isna()
        return frame
