"""Pandera DataFrameModels for staged CFB entities.

Every fact and reference table carries UTC ``event_time`` (when the row's
information became knowable) and ``ingested_at``, with
``event_time <= ingested_at``. Range checks follow DESIGN §8 step 2 where
applicable: ``0 <= points <= 100``, ``|spread| < 70``, totals in ``[20, 100]``.

**Canonical game identity (AUDIT-6):** CFBD's stable numeric ``game_id`` is the
only canonical game key across sources. Odds API (and other non-CFBD) event ids
are mapped through :class:`OddsCfbdGameCrosswalkSchema`; the derived matcher
string ``game_key`` (season / teams / kickoff_date) is matcher input only and
must never replace ``game_id`` as the identity of record.
"""

from __future__ import annotations

from datetime import UTC

import pandas as pd  # type: ignore[import-untyped]
import pandera.pandas as pa
from pandera.engines.pandas_engine import DateTime
from pandera.typing import Series

_UTC_DT = {"tz": UTC}


class _TimedModel(pa.DataFrameModel):
    """Shared UTC event/ingest timestamps and temporal sanity check."""

    event_time: Series[DateTime] = pa.Field(dtype_kwargs=_UTC_DT)
    ingested_at: Series[DateTime] = pa.Field(dtype_kwargs=_UTC_DT)

    @pa.dataframe_check
    def event_time_le_ingested_at(  # type: ignore[misc]
        cls, df: pd.DataFrame
    ) -> pd.Series:
        """DESIGN §8: no event_time may post-date ingestion."""
        return df["event_time"] <= df["ingested_at"]

    class Config:
        strict = True
        coerce = True


class GamesSchema(_TimedModel):
    """Game schedule / result rows (one per contest)."""

    game_id: Series[pa.Int64] = pa.Field(ge=0)
    season: Series[pa.Int32] = pa.Field(ge=1900, le=2100)
    week: Series[pa.Int32] = pa.Field(ge=0, le=25)
    season_type: Series[str] = pa.Field(isin=["regular", "postseason"])
    start_date: Series[DateTime] = pa.Field(dtype_kwargs=_UTC_DT)
    home_team_id: Series[pa.Int64] = pa.Field(ge=0)
    away_team_id: Series[pa.Int64] = pa.Field(ge=0)
    home_points: Series[pa.Int32] = pa.Field(ge=0, le=100, nullable=True)
    away_points: Series[pa.Int32] = pa.Field(ge=0, le=100, nullable=True)
    neutral_site: Series[pa.Bool] = pa.Field()
    conference_game: Series[pa.Bool] = pa.Field()
    venue_id: Series[pa.Int64] = pa.Field(ge=0, nullable=True)
    completed: Series[pa.Bool] = pa.Field()
    # AUDIT-6 / Task 5: True when event_time is kickoff+duration (not a real
    # completion timestamp). See docs/adr/0004-event-time-estimated-schema.md.
    event_time_estimated: Series[pa.Bool] = pa.Field()
    source_version: Series[str] = pa.Field(nullable=True)


class PlaysSchema(_TimedModel):
    """Play-by-play (PBP) rows."""

    # CFBD sometimes emits negative play/drive ids; treat them as opaque keys.
    play_id: Series[pa.Int64] = pa.Field()
    game_id: Series[pa.Int64] = pa.Field(ge=0)
    drive_id: Series[pa.Int64] = pa.Field(nullable=True)
    season: Series[pa.Int32] = pa.Field(ge=1900, le=2100)
    week: Series[pa.Int32] = pa.Field(ge=0, le=25)
    offense_id: Series[pa.Int64] = pa.Field(ge=0)
    defense_id: Series[pa.Int64] = pa.Field(ge=0)
    period: Series[pa.Int32] = pa.Field(ge=1, le=8)
    down: Series[pa.Int32] = pa.Field(ge=0, le=4, nullable=True)
    distance: Series[pa.Int32] = pa.Field(nullable=True)
    yards_to_goal: Series[pa.Int32] = pa.Field(ge=0, le=100, nullable=True)
    play_type: Series[str] = pa.Field(nullable=True)
    yards_gained: Series[pa.Int32] = pa.Field(nullable=True)
    epa: Series[pa.Float64] = pa.Field(nullable=True)
    wp: Series[pa.Float64] = pa.Field(ge=0.0, le=1.0, nullable=True)
    success: Series[pa.Bool] = pa.Field(nullable=True)
    scoring: Series[pa.Bool] = pa.Field(nullable=True)
    source_version: Series[str] = pa.Field(nullable=True)


