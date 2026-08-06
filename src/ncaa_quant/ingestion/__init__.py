"""Data ingestion clients for CFBD, odds, weather, and rosters."""

from ncaa_quant.ingestion.cfbd import (
    CFBDClient,
    CfbdIngestResult,
    run_cfbd_backfill,
    run_cfbd_incremental,
)
from ncaa_quant.ingestion.odds_api import (
    OddsAPIClient,
    OddsIngestResult,
    RateLimitBudgetError,
    archive_raw_response,
    dedupe_snapshots,
    make_game_key,
    normalize_odds_payload,
    normalize_team_name,
    run_odds_ingest,
)
from ncaa_quant.ingestion.weather import (
    OpenMeteoClient,
    WeatherIngestResult,
    run_weather_forecast_upcoming,
    run_weather_historical,
)

__all__ = [
    "CFBDClient",
    "CfbdIngestResult",
    "OddsAPIClient",
    "OddsIngestResult",
    "OpenMeteoClient",
    "RateLimitBudgetError",
    "WeatherIngestResult",
    "archive_raw_response",
    "dedupe_snapshots",
    "make_game_key",
    "normalize_odds_payload",
    "normalize_team_name",
    "run_cfbd_backfill",
    "run_cfbd_incremental",
    "run_odds_ingest",
    "run_weather_forecast_upcoming",
    "run_weather_historical",
]
