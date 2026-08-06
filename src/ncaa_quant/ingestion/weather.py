"""Weather and venue enrichment (Open-Meteo + CFBD venues).

Builds the venue reference table from CFBD ``/venues`` plus manual corrections
in ``configs/venues_overrides.yaml``, then attaches kickoff-hour weather from
Open-Meteo (historical archive for past games, forecast for upcoming).

**Venue columns.** Task 6 names ``lat``/``lon``/``is_dome``; the staged schema
keeps CFBD names ``latitude``/``longitude``/``dome`` and adds ``surface`` and
``timezone``. Weather code treats ``dome`` as the is_dome flag.

**Dome handling.** Domed venues get neutral sentinel weather values AND
``weather_applicable=False``. Downstream must key off the flag, never the
sentinel.

**Forecast vs actual.** ``obs_kind`` is ``actual`` or ``forecast``. Forecasts
pulled on different days for the same game are all retained with their own
``captured_at``. Historical actuals are stored separately and never overwrite
a forecast row.

**event_time.** Actuals use kickoff UTC (conditions at the matched local hour).
Forecasts use ``captured_at`` (when the forecast became knowable).
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Literal
from zoneinfo import ZoneInfo

import httpx
import pandas as pd  # type: ignore[import-untyped]
from omegaconf import OmegaConf
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from ncaa_quant.config import AppConfig, load_config, load_secrets
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.ingestion.cfbd import (
    CFBDClient,
    archive_raw_cfbd,
    normalize_venues_payload,
    parse_seasons_arg,
    preseason_event_time,
)
from ncaa_quant.utils.logging import get_logger
from ncaa_quant.utils.timeutils import to_utc, week_of

logging.getLogger("httpx").setLevel(logging.WARNING)

SOURCE_VERSION: Final[str] = "open_meteo_v1"
VENUE_SOURCE_VERSION: Final[str] = "cfbd_venues_enriched_v1"
ARCHIVE_URL: Final[str] = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL: Final[str] = "https://api.open-meteo.com/v1/forecast"

# Open-Meteo hourly variables. precip_prob is forecast-oriented; archive may omit.
HOURLY_VARS: Final[str] = (
    "temperature_2m,relative_humidity_2m,precipitation,snowfall,"
    "wind_speed_10m,wind_gusts_10m,precipitation_probability"
)

ObsKind = Literal["actual", "forecast"]

# Neutral sentinels for dome rows — NEVER interpret these as real weather when
# weather_applicable is False.
DOME_TEMP_C: Final[float] = 21.0
DOME_WIND_MS: Final[float] = 0.0
DOME_PRECIP_MM: Final[float] = 0.0
DOME_PRECIP_PROB: Final[float] = 0.0
DOME_HUMIDITY: Final[float] = 50.0
DOME_SNOW_CM: Final[float] = 0.0

# Primary IANA zone per USPS state / territory for CFB venues. Split-zone
# states use the majority zone; overrides YAML corrects edge venues.
_STATE_TIMEZONES: Final[dict[str, str]] = {
    "AL": "America/Chicago",
    "AK": "America/Anchorage",
    "AZ": "America/Phoenix",
    "AR": "America/Chicago",
    "CA": "America/Los_Angeles",
    "CO": "America/Denver",
    "CT": "America/New_York",
    "DC": "America/New_York",
    "DE": "America/New_York",
    "FL": "America/New_York",
    "GA": "America/New_York",
    "HI": "Pacific/Honolulu",
    "IA": "America/Chicago",
    "ID": "America/Boise",
    "IL": "America/Chicago",
    "IN": "America/Indiana/Indianapolis",
    "KS": "America/Chicago",
    "KY": "America/New_York",
    "LA": "America/Chicago",
    "MA": "America/New_York",
    "MD": "America/New_York",
    "ME": "America/New_York",
    "MI": "America/Detroit",
    "MN": "America/Chicago",
    "MO": "America/Chicago",
    "MS": "America/Chicago",
    "MT": "America/Denver",
    "NC": "America/New_York",
    "ND": "America/Chicago",
    "NE": "America/Chicago",
    "NH": "America/New_York",
    "NJ": "America/New_York",
    "NM": "America/Denver",
    "NV": "America/Los_Angeles",
    "NY": "America/New_York",
    "OH": "America/New_York",
    "OK": "America/Chicago",
    "OR": "America/Los_Angeles",
    "PA": "America/New_York",
    "RI": "America/New_York",
    "SC": "America/New_York",
    "SD": "America/Chicago",
    "TN": "America/Chicago",
    "TX": "America/Chicago",
    "UT": "America/Denver",
    "VA": "America/New_York",
    "VT": "America/New_York",
    "WA": "America/Los_Angeles",
    "WI": "America/Chicago",
    "WV": "America/New_York",
    "WY": "America/Denver",
}

_WEATHER_COLS: Final[tuple[str, ...]] = (
    "game_id",
    "season",
    "week",
    "venue_id",
    "obs_kind",
    "temp_c",
    "wind_speed_ms",
    "wind_gust_ms",
    "precip_mm",
    "precip_prob",
    "humidity",
    "snow",
    "weather_applicable",
    "captured_at",
    "source_version",
    "event_time",
    "ingested_at",
)

__all__ = (
    "DOME_TEMP_C",
    "MissingVenueCoordsError",
    "OpenMeteoClient",
    "OpenMeteoError",
    "WeatherIngestResult",
    "apply_venue_overrides",
    "assert_fbs_host_coords",
    "coverage_report",
    "dome_weather_fields",
    "infer_timezone",
    "load_venue_overrides",
    "local_kickoff_hour",
    "parse_seasons_arg",
    "run_venues_enrichment",
    "run_weather_forecast_upcoming",
    "run_weather_historical",
    "surface_from_grass",
)


class MissingVenueCoordsError(RuntimeError):
    """FBS-hosting venue(s) lack lat/lon after overrides — list for manual fill."""


class OpenMeteoError(RuntimeError):
    """Non-retryable Open-Meteo failure."""


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return False


@dataclass(frozen=True)
class WeatherIngestResult:
    """Summary of a weather / venue enrichment run."""

    seasons: tuple[int, ...]
    venues_written: int
    rows_written: int
    rows_skipped: int
    raw_paths: tuple[Path, ...] = field(default_factory=tuple)
    gaps: tuple[str, ...] = field(default_factory=tuple)


def surface_from_grass(grass: bool | None) -> str | None:
    """Map CFBD ``grass`` bool to a surface label."""
    if grass is True:
        return "grass"
    if grass is False:
        return "turf"
    return None


def infer_timezone(state: str | None) -> str | None:
    """Map a USPS state code to a primary IANA timezone, or None."""
    if state is None:
        return None
    key = str(state).strip().upper()
    if not key:
        return None
    return _STATE_TIMEZONES.get(key)


def load_venue_overrides(path: Path | str) -> dict[int, dict[str, Any]]:
    """Load ``configs/venues_overrides.yaml`` keyed by ``venue_id``."""
    file_path = Path(path)
    if not file_path.is_file():
        return {}
    loaded = OmegaConf.to_container(OmegaConf.load(file_path), resolve=True)
    if not isinstance(loaded, dict):
        msg = f"venues overrides root must be a mapping, got {type(loaded)!r}"
        raise TypeError(msg)
    block = loaded.get("venues", loaded)
    if not isinstance(block, dict):
        msg = f"venues overrides must be a mapping, got {type(block)!r}"
        raise TypeError(msg)
    out: dict[int, dict[str, Any]] = {}
    for key, value in block.items():
        if not isinstance(value, dict):
            continue
        out[int(key)] = dict(value)
    return out


def apply_venue_overrides(
    venues: pd.DataFrame,
    overrides: Mapping[int, Mapping[str, Any]],
) -> pd.DataFrame:
    """Apply per-venue overrides; fill ``surface`` / ``timezone`` defaults."""
    if venues.empty:
        return venues.copy()
    frame = venues.copy()
    if "surface" not in frame.columns:
        frame["surface"] = None
    if "timezone" not in frame.columns:
        frame["timezone"] = None

    for idx, row in frame.iterrows():
        venue_id = int(row["venue_id"])
        ov = overrides.get(venue_id, {})

        if "name" in ov and ov["name"] is not None:
            frame.at[idx, "name"] = str(ov["name"])
        if "city" in ov:
            frame.at[idx, "city"] = None if ov["city"] is None else str(ov["city"])
        if "state" in ov:
            frame.at[idx, "state"] = None if ov["state"] is None else str(ov["state"])

        lat = ov.get("lat", ov.get("latitude"))
        lon = ov.get("lon", ov.get("longitude"))
        if lat is not None:
            frame.at[idx, "latitude"] = float(lat)
        if lon is not None:
            frame.at[idx, "longitude"] = float(lon)
        if "elevation_m" in ov and ov["elevation_m"] is not None:
            frame.at[idx, "elevation_m"] = float(ov["elevation_m"])
        if "capacity" in ov and ov["capacity"] is not None:
            frame.at[idx, "capacity"] = int(ov["capacity"])

        if "is_dome" in ov:
            frame.at[idx, "dome"] = bool(ov["is_dome"])
        elif "dome" in ov:
            frame.at[idx, "dome"] = bool(ov["dome"])

        if "grass" in ov and ov["grass"] is not None:
            frame.at[idx, "grass"] = bool(ov["grass"])

        if "surface" in ov and ov["surface"] is not None:
            frame.at[idx, "surface"] = str(ov["surface"])
        elif pd.isna(frame.at[idx, "surface"]) or frame.at[idx, "surface"] in ("", None):
            grass_val = frame.at[idx, "grass"]
            grass_bool = None if pd.isna(grass_val) else bool(grass_val)
            frame.at[idx, "surface"] = surface_from_grass(grass_bool)

        if "timezone" in ov and ov["timezone"] is not None:
            frame.at[idx, "timezone"] = str(ov["timezone"])
        elif pd.isna(frame.at[idx, "timezone"]) or frame.at[idx, "timezone"] in ("", None):
            state_val = frame.at[idx, "state"]
            state_str = None if pd.isna(state_val) else str(state_val)
            frame.at[idx, "timezone"] = infer_timezone(state_str)

    return frame


def assert_fbs_host_coords(
    venues: pd.DataFrame,
    games: pd.DataFrame,
) -> None:
    """Hard-error if any venue hosting an FBS game lacks lat/lon (list them)."""
    if games.empty:
        return
    used = {int(v) for v in games["venue_id"].dropna().tolist()}
    if not used:
        return
    hosts = venues[venues["venue_id"].isin(used)]
    missing_rows: list[str] = []
    present_ids = set(hosts["venue_id"].astype(int))
    for vid in sorted(used - present_ids):
        missing_rows.append(f"venue_id={vid} (absent from venues table)")
    for _, row in hosts.iterrows():
        lat = row.get("latitude")
        lon = row.get("longitude")
        if pd.isna(lat) or pd.isna(lon):
            missing_rows.append(
                f"venue_id={int(row['venue_id'])} name={row.get('name')!r} "
                f"city={row.get('city')!r} state={row.get('state')!r}"
            )
    if missing_rows:
        listing = "\n".join(f"  - {line}" for line in missing_rows)
        msg = (
            "Missing lat/lon for FBS-hosting venue(s); add to "
            "configs/venues_overrides.yaml:\n"
            f"{listing}"
        )
        raise MissingVenueCoordsError(msg)


def local_kickoff_hour(kickoff_utc: datetime, timezone_name: str) -> datetime:
    """Floor kickoff to the venue-local hour (tz-aware in ``timezone_name``).

    A 7pm local kickoff in Hawaii and in Boston both resolve to hour 19 in
    their respective zones.
    """
    kick = to_utc(kickoff_utc)
    local = kick.astimezone(ZoneInfo(timezone_name))
    return local.replace(minute=0, second=0, microsecond=0)


def dome_weather_fields() -> dict[str, float]:
    """Neutral sentinel weather values for domed venues."""
    return {
        "temp_c": DOME_TEMP_C,
        "wind_speed_ms": DOME_WIND_MS,
        "wind_gust_ms": DOME_WIND_MS,
        "precip_mm": DOME_PRECIP_MM,
        "precip_prob": DOME_PRECIP_PROB,
        "humidity": DOME_HUMIDITY,
        "snow": DOME_SNOW_CM,
    }


def _as_float(value: Any) -> float | None:
    if value is None or value == "" or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class OpenMeteoClient:
    """httpx client for Open-Meteo archive and forecast endpoints."""

    def __init__(
        self,
        *,
        requests_per_second: float = 2.0,
        timeout: float = 60.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout)
        self._min_interval = 1.0 / requests_per_second if requests_per_second > 0 else 0.0
        self._last_request_at = 0.0

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> OpenMeteoClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _throttle(self) -> None:
        if self._min_interval <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _get(self, url: str, params: Mapping[str, Any]) -> bytes:
        self._throttle()
        response = self._client.get(url, params=dict(params))
        self._last_request_at = time.monotonic()
        if response.status_code >= 400:
            response.raise_for_status()
        return response.content

    def fetch_archive(
        self,
        *,
        latitude: float,
        longitude: float,
        local_date: str,
        timezone_name: str,
    ) -> bytes:
        """GET historical hourly weather for one local calendar day."""
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "start_date": local_date,
            "end_date": local_date,
            "hourly": HOURLY_VARS,
            "wind_speed_unit": "ms",
            "timezone": timezone_name,
        }
        return self._get(ARCHIVE_URL, params)

    def fetch_forecast(
        self,
        *,
        latitude: float,
        longitude: float,
        local_date: str,
        timezone_name: str,
    ) -> bytes:
        """GET forecast hourly weather for one local calendar day."""
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "start_date": local_date,
            "end_date": local_date,
            "hourly": HOURLY_VARS,
            "wind_speed_unit": "ms",
            "timezone": timezone_name,
        }
        return self._get(FORECAST_URL, params)


def extract_hour_obs(
    payload: bytes | str | Mapping[str, Any],
    local_hour: datetime,
) -> dict[str, float | None]:
    """Pick the hourly observation matching ``local_hour`` (venue-local)."""
    data: Any = json.loads(payload) if isinstance(payload, (bytes, str)) else payload
    if not isinstance(data, dict):
        msg = "Open-Meteo payload must be a JSON object"
        raise OpenMeteoError(msg)
    hourly = data.get("hourly")
    if not isinstance(hourly, dict):
        msg = "Open-Meteo payload missing hourly block"
        raise OpenMeteoError(msg)
    times = hourly.get("time") or []
    target = local_hour.strftime("%Y-%m-%dT%H:00")
    try:
        idx = list(times).index(target)
    except ValueError as exc:
        msg = f"no hourly slot for local kickoff hour {target!r}"
        raise OpenMeteoError(msg) from exc

    def _at(key: str) -> float | None:
        series = hourly.get(key)
        if not isinstance(series, list) or idx >= len(series):
            return None
        return _as_float(series[idx])

    return {
        "temp_c": _at("temperature_2m"),
        "wind_speed_ms": _at("wind_speed_10m"),
        "wind_gust_ms": _at("wind_gusts_10m"),
        "precip_mm": _at("precipitation"),
        "precip_prob": _at("precipitation_probability"),
        "humidity": _at("relative_humidity_2m"),
        "snow": _at("snowfall"),
    }


def archive_raw_weather(
    raw_root: Path,
    captured_at: datetime,
    body: bytes | str,
    *,
    kind: ObsKind,
    game_id: int,
    local_hour: datetime,
) -> Path:
    """Write payload under ``data/raw/open_meteo/{date}/…json`` before parse."""
    captured = to_utc(captured_at)
    day = captured.date().isoformat()
    stamp = captured.strftime("%Y%m%dT%H%M%S%fZ")
    local_tag = local_hour.strftime("%Y%m%dT%H%M")
    directory = raw_root / day
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{kind}_g{game_id}_{local_tag}_{stamp}.json"
    payload = body if isinstance(body, bytes) else body.encode("utf-8")
    path.write_bytes(payload)
    return path


def _venue_lookup(venues: pd.DataFrame) -> dict[int, pd.Series]:
    return {int(row["venue_id"]): row for _, row in venues.iterrows()}


def _game_week(row: pd.Series, season: int) -> int:
    if pd.notna(row.get("week")):
        return int(row["week"])
    start = row["start_date"]
    start_dt = start.to_pydatetime() if isinstance(start, pd.Timestamp) else start
    return week_of(to_utc(start_dt), season)


def _build_weather_row(
    *,
    game: pd.Series,
    obs_kind: ObsKind,
    fields: Mapping[str, float | None],
    weather_applicable: bool,
    captured_at: datetime,
    ingested_at: datetime,
    event_time: datetime,
    source_version: str = SOURCE_VERSION,
) -> dict[str, Any]:
    season = int(game["season"])
    return {
        "game_id": int(game["game_id"]),
        "season": season,
        "week": _game_week(game, season),
        "venue_id": int(game["venue_id"]) if pd.notna(game.get("venue_id")) else None,
        "obs_kind": obs_kind,
        "temp_c": fields.get("temp_c"),
        "wind_speed_ms": fields.get("wind_speed_ms"),
        "wind_gust_ms": fields.get("wind_gust_ms"),
        "precip_mm": fields.get("precip_mm"),
        "precip_prob": fields.get("precip_prob"),
        "humidity": fields.get("humidity"),
        "snow": fields.get("snow"),
        "weather_applicable": weather_applicable,
        "captured_at": to_utc(captured_at),
        "source_version": source_version,
        "event_time": to_utc(event_time),
        "ingested_at": to_utc(ingested_at),
    }


def _existing_actual_game_ids(store: ParquetStore, season: int) -> set[int]:
    existing = store.read("weather", {"season": season})
    if existing.empty:
        return set()
    actuals = existing[existing["obs_kind"] == "actual"]
    if actuals.empty:
        return set()
    return {int(g) for g in actuals["game_id"].tolist()}


def _write_weather_rows(store: ParquetStore, rows: Sequence[Mapping[str, Any]]) -> int:
    """Merge weather rows into season/week partitions without clobbering peers."""
    if not rows:
        return 0
    frame = pd.DataFrame(list(rows))
    written = 0
    for (season, week), group in frame.groupby(["season", "week"], sort=True):
        partition = {"season": int(season), "week": int(week)}
        existing = store.read("weather", partition)
        if not existing.empty:
            new_keys: set[tuple[Any, ...]] = set()
            for _, row in group.iterrows():
                if row["obs_kind"] == "actual":
                    new_keys.add(("actual", int(row["game_id"])))
                else:
                    cap = to_utc(
                        row["captured_at"].to_pydatetime()
                        if isinstance(row["captured_at"], pd.Timestamp)
                        else row["captured_at"]
                    )
                    new_keys.add(("forecast", int(row["game_id"]), cap.isoformat()))

            keep_mask: list[bool] = []
            for _, row in existing.iterrows():
                if row["obs_kind"] == "actual":
                    keep_mask.append(("actual", int(row["game_id"])) not in new_keys)
                else:
                    cap = to_utc(
                        row["captured_at"].to_pydatetime()
                        if isinstance(row["captured_at"], pd.Timestamp)
                        else row["captured_at"]
                    )
                    key = ("forecast", int(row["game_id"]), cap.isoformat())
                    keep_mask.append(key not in new_keys)
            existing = existing[keep_mask]
            merged = pd.concat([existing, group], ignore_index=True)
        else:
            merged = group.reset_index(drop=True)
        store.write_partition("weather", merged, partition, mode="overwrite")
        written += len(group)
    return written


def run_venues_enrichment(
    seasons: Sequence[int],
    *,
    config: AppConfig | None = None,
    cfbd_client: CFBDClient | None = None,
    overrides: Mapping[int, Mapping[str, Any]] | None = None,
    games_by_season: Mapping[int, pd.DataFrame] | None = None,
) -> WeatherIngestResult:
    """Fetch CFBD venues, apply overrides, write enriched ``venues`` partitions."""
    cfg = config or load_config()
    log = get_logger("ncaa_quant.ingestion.weather")
    season_tuple = tuple(int(s) for s in seasons)
    overrides_map = (
        dict(overrides)
        if overrides is not None
        else load_venue_overrides(cfg.data.venues_overrides_path)
    )
    ingested = datetime.now(tz=UTC)
    owns_client = cfbd_client is None
    if cfbd_client is None:
        key = load_secrets().cfbd_api_key.get_secret_value()
        client = CFBDClient(
            key,
            requests_per_second=cfg.data.cfbd_requests_per_second,
        )
    else:
        client = cfbd_client
    raw_paths: list[Path] = []
    try:
        body = client.fetch_venues()
        raw_path = archive_raw_cfbd(
            Path(cfg.paths.raw_dir) / "cfbd",
            ingested,
            body,
            endpoint="venues_enrich",
            season=season_tuple[0] if season_tuple else None,
        )
        raw_paths.append(raw_path)

        venues_written = 0
        with ParquetStore(cfg.paths.staged_dir) as store:
            for season in season_tuple:
                base = normalize_venues_payload(body, season=season, ingested_at=ingested)
                enriched = apply_venue_overrides(base, overrides_map)
                enriched["source_version"] = VENUE_SOURCE_VERSION
                enriched["event_time"] = preseason_event_time(season)
                enriched["ingested_at"] = ingested

                games = (
                    games_by_season[season]
                    if games_by_season is not None and season in games_by_season
                    else store.read("games", {"season": season})
                )
                assert_fbs_host_coords(enriched, games)

                if not games.empty:
                    used = {int(v) for v in games["venue_id"].dropna().tolist()}
                    hosts = enriched[enriched["venue_id"].isin(used)]
                    missing_tz = hosts[
                        hosts["timezone"].isna() | (hosts["timezone"].astype(str).str.strip() == "")
                    ]
                    if not missing_tz.empty:
                        listing = "\n".join(
                            f"  - venue_id={int(r.venue_id)} name={r.name!r} state={r.state!r}"
                            for r in missing_tz.itertuples()
                        )
                        msg = (
                            "Missing timezone for FBS-hosting venue(s); add to "
                            "configs/venues_overrides.yaml:\n"
                            f"{listing}"
                        )
                        raise MissingVenueCoordsError(msg)

                store.write_partition(
                    "venues",
                    enriched,
                    {"season": season},
                    mode="overwrite",
                )
                venues_written += len(enriched)
                log.info("venues_enriched", season=season, rows=len(enriched))
    finally:
        if owns_client:
            client.close()

    return WeatherIngestResult(
        seasons=season_tuple,
        venues_written=venues_written,
        rows_written=0,
        rows_skipped=0,
        raw_paths=tuple(raw_paths),
    )


def _weather_for_game(
    *,
    game: pd.Series,
    venue: pd.Series,
    client: OpenMeteoClient,
    obs_kind: ObsKind,
    captured_at: datetime,
    ingested_at: datetime,
    raw_root: Path,
) -> tuple[dict[str, Any], Path | None]:
    is_dome = bool(venue["dome"]) if pd.notna(venue.get("dome")) else False
    if is_dome:
        kickoff = to_utc(
            game["start_date"].to_pydatetime()
            if isinstance(game["start_date"], pd.Timestamp)
            else game["start_date"]
        )
        row = _build_weather_row(
            game=game,
            obs_kind=obs_kind,
            fields=dome_weather_fields(),
            weather_applicable=False,
            captured_at=captured_at,
            ingested_at=ingested_at,
            event_time=captured_at if obs_kind == "forecast" else kickoff,
        )
        return row, None

    tz_name = str(venue["timezone"])
    start = game["start_date"]
    kickoff = to_utc(start.to_pydatetime() if isinstance(start, pd.Timestamp) else start)
    local_hour = local_kickoff_hour(kickoff, tz_name)
    local_date = local_hour.date().isoformat()
    lat = float(venue["latitude"])
    lon = float(venue["longitude"])

    if obs_kind == "actual":
        body = client.fetch_archive(
            latitude=lat,
            longitude=lon,
            local_date=local_date,
            timezone_name=tz_name,
        )
    else:
        body = client.fetch_forecast(
            latitude=lat,
            longitude=lon,
            local_date=local_date,
            timezone_name=tz_name,
        )

    raw_path = archive_raw_weather(
        raw_root,
        captured_at,
        body,
        kind=obs_kind,
        game_id=int(game["game_id"]),
        local_hour=local_hour,
    )
    fields = extract_hour_obs(body, local_hour)
    event_time = captured_at if obs_kind == "forecast" else kickoff
    row = _build_weather_row(
        game=game,
        obs_kind=obs_kind,
        fields=fields,
        weather_applicable=True,
        captured_at=captured_at,
        ingested_at=ingested_at,
        event_time=event_time,
    )
    return row, raw_path


def run_weather_historical(
    seasons: Sequence[int],
    *,
    config: AppConfig | None = None,
    force: bool = False,
    enrich_venues: bool = True,
    open_meteo_client: OpenMeteoClient | None = None,
    cfbd_client: CFBDClient | None = None,
) -> WeatherIngestResult:
    """Attach historical (actual) kickoff-hour weather for ``seasons``."""
    cfg = config or load_config()
    log = get_logger("ncaa_quant.ingestion.weather")
    season_tuple = tuple(int(s) for s in seasons)
    raw_paths: list[Path] = []
    gaps: list[str] = []

    if enrich_venues:
        venue_result = run_venues_enrichment(
            season_tuple,
            config=cfg,
            cfbd_client=cfbd_client,
        )
        raw_paths.extend(venue_result.raw_paths)
        venues_written = venue_result.venues_written
    else:
        venues_written = 0

    owns_wx = open_meteo_client is None
    client = open_meteo_client or OpenMeteoClient(
        requests_per_second=cfg.data.open_meteo_requests_per_second,
    )
    ingested = datetime.now(tz=UTC)
    raw_root = Path(cfg.paths.raw_dir) / "open_meteo"
    rows_written = 0
    rows_skipped = 0

    try:
        with ParquetStore(cfg.paths.staged_dir) as store:
            for season in season_tuple:
                games = store.read("games", {"season": season})
                venues = store.read("venues", {"season": season})
                if games.empty:
                    gaps.append(f"season {season}: no staged games")
                    continue
                if venues.empty:
                    msg = f"season {season}: no staged venues — run venue enrichment"
                    raise RuntimeError(msg)
                assert_fbs_host_coords(venues, games)
                by_id = _venue_lookup(venues)
                done = set() if force else _existing_actual_game_ids(store, season)
                batch: list[dict[str, Any]] = []

                for _, game in games.iterrows():
                    gid = int(game["game_id"])
                    if gid in done:
                        rows_skipped += 1
                        continue
                    vid = game.get("venue_id")
                    if pd.isna(vid):
                        gaps.append(f"game_id={gid}: missing venue_id")
                        continue
                    venue = by_id.get(int(vid))
                    if venue is None:
                        gaps.append(f"game_id={gid}: venue_id={int(vid)} not in venues")
                        continue
                    try:
                        row, raw_path = _weather_for_game(
                            game=game,
                            venue=venue,
                            client=client,
                            obs_kind="actual",
                            captured_at=ingested,
                            ingested_at=ingested,
                            raw_root=raw_root,
                        )
                    except (OpenMeteoError, httpx.HTTPError, KeyError, ValueError) as exc:
                        gaps.append(f"game_id={gid}: {exc}")
                        log.warning("weather_game_failed", game_id=gid, error=str(exc))
                        continue
                    batch.append(row)
                    if raw_path is not None:
                        raw_paths.append(raw_path)

                rows_written += _write_weather_rows(store, batch)
                log.info(
                    "weather_season_complete",
                    season=season,
                    written=len(batch),
                    skipped=rows_skipped,
                    gaps=len(gaps),
                )
    finally:
        if owns_wx:
            client.close()

    return WeatherIngestResult(
        seasons=season_tuple,
        venues_written=venues_written,
        rows_written=rows_written,
        rows_skipped=rows_skipped,
        raw_paths=tuple(raw_paths),
        gaps=tuple(gaps),
    )


def run_weather_forecast_upcoming(
    *,
    config: AppConfig | None = None,
    horizon_days: int = 16,
    open_meteo_client: OpenMeteoClient | None = None,
    cfbd_client: CFBDClient | None = None,
) -> WeatherIngestResult:
    """Pull forecasts for upcoming staged games within ``horizon_days``."""
    cfg = config or load_config()
    log = get_logger("ncaa_quant.ingestion.weather")
    now = datetime.now(tz=UTC)
    horizon_end = now + timedelta(days=horizon_days)
    season = now.year if now.month >= 8 else now.year - 1
    seasons = (season,) if now.month != 1 else (season, season + 1)

    venue_result = run_venues_enrichment(seasons, config=cfg, cfbd_client=cfbd_client)
    owns_wx = open_meteo_client is None
    client = open_meteo_client or OpenMeteoClient(
        requests_per_second=cfg.data.open_meteo_requests_per_second,
    )
    ingested = now
    raw_root = Path(cfg.paths.raw_dir) / "open_meteo"
    raw_paths: list[Path] = list(venue_result.raw_paths)
    gaps: list[str] = []
    batch: list[dict[str, Any]] = []
    written = 0

    try:
        with ParquetStore(cfg.paths.staged_dir) as store:
            frames = []
            for s in seasons:
                part = store.read("games", {"season": s})
                if not part.empty:
                    frames.append(part)
            if not frames:
                return WeatherIngestResult(
                    seasons=seasons,
                    venues_written=venue_result.venues_written,
                    rows_written=0,
                    rows_skipped=0,
                    raw_paths=tuple(raw_paths),
                    gaps=("no staged games for upcoming seasons",),
                )
            games = pd.concat(frames, ignore_index=True)
            venue_frames = [store.read("venues", {"season": s}) for s in seasons]
            venues = pd.concat(
                [vf for vf in venue_frames if not vf.empty],
                ignore_index=True,
            )
            venues = venues.sort_values("season").drop_duplicates("venue_id", keep="last")
            by_id = _venue_lookup(venues)

            for _, game in games.iterrows():
                start = game["start_date"]
                kickoff = to_utc(
                    start.to_pydatetime() if isinstance(start, pd.Timestamp) else start
                )
                if kickoff <= now or kickoff > horizon_end:
                    continue
                vid = game.get("venue_id")
                if pd.isna(vid):
                    gaps.append(f"game_id={int(game['game_id'])}: missing venue_id")
                    continue
                venue = by_id.get(int(vid))
                if venue is None:
                    gaps.append(f"game_id={int(game['game_id'])}: venue_id={int(vid)} missing")
                    continue
                try:
                    row, raw_path = _weather_for_game(
                        game=game,
                        venue=venue,
                        client=client,
                        obs_kind="forecast",
                        captured_at=ingested,
                        ingested_at=ingested,
                        raw_root=raw_root,
                    )
                except (OpenMeteoError, httpx.HTTPError, KeyError, ValueError) as exc:
                    gaps.append(f"game_id={int(game['game_id'])}: {exc}")
                    log.warning(
                        "weather_forecast_failed",
                        game_id=int(game["game_id"]),
                        error=str(exc),
                    )
                    continue
                batch.append(row)
                if raw_path is not None:
                    raw_paths.append(raw_path)

            written = _write_weather_rows(store, batch)
    finally:
        if owns_wx:
            client.close()

    return WeatherIngestResult(
        seasons=seasons,
        venues_written=venue_result.venues_written,
        rows_written=written,
        rows_skipped=0,
        raw_paths=tuple(raw_paths),
        gaps=tuple(gaps),
    )


def coverage_report(store: ParquetStore, season: int) -> list[str]:
    """Outdoor FBS weather coverage lines for ``season`` (acceptance)."""
    games = store.read("games", {"season": season})
    venues = store.read("venues", {"season": season})
    weather = store.read("weather", {"season": season})
    lines = [f"weather coverage season={season}"]
    if games.empty:
        lines.append("  no staged games")
        return lines

    by_id = _venue_lookup(venues) if not venues.empty else {}
    outdoor_ids: list[int] = []
    dome_ids: list[int] = []
    for _, game in games.iterrows():
        gid = int(game["game_id"])
        vid = game.get("venue_id")
        if pd.isna(vid):
            outdoor_ids.append(gid)
            continue
        venue = by_id.get(int(vid))
        is_dome = (
            bool(venue["dome"]) if venue is not None and pd.notna(venue.get("dome")) else False
        )
        if is_dome:
            dome_ids.append(gid)
        else:
            outdoor_ids.append(gid)

    actuals = weather[weather["obs_kind"] == "actual"] if not weather.empty else pd.DataFrame()
    covered = set(actuals["game_id"].astype(int)) if not actuals.empty else set()
    outdoor_covered = [g for g in outdoor_ids if g in covered]
    outdoor_gaps = [g for g in outdoor_ids if g not in covered]
    dome_covered = [g for g in dome_ids if g in covered]

    outdoor_pct = 100.0 * len(outdoor_covered) / len(outdoor_ids) if outdoor_ids else 100.0
    lines.append(
        f"  outdoor FBS games={len(outdoor_ids)} "
        f"with_actual_weather={len(outdoor_covered)} "
        f"coverage={outdoor_pct:.1f}%"
    )
    lines.append(f"  dome games={len(dome_ids)} with_sentinel_rows={len(dome_covered)}")
    if outdoor_gaps:
        preview = ", ".join(str(g) for g in outdoor_gaps[:25])
        more = "" if len(outdoor_gaps) <= 25 else f" … (+{len(outdoor_gaps) - 25} more)"
        lines.append(f"  outdoor gaps game_id=[{preview}{more}]")
    else:
        lines.append("  outdoor gaps: none")
    return lines