class DrivesSchema(_TimedModel):
    """Drive-level aggregates within a game."""

    # CFBD sometimes emits negative drive ids; treat them as opaque keys.
    drive_id: Series[pa.Int64] = pa.Field()
    game_id: Series[pa.Int64] = pa.Field(ge=0)
    season: Series[pa.Int32] = pa.Field(ge=1900, le=2100)
    week: Series[pa.Int32] = pa.Field(ge=0, le=25)
    offense_id: Series[pa.Int64] = pa.Field(ge=0)
    defense_id: Series[pa.Int64] = pa.Field(ge=0)
    start_period: Series[pa.Int32] = pa.Field(ge=1, le=8, nullable=True)
    end_period: Series[pa.Int32] = pa.Field(ge=1, le=8, nullable=True)
    plays: Series[pa.Int32] = pa.Field(ge=0, nullable=True)
    yards: Series[pa.Int32] = pa.Field(nullable=True)
    scoring: Series[pa.Bool] = pa.Field(nullable=True)
    start_yards_to_goal: Series[pa.Int32] = pa.Field(ge=0, le=100, nullable=True)
    end_yards_to_goal: Series[pa.Int32] = pa.Field(ge=0, le=100, nullable=True)
    points: Series[pa.Int32] = pa.Field(ge=0, le=100, nullable=True)
    source_version: Series[str] = pa.Field(nullable=True)


class AdvancedBoxSchema(_TimedModel):
    """Advanced box-score / efficiency metrics per team-game."""

    game_id: Series[pa.Int64] = pa.Field(ge=0)
    team_id: Series[pa.Int64] = pa.Field(ge=0)
    season: Series[pa.Int32] = pa.Field(ge=1900, le=2100)
    week: Series[pa.Int32] = pa.Field(ge=0, le=25)
    offense_epa: Series[pa.Float64] = pa.Field(nullable=True)
    defense_epa: Series[pa.Float64] = pa.Field(nullable=True)
    success_rate: Series[pa.Float64] = pa.Field(ge=0.0, le=1.0, nullable=True)
    explosiveness: Series[pa.Float64] = pa.Field(nullable=True)
    havoc_rate: Series[pa.Float64] = pa.Field(ge=0.0, le=1.0, nullable=True)
    finishing_drives: Series[pa.Float64] = pa.Field(nullable=True)
    field_position: Series[pa.Float64] = pa.Field(nullable=True)
    points: Series[pa.Int32] = pa.Field(ge=0, le=100, nullable=True)
    source_version: Series[str] = pa.Field(nullable=True)


class LinesHistoricalSchema(_TimedModel):
    """Closing / historical book lines (CFBD lines endpoint)."""

    game_id: Series[pa.Int64] = pa.Field(ge=0)
    season: Series[pa.Int32] = pa.Field(ge=1900, le=2100)
    week: Series[pa.Int32] = pa.Field(ge=0, le=25)
    book: Series[str] = pa.Field()
    line_type: Series[str] = pa.Field(isin=["open", "close", "other"])
    spread: Series[pa.Float64] = pa.Field(gt=-70.0, lt=70.0, nullable=True)
    total: Series[pa.Float64] = pa.Field(ge=20.0, le=100.0, nullable=True)
    home_ml: Series[pa.Float64] = pa.Field(nullable=True)
    away_ml: Series[pa.Float64] = pa.Field(nullable=True)
    source_version: Series[str] = pa.Field(nullable=True)


