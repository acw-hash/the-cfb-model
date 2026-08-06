"""CollegeFootballData.com (CFBD) ingestion.

Raw archival happens *before* parse under ``data/raw/cfbd/{date}/…json`` so a
parser failure never loses the payload. Staged writes go through
:class:`~ncaa_quant.data.storage.ParquetStore`.

event_time assignment (PIT-critical)
------------------------------------
| Endpoint / table        | event_time                                              |
|-------------------------|---------------------------------------------------------|
| games, plays, drives,   | ``start_date + GAME_DURATION`` (3.5h). CFBD does not    |
| advanced_box            | expose a reliable completion timestamp.                 |
| lines_historical        | kickoff ``start_date`` (conservative latest for open/   |
|                         | close when open timestamps are absent).                 |
| talent, returning,      | ``{season}-08-01T00:00:00Z`` documented preseason.      |
| recruiting, teams,      |                                                         |
| venues, coaches,        |                                                         |
| rosters                 |                                                         |
| portal                  | ``transfer_date`` when present; else preseason Aug 1.   |

``/games/teams`` (traditional box) is fetched and archived only — no staged
schema exists; scores already live on ``games``.

``/teams`` is included (beyond the task bullet list) to populate ``teams`` and
resolve school→``team_id`` for reference endpoints.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import httpx
import pandas as pd  # type: ignore[import-untyped]
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from ncaa_quant.config import AppConfig, load_config, load_secrets
from ncaa_quant.data.schemas import GAME_GRAINED_TABLES, REFERENCE_TABLES
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.ingestion.teams import load_team_name_map, normalize_team_name
from ncaa_quant.utils.logging import get_logger
from ncaa_quant.utils.timeutils import season_of, to_utc, week_of

logging.getLogger("httpx").setLevel(logging.WARNING)

SOURCE_VERSION: Final[str] = "cfbd_v1"
BASE_URL: Final[str] = "https://api.collegefootballdata.com"
GAME_DURATION: Final[timedelta] = timedelta(hours=3, minutes=30)
PRESEASON_MONTH: Final[int] = 8
PRESEASON_DAY: Final[int] = 1
DEFAULT_RATE_LIMIT_RESERVE: Final[int] = 10
# Regular-season weeks 0–15 cover Week Zero through late November; postseason
# continues into higher week numbers returned by CFBD.
MAX_REGULAR_WEEK: Final[int] = 16

_REMAINING_HEADERS: Final[tuple[str, ...]] = (
    "x-ratelimit-remaining",
    "X-RateLimit-Remaining",
    "x-calllimit-remaining",
    "X-CallLimit-Remaining",
)

# Short name → (API path, staged table, grain)
# grain: "season_week" | "season" | "archive_only"
ENDPOINT_SPECS: Final[dict[str, dict[str, str]]] = {
    "games": {"path": "/games", "table": "games", "grain": "season_week"},
    "plays": {"path": "/plays", "table": "plays", "grain": "season_week"},
    "drives": {"path": "/drives", "table": "drives", "grain": "season_week"},
    "games_teams": {
        "path": "/games/teams",
        "table": "",
        "grain": "archive_only",
    },
    "advanced": {
        "path": "/stats/game/advanced",
        "table": "advanced_box",
        "grain": "season_week",
    },
    "lines": {"path": "/lines", "table": "lines_historical", "grain": "season_week"},
    "talent": {"path": "/talent", "table": "talent", "grain": "season"},
    "returning": {
        "path": "/player/returning",
        "table": "returning_production",
        "grain": "season",
    },
    "recruiting": {
        "path": "/recruiting/teams",
        "table": "recruiting",
        "grain": "season",
    },
    "portal": {"path": "/player/portal", "table": "portal", "grain": "season"},
    "coaches": {"path": "/coaches", "table": "coaches", "grain": "season"},
    "venues": {"path": "/venues", "table": "venues", "grain": "season"},
    "roster": {"path": "/roster", "table": "rosters", "grain": "season"},
    "teams": {"path": "/teams", "table": "teams", "grain": "season"},
}

STAGED_ENDPOINTS: Final[tuple[str, ...]] = tuple(
    name for name, spec in ENDPOINT_SPECS.items() if spec["grain"] != "archive_only"
)
DEFAULT_ENDPOINTS: Final[tuple[str, ...]] = STAGED_ENDPOINTS + ("games_teams",)

_CLASSIFICATION_MAP: Final[dict[str, str]] = {
    "fbs": "fbs",
    "fcs": "fcs",
    "ii": "ii",
    "iii": "iii",
    "d2": "ii",
    "d3": "iii",
}


class RateLimitBudgetError(RuntimeError):
    """Raised when remaining CFBD calls are below the configured reserve."""


class CFBDError(RuntimeError):
    """Non-retryable CFBD API failure."""


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return False


def preseason_event_time(season: int) -> datetime:
    """Documented preseason instant: Aug 1 of ``season`` at 00:00 UTC."""
    return datetime(season, PRESEASON_MONTH, PRESEASON_DAY, tzinfo=UTC)


def game_event_time(start_date: datetime) -> datetime:
    """Knowable-at for game results / PBP: kickoff + duration estimate."""
    return to_utc(start_date) + GAME_DURATION


@dataclass(frozen=True)
class CfbdIngestResult:
    """Summary of a backfill or incremental run."""

    seasons: tuple[int, ...]
    partitions_written: int
    partitions_skipped: int
    rows_written: int
    raw_paths: tuple[Path, ...] = field(default_factory=tuple)


def archive_raw_cfbd(
    raw_root: Path,
    captured_at: datetime,
    body: bytes | str,
    *,
    endpoint: str,
    season: int | None = None,
    week: int | None = None,
    season_type: str | None = None,
) -> Path:
    """Write the API payload under ``raw_root/{date}/{endpoint}_….json``."""
    captured = to_utc(captured_at)
    day = captured.date().isoformat()
    stamp = captured.strftime("%Y%m%dT%H%M%S%fZ")
    parts = [endpoint]
    if season is not None:
        parts.append(f"s{season}")
    if week is not None:
        parts.append(f"w{week}")
    if season_type:
        parts.append(season_type)
    parts.append(stamp)
    directory = raw_root / day
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ("_".join(parts) + ".json")
    payload = body if isinstance(body, bytes) else body.encode("utf-8")
    path.write_bytes(payload)
    return path


def is_partition_complete(
    store: ParquetStore,
    table: str,
    partition: Mapping[str, int],
) -> bool:
    """True when ``part.parquet`` already exists for this table/partition."""
    return store._partition_path(table, partition).exists()  # noqa: SLF001


def parse_seasons_arg(value: str) -> tuple[int, ...]:
    """Parse ``YYYY`` or ``YYYY-YYYY`` into an inclusive season tuple."""
    text = value.strip()
    if "-" in text:
        left, right = text.split("-", 1)
        start, end = int(left), int(right)
        if end < start:
            msg = f"invalid season range {value!r}"
            raise ValueError(msg)
        return tuple(range(start, end + 1))
    return (int(text),)


def _parse_json_list(payload: bytes | str | list[Any]) -> list[Any]:
    data = json.loads(payload) if isinstance(payload, (bytes, str)) else payload
    if not isinstance(data, list):
        msg = "CFBD payload must be a JSON array"
        raise CFBDError(msg)
    return data


def _parse_ts(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return to_utc(value) if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value).replace("Z", "+00:00")
    try:
        return to_utc(datetime.fromisoformat(text))
    except ValueError:
        return None


def _period(value: Any) -> int | None:
    """CFBD occasionally emits period 0; treat out-of-range as null."""
    period = _as_int(value)
    if period is None or period < 1 or period > 8:
        return None
    return period


def _down(value: Any) -> int | None:
    """CFBD occasionally emits down > 4; treat out-of-range as null."""
    down = _as_int(value)
    if down is None or down < 0 or down > 4:
        return None
    return down


def _yards_to_goal(value: Any) -> int | None:
    """Clamp invalid CFBD yards-to-goal to null (schema requires 0–100)."""
    ytg = _as_int(value)
    if ytg is None or ytg < 0 or ytg > 100:
        return None
    return ytg


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return bool(value)


def _pct_unit(value: Any) -> float | None:
    """Normalize a percentage that may be 0–1 or 0–100 into ``[0, 1]``."""
    num = _as_float(value)
    if num is None:
        return None
    if num > 1.0:
        return num / 100.0
    return num


def _empty_df(columns: Sequence[str]) -> pd.DataFrame:
    return pd.DataFrame({col: pd.Series(dtype="object") for col in columns})


def _resolve_team_id(
    school: str | None,
    school_to_id: Mapping[str, int],
    team_map: Mapping[str, str],
) -> int | None:
    if not school:
        return None
    canonical = normalize_team_name(str(school), team_map)
    if canonical in school_to_id:
        return school_to_id[canonical]
    # Direct key (CFBD school already canonical).
    if school in school_to_id:
        return school_to_id[school]
    folded = " ".join(str(school).split()).casefold()
    for key, tid in school_to_id.items():
        if key.casefold() == folded or key.casefold() == canonical.casefold():
            return tid
    return None


class CFBDClient:
    """Typed httpx client for CollegeFootballData.com."""

    def __init__(
        self,
        api_key: str,
        *,
        requests_per_second: float = 2.0,
        rate_limit_reserve: int = DEFAULT_RATE_LIMIT_RESERVE,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
        base_url: str = BASE_URL,
    ) -> None:
        if not api_key:
            msg = "CFBD_API_KEY is empty"
            raise ValueError(msg)
        self._api_key = api_key
        self._min_interval = 1.0 / requests_per_second if requests_per_second > 0 else 0.0
        self._reserve = rate_limit_reserve
        self._remaining: int | None = None
        self._last_request_at: float = 0.0
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    @property
    def remaining_requests(self) -> int | None:
        """Last-seen rate-limit remaining header, or None before first call."""
        return self._remaining

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def __enter__(self) -> CFBDClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _throttle(self) -> None:
        if self._min_interval <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

    def _guard_budget(self) -> None:
        if self._remaining is not None and self._remaining < self._reserve:
            msg = (
                f"CFBD remaining requests {self._remaining} below reserve threshold {self._reserve}"
            )
            raise RateLimitBudgetError(msg)

    def _update_budget(self, headers: httpx.Headers) -> None:
        log = get_logger(__name__)
        raw_remaining: str | None = None
        for key in _REMAINING_HEADERS:
            if key in headers:
                raw_remaining = headers.get(key)
                break
        if raw_remaining is not None:
            try:
                self._remaining = int(float(raw_remaining))
            except ValueError:
                log.warning("unparseable_cfbd_rate_limit_header", header=raw_remaining)
        log.info(
            "cfbd_rate_limit",
            requests_remaining=raw_remaining,
            reserve=self._reserve,
        )

    def get(self, path: str, params: Mapping[str, Any] | None = None) -> bytes:
        """GET ``path`` with retry / throttle / budget guard; return raw body."""
        self._guard_budget()
        self._throttle()
        return self._get_with_retry(path, dict(params or {}))

    def _get_with_retry(self, path: str, params: dict[str, Any]) -> bytes:
        @retry(
            reraise=True,
            stop=stop_after_attempt(5),
            wait=wait_exponential(multiplier=1, min=1, max=30),
            retry=retry_if_exception(_is_retryable),
        )
        def _once() -> bytes:
            response = self._client.get(path, params=params)
            self._last_request_at = time.monotonic()
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError:
                self._update_budget(response.headers)
                raise
            self._update_budget(response.headers)
            return response.content

        return _once()

    # --- typed wrappers -------------------------------------------------

    def fetch_games(
        self,
        year: int,
        *,
        week: int | None = None,
        season_type: str = "regular",
        classification: str = "fbs",
    ) -> bytes:
        params: dict[str, Any] = {
            "year": year,
            "seasonType": season_type,
            "classification": classification,
        }
        if week is not None:
            params["week"] = week
        return self.get("/games", params)

    def fetch_plays(
        self,
        year: int,
        week: int,
        *,
        season_type: str = "regular",
        classification: str = "fbs",
    ) -> bytes:
        return self.get(
            "/plays",
            {
                "year": year,
                "week": week,
                "seasonType": season_type,
                "classification": classification,
            },
        )

    def fetch_drives(
        self,
        year: int,
        week: int,
        *,
        season_type: str = "regular",
    ) -> bytes:
        return self.get(
            "/drives",
            {"year": year, "week": week, "seasonType": season_type},
        )

    def fetch_games_teams(
        self,
        year: int,
        week: int,
        *,
        season_type: str = "regular",
    ) -> bytes:
        return self.get(
            "/games/teams",
            {"year": year, "week": week, "seasonType": season_type},
        )

    def fetch_advanced(
        self,
        year: int,
        week: int,
        *,
        season_type: str = "regular",
    ) -> bytes:
        return self.get(
            "/stats/game/advanced",
            {"year": year, "week": week, "seasonType": season_type},
        )

    def fetch_lines(
        self,
        year: int,
        week: int,
        *,
        season_type: str = "regular",
    ) -> bytes:
        return self.get(
            "/lines",
            {"year": year, "week": week, "seasonType": season_type},
        )

    def fetch_talent(self, year: int) -> bytes:
        return self.get("/talent", {"year": year})

    def fetch_returning(self, year: int) -> bytes:
        return self.get("/player/returning", {"year": year})

    def fetch_recruiting(self, year: int) -> bytes:
        return self.get("/recruiting/teams", {"year": year})

    def fetch_portal(self, year: int) -> bytes:
        return self.get("/player/portal", {"year": year})

    def fetch_coaches(self, year: int) -> bytes:
        return self.get("/coaches", {"year": year})

    def fetch_venues(self) -> bytes:
        return self.get("/venues")

    def fetch_roster(self, year: int) -> bytes:
        return self.get("/roster", {"year": year})

    def fetch_teams(self, year: int, *, classification: str | None = None) -> bytes:
        params: dict[str, Any] = {"year": year}
        if classification:
            params["classification"] = classification
        return self.get("/teams", params)


# --- normalizers -----------------------------------------------------------

_GAMES_COLS: Final[tuple[str, ...]] = (
    "game_id",
    "season",
    "week",
    "season_type",
    "start_date",
    "home_team_id",
    "away_team_id",
    "home_points",
    "away_points",
    "neutral_site",
    "conference_game",
    "venue_id",
    "completed",
    "source_version",
    "event_time",
    "ingested_at",
)

_PLAYS_COLS: Final[tuple[str, ...]] = (
    "play_id",
    "game_id",
    "drive_id",
    "season",
    "week",
    "offense_id",
    "defense_id",
    "period",
    "down",
    "distance",
    "yards_to_goal",
    "play_type",
    "yards_gained",
    "epa",
    "wp",
    "success",
    "scoring",
    "source_version",
    "event_time",
    "ingested_at",
)

_DRIVES_COLS: Final[tuple[str, ...]] = (
    "drive_id",
    "game_id",
    "season",
    "week",
    "offense_id",
    "defense_id",
    "start_period",
    "end_period",
    "plays",
    "yards",
    "scoring",
    "start_yards_to_goal",
    "end_yards_to_goal",
    "points",
    "source_version",
    "event_time",
    "ingested_at",
)

_ADVANCED_COLS: Final[tuple[str, ...]] = (
    "game_id",
    "team_id",
    "season",
    "week",
    "offense_epa",
    "defense_epa",
    "success_rate",
    "explosiveness",
    "havoc_rate",
    "finishing_drives",
    "field_position",
    "points",
    "source_version",
    "event_time",
    "ingested_at",
)

_LINES_COLS: Final[tuple[str, ...]] = (
    "game_id",
    "season",
    "week",
    "book",
    "line_type",
    "spread",
    "total",
    "home_ml",
    "away_ml",
    "source_version",
    "event_time",
    "ingested_at",
)

_TEAMS_COLS: Final[tuple[str, ...]] = (
    "team_id",
    "season",
    "school",
    "conference",
    "abbreviation",
    "classification",
    "source_version",
    "event_time",
    "ingested_at",
)

_VENUES_COLS: Final[tuple[str, ...]] = (
    "venue_id",
    "season",
    "name",
    "city",
    "state",
    "latitude",
    "longitude",
    "elevation_m",
    "capacity",
    "grass",
    "dome",
    "surface",
    "timezone",
    "source_version",
    "event_time",
    "ingested_at",
)

_COACHES_COLS: Final[tuple[str, ...]] = (
    "coach_id",
    "season",
    "team_id",
    "first_name",
    "last_name",
    "games",
    "wins",
    "losses",
    "source_version",
    "event_time",
    "ingested_at",
)

_ROSTERS_COLS: Final[tuple[str, ...]] = (
    "season",
    "team_id",
    "athlete_id",
    "name",
    "position",
    "year",
    "source_version",
    "event_time",
    "ingested_at",
)

_TALENT_COLS: Final[tuple[str, ...]] = (
    "season",
    "team_id",
    "talent",
    "source_version",
    "event_time",
    "ingested_at",
)

_RETURNING_COLS: Final[tuple[str, ...]] = (
    "season",
    "team_id",
    "offense_pct",
    "defense_pct",
    "overall_pct",
    "source_version",
    "event_time",
    "ingested_at",
)

_RECRUITING_COLS: Final[tuple[str, ...]] = (
    "season",
    "team_id",
    "rank",
    "points",
    "average_rating",
    "blue_chip_ratio",
    "source_version",
    "event_time",
    "ingested_at",
)

_PORTAL_COLS: Final[tuple[str, ...]] = (
    "season",
    "athlete_id",
    "athlete_name",
    "origin_team_id",
    "dest_team_id",
    "transfer_date",
    "rating",
    "source_version",
    "event_time",
    "ingested_at",
)


def normalize_games_payload(
    payload: bytes | str | list[Any],
    *,
    ingested_at: datetime,
    source_version: str = SOURCE_VERSION,
) -> pd.DataFrame:
    """Normalize CFBD ``/games`` into ``games`` schema rows."""
    ingested = to_utc(ingested_at)
    rows: list[dict[str, Any]] = []
    for item in _parse_json_list(payload):
        if not isinstance(item, dict):
            continue
        game_id = _as_int(item.get("id"))
        season = _as_int(item.get("season"))
        start = _parse_ts(item.get("start_date") or item.get("startDate"))
        home_id = _as_int(item.get("home_id") or item.get("homeId"))
        away_id = _as_int(item.get("away_id") or item.get("awayId"))
        if game_id is None or season is None or start is None or home_id is None or away_id is None:
            continue
        week = _as_int(item.get("week"))
        if week is None:
            week = week_of(start, season)
        season_type_raw = str(item.get("season_type") or item.get("seasonType") or "regular")
        season_type = "postseason" if "post" in season_type_raw.casefold() else "regular"
        rows.append(
            {
                "game_id": game_id,
                "season": season,
                "week": week,
                "season_type": season_type,
                "start_date": start,
                "home_team_id": home_id,
                "away_team_id": away_id,
                "home_points": _as_int(item.get("home_points") or item.get("homePoints")),
                "away_points": _as_int(item.get("away_points") or item.get("awayPoints")),
                "neutral_site": bool(
                    _as_bool(
                        item.get("neutral_site")
                        if "neutral_site" in item
                        else item.get("neutralSite")
                    )
                    or False
                ),
                "conference_game": bool(
                    _as_bool(
                        item.get("conference_game")
                        if "conference_game" in item
                        else item.get("conferenceGame")
                    )
                    or False
                ),
                "venue_id": _as_int(item.get("venue_id") or item.get("venueId")),
                "completed": bool(
                    _as_bool(item.get("completed")) if item.get("completed") is not None else False
                ),
                "source_version": source_version,
                "event_time": game_event_time(start),
                "ingested_at": ingested,
            }
        )
    return pd.DataFrame(rows) if rows else _empty_df(_GAMES_COLS)


def normalize_plays_payload(
    payload: bytes | str | list[Any],
    *,
    season: int,
    week: int,
    ingested_at: datetime,
    school_to_id: Mapping[str, int],
    team_map: Mapping[str, str],
    game_start_by_id: Mapping[int, datetime],
    source_version: str = SOURCE_VERSION,
) -> pd.DataFrame:
    """Normalize CFBD ``/plays`` into ``plays`` schema rows."""
    ingested = to_utc(ingested_at)
    rows: list[dict[str, Any]] = []
    for item in _parse_json_list(payload):
        if not isinstance(item, dict):
            continue
        play_id = _as_int(item.get("id") or item.get("play_id"))
        game_id = _as_int(item.get("game_id") or item.get("gameId"))
        if play_id is None or game_id is None:
            continue
        offense_id = _as_int(item.get("offense_id") or item.get("offenseId"))
        defense_id = _as_int(item.get("defense_id") or item.get("defenseId"))
        if offense_id is None:
            offense_id = _resolve_team_id(
                item.get("offense") if isinstance(item.get("offense"), str) else None,
                school_to_id,
                team_map,
            )
        if defense_id is None:
            defense_id = _resolve_team_id(
                item.get("defense") if isinstance(item.get("defense"), str) else None,
                school_to_id,
                team_map,
            )
        if offense_id is None or defense_id is None:
            continue
        period = _period(item.get("period")) or 1
        start = game_start_by_id.get(game_id)
        event = game_event_time(start) if start is not None else preseason_event_time(season)
        success_raw = item.get("success")
        scoring_raw = item.get("scoring")
        rows.append(
            {
                "play_id": play_id,
                "game_id": game_id,
                "drive_id": _as_int(item.get("drive_id") or item.get("driveId")),
                "season": season,
                "week": week,
                "offense_id": offense_id,
                "defense_id": defense_id,
                "period": period,
                "down": _down(item.get("down")),
                "distance": _as_int(item.get("distance")),
                "yards_to_goal": _yards_to_goal(
                    item.get("yards_to_goal")
                    if item.get("yards_to_goal") is not None
                    else item.get("yardsToGoal")
                ),
                "play_type": (
                    str(item["play_type"])
                    if item.get("play_type") is not None
                    else (str(item["playType"]) if item.get("playType") is not None else None)
                ),
                "yards_gained": _as_int(item.get("yards_gained") or item.get("yardsGained")),
                "epa": _as_float(
                    item.get("ppa") if item.get("ppa") is not None else item.get("epa")
                ),
                "wp": _as_float(
                    item.get("homeWinProb")
                    if item.get("homeWinProb") is not None
                    else item.get("wp")
                ),
                "success": _as_bool(success_raw),
                "scoring": _as_bool(scoring_raw),
                "source_version": source_version,
                "event_time": event,
                "ingested_at": ingested,
            }
        )
    return pd.DataFrame(rows) if rows else _empty_df(_PLAYS_COLS)


def normalize_drives_payload(
    payload: bytes | str | list[Any],
    *,
    season: int,
    week: int,
    ingested_at: datetime,
    school_to_id: Mapping[str, int],
    team_map: Mapping[str, str],
    game_start_by_id: Mapping[int, datetime],
    source_version: str = SOURCE_VERSION,
) -> pd.DataFrame:
    """Normalize CFBD ``/drives`` into ``drives`` schema rows."""
    ingested = to_utc(ingested_at)
    rows: list[dict[str, Any]] = []
    for item in _parse_json_list(payload):
        if not isinstance(item, dict):
            continue
        drive_id = _as_int(item.get("id") or item.get("drive_id") or item.get("driveId"))
        game_id = _as_int(item.get("game_id") or item.get("gameId"))
        if drive_id is None or game_id is None:
            continue
        offense_id = _resolve_team_id(
            str(item["offense"]) if item.get("offense") is not None else None,
            school_to_id,
            team_map,
        )
        defense_id = _resolve_team_id(
            str(item["defense"]) if item.get("defense") is not None else None,
            school_to_id,
            team_map,
        )
        if offense_id is None or defense_id is None:
            continue
        start = game_start_by_id.get(game_id)
        event = game_event_time(start) if start is not None else preseason_event_time(season)
        end_off = _as_int(
            item["end_offense_score"]
            if item.get("end_offense_score") is not None
            else item.get("endOffenseScore")
        )
        start_off = _as_int(
            item["start_offense_score"]
            if item.get("start_offense_score") is not None
            else item.get("startOffenseScore")
        )
        points = None
        if end_off is not None and start_off is not None:
            points = max(0, end_off - start_off)
        rows.append(
            {
                "drive_id": drive_id,
                "game_id": game_id,
                "season": season,
                "week": week,
                "offense_id": offense_id,
                "defense_id": defense_id,
                "start_period": _period(
                    item.get("start_period")
                    if item.get("start_period") is not None
                    else item.get("startPeriod")
                ),
                "end_period": _period(
                    item.get("end_period")
                    if item.get("end_period") is not None
                    else item.get("endPeriod")
                ),
                "plays": _as_int(item.get("plays")),
                "yards": _as_int(item.get("yards")),
                "scoring": _as_bool(item.get("scoring")),
                "start_yards_to_goal": _yards_to_goal(
                    item.get("start_yards_to_goal")
                    if item.get("start_yards_to_goal") is not None
                    else item.get("startYardsToGoal")
                ),
                "end_yards_to_goal": _yards_to_goal(
                    item.get("end_yards_to_goal")
                    if item.get("end_yards_to_goal") is not None
                    else item.get("endYardsToGoal")
                ),
                "points": points,
                "source_version": source_version,
                "event_time": event,
                "ingested_at": ingested,
            }
        )
    return pd.DataFrame(rows) if rows else _empty_df(_DRIVES_COLS)


def normalize_advanced_payload(
    payload: bytes | str | list[Any],
    *,
    season: int,
    week: int,
    ingested_at: datetime,
    school_to_id: Mapping[str, int],
    team_map: Mapping[str, str],
    game_start_by_id: Mapping[int, datetime],
    source_version: str = SOURCE_VERSION,
) -> pd.DataFrame:
    """Normalize CFBD ``/stats/game/advanced`` into ``advanced_box`` rows."""
    ingested = to_utc(ingested_at)
    rows: list[dict[str, Any]] = []
    for item in _parse_json_list(payload):
        if not isinstance(item, dict):
            continue
        game_id = _as_int(item.get("gameId") or item.get("game_id"))
        team_name = item.get("team")
        team_id = _resolve_team_id(
            str(team_name) if team_name is not None else None,
            school_to_id,
            team_map,
        )
        if game_id is None or team_id is None:
            continue
        offense_raw = item.get("offense")
        defense_raw = item.get("defense")
        offense: dict[str, Any] = offense_raw if isinstance(offense_raw, dict) else {}
        defense: dict[str, Any] = defense_raw if isinstance(defense_raw, dict) else {}
        start = game_start_by_id.get(game_id)
        event = game_event_time(start) if start is not None else preseason_event_time(season)
        havoc_raw = defense.get("havoc")
        havoc: dict[str, Any] = havoc_raw if isinstance(havoc_raw, dict) else {}
        finishing = (
            offense.get("pointsPerOpportunity")
            if offense.get("pointsPerOpportunity") is not None
            else offense.get("finishingDrives")
        )
        field_pos = offense.get("fieldPosition")
        field_pos_val = None
        if isinstance(field_pos, dict):
            field_pos_val = _as_float(field_pos.get("averageStart"))
        elif field_pos is not None:
            field_pos_val = _as_float(field_pos)
        havoc_rate = _as_float(havoc.get("total")) if havoc else _as_float(defense.get("havoc"))
        rows.append(
            {
                "game_id": game_id,
                "team_id": team_id,
                "season": int(item.get("season") or season),
                "week": int(item.get("week") or week),
                "offense_epa": _as_float(offense.get("ppa")),
                "defense_epa": _as_float(defense.get("ppa")),
                "success_rate": _as_float(offense.get("successRate")),
                "explosiveness": _as_float(offense.get("explosiveness")),
                "havoc_rate": havoc_rate,
                "finishing_drives": _as_float(finishing),
                "field_position": field_pos_val,
                "points": None,
                "source_version": source_version,
                "event_time": event,
                "ingested_at": ingested,
            }
        )
    return pd.DataFrame(rows) if rows else _empty_df(_ADVANCED_COLS)


def normalize_lines_payload(
    payload: bytes | str | list[Any],
    *,
    season: int,
    week: int,
    ingested_at: datetime,
    game_start_by_id: Mapping[int, datetime],
    source_version: str = SOURCE_VERSION,
) -> pd.DataFrame:
    """Normalize CFBD ``/lines`` into ``lines_historical`` rows (open + close)."""
    ingested = to_utc(ingested_at)
    rows: list[dict[str, Any]] = []
    for item in _parse_json_list(payload):
        if not isinstance(item, dict):
            continue
        game_id = _as_int(item.get("id") or item.get("gameId") or item.get("game_id"))
        if game_id is None:
            continue
        start = game_start_by_id.get(game_id)
        event = to_utc(start) if start is not None else preseason_event_time(season)
        g_season = _as_int(item.get("season")) or season
        g_week = _as_int(item.get("week")) or week
        lines = item.get("lines") or []
        if not isinstance(lines, list):
            continue
        for line in lines:
            if not isinstance(line, dict):
                continue
            book = str(line.get("provider") or line.get("book") or "unknown")
            spread_close = _as_float(line.get("spread"))
            spread_open = _as_float(line.get("spreadOpen") or line.get("spread_open"))
            total_close = _as_float(line.get("overUnder") or line.get("over_under"))
            total_open = _as_float(line.get("overUnderOpen") or line.get("over_under_open"))
            home_ml = _as_float(line.get("homeMoneyline") or line.get("home_moneyline"))
            away_ml = _as_float(line.get("awayMoneyline") or line.get("away_moneyline"))

            def _clip_total(val: float | None) -> float | None:
                if val is None:
                    return None
                # Schema requires totals in [20, 100]; drop out-of-range early.
                if val < 20.0 or val > 100.0:
                    return None
                return val

            def _clip_spread(val: float | None) -> float | None:
                if val is None:
                    return None
                if val <= -70.0 or val >= 70.0:
                    return None
                return val

            if spread_open is not None or total_open is not None:
                rows.append(
                    {
                        "game_id": game_id,
                        "season": g_season,
                        "week": g_week,
                        "book": book,
                        "line_type": "open",
                        "spread": _clip_spread(spread_open),
                        "total": _clip_total(total_open),
                        "home_ml": None,
                        "away_ml": None,
                        "source_version": source_version,
                        "event_time": event,
                        "ingested_at": ingested,
                    }
                )
            rows.append(
                {
                    "game_id": game_id,
                    "season": g_season,
                    "week": g_week,
                    "book": book,
                    "line_type": "close",
                    "spread": _clip_spread(spread_close),
                    "total": _clip_total(total_close),
                    "home_ml": home_ml,
                    "away_ml": away_ml,
                    "source_version": source_version,
                    "event_time": event,
                    "ingested_at": ingested,
                }
            )
    return pd.DataFrame(rows) if rows else _empty_df(_LINES_COLS)


def normalize_teams_payload(
    payload: bytes | str | list[Any],
    *,
    season: int,
    ingested_at: datetime,
    team_map: Mapping[str, str],
    source_version: str = SOURCE_VERSION,
) -> pd.DataFrame:
    """Normalize CFBD ``/teams`` into ``teams`` schema rows."""
    ingested = to_utc(ingested_at)
    event = preseason_event_time(season)
    rows: list[dict[str, Any]] = []
    for item in _parse_json_list(payload):
        if not isinstance(item, dict):
            continue
        team_id = _as_int(item.get("id"))
        school_raw = item.get("school")
        if team_id is None or school_raw is None:
            continue
        school = normalize_team_name(str(school_raw), team_map)
        classification_raw = str(item.get("classification") or "other").casefold()
        classification = _CLASSIFICATION_MAP.get(classification_raw, "other")
        rows.append(
            {
                "team_id": team_id,
                "season": season,
                "school": school,
                "conference": (
                    str(item["conference"]) if item.get("conference") is not None else None
                ),
                "abbreviation": (
                    str(item["abbreviation"]) if item.get("abbreviation") is not None else None
                ),
                "classification": classification,
                "source_version": source_version,
                "event_time": event,
                "ingested_at": ingested,
            }
        )
    return pd.DataFrame(rows) if rows else _empty_df(_TEAMS_COLS)


def normalize_venues_payload(
    payload: bytes | str | list[Any],
    *,
    season: int,
    ingested_at: datetime,
    source_version: str = SOURCE_VERSION,
) -> pd.DataFrame:
    """Normalize CFBD ``/venues`` into ``venues`` schema rows."""
    ingested = to_utc(ingested_at)
    event = preseason_event_time(season)
    rows: list[dict[str, Any]] = []
    for item in _parse_json_list(payload):
        if not isinstance(item, dict):
            continue
        venue_id = _as_int(item.get("id"))
        name = item.get("name")
        if venue_id is None or name is None:
            continue
        elev = _as_float(item.get("elevation"))
        grass = _as_bool(item.get("grass"))
        if grass is True:
            surface: str | None = "grass"
        elif grass is False:
            surface = "turf"
        else:
            surface = None
        rows.append(
            {
                "venue_id": venue_id,
                "season": season,
                "name": str(name),
                "city": str(item["city"]) if item.get("city") is not None else None,
                "state": str(item["state"]) if item.get("state") is not None else None,
                "latitude": _as_float(item.get("latitude")),
                "longitude": _as_float(item.get("longitude")),
                "elevation_m": elev,
                "capacity": _as_int(item.get("capacity")),
                "grass": grass,
                "dome": _as_bool(item.get("dome")),
                "surface": surface,
                "timezone": None,  # filled by weather venue enrichment (Task 6)
                "source_version": source_version,
                "event_time": event,
                "ingested_at": ingested,
            }
        )
    return pd.DataFrame(rows) if rows else _empty_df(_VENUES_COLS)


def normalize_coaches_payload(
    payload: bytes | str | list[Any],
    *,
    season: int,
    ingested_at: datetime,
    school_to_id: Mapping[str, int],
    team_map: Mapping[str, str],
    source_version: str = SOURCE_VERSION,
) -> pd.DataFrame:
    """Normalize CFBD ``/coaches`` into ``coaches`` schema rows for ``season``."""
    ingested = to_utc(ingested_at)
    event = preseason_event_time(season)
    rows: list[dict[str, Any]] = []
    for item in _parse_json_list(payload):
        if not isinstance(item, dict):
            continue
        first = str(item.get("first_name") or item.get("firstName") or "")
        last = str(item.get("last_name") or item.get("lastName") or "")
        if not first and not last:
            continue
        seasons = item.get("seasons") or []
        if not isinstance(seasons, list):
            continue
        for season_row in seasons:
            if not isinstance(season_row, dict):
                continue
            year = _as_int(season_row.get("year"))
            if year != season:
                continue
            school = season_row.get("school")
            team_id = _resolve_team_id(
                str(school) if school is not None else None,
                school_to_id,
                team_map,
            )
            if team_id is None:
                continue
            coach_id = f"{first}.{last}.{team_id}.{season}".casefold().replace(" ", "_")
            rows.append(
                {
                    "coach_id": coach_id,
                    "season": season,
                    "team_id": team_id,
                    "first_name": first,
                    "last_name": last,
                    "games": _as_int(season_row.get("games")),
                    "wins": _as_int(season_row.get("wins")),
                    "losses": _as_int(season_row.get("losses")),
                    "source_version": source_version,
                    "event_time": event,
                    "ingested_at": ingested,
                }
            )
    return pd.DataFrame(rows) if rows else _empty_df(_COACHES_COLS)


def normalize_roster_payload(
    payload: bytes | str | list[Any],
    *,
    season: int,
    ingested_at: datetime,
    school_to_id: Mapping[str, int],
    team_map: Mapping[str, str],
    source_version: str = SOURCE_VERSION,
) -> pd.DataFrame:
    """Normalize CFBD ``/roster`` into ``rosters`` schema rows."""
    ingested = to_utc(ingested_at)
    event = preseason_event_time(season)
    rows: list[dict[str, Any]] = []
    for item in _parse_json_list(payload):
        if not isinstance(item, dict):
            continue
        athlete_id = _as_int(item.get("id"))
        team_name = item.get("team")
        team_id = _resolve_team_id(
            str(team_name) if team_name is not None else None,
            school_to_id,
            team_map,
        )
        if athlete_id is None or team_id is None:
            continue
        first = str(item.get("first_name") or item.get("firstName") or "")
        last = str(item.get("last_name") or item.get("lastName") or "")
        name = f"{first} {last}".strip() or str(item.get("name") or athlete_id)
        year_val = item.get("year")
        year_str = None if year_val is None else str(year_val)
        rows.append(
            {
                "season": season,
                "team_id": team_id,
                "athlete_id": athlete_id,
                "name": name,
                "position": (str(item["position"]) if item.get("position") is not None else None),
                "year": year_str,
                "source_version": source_version,
                "event_time": event,
                "ingested_at": ingested,
            }
        )
    return pd.DataFrame(rows) if rows else _empty_df(_ROSTERS_COLS)


def normalize_talent_payload(
    payload: bytes | str | list[Any],
    *,
    season: int,
    ingested_at: datetime,
    school_to_id: Mapping[str, int],
    team_map: Mapping[str, str],
    source_version: str = SOURCE_VERSION,
) -> pd.DataFrame:
    """Normalize CFBD ``/talent`` into ``talent`` schema rows."""
    ingested = to_utc(ingested_at)
    event = preseason_event_time(season)
    rows: list[dict[str, Any]] = []
    for item in _parse_json_list(payload):
        if not isinstance(item, dict):
            continue
        school = item.get("school") or item.get("team")
        team_id = _resolve_team_id(
            str(school) if school is not None else None,
            school_to_id,
            team_map,
        )
        if team_id is None:
            continue
        year = _as_int(item.get("year") or item.get("season")) or season
        rows.append(
            {
                "season": year,
                "team_id": team_id,
                "talent": _as_float(item.get("talent")),
                "source_version": source_version,
                "event_time": event,
                "ingested_at": ingested,
            }
        )
    return pd.DataFrame(rows) if rows else _empty_df(_TALENT_COLS)


def normalize_returning_payload(
    payload: bytes | str | list[Any],
    *,
    season: int,
    ingested_at: datetime,
    school_to_id: Mapping[str, int],
    team_map: Mapping[str, str],
    source_version: str = SOURCE_VERSION,
) -> pd.DataFrame:
    """Normalize CFBD ``/player/returning`` into ``returning_production`` rows."""
    ingested = to_utc(ingested_at)
    event = preseason_event_time(season)
    rows: list[dict[str, Any]] = []
    for item in _parse_json_list(payload):
        if not isinstance(item, dict):
            continue
        school = item.get("team") or item.get("school")
        team_id = _resolve_team_id(
            str(school) if school is not None else None,
            school_to_id,
            team_map,
        )
        if team_id is None:
            continue
        percent_raw = item.get("percentPPA")
        percent: dict[str, Any] = percent_raw if isinstance(percent_raw, dict) else {}
        overall = _pct_unit(
            percent.get("total") if percent else item.get("overall_pct") or item.get("percentPPA")
        )
        offense = _pct_unit(
            percent.get("offense")
            if percent and percent.get("offense") is not None
            else item.get("offense_pct") or percent.get("passing")
        )
        defense = _pct_unit(
            item.get("defense_pct")
            if item.get("defense_pct") is not None
            else (percent.get("defense") if percent else None)
        )
        # CFBD returning endpoint is offense-production oriented; defense often absent.
        rows.append(
            {
                "season": _as_int(item.get("season")) or season,
                "team_id": team_id,
                "offense_pct": offense if offense is not None else overall,
                "defense_pct": defense,
                "overall_pct": overall,
                "source_version": source_version,
                "event_time": event,
                "ingested_at": ingested,
            }
        )
    return pd.DataFrame(rows) if rows else _empty_df(_RETURNING_COLS)


def normalize_recruiting_payload(
    payload: bytes | str | list[Any],
    *,
    season: int,
    ingested_at: datetime,
    school_to_id: Mapping[str, int],
    team_map: Mapping[str, str],
    source_version: str = SOURCE_VERSION,
) -> pd.DataFrame:
    """Normalize CFBD ``/recruiting/teams`` into ``recruiting`` schema rows."""
    ingested = to_utc(ingested_at)
    event = preseason_event_time(season)
    rows: list[dict[str, Any]] = []
    for item in _parse_json_list(payload):
        if not isinstance(item, dict):
            continue
        school = item.get("team") or item.get("school")
        team_id = _resolve_team_id(
            str(school) if school is not None else None,
            school_to_id,
            team_map,
        )
        if team_id is None:
            continue
        blue = _as_int(item.get("blueChips") or item.get("blue_chips"))
        commits = _as_int(item.get("totalCommits") or item.get("total_commits"))
        ratio = None
        if blue is not None and commits is not None and commits > 0:
            ratio = blue / commits
        rows.append(
            {
                "season": _as_int(item.get("year") or item.get("season")) or season,
                "team_id": team_id,
                "rank": _as_int(item.get("rank")),
                "points": _as_float(item.get("points")),
                "average_rating": _as_float(
                    item.get("averageRating") or item.get("average_rating")
                ),
                "blue_chip_ratio": ratio,
                "source_version": source_version,
                "event_time": event,
                "ingested_at": ingested,
            }
        )
    return pd.DataFrame(rows) if rows else _empty_df(_RECRUITING_COLS)


def normalize_portal_payload(
    payload: bytes | str | list[Any],
    *,
    season: int,
    ingested_at: datetime,
    school_to_id: Mapping[str, int],
    team_map: Mapping[str, str],
    source_version: str = SOURCE_VERSION,
) -> pd.DataFrame:
    """Normalize CFBD ``/player/portal`` into ``portal`` schema rows."""
    ingested = to_utc(ingested_at)
    fallback = preseason_event_time(season)
    rows: list[dict[str, Any]] = []
    for idx, item in enumerate(_parse_json_list(payload)):
        if not isinstance(item, dict):
            continue
        athlete_id = _as_int(item.get("athleteId") or item.get("id") or item.get("athlete_id"))
        if athlete_id is None:
            # Deterministic stand-in when CFBD omits athlete id.
            athlete_id = idx + 1
        first = str(item.get("firstName") or item.get("first_name") or "")
        last = str(item.get("lastName") or item.get("last_name") or "")
        name = f"{first} {last}".strip() or None
        origin = _resolve_team_id(
            str(item["origin"]) if item.get("origin") is not None else None,
            school_to_id,
            team_map,
        )
        dest = _resolve_team_id(
            str(item["destination"]) if item.get("destination") is not None else None,
            school_to_id,
            team_map,
        )
        transfer = _parse_ts(item.get("transferDate") or item.get("transfer_date"))
        event = transfer if transfer is not None else fallback
        rows.append(
            {
                "season": _as_int(item.get("season") or item.get("year")) or season,
                "athlete_id": athlete_id,
                "athlete_name": name,
                "origin_team_id": origin,
                "dest_team_id": dest,
                "transfer_date": transfer,
                "rating": _as_float(item.get("rating")),
                "source_version": source_version,
                "event_time": event,
                "ingested_at": ingested,
            }
        )
    return pd.DataFrame(rows) if rows else _empty_df(_PORTAL_COLS)


# --- orchestration ---------------------------------------------------------


def _school_to_id_from_teams(df: pd.DataFrame) -> dict[str, int]:
    out: dict[str, int] = {}
    if df.empty:
        return out
    for _, row in df.iterrows():
        out[str(row["school"])] = int(row["team_id"])
    return out


def _game_starts_from_games(df: pd.DataFrame) -> dict[int, datetime]:
    out: dict[int, datetime] = {}
    if df.empty:
        return out
    for _, row in df.iterrows():
        start = row["start_date"]
        if isinstance(start, datetime):
            out[int(row["game_id"])] = to_utc(start)
        else:
            ts = pd.Timestamp(start)
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            out[int(row["game_id"])] = to_utc(ts.to_pydatetime())
    return out


def _write_partition(
    store: ParquetStore,
    table: str,
    df: pd.DataFrame,
    partition: Mapping[str, int],
) -> int:
    """Write ``df`` (possibly empty with columns) and return row count."""
    if df.empty and list(df.columns):
        # Ensure pandera sees typed empty frame with required columns.
        store.write_partition(table, df, partition, mode="overwrite", validate=False)
        # Re-read path exists; for empty we skip validation to avoid dtype pain.
        # Prefer validating non-empty; for empty write a minimal validated frame
        # by constructing via schema coerce when possible.
        return 0
    store.write_partition(table, df, partition, mode="overwrite", validate=True)
    return len(df)


def _ensure_empty_partition(
    store: ParquetStore,
    table: str,
    partition: Mapping[str, int],
    columns: Sequence[str],
) -> None:
    """Write an empty partition marker so resume skips this slot."""
    path = store._partition_path(table, partition)  # noqa: SLF001
    if path.exists():
        return
    empty = _empty_df(columns)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Bypass schema validation for zero-row frames (pandera dtype issues).
    store.write_partition(table, empty, partition, mode="overwrite", validate=False)


@dataclass
class _RunState:
    partitions_written: int = 0
    partitions_skipped: int = 0
    rows_written: int = 0
    raw_paths: list[Path] = field(default_factory=list)
    school_to_id: dict[str, int] = field(default_factory=dict)
    game_starts: dict[int, datetime] = field(default_factory=dict)
    week_season_types: dict[int, set[str]] = field(default_factory=dict)


def _fetch_archive(
    client: CFBDClient,
    raw_root: Path,
    *,
    endpoint: str,
    fetcher: Callable[[], bytes],
    season: int | None = None,
    week: int | None = None,
    season_type: str | None = None,
) -> tuple[bytes, Path]:
    body = fetcher()
    captured = datetime.now(tz=UTC)
    path = archive_raw_cfbd(
        raw_root,
        captured,
        body,
        endpoint=endpoint,
        season=season,
        week=week,
        season_type=season_type,
    )
    return body, path


def _season_partition_done(
    store: ParquetStore,
    endpoint: str,
    season: int,
    force: bool,
) -> bool:
    table = ENDPOINT_SPECS[endpoint]["table"]
    if not table:
        return False
    if force:
        return False
    return is_partition_complete(store, table, {"season": season})


def _week_partition_done(
    store: ParquetStore,
    endpoint: str,
    season: int,
    week: int,
    force: bool,
) -> bool:
    table = ENDPOINT_SPECS[endpoint]["table"]
    if not table:
        return False
    if force:
        return False
    return is_partition_complete(store, table, {"season": season, "week": week})


def _ingest_season_reference(
    *,
    endpoint: str,
    season: int,
    client: CFBDClient,
    store: ParquetStore,
    raw_root: Path,
    state: _RunState,
    team_map: Mapping[str, str],
    force: bool,
    ingested_at: datetime,
    log: Any,
) -> None:
    if endpoint not in ENDPOINT_SPECS or ENDPOINT_SPECS[endpoint]["grain"] != "season":
        return
    table = ENDPOINT_SPECS[endpoint]["table"]
    if _season_partition_done(store, endpoint, season, force):
        state.partitions_skipped += 1
        log.info(
            "cfbd_partition_skipped",
            endpoint=endpoint,
            season=season,
            table=table,
        )
        return

    fetchers: dict[str, Callable[[], bytes]] = {
        "teams": lambda: client.fetch_teams(season),
        "venues": client.fetch_venues,
        "coaches": lambda: client.fetch_coaches(season),
        "roster": lambda: client.fetch_roster(season),
        "talent": lambda: client.fetch_talent(season),
        "returning": lambda: client.fetch_returning(season),
        "recruiting": lambda: client.fetch_recruiting(season),
        "portal": lambda: client.fetch_portal(season),
    }
    body, raw_path = _fetch_archive(
        client,
        raw_root,
        endpoint=endpoint,
        fetcher=fetchers[endpoint],
        season=season,
    )
    state.raw_paths.append(raw_path)

    normalizers: dict[str, Callable[..., pd.DataFrame]] = {
        "teams": lambda: normalize_teams_payload(
            body, season=season, ingested_at=ingested_at, team_map=team_map
        ),
        "venues": lambda: normalize_venues_payload(body, season=season, ingested_at=ingested_at),
        "coaches": lambda: normalize_coaches_payload(
            body,
            season=season,
            ingested_at=ingested_at,
            school_to_id=state.school_to_id,
            team_map=team_map,
        ),
        "roster": lambda: normalize_roster_payload(
            body,
            season=season,
            ingested_at=ingested_at,
            school_to_id=state.school_to_id,
            team_map=team_map,
        ),
        "talent": lambda: normalize_talent_payload(
            body,
            season=season,
            ingested_at=ingested_at,
            school_to_id=state.school_to_id,
            team_map=team_map,
        ),
        "returning": lambda: normalize_returning_payload(
            body,
            season=season,
            ingested_at=ingested_at,
            school_to_id=state.school_to_id,
            team_map=team_map,
        ),
        "recruiting": lambda: normalize_recruiting_payload(
            body,
            season=season,
            ingested_at=ingested_at,
            school_to_id=state.school_to_id,
            team_map=team_map,
        ),
        "portal": lambda: normalize_portal_payload(
            body,
            season=season,
            ingested_at=ingested_at,
            school_to_id=state.school_to_id,
            team_map=team_map,
        ),
    }
    frame = normalizers[endpoint]()
    if endpoint == "teams":
        state.school_to_id.update(_school_to_id_from_teams(frame))

    if frame.empty:
        _ensure_empty_partition(store, table, {"season": season}, list(frame.columns))
        rows = 0
    else:
        rows = _write_partition(store, table, frame, {"season": season})
    state.partitions_written += 1
    state.rows_written += rows
    log.info(
        "cfbd_partition_written",
        endpoint=endpoint,
        season=season,
        table=table,
        rows=rows,
        raw_path=str(raw_path),
    )


def _ingest_games_season(
    *,
    season: int,
    client: CFBDClient,
    store: ParquetStore,
    raw_root: Path,
    state: _RunState,
    force: bool,
    ingested_at: datetime,
    weeks_filter: set[int] | None,
    log: Any,
) -> None:
    """Fetch regular + postseason games and write per-week partitions."""
    existing_season = store.read("games", filters={"season": season})
    if not force and not existing_season.empty and weeks_filter is None:
        state.game_starts.update(_game_starts_from_games(existing_season))
        if "season_type" in existing_season.columns:
            for _, row in existing_season.iterrows():
                state.week_season_types.setdefault(int(row["week"]), set()).add(
                    str(row["season_type"])
                )
        # Count week partitions as skipped.
        for week in sorted(int(w) for w in existing_season["week"].unique()):
            state.partitions_skipped += 1
            log.info(
                "cfbd_partition_skipped",
                endpoint="games",
                season=season,
                week=week,
                reason="season_already_staged",
            )
        return

    frames: list[pd.DataFrame] = []
    week_season_types: dict[int, set[str]] = {}
    for season_type in ("regular", "postseason"):
        stype = season_type

        def _fetch_games(st: str = stype) -> bytes:
            return client.fetch_games(season, season_type=st)

        body, raw_path = _fetch_archive(
            client,
            raw_root,
            endpoint="games",
            fetcher=_fetch_games,
            season=season,
            season_type=season_type,
        )
        state.raw_paths.append(raw_path)
        frame = normalize_games_payload(body, ingested_at=ingested_at)
        if frame.empty:
            continue
        frames.append(frame)
        for week in frame["week"].unique():
            week_season_types.setdefault(int(week), set()).add(season_type)

    if not frames:
        return

    combined = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["game_id"], keep="last")
    state.game_starts.update(_game_starts_from_games(combined))
    state.week_season_types = week_season_types

    for week, part in combined.groupby("week", sort=True):
        week_i = int(week)
        if weeks_filter is not None and week_i not in weeks_filter:
            continue
        if _week_partition_done(store, "games", season, week_i, force):
            existing = store.read("games", filters={"season": season, "week": week_i})
            if not existing.empty:
                state.game_starts.update(_game_starts_from_games(existing))
            state.partitions_skipped += 1
            log.info(
                "cfbd_partition_skipped",
                endpoint="games",
                season=season,
                week=week_i,
            )
            continue
        partition = {"season": season, "week": week_i}
        rows = _write_partition(store, "games", part, partition)
        state.partitions_written += 1
        state.rows_written += rows
        log.info(
            "cfbd_partition_written",
            endpoint="games",
            season=season,
            week=week_i,
            rows=rows,
        )


def _weeks_for_season(store: ParquetStore, season: int, state: _RunState) -> list[int]:
    weeks: set[int] = set(state.week_season_types)
    games = store.read("games", filters={"season": season})
    if not games.empty:
        weeks.update(int(w) for w in games["week"].unique())
        state.game_starts.update(_game_starts_from_games(games))
        if "season_type" in games.columns:
            for _, row in games.iterrows():
                state.week_season_types.setdefault(int(row["week"]), set()).add(
                    str(row["season_type"])
                )
    if not weeks:
        weeks.update(range(0, MAX_REGULAR_WEEK + 1))
        for w in weeks:
            state.week_season_types.setdefault(w, set()).add("regular")
    return sorted(weeks)


def _archive_only_marker(raw_root: Path, endpoint: str, season: int, week: int) -> Path:
    return raw_root / "_complete" / endpoint / f"s{season}" / f"w{week}.done"


def _ingest_week_endpoint(
    *,
    endpoint: str,
    season: int,
    week: int,
    season_types: Sequence[str],
    client: CFBDClient,
    store: ParquetStore,
    raw_root: Path,
    state: _RunState,
    team_map: Mapping[str, str],
    force: bool,
    ingested_at: datetime,
    log: Any,
) -> None:
    spec = ENDPOINT_SPECS[endpoint]
    table = spec["table"]

    if spec["grain"] == "archive_only":
        marker = _archive_only_marker(raw_root, endpoint, season, week)
        if marker.exists() and not force:
            state.partitions_skipped += 1
            log.info(
                "cfbd_archive_only_skipped",
                endpoint=endpoint,
                season=season,
                week=week,
            )
            return
        for season_type in season_types:
            stype = season_type

            def _fetch_box(st: str = stype) -> bytes:
                return client.fetch_games_teams(season, week, season_type=st)

            body, raw_path = _fetch_archive(
                client,
                raw_root,
                endpoint=endpoint,
                fetcher=_fetch_box,
                season=season,
                week=week,
                season_type=season_type,
            )
            state.raw_paths.append(raw_path)
            log.info(
                "cfbd_archive_only",
                endpoint=endpoint,
                season=season,
                week=week,
                season_type=season_type,
                raw_path=str(raw_path),
            )
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("ok\n", encoding="utf-8")
        state.partitions_written += 1
        return

    if _week_partition_done(store, endpoint, season, week, force):
        state.partitions_skipped += 1
        log.info(
            "cfbd_partition_skipped",
            endpoint=endpoint,
            season=season,
            week=week,
            table=table,
        )
        return

    frames: list[pd.DataFrame] = []
    for season_type in season_types:
        stype = season_type

        def _fetch_plays(st: str = stype) -> bytes:
            return client.fetch_plays(season, week, season_type=st)

        def _fetch_drives(st: str = stype) -> bytes:
            return client.fetch_drives(season, week, season_type=st)

        def _fetch_advanced(st: str = stype) -> bytes:
            return client.fetch_advanced(season, week, season_type=st)

        def _fetch_lines(st: str = stype) -> bytes:
            return client.fetch_lines(season, week, season_type=st)

        fetchers: dict[str, Callable[[], bytes]] = {
            "plays": _fetch_plays,
            "drives": _fetch_drives,
            "advanced": _fetch_advanced,
            "lines": _fetch_lines,
        }
        body, raw_path = _fetch_archive(
            client,
            raw_root,
            endpoint=endpoint,
            fetcher=fetchers[endpoint],
            season=season,
            week=week,
            season_type=season_type,
        )
        state.raw_paths.append(raw_path)

        if endpoint == "plays":
            frame = normalize_plays_payload(
                body,
                season=season,
                week=week,
                ingested_at=ingested_at,
                school_to_id=state.school_to_id,
                team_map=team_map,
                game_start_by_id=state.game_starts,
            )
        elif endpoint == "drives":
            frame = normalize_drives_payload(
                body,
                season=season,
                week=week,
                ingested_at=ingested_at,
                school_to_id=state.school_to_id,
                team_map=team_map,
                game_start_by_id=state.game_starts,
            )
        elif endpoint == "advanced":
            frame = normalize_advanced_payload(
                body,
                season=season,
                week=week,
                ingested_at=ingested_at,
                school_to_id=state.school_to_id,
                team_map=team_map,
                game_start_by_id=state.game_starts,
            )
        else:
            frame = normalize_lines_payload(
                body,
                season=season,
                week=week,
                ingested_at=ingested_at,
                game_start_by_id=state.game_starts,
            )
        if not frame.empty:
            frames.append(frame)

    cols_map = {
        "plays": _PLAYS_COLS,
        "drives": _DRIVES_COLS,
        "advanced": _ADVANCED_COLS,
        "lines": _LINES_COLS,
    }
    partition = {"season": season, "week": week}
    if not frames:
        _ensure_empty_partition(store, table, partition, cols_map[endpoint])
        rows = 0
    else:
        frame = pd.concat(frames, ignore_index=True)
        dedupe_keys: dict[str, list[str]] = {
            "plays": ["play_id"],
            "drives": ["drive_id"],
            "advanced": ["game_id", "team_id"],
            "lines": ["game_id", "book", "line_type"],
        }
        frame = frame.drop_duplicates(subset=dedupe_keys[endpoint], keep="last")
        rows = _write_partition(store, table, frame, partition)
    state.partitions_written += 1
    state.rows_written += rows
    log.info(
        "cfbd_partition_written",
        endpoint=endpoint,
        season=season,
        week=week,
        table=table,
        rows=rows,
    )


def _load_teams_map_into_state(store: ParquetStore, season: int, state: _RunState) -> None:
    teams = store.read("teams", filters={"season": season})
    if not teams.empty:
        state.school_to_id.update(_school_to_id_from_teams(teams))


def run_cfbd_backfill(
    *,
    seasons: Sequence[int],
    endpoints: Sequence[str] | None = None,
    force: bool = False,
    config: AppConfig | None = None,
    api_key: str | None = None,
    raw_root: Path | str | None = None,
    staged_root: Path | str | None = None,
    client: CFBDClient | None = None,
    team_map: Mapping[str, str] | None = None,
    weeks: Sequence[int] | None = None,
) -> CfbdIngestResult:
    """Resumable CFBD backfill for ``seasons`` and selected ``endpoints``."""
    cfg = config or load_config()
    key = api_key if api_key is not None else load_secrets().cfbd_api_key.get_secret_value()
    selected = tuple(endpoints) if endpoints is not None else DEFAULT_ENDPOINTS
    unknown = [e for e in selected if e not in ENDPOINT_SPECS]
    if unknown:
        msg = f"unknown endpoints: {unknown}; known: {sorted(ENDPOINT_SPECS)}"
        raise ValueError(msg)

    raw_dir = Path(raw_root) if raw_root is not None else Path(cfg.paths.raw_dir) / "cfbd"
    staged_dir = Path(staged_root) if staged_root is not None else Path(cfg.paths.staged_dir)
    names = (
        dict(team_map)
        if team_map is not None
        else load_team_name_map(Path(cfg.data.team_names_path))
    )
    weeks_filter = set(weeks) if weeks is not None else None
    ingested_at = datetime.now(tz=UTC)
    log = get_logger(__name__)
    state = _RunState()

    owns_client = client is None
    cfbd = client or CFBDClient(
        key,
        requests_per_second=cfg.data.cfbd_requests_per_second,
    )
    try:
        with ParquetStore(staged_dir) as store:
            for season in seasons:
                log.info("cfbd_season_start", season=season, endpoints=list(selected))
                # Teams first for id resolution.
                ordered_ref = [
                    e
                    for e in (
                        "teams",
                        "venues",
                        "talent",
                        "returning",
                        "recruiting",
                        "portal",
                        "coaches",
                        "roster",
                    )
                    if e in selected
                ]
                if "teams" not in ordered_ref:
                    # Still need school map; load from store or fetch lightly.
                    _load_teams_map_into_state(store, season, state)
                    if not state.school_to_id:
                        _ingest_season_reference(
                            endpoint="teams",
                            season=season,
                            client=cfbd,
                            store=store,
                            raw_root=raw_dir,
                            state=state,
                            team_map=names,
                            force=force,
                            ingested_at=ingested_at,
                            log=log,
                        )
                for endpoint in ordered_ref:
                    _ingest_season_reference(
                        endpoint=endpoint,
                        season=season,
                        client=cfbd,
                        store=store,
                        raw_root=raw_dir,
                        state=state,
                        team_map=names,
                        force=force,
                        ingested_at=ingested_at,
                        log=log,
                    )
                    if endpoint == "teams":
                        _load_teams_map_into_state(store, season, state)

                if "games" in selected:
                    _ingest_games_season(
                        season=season,
                        client=cfbd,
                        store=store,
                        raw_root=raw_dir,
                        state=state,
                        force=force,
                        ingested_at=ingested_at,
                        weeks_filter=weeks_filter,
                        log=log,
                    )
                else:
                    _load_teams_map_into_state(store, season, state)
                    games = store.read("games", filters={"season": season})
                    if not games.empty:
                        state.game_starts.update(_game_starts_from_games(games))

                week_endpoints = [
                    e
                    for e in ("plays", "drives", "advanced", "lines", "games_teams")
                    if e in selected
                ]
                if week_endpoints:
                    for week in _weeks_for_season(store, season, state):
                        if weeks_filter is not None and week not in weeks_filter:
                            continue
                        season_types = sorted(state.week_season_types.get(week) or {"regular"})
                        for endpoint in week_endpoints:
                            _ingest_week_endpoint(
                                endpoint=endpoint,
                                season=season,
                                week=week,
                                season_types=season_types,
                                client=cfbd,
                                store=store,
                                raw_root=raw_dir,
                                state=state,
                                team_map=names,
                                force=force,
                                ingested_at=ingested_at,
                                log=log,
                            )
                log.info(
                    "cfbd_season_done",
                    season=season,
                    partitions_written=state.partitions_written,
                    partitions_skipped=state.partitions_skipped,
                    rows_written=state.rows_written,
                )
    finally:
        if owns_client:
            cfbd.close()

    return CfbdIngestResult(
        seasons=tuple(seasons),
        partitions_written=state.partitions_written,
        partitions_skipped=state.partitions_skipped,
        rows_written=state.rows_written,
        raw_paths=tuple(state.raw_paths),
    )


def run_cfbd_incremental(
    *,
    config: AppConfig | None = None,
    api_key: str | None = None,
    raw_root: Path | str | None = None,
    staged_root: Path | str | None = None,
    client: CFBDClient | None = None,
    team_map: Mapping[str, str] | None = None,
    endpoints: Sequence[str] | None = None,
    now: datetime | None = None,
) -> CfbdIngestResult:
    """Pull current season's missing partitions and recently-changed weeks."""
    cfg = config or load_config()
    ts = to_utc(now or datetime.now(tz=UTC))
    season = season_of(ts)
    season = max(cfg.data.start_season, min(cfg.data.end_season, season))
    staged_dir = Path(staged_root) if staged_root is not None else Path(cfg.paths.staged_dir)

    current_week = week_of(ts, season)
    force_weeks: set[int] = {current_week}

    with ParquetStore(staged_dir) as store:
        games = store.read("games", filters={"season": season})
        if not games.empty and "completed" in games.columns:
            incomplete = games.loc[~games["completed"].astype(bool), "week"]
            force_weeks.update(int(w) for w in incomplete.unique())

    # First pass: fill missing (no force).
    result_missing = run_cfbd_backfill(
        seasons=[season],
        endpoints=endpoints,
        force=False,
        config=cfg,
        api_key=api_key,
        raw_root=raw_root,
        staged_root=staged_root,
        client=client,
        team_map=team_map,
    )
    # Second pass: refresh recently-changed weeks.
    result_force = run_cfbd_backfill(
        seasons=[season],
        endpoints=endpoints,
        force=True,
        config=cfg,
        api_key=api_key,
        raw_root=raw_root,
        staged_root=staged_root,
        client=client,
        team_map=team_map,
        weeks=sorted(force_weeks),
    )
    return CfbdIngestResult(
        seasons=(season,),
        partitions_written=result_missing.partitions_written + result_force.partitions_written,
        partitions_skipped=result_missing.partitions_skipped + result_force.partitions_skipped,
        rows_written=result_missing.rows_written + result_force.rows_written,
        raw_paths=result_missing.raw_paths + result_force.raw_paths,
    )


def resolve_endpoints(names: Iterable[str] | None) -> tuple[str, ...]:
    """Validate and return endpoint short names (default = all)."""
    if names is None:
        return DEFAULT_ENDPOINTS
    out = tuple(names)
    unknown = [e for e in out if e not in ENDPOINT_SPECS]
    if unknown:
        msg = f"unknown endpoints: {unknown}; known: {sorted(ENDPOINT_SPECS)}"
        raise ValueError(msg)
    return out


# Silence unused-import lint for table frozensets kept for documentation linkage.
_ = (GAME_GRAINED_TABLES, REFERENCE_TABLES)
