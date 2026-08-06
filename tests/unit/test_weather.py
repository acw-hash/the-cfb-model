"""Tests for weather and venue enrichment (Task 6)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import pytest
from typer.testing import CliRunner

from ncaa_quant.cli import app
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.ingestion.weather import (
    DOME_TEMP_C,
    MissingVenueCoordsError,
    OpenMeteoClient,
    apply_venue_overrides,
    assert_fbs_host_coords,
    dome_weather_fields,
    extract_hour_obs,
    local_kickoff_hour,
    run_weather_historical,
)
from ncaa_quant.utils.logging import configure_logging

INGESTED = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)


def _hourly_payload(local_times: list[str], **series: list[float | None]) -> dict:
    hourly: dict = {"time": local_times}
    hourly.update(series)
    return {"hourly": hourly}


def test_local_kickoff_hour_hawaii_and_boston() -> None:
    """7pm local in Hawaii and Boston both resolve to hour 19 in-zone."""
    # 2023-09-02 19:00 Hawaii → 2023-09-03 05:00 UTC
    hawaii_utc = datetime(2023, 9, 3, 5, 0, tzinfo=UTC)
    hi_local = local_kickoff_hour(hawaii_utc, "Pacific/Honolulu")
    assert hi_local.hour == 19
    assert hi_local.tzinfo == ZoneInfo("Pacific/Honolulu")

    # 2023-09-02 19:00 America/New_York → 2023-09-02 23:00 UTC
    boston_utc = datetime(2023, 9, 2, 23, 0, tzinfo=UTC)
    bos_local = local_kickoff_hour(boston_utc, "America/New_York")
    assert bos_local.hour == 19
    assert bos_local.tzinfo == ZoneInfo("America/New_York")


def test_dome_flag_uses_sentinels_and_not_applicable() -> None:
    fields = dome_weather_fields()
    assert fields["temp_c"] == DOME_TEMP_C
    assert fields["wind_speed_ms"] == 0.0
    assert fields["precip_mm"] == 0.0
    # Downstream keys off weather_applicable, never these values alone.
    assert "weather_applicable" not in fields


def test_missing_venue_coords_hard_error() -> None:
    venues = pd.DataFrame(
        [
            {
                "venue_id": 1,
                "name": "Mystery Bowl",
                "city": "Nowhere",
                "state": "ZZ",
                "latitude": None,
                "longitude": None,
            }
        ]
    )
    games = pd.DataFrame([{"game_id": 10, "venue_id": 1}])
    with pytest.raises(MissingVenueCoordsError) as exc_info:
        assert_fbs_host_coords(venues, games)
    assert "venue_id=1" in str(exc_info.value)
    assert "venues_overrides.yaml" in str(exc_info.value)


def test_apply_overrides_and_timezone_inference() -> None:
    venues = pd.DataFrame(
        [
            {
                "venue_id": 3504,
                "name": "Aviva Stadium",
                "city": "Dublin",
                "state": "",
                "latitude": 53.3,
                "longitude": -6.2,
                "elevation_m": 10.0,
                "capacity": 50000,
                "grass": True,
                "dome": False,
                "surface": None,
                "timezone": None,
            },
            {
                "venue_id": 100,
                "name": "Bryant-Denny",
                "city": "Tuscaloosa",
                "state": "AL",
                "latitude": 33.2,
                "longitude": -87.5,
                "elevation_m": 70.0,
                "capacity": 100000,
                "grass": True,
                "dome": False,
                "surface": None,
                "timezone": None,
            },
        ]
    )
    out = apply_venue_overrides(
        venues,
        {3504: {"timezone": "Europe/Dublin", "surface": "grass", "is_dome": False}},
    )
    assert out.loc[out["venue_id"] == 3504, "timezone"].iloc[0] == "Europe/Dublin"
    assert out.loc[out["venue_id"] == 100, "timezone"].iloc[0] == "America/Chicago"
    assert out.loc[out["venue_id"] == 100, "surface"].iloc[0] == "grass"


def test_forecast_actual_separation(tmp_path: Path) -> None:
    """Writing an actual must not erase a prior forecast for the same game."""
    configure_logging()
    staged = tmp_path / "staged"
    kickoff = datetime(2023, 9, 2, 23, 0, tzinfo=UTC)
    games = pd.DataFrame(
        [
            {
                "game_id": 401520182,
                "season": 2023,
                "week": 1,
                "season_type": "regular",
                "start_date": kickoff,
                "home_team_id": 333,
                "away_team_id": 99,
                "home_points": 56,
                "away_points": 19,
                "neutral_site": False,
                "conference_game": True,
                "venue_id": 3853,
                "completed": True,
                "source_version": "test",
                "event_time": kickoff,
                "ingested_at": INGESTED,
            }
        ]
    )
    venues = pd.DataFrame(
        [
            {
                "venue_id": 3853,
                "season": 2023,
                "name": "Bryant-Denny Stadium",
                "city": "Tuscaloosa",
                "state": "AL",
                "latitude": 33.208,
                "longitude": -87.550,
                "elevation_m": 70.0,
                "capacity": 100077,
                "grass": True,
                "dome": False,
                "surface": "grass",
                "timezone": "America/Chicago",
                "source_version": "test",
                "event_time": INGESTED,
                "ingested_at": INGESTED,
            }
        ]
    )

    forecast_cap = datetime(2023, 9, 1, 12, 0, tzinfo=UTC)
    forecast_row = {
        "game_id": 401520182,
        "season": 2023,
        "week": 1,
        "venue_id": 3853,
        "obs_kind": "forecast",
        "temp_c": 30.0,
        "wind_speed_ms": 3.0,
        "wind_gust_ms": 5.0,
        "precip_mm": 0.0,
        "precip_prob": 10.0,
        "humidity": 55.0,
        "snow": 0.0,
        "weather_applicable": True,
        "captured_at": forecast_cap,
        "source_version": "test",
        "event_time": forecast_cap,
        "ingested_at": INGESTED,
    }
    actual_row = {
        **forecast_row,
        "obs_kind": "actual",
        "temp_c": 28.0,
        "precip_prob": None,
        "captured_at": INGESTED,
        "event_time": kickoff,
    }

    with ParquetStore(staged) as store:
        store.write_partition("games", games, {"season": 2023, "week": 1})
        store.write_partition("venues", venues, {"season": 2023})
        store.write_partition(
            "weather",
            pd.DataFrame([forecast_row]),
            {"season": 2023, "week": 1},
        )
        # Simulate merge write used by weather ingest.
        from ncaa_quant.ingestion.weather import _write_weather_rows

        _write_weather_rows(store, [actual_row])
        wx = store.read("weather", {"season": 2023})

    kinds = sorted(wx["obs_kind"].tolist())
    assert kinds == ["actual", "forecast"]
    assert float(wx.loc[wx["obs_kind"] == "forecast", "temp_c"].iloc[0]) == 30.0
    assert float(wx.loc[wx["obs_kind"] == "actual", "temp_c"].iloc[0]) == 28.0


def test_extract_hour_and_dome_path_via_mock_client(tmp_path: Path) -> None:
    configure_logging()
    kickoff = datetime(2023, 9, 2, 23, 0, tzinfo=UTC)  # 18:00 America/Chicago
    local = local_kickoff_hour(kickoff, "America/Chicago")
    assert local.hour == 18

    payload = _hourly_payload(
        [f"2023-09-02T{h:02d}:00" for h in range(24)],
        temperature_2m=[20.0 + h for h in range(24)],
        relative_humidity_2m=[50.0] * 24,
        precipitation=[0.0] * 24,
        snowfall=[0.0] * 24,
        wind_speed_10m=[2.0] * 24,
        wind_gusts_10m=[4.0] * 24,
        precipitation_probability=[None] * 24,
    )
    obs = extract_hour_obs(payload, local)
    assert obs["temp_c"] == pytest.approx(38.0)  # 20 + 18

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    client = OpenMeteoClient(client=httpx.Client(transport=transport), requests_per_second=0)

    staged = tmp_path / "staged"
    raw = tmp_path / "raw"
    games = pd.DataFrame(
        [
            {
                "game_id": 1,
                "season": 2023,
                "week": 1,
                "season_type": "regular",
                "start_date": kickoff,
                "home_team_id": 1,
                "away_team_id": 2,
                "home_points": 21,
                "away_points": 14,
                "neutral_site": False,
                "conference_game": False,
                "venue_id": 10,
                "completed": True,
                "source_version": "test",
                "event_time": kickoff,
                "ingested_at": INGESTED,
            },
            {
                "game_id": 2,
                "season": 2023,
                "week": 1,
                "season_type": "regular",
                "start_date": kickoff,
                "home_team_id": 3,
                "away_team_id": 4,
                "home_points": 17,
                "away_points": 10,
                "neutral_site": False,
                "conference_game": False,
                "venue_id": 20,
                "completed": True,
                "source_version": "test",
                "event_time": kickoff,
                "ingested_at": INGESTED,
            },
        ]
    )
    venues = pd.DataFrame(
        [
            {
                "venue_id": 10,
                "season": 2023,
                "name": "Outdoor",
                "city": "Tuscaloosa",
                "state": "AL",
                "latitude": 33.2,
                "longitude": -87.5,
                "elevation_m": 70.0,
                "capacity": 100000,
                "grass": True,
                "dome": False,
                "surface": "grass",
                "timezone": "America/Chicago",
                "source_version": "test",
                "event_time": INGESTED,
                "ingested_at": INGESTED,
            },
            {
                "venue_id": 20,
                "season": 2023,
                "name": "Dome",
                "city": "Detroit",
                "state": "MI",
                "latitude": 42.3,
                "longitude": -83.0,
                "elevation_m": 180.0,
                "capacity": 65000,
                "grass": False,
                "dome": True,
                "surface": "turf",
                "timezone": "America/Detroit",
                "source_version": "test",
                "event_time": INGESTED,
                "ingested_at": INGESTED,
            },
        ]
    )
    with ParquetStore(staged) as store:
        store.write_partition("games", games, {"season": 2023, "week": 1})
        store.write_partition("venues", venues, {"season": 2023})

    from ncaa_quant.config import AppConfig, DataConfig, PathsConfig

    cfg = AppConfig(
        paths=PathsConfig(
            data_dir=str(tmp_path),
            raw_dir=str(raw),
            staged_dir=str(staged),
            features_dir=str(tmp_path / "features"),
            predictions_dir=str(tmp_path / "predictions"),
        ),
        data=DataConfig(open_meteo_requests_per_second=0.0),
    )
    result = run_weather_historical(
        [2023],
        config=cfg,
        force=True,
        enrich_venues=False,
        open_meteo_client=client,
    )
    assert result.rows_written == 2
    with ParquetStore(staged) as store:
        wx = store.read("weather", {"season": 2023})
    outdoor = wx[wx["game_id"] == 1].iloc[0]
    dome = wx[wx["game_id"] == 2].iloc[0]
    assert bool(outdoor["weather_applicable"]) is True
    assert outdoor["temp_c"] == pytest.approx(38.0)
    assert bool(dome["weather_applicable"]) is False
    assert dome["temp_c"] == DOME_TEMP_C


def test_cli_weather_requires_args() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["ingest", "weather"])
    assert result.exit_code == 2


def test_load_venue_overrides_and_missing_file(tmp_path: Path) -> None:
    from ncaa_quant.ingestion.weather import load_venue_overrides

    assert load_venue_overrides(tmp_path / "missing.yaml") == {}
    path = tmp_path / "overrides.yaml"
    path.write_text(
        "venues:\n  99:\n    timezone: America/Denver\n    lat: 39.7\n    lon: -104.9\n",
        encoding="utf-8",
    )
    loaded = load_venue_overrides(path)
    assert loaded[99]["timezone"] == "America/Denver"
    assert loaded[99]["lat"] == pytest.approx(39.7)


def test_extract_hour_obs_errors() -> None:
    from ncaa_quant.ingestion.weather import OpenMeteoError

    local = datetime(2023, 9, 2, 18, 0, tzinfo=ZoneInfo("America/Chicago"))
    with pytest.raises(OpenMeteoError, match="JSON object"):
        extract_hour_obs([1, 2, 3], local)
    with pytest.raises(OpenMeteoError, match="hourly"):
        extract_hour_obs({"daily": {}}, local)
    with pytest.raises(OpenMeteoError, match="no hourly slot"):
        extract_hour_obs({"hourly": {"time": ["2023-09-02T00:00"]}}, local)


def test_run_venues_enrichment_mocked(tmp_path: Path) -> None:
    from ncaa_quant.config import AppConfig, DataConfig, PathsConfig
    from ncaa_quant.ingestion.cfbd import CFBDClient
    from ncaa_quant.ingestion.weather import run_venues_enrichment

    venues_payload = [
        {
            "id": 1,
            "name": "Bryant-Denny",
            "city": "Tuscaloosa",
            "state": "AL",
            "latitude": 33.2,
            "longitude": -87.5,
            "elevation": 70.0,
            "capacity": 100000,
            "grass": True,
            "dome": False,
        },
        {
            "id": 3504,
            "name": "Aviva Stadium",
            "city": "Dublin",
            "state": "",
            "latitude": 53.3,
            "longitude": -6.2,
            "elevation": 10.0,
            "capacity": 50000,
            "grass": True,
            "dome": False,
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/venues")
        return httpx.Response(200, json=venues_payload)

    client = CFBDClient(
        "test-key",
        transport=httpx.MockTransport(handler),
        requests_per_second=1000.0,
    )
    staged = tmp_path / "staged"
    raw = tmp_path / "raw"
    kickoff = datetime(2023, 9, 2, 23, 0, tzinfo=UTC)
    games = pd.DataFrame(
        [
            {
                "game_id": 1,
                "season": 2023,
                "week": 1,
                "season_type": "regular",
                "start_date": kickoff,
                "home_team_id": 1,
                "away_team_id": 2,
                "home_points": 21,
                "away_points": 14,
                "neutral_site": False,
                "conference_game": False,
                "venue_id": 1,
                "completed": True,
                "source_version": "test",
                "event_time": kickoff,
                "ingested_at": INGESTED,
            },
            {
                "game_id": 2,
                "season": 2023,
                "week": 1,
                "season_type": "regular",
                "start_date": kickoff,
                "home_team_id": 3,
                "away_team_id": 4,
                "home_points": 17,
                "away_points": 10,
                "neutral_site": True,
                "conference_game": False,
                "venue_id": 3504,
                "completed": True,
                "source_version": "test",
                "event_time": kickoff,
                "ingested_at": INGESTED,
            },
        ]
    )
    with ParquetStore(staged) as store:
        store.write_partition("games", games, {"season": 2023, "week": 1})

    overrides = tmp_path / "venues_overrides.yaml"
    overrides.write_text(
        "venues:\n  3504:\n    timezone: Europe/Dublin\n",
        encoding="utf-8",
    )
    cfg = AppConfig(
        paths=PathsConfig(
            data_dir=str(tmp_path),
            raw_dir=str(raw),
            staged_dir=str(staged),
            features_dir=str(tmp_path / "features"),
            predictions_dir=str(tmp_path / "predictions"),
        ),
        data=DataConfig(venues_overrides_path=str(overrides)),
    )
    result = run_venues_enrichment([2023], config=cfg, cfbd_client=client)
    assert result.venues_written >= 2
    with ParquetStore(staged) as store:
        venues = store.read("venues", {"season": 2023})
    assert venues.loc[venues["venue_id"] == 1, "timezone"].iloc[0] == "America/Chicago"
    assert venues.loc[venues["venue_id"] == 3504, "timezone"].iloc[0] == "Europe/Dublin"


def test_coverage_report_and_forecast_upcoming(tmp_path: Path) -> None:
    from ncaa_quant.config import AppConfig, DataConfig, PathsConfig
    from ncaa_quant.ingestion.cfbd import CFBDClient
    from ncaa_quant.ingestion.weather import (
        coverage_report,
        run_weather_forecast_upcoming,
    )

    kickoff_future = datetime.now(tz=UTC).replace(microsecond=0) + pd.Timedelta(days=3)
    # Ensure timezone-aware python datetime
    if isinstance(kickoff_future, pd.Timestamp):
        kickoff_future = kickoff_future.to_pydatetime()
    season = kickoff_future.year if kickoff_future.month >= 8 else kickoff_future.year - 1

    venues_payload = [
        {
            "id": 10,
            "name": "Outdoor",
            "city": "Boston",
            "state": "MA",
            "latitude": 42.3,
            "longitude": -71.1,
            "elevation": 10.0,
            "capacity": 60000,
            "grass": True,
            "dome": False,
        }
    ]

    def cfbd_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=venues_payload)

    local = local_kickoff_hour(kickoff_future, "America/New_York")
    times = [f"{local.date().isoformat()}T{h:02d}:00" for h in range(24)]
    payload = _hourly_payload(
        times,
        temperature_2m=[15.0] * 24,
        relative_humidity_2m=[60.0] * 24,
        precipitation=[0.1] * 24,
        snowfall=[0.0] * 24,
        wind_speed_10m=[3.0] * 24,
        wind_gusts_10m=[5.0] * 24,
        precipitation_probability=[20.0] * 24,
    )

    def wx_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    staged = tmp_path / "staged"
    raw = tmp_path / "raw"
    games = pd.DataFrame(
        [
            {
                "game_id": 99,
                "season": season,
                "week": 1,
                "season_type": "regular",
                "start_date": kickoff_future,
                "home_team_id": 1,
                "away_team_id": 2,
                "home_points": None,
                "away_points": None,
                "neutral_site": False,
                "conference_game": False,
                "venue_id": 10,
                "completed": False,
                "source_version": "test",
                "event_time": kickoff_future,
                "ingested_at": kickoff_future,
            }
        ]
    )
    with ParquetStore(staged) as store:
        store.write_partition("games", games, {"season": season, "week": 1})

    cfg = AppConfig(
        paths=PathsConfig(
            data_dir=str(tmp_path),
            raw_dir=str(raw),
            staged_dir=str(staged),
            features_dir=str(tmp_path / "features"),
            predictions_dir=str(tmp_path / "predictions"),
        ),
        data=DataConfig(open_meteo_requests_per_second=0.0),
    )
    cfbd = CFBDClient(
        "test-key",
        transport=httpx.MockTransport(cfbd_handler),
        requests_per_second=1000.0,
    )
    wx = OpenMeteoClient(
        client=httpx.Client(transport=httpx.MockTransport(wx_handler)),
        requests_per_second=0.0,
    )
    result = run_weather_forecast_upcoming(
        config=cfg,
        horizon_days=10,
        open_meteo_client=wx,
        cfbd_client=cfbd,
    )
    assert result.rows_written == 1

    # Seed an actual for coverage_report outdoor path.
    with ParquetStore(staged) as store:
        existing = store.read("weather", {"season": season})
        actual = existing.iloc[0].to_dict()
        actual["obs_kind"] = "actual"
        actual["captured_at"] = kickoff_future
        actual["event_time"] = kickoff_future
        actual["ingested_at"] = kickoff_future
        from ncaa_quant.ingestion.weather import _write_weather_rows

        _write_weather_rows(store, [actual])
        lines = coverage_report(store, season)
    assert any("coverage=100.0%" in line for line in lines)
    assert any("outdoor gaps: none" in line for line in lines)


def test_cli_weather_historical_monkeypatched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ncaa_quant.ingestion import weather as weather_mod

    class _Result:
        seasons = (2023,)
        venues_written = 1
        rows_written = 2
        rows_skipped = 0
        raw_paths = ()
        gaps = ()

    monkeypatch.setattr(weather_mod, "run_weather_historical", lambda **kwargs: _Result())
    monkeypatch.setattr(
        weather_mod,
        "coverage_report",
        lambda store, season: [f"weather coverage season={season}", "  outdoor gaps: none"],
    )

    runner = CliRunner()
    result = runner.invoke(app, ["ingest", "weather", "--seasons", "2023"])
    assert result.exit_code == 0
    assert "rows_written=2" in result.stdout
    assert "outdoor gaps: none" in result.stdout


def test_open_meteo_client_archive_and_forecast() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        local_times = [f"2023-09-02T{h:02d}:00" for h in range(24)]
        return httpx.Response(
            200,
            json=_hourly_payload(
                local_times,
                temperature_2m=[10.0] * 24,
                relative_humidity_2m=[40.0] * 24,
                precipitation=[0.0] * 24,
                snowfall=[0.0] * 24,
                wind_speed_10m=[1.0] * 24,
                wind_gusts_10m=[2.0] * 24,
                precipitation_probability=[5.0] * 24,
            ),
        )

    with OpenMeteoClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        requests_per_second=0.0,
    ) as client:
        body = client.fetch_archive(
            latitude=33.2,
            longitude=-87.5,
            local_date="2023-09-02",
            timezone_name="America/Chicago",
        )
        assert b"temperature_2m" in body
        body2 = client.fetch_forecast(
            latitude=33.2,
            longitude=-87.5,
            local_date="2023-09-02",
            timezone_name="America/Chicago",
        )
        assert b"hourly" in body2
    assert any("archive" in u for u in seen)
    assert any("forecast" in u for u in seen)


def test_missing_coords_for_absent_venue() -> None:
    venues = pd.DataFrame([{"venue_id": 1, "name": "A", "latitude": 1.0, "longitude": 2.0}])
    games = pd.DataFrame([{"game_id": 10, "venue_id": 99}])
    with pytest.raises(MissingVenueCoordsError, match="absent from venues table"):
        assert_fbs_host_coords(venues, games)