class OddsSnapshotsSchema(_TimedModel):
    """Odds snapshots from The Odds API (live capture or historical backfill).

    **Canonical identity:** ``game_id`` is CFBD's stable numeric game id
    (AUDIT-6). It is nullable only until the Odds↔CFBD crosswalk resolves the
    row; never invent a substitute. ``game_key`` is the derived
    (season, home, away, kickoff_date) matcher input used to *find* that id —
    not a second identity of record (never pack identity into ``snapshot_id``).

    ``captured_at`` is when the book price was observed. ``event_time`` is when
    that information became knowable for PIT joins (live: equals ``captured_at``;
    historical: the envelope's returned ``timestamp``, never the request ``date``).
    ``price`` is American odds.

    ``snapshot_source`` is ``live`` or ``historical``. ``decision_point`` names
    the pre-registered schedule entry for historical rows (null for live).
    ``n_books_available`` is the bookmaker count in the source payload for that
    event (coverage grows over seasons — never pool without this covariate).
    """

    snapshot_id: Series[str] = pa.Field()
    game_key: Series[str] = pa.Field()  # derived matcher input only
    game_id: Series[pa.Int64] = pa.Field(ge=0, nullable=True)  # CFBD canonical
    season: Series[pa.Int32] = pa.Field(ge=1900, le=2100, nullable=True)
    week: Series[pa.Int32] = pa.Field(ge=0, le=25, nullable=True)
    book: Series[str] = pa.Field()
    market: Series[str] = pa.Field(isin=["spread", "total", "h2h"])
    side: Series[str] = pa.Field(nullable=True)
    line: Series[pa.Float64] = pa.Field(nullable=True)
    price: Series[pa.Float64] = pa.Field()  # American odds (e.g. -110, +150)
    home_team: Series[str] = pa.Field(nullable=True)
    away_team: Series[str] = pa.Field(nullable=True)
    captured_at: Series[DateTime] = pa.Field(dtype_kwargs=_UTC_DT)
    source_version: Series[str] = pa.Field(nullable=True)
    snapshot_source: Series[str] = pa.Field(isin=["live", "historical"])
    decision_point: Series[str] = pa.Field(nullable=True)
    n_books_available: Series[pa.Int32] = pa.Field(ge=0)

    @pa.dataframe_check
    def line_sanity(cls, df: pd.DataFrame) -> pd.Series:  # type: ignore[misc]
        """Apply §8 spread/total bounds when market implies them."""
        spread_ok = (
            df["line"].isna()
            | (df["market"] != "spread")
            | ((df["line"] > -70.0) & (df["line"] < 70.0))
        )
        total_ok = (
            df["line"].isna()
            | (df["market"] != "total")
            | ((df["line"] >= 20.0) & (df["line"] <= 100.0))
        )
        return spread_ok & total_ok


class OddsCfbdGameCrosswalkSchema(_TimedModel):
    """Odds API event id ↔ CFBD ``game_id`` crosswalk (AUDIT-6).

    ``game_id`` is the only canonical game identity. Odds events are matched via
    normalized team pair + kickoff within ±36h. Ambiguous matches are
    ``match_status='quarantined'`` with ``game_id`` null — never guessed.
    ``game_key`` retains the derived (season, home, away, kickoff_date) matcher
    input only.

    Schema is registered in Task 3; row population is Task 4 / Task 5.
    """

    odds_event_id: Series[str] = pa.Field()
    game_id: Series[pa.Int64] = pa.Field(ge=0, nullable=True)  # CFBD canonical
    game_key: Series[str] = pa.Field()  # derived matcher input only
    season: Series[pa.Int32] = pa.Field(ge=1900, le=2100)
    home_team: Series[str] = pa.Field()
    away_team: Series[str] = pa.Field()
    kickoff: Series[DateTime] = pa.Field(dtype_kwargs=_UTC_DT)
    kickoff_delta_hours: Series[pa.Float64] = pa.Field(nullable=True)
    match_status: Series[str] = pa.Field(isin=["matched", "quarantined", "unmatched"])
    source_version: Series[str] = pa.Field(nullable=True)

    @pa.dataframe_check
    def matched_requires_game_id(cls, df: pd.DataFrame) -> pd.Series:  # type: ignore[misc]
        """Matched rows must carry the CFBD canonical id; quarantined may not."""
        matched = df["match_status"] == "matched"
        return (~matched) | df["game_id"].notna()


class TeamsSchema(_TimedModel):
    """Team reference rows."""

    team_id: Series[pa.Int64] = pa.Field(ge=0)
    season: Series[pa.Int32] = pa.Field(ge=1900, le=2100)
    school: Series[str] = pa.Field()
    conference: Series[str] = pa.Field(nullable=True)
    abbreviation: Series[str] = pa.Field(nullable=True)
    classification: Series[str] = pa.Field(isin=["fbs", "fcs", "ii", "iii", "other"], nullable=True)
    source_version: Series[str] = pa.Field(nullable=True)


class VenuesSchema(_TimedModel):
    """Stadium / venue reference rows.

    Column names keep CFBD conventions (``latitude``/``longitude``/``dome``).
    Task 6 enrichment adds ``surface`` and ``timezone``; ``dome`` is the
    ``is_dome`` flag used by weather (downstream must not rename).
    """

    venue_id: Series[pa.Int64] = pa.Field(ge=0)
    season: Series[pa.Int32] = pa.Field(ge=1900, le=2100)
    name: Series[str] = pa.Field()
    city: Series[str] = pa.Field(nullable=True)
    state: Series[str] = pa.Field(nullable=True)
    latitude: Series[pa.Float64] = pa.Field(ge=-90.0, le=90.0, nullable=True)
    longitude: Series[pa.Float64] = pa.Field(ge=-180.0, le=180.0, nullable=True)
    elevation_m: Series[pa.Float64] = pa.Field(nullable=True)
    capacity: Series[pa.Int32] = pa.Field(ge=0, nullable=True)
    grass: Series[pa.Bool] = pa.Field(nullable=True)
    dome: Series[pa.Bool] = pa.Field(nullable=True)  # is_dome
    surface: Series[str] = pa.Field(nullable=True)
    timezone: Series[str] = pa.Field(nullable=True)  # IANA tz name
    source_version: Series[str] = pa.Field(nullable=True)


class WeatherSchema(_TimedModel):
    """Kickoff-hour weather (Open-Meteo historical actuals or forecasts).

    ``obs_kind`` separates archive actuals from forecasts so a later actual
    never overwrites an earlier forecast row. Multiple forecast pulls for the
    same game are retained with distinct ``captured_at`` values.

    Dome venues set ``weather_applicable=False`` and fill weather fields with
    neutral sentinels — downstream code must key off the flag, never the
    sentinel values.
    """

    game_id: Series[pa.Int64] = pa.Field(ge=0)
    season: Series[pa.Int32] = pa.Field(ge=1900, le=2100)
    week: Series[pa.Int32] = pa.Field(ge=0, le=25)
    venue_id: Series[pa.Int64] = pa.Field(ge=0, nullable=True)
    obs_kind: Series[str] = pa.Field(isin=["actual", "forecast"])
    temp_c: Series[pa.Float64] = pa.Field(nullable=True)
    wind_speed_ms: Series[pa.Float64] = pa.Field(ge=0.0, nullable=True)
    wind_gust_ms: Series[pa.Float64] = pa.Field(ge=0.0, nullable=True)
    precip_mm: Series[pa.Float64] = pa.Field(ge=0.0, nullable=True)
    precip_prob: Series[pa.Float64] = pa.Field(ge=0.0, le=100.0, nullable=True)
    humidity: Series[pa.Float64] = pa.Field(ge=0.0, le=100.0, nullable=True)
    snow: Series[pa.Float64] = pa.Field(ge=0.0, nullable=True)  # cm
    weather_applicable: Series[pa.Bool] = pa.Field()
    captured_at: Series[DateTime] = pa.Field(dtype_kwargs=_UTC_DT)
    source_version: Series[str] = pa.Field(nullable=True)


class CoachesSchema(_TimedModel):
    """Head-coach tenure rows."""

    coach_id: Series[str] = pa.Field()
    season: Series[pa.Int32] = pa.Field(ge=1900, le=2100)
    team_id: Series[pa.Int64] = pa.Field(ge=0)
    first_name: Series[str] = pa.Field()
    last_name: Series[str] = pa.Field()
    games: Series[pa.Int32] = pa.Field(ge=0, nullable=True)
    wins: Series[pa.Int32] = pa.Field(ge=0, nullable=True)
    losses: Series[pa.Int32] = pa.Field(ge=0, nullable=True)
    source_version: Series[str] = pa.Field(nullable=True)


class RostersSchema(_TimedModel):
    """Season roster membership."""

    season: Series[pa.Int32] = pa.Field(ge=1900, le=2100)
    team_id: Series[pa.Int64] = pa.Field(ge=0)
    # CFBD sometimes emits negative athlete ids; treat as opaque keys (cf. play_id).
    athlete_id: Series[pa.Int64] = pa.Field()
    name: Series[str] = pa.Field()
    position: Series[str] = pa.Field(nullable=True)
    year: Series[str] = pa.Field(nullable=True)
    source_version: Series[str] = pa.Field(nullable=True)


class TalentSchema(_TimedModel):
    """Team talent composite (247 via CFBD)."""

    season: Series[pa.Int32] = pa.Field(ge=1900, le=2100)
    team_id: Series[pa.Int64] = pa.Field(ge=0)
    talent: Series[pa.Float64] = pa.Field(nullable=True)
    source_version: Series[str] = pa.Field(nullable=True)


class ReturningProductionSchema(_TimedModel):
    """Preseason returning-production percentages.

    Percentages may be outside [0, 1] or negative when CFBD reports deltas /
    signed production shares — validate null-with-indicator downstream, never
    reject legitimate negatives (Task 12 / Task 23-FIX).
    """

    season: Series[pa.Int32] = pa.Field(ge=1900, le=2100)
    team_id: Series[pa.Int64] = pa.Field(ge=0)
    offense_pct: Series[pa.Float64] = pa.Field(nullable=True)
    defense_pct: Series[pa.Float64] = pa.Field(nullable=True)
    overall_pct: Series[pa.Float64] = pa.Field(nullable=True)
    source_version: Series[str] = pa.Field(nullable=True)


class RecruitingSchema(_TimedModel):
    """Team recruiting-class aggregates.

    CFBD emits small negative ``points`` for some 2014–2018 classes (observed
    ``-0.04``). Rejecting them left recruiting partitions unwritten while the
    Task 23 run still published numbers — Phase 2 forbids that silent skip.
    Allow signed points; treat null-with-indicator downstream rather than
    dropping the season.
    """

    season: Series[pa.Int32] = pa.Field(ge=1900, le=2100)
    team_id: Series[pa.Int64] = pa.Field(ge=0)
    rank: Series[pa.Int32] = pa.Field(ge=1, nullable=True)
    points: Series[pa.Float64] = pa.Field(nullable=True)
    average_rating: Series[pa.Float64] = pa.Field(nullable=True)
    blue_chip_ratio: Series[pa.Float64] = pa.Field(ge=0.0, le=1.0, nullable=True)
    source_version: Series[str] = pa.Field(nullable=True)


class PortalSchema(_TimedModel):
    """Transfer-portal movement rows."""

    season: Series[pa.Int32] = pa.Field(ge=1900, le=2100)
    athlete_id: Series[pa.Int64] = pa.Field()
    athlete_name: Series[str] = pa.Field(nullable=True)
    origin_team_id: Series[pa.Int64] = pa.Field(ge=0, nullable=True)
    dest_team_id: Series[pa.Int64] = pa.Field(ge=0, nullable=True)
    transfer_date: Series[DateTime] = pa.Field(dtype_kwargs=_UTC_DT, nullable=True)
    rating: Series[pa.Float64] = pa.Field(nullable=True)
    source_version: Series[str] = pa.Field(nullable=True)


class QbStatusSchema(_TimedModel):
    """Manual / prospective QB availability rows (DESIGN §3.3 / §3.4)."""

    game_id: Series[pa.Int64] = pa.Field(ge=0)
    team_id: Series[pa.Int64] = pa.Field(ge=0)
    season: Series[pa.Int32] = pa.Field(ge=1900, le=2100)
    status: Series[str] = pa.Field(isin=["starter", "backup", "unknown"])
    source_version: Series[str] = pa.Field(nullable=True)


# Table name → schema (used by storage validation).
SCHEMA_REGISTRY: dict[str, type[_TimedModel]] = {
    "games": GamesSchema,
    "plays": PlaysSchema,
    "drives": DrivesSchema,
    "advanced_box": AdvancedBoxSchema,
    "lines_historical": LinesHistoricalSchema,
    "odds_snapshots": OddsSnapshotsSchema,
    "odds_cfbd_game_crosswalk": OddsCfbdGameCrosswalkSchema,
    "weather": WeatherSchema,
    "teams": TeamsSchema,
    "venues": VenuesSchema,
    "coaches": CoachesSchema,
    "rosters": RostersSchema,
    "talent": TalentSchema,
    "returning_production": ReturningProductionSchema,
    "recruiting": RecruitingSchema,
    "portal": PortalSchema,
    "qb_status": QbStatusSchema,
}

# Game-grained tables partition by (season, week); reference by (season).
GAME_GRAINED_TABLES: frozenset[str] = frozenset(
    {
        "games",
        "plays",
        "drives",
        "advanced_box",
        "lines_historical",
        "odds_snapshots",
        "weather",
    }
)
REFERENCE_TABLES: frozenset[str] = frozenset(
    {
        "teams",
        "venues",
        "coaches",
        "rosters",
        "talent",
        "returning_production",
        "recruiting",
        "portal",
        "qb_status",
        # Season-keyed; postpone keeps one CFBD id across week shifts.
        "odds_cfbd_game_crosswalk",
    }
)


def get_schema(table: str) -> type[_TimedModel]:
    """Return the pandera model for ``table`` or raise ``KeyError``."""
    try:
        return SCHEMA_REGISTRY[table]
    except KeyError as exc:
        known = ", ".join(sorted(SCHEMA_REGISTRY))
        msg = f"unknown table {table!r}; known: {known}"
        raise KeyError(msg) from exc


def validate_table(table: str, df: pd.DataFrame) -> pd.DataFrame:
    """Validate ``df`` against the registered schema for ``table``."""
    schema = get_schema(table)
    return schema.validate(df, lazy=True)
