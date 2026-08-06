"""Tests for The Odds API snapshot ingestion (live + historical)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd
import pytest

from ncaa_quant.config import AppConfig, load_config
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.ingestion.odds_api import (
    CalibrationError,
    HistoricalBudgetCeilingError,
    OddsAPIClient,
    RateLimitBudgetError,
    archive_historical_response,
    archive_raw_response,
    asof_tolerance_for,
    dedupe_snapshots,
    estimate_historical_credits,
    is_unit_complete,
    load_team_name_map,
    make_game_key,
    mark_unit_complete,
    normalize_odds_payload,
    normalize_team_name,
    parse_historical_envelope,
    plan_historical_units,
    run_historical_backfill,
    run_odds_ingest,
    run_odds_raw_capture,
    tuesday_0600_et_for_week,
    within_asof_tolerance,
    write_odds_snapshots,
)
from ncaa_quant.utils.logging import configure_logging

SAMPLE_PAYLOAD = [
    {
        "id": "evt1",
        "sport_key": "americanfootball_ncaaf",
        "commence_time": "2024-09-07T19:00:00Z",
        "home_team": "Michigan Wolverines",
        "away_team": "Texas Longhorns",
        "bookmakers": [
            {
                "key": "draftkings",
                "title": "DraftKings",
                "markets": [
                    {
                        "key": "spreads",
                        "outcomes": [
                            {"name": "Michigan Wolverines", "price": -110, "point": -3.5},
                            {"name": "Texas Longhorns", "price": -110, "point": 3.5},
                        ],
                    },
                    {
                        "key": "totals",
                        "outcomes": [
                            {"name": "Over", "price": -105, "point": 48.5},
                            {"name": "Under", "price": -115, "point": 48.5},
                        ],
                    },
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Michigan Wolverines", "price": -165},
                            {"name": "Texas Longhorns", "price": 140},
                        ],
                    },
                ],
            }
        ],
    }
]


def _historical_envelope(
    data: list[object],
    *,
    timestamp: str = "2024-09-03T10:00:00Z",
    previous: str = "2024-09-03T09:55:00Z",
    next_ts: str = "2024-09-03T10:05:00Z",
) -> dict[str, object]:
    return {
        "timestamp": timestamp,
        "previous_timestamp": previous,
        "next_timestamp": next_ts,
        "data": data,
    }


@pytest.fixture(autouse=True)
def _logging() -> None:
    configure_logging(level="INFO")


@pytest.fixture
def team_map() -> dict[str, str]:
    return load_team_name_map(Path("configs/team_names.yaml"))


def test_normalize_team_name_map_hit(team_map: dict[str, str]) -> None:
    assert normalize_team_name("Michigan Wolverines", team_map) == "Michigan"
    assert normalize_team_name("  ohio STATE   buckeyes ", team_map) == "Ohio State"
    assert normalize_team_name("Miami (OH) RedHawks", team_map) == "Miami (OH)"


def test_normalize_team_name_mascot_fallback(team_map: dict[str, str]) -> None:
    assert normalize_team_name("Exampleville Wildcats", team_map) == "Exampleville"


def test_make_game_key_stable(team_map: dict[str, str]) -> None:
    home = normalize_team_name("Michigan Wolverines", team_map)
    away = normalize_team_name("Texas Longhorns", team_map)
    key = make_game_key(2024, home, away, datetime(2024, 9, 7, tzinfo=UTC).date())
    assert key == "2024:Michigan:Texas:2024-09-07"


def test_normalize_payload_shape(team_map: dict[str, str]) -> None:
    captured = datetime(2024, 9, 1, 12, 0, 0, tzinfo=UTC)
    df = normalize_odds_payload(
        SAMPLE_PAYLOAD,
        captured_at=captured,
        ingested_at=captured,
        team_map=team_map,
    )
    assert len(df) == 6
    assert set(df["market"]) == {"spread", "total", "h2h"}
    assert (df["game_key"] == "2024:Michigan:Texas:2024-09-07").all()
    assert (df["event_time"] == df["captured_at"]).all()
    assert (df["snapshot_source"] == "live").all()
    assert df["decision_point"].isna().all()
    assert (df["n_books_available"] == 1).all()
    assert df["price"].dtype == float


def test_dedupe_same_minute_keeps_one() -> None:
    captured = pd.Timestamp("2024-09-01T12:00:30", tz="UTC")
    other = pd.Timestamp("2024-09-01T12:00:55", tz="UTC")
    rows = []
    for ts in (captured, other):
        rows.append(
            {
                "snapshot_id": f"id-{ts.second}",
                "game_key": "2024:A:B:2024-09-01",
                "game_id": None,
                "season": 2024,
                "week": 1,
                "book": "draftkings",
                "market": "spread",
                "side": "A",
                "line": -3.5,
                "price": -110.0,
                "home_team": "A",
                "away_team": "B",
                "captured_at": ts,
                "source_version": "test",
                "snapshot_source": "live",
                "decision_point": None,
                "n_books_available": 1,
                "event_time": ts,
                "ingested_at": ts,
            }
        )
    out = dedupe_snapshots(pd.DataFrame(rows))
    assert len(out) == 1


def test_dedupe_retains_line_movement_across_minutes() -> None:
    t0 = pd.Timestamp("2024-09-01T12:00:00", tz="UTC")
    t1 = pd.Timestamp("2024-09-01T12:01:00", tz="UTC")
    rows = [
        {
            "snapshot_id": "a",
            "game_key": "2024:A:B:2024-09-01",
            "game_id": None,
            "season": 2024,
            "week": 1,
            "book": "draftkings",
            "market": "spread",
            "side": "A",
            "line": -3.5,
            "price": -110.0,
            "home_team": "A",
            "away_team": "B",
            "captured_at": t0,
            "source_version": "test",
            "snapshot_source": "live",
            "decision_point": None,
            "n_books_available": 1,
            "event_time": t0,
            "ingested_at": t0,
        },
        {
            "snapshot_id": "b",
            "game_key": "2024:A:B:2024-09-01",
            "game_id": None,
            "season": 2024,
            "week": 1,
            "book": "draftkings",
            "market": "spread",
            "side": "A",
            "line": -3.5,
            "price": -110.0,
            "home_team": "A",
            "away_team": "B",
            "captured_at": t1,
            "source_version": "test",
            "snapshot_source": "live",
            "decision_point": None,
            "n_books_available": 1,
            "event_time": t1,
            "ingested_at": t1,
        },
    ]
    out = dedupe_snapshots(pd.DataFrame(rows))
    assert len(out) == 2


def test_dedupe_historical_against_live_same_minute(team_map: dict[str, str]) -> None:
    captured = datetime(2024, 9, 1, 12, 0, 10, tzinfo=UTC)
    live = normalize_odds_payload(
        SAMPLE_PAYLOAD,
        captured_at=captured,
        ingested_at=captured,
        team_map=team_map,
        snapshot_source="live",
    )
    hist = normalize_odds_payload(
        SAMPLE_PAYLOAD,
        captured_at=captured,
        ingested_at=captured,
        team_map=team_map,
        snapshot_source="historical",
        decision_point="slot_close",
        event_time=captured,
    )
    out = dedupe_snapshots(pd.concat([live, hist], ignore_index=True))
    assert len(out) == len(live)


def test_retry_on_500_then_success() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(500, json={"message": "boom"})
        return httpx.Response(
            200,
            content=json.dumps(SAMPLE_PAYLOAD).encode(),
            headers={"x-requests-remaining": "400", "x-requests-used": "100"},
        )

    transport = httpx.MockTransport(handler)
    with OddsAPIClient(
        "test-key",
        books=["draftkings"],
        markets=["h2h", "spreads", "totals"],
        rate_limit_reserve=50,
        transport=transport,
    ) as client:
        body, headers = client.fetch_odds()
    assert attempts["n"] == 3
    assert json.loads(body)[0]["id"] == "evt1"
    assert headers["x-requests-remaining"] == "400"


def test_retry_exhausted_on_persistent_500() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"message": "down"})

    transport = httpx.MockTransport(handler)
    with (
        OddsAPIClient(
            "test-key",
            books=["draftkings"],
            markets=["h2h"],
            rate_limit_reserve=50,
            transport=transport,
        ) as client,
        pytest.raises(httpx.HTTPStatusError),
    ):
        client.fetch_odds()


def test_rate_limit_guard_trips() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200,
            content=b"[]",
            headers={"x-requests-remaining": "10", "x-requests-used": "490"},
        )

    transport = httpx.MockTransport(handler)
    with OddsAPIClient(
        "test-key",
        books=["draftkings"],
        markets=["h2h"],
        rate_limit_reserve=50,
        transport=transport,
    ) as client:
        client.fetch_odds()
        assert client.remaining_requests == 10
        with pytest.raises(RateLimitBudgetError, match="below reserve"):
            client.fetch_odds()
    assert calls["n"] == 1


def test_historical_budget_leaves_live_reserve() -> None:
    """Historical trips on the live floor; live reserve stays intact."""
    calls = {"n": 0}
    live_reserve = 50

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200,
            content=json.dumps(_historical_envelope([])).encode(),
            headers={
                "x-requests-remaining": str(live_reserve),
                "x-requests-last": "30",
                "x-requests-used": "100",
            },
        )

    transport = httpx.MockTransport(handler)
    with OddsAPIClient(
        "test-key",
        books=["draftkings"],
        markets=["h2h", "spreads", "totals"],
        rate_limit_reserve=live_reserve,
        budget_kind="historical",
        historical_credit_ceiling=10_000,
        credits_per_historical_call=30,
        transport=transport,
    ) as hist_client:
        hist_client.fetch_historical_odds(datetime(2024, 9, 3, 10, 0, tzinfo=UTC))
        assert hist_client.remaining_requests == live_reserve
        with pytest.raises(RateLimitBudgetError, match="live reserve"):
            hist_client.fetch_historical_odds(datetime(2024, 9, 3, 11, 0, tzinfo=UTC))
        assert hist_client.remaining_requests == live_reserve
    assert calls["n"] == 1


def test_historical_ceiling_guard() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=json.dumps(_historical_envelope([])).encode(),
            headers={
                "x-requests-remaining": "5000",
                "x-requests-last": "30",
            },
        )

    transport = httpx.MockTransport(handler)
    with OddsAPIClient(
        "test-key",
        books=["draftkings"],
        markets=["h2h", "spreads", "totals"],
        rate_limit_reserve=50,
        budget_kind="historical",
        historical_credit_ceiling=30,
        credits_per_historical_call=30,
        transport=transport,
    ) as client:
        client.fetch_historical_odds(datetime(2024, 9, 3, 10, 0, tzinfo=UTC))
        with pytest.raises(HistoricalBudgetCeilingError, match="ceiling"):
            client.fetch_historical_odds(datetime(2024, 9, 3, 11, 0, tzinfo=UTC))


def test_raw_archival_before_parse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps(SAMPLE_PAYLOAD).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=body,
            headers={"x-requests-remaining": "200"},
        )

    def boom(*_args: object, **_kwargs: object) -> pd.DataFrame:
        raise RuntimeError("parse exploded")

    monkeypatch.setattr(
        "ncaa_quant.ingestion.odds_api.normalize_odds_payload",
        boom,
    )
    transport = httpx.MockTransport(handler)
    client = OddsAPIClient(
        "test-key",
        books=["draftkings"],
        markets=["h2h"],
        rate_limit_reserve=50,
        transport=transport,
    )
    raw_root = tmp_path / "raw"
    with pytest.raises(RuntimeError, match="parse exploded"):
        run_odds_ingest(
            config=load_config(),
            api_key="test-key",
            raw_root=raw_root,
            staged_root=tmp_path / "staged",
            client=client,
            team_map={},
            captured_at=datetime(2024, 9, 1, 15, 30, 0, tzinfo=UTC),
        )
    archived = list(raw_root.rglob("*.json"))
    assert len(archived) == 1
    assert archived[0].read_bytes() == body


def test_write_twice_same_minute_no_duplicate_rows(
    tmp_path: Path,
    team_map: dict[str, str],
) -> None:
    captured = datetime(2024, 9, 1, 12, 0, 10, tzinfo=UTC)
    df = normalize_odds_payload(
        SAMPLE_PAYLOAD,
        captured_at=captured,
        ingested_at=captured,
        team_map=team_map,
    )
    with ParquetStore(tmp_path / "staged") as store:
        n1 = write_odds_snapshots(store, df)
        n2 = write_odds_snapshots(store, df)
        all_rows = store.read("odds_snapshots")
    assert n1 == len(df)
    assert n2 == 0
    assert len(all_rows) == len(df)


def test_archive_raw_path_layout(tmp_path: Path) -> None:
    captured = datetime(2024, 9, 1, 15, 30, 45, tzinfo=UTC)
    path = archive_raw_response(tmp_path, captured, b'{"ok":true}')
    assert path.parent.name == "2024-09-01"
    assert path.name.endswith(".json")
    assert path.read_bytes() == b'{"ok":true}'


def test_archive_historical_path_layout(tmp_path: Path) -> None:
    requested = datetime(2024, 9, 3, 10, 0, 0, tzinfo=UTC)
    returned = datetime(2024, 9, 3, 9, 55, 0, tzinfo=UTC)
    path = archive_historical_response(tmp_path, requested, returned, b'{"ok":true}')
    assert path.parent.name == "2024-09-03"
    assert "20240903T100000" in path.name
    assert "20240903T095500" in path.name


def test_event_time_is_returned_timestamp_not_request(
    team_map: dict[str, str],
) -> None:
    requested = datetime(2024, 9, 3, 10, 0, 0, tzinfo=UTC)
    returned = datetime(2024, 9, 3, 9, 55, 0, tzinfo=UTC)
    envelope = parse_historical_envelope(
        _historical_envelope(
            SAMPLE_PAYLOAD,
            timestamp=returned.strftime("%Y-%m-%dT%H:%M:%SZ"),
        ),
        requested_at=requested,
    )
    assert envelope.timestamp == returned
    assert envelope.timestamp != requested
    df = normalize_odds_payload(
        envelope.data,
        captured_at=envelope.timestamp,
        ingested_at=datetime(2024, 9, 3, 12, 0, tzinfo=UTC),
        team_map=team_map,
        snapshot_source="historical",
        decision_point="tuesday_0600_et",
        event_time=envelope.timestamp,
    )
    assert (df["event_time"] == returned).all()
    assert (df["event_time"] != requested).all()
    assert (df["captured_at"] == returned).all()


def test_asof_tolerance_by_era() -> None:
    pre = datetime(2021, 10, 1, 12, 0, tzinfo=UTC)
    post = datetime(2023, 10, 1, 12, 0, tzinfo=UTC)
    assert asof_tolerance_for(pre) == timedelta(minutes=10)
    assert asof_tolerance_for(post) == timedelta(minutes=5)
    assert within_asof_tolerance(pre, pre - timedelta(minutes=9))
    assert not within_asof_tolerance(pre, pre - timedelta(minutes=11))
    assert within_asof_tolerance(post, post - timedelta(minutes=5))
    assert not within_asof_tolerance(post, post - timedelta(minutes=6))


def _games_rows(*kicks: datetime) -> pd.DataFrame:
    rows = []
    for i, kick in enumerate(kicks, start=1):
        rows.append(
            {
                "game_id": i,
                "season": 2024,
                "week": 1,
                "season_type": "regular",
                "start_date": kick,
                "home_team_id": i * 2,
                "away_team_id": i * 2 + 1,
                "home_points": None,
                "away_points": None,
                "neutral_site": False,
                "conference_game": False,
                "venue_id": None,
                "completed": False,
                "source_version": "test",
                "event_time": kick,
                "ingested_at": kick,
            }
        )
    return pd.DataFrame(rows)


def test_estimator_arithmetic(tmp_path: Path) -> None:
    staged = tmp_path / "staged"
    kick_a = datetime(2024, 9, 7, 16, 0, tzinfo=UTC)
    kick_b = datetime(2024, 9, 7, 19, 0, tzinfo=UTC)
    kick_c = datetime(2024, 9, 7, 16, 0, tzinfo=UTC)
    cfg = load_config()
    with ParquetStore(staged) as store:
        store.write_partition(
            "games",
            _games_rows(kick_a, kick_b, kick_c),
            {"season": 2024, "week": 1},
        )
        plan, lines = estimate_historical_credits(store, [2024], config=cfg)
    assert plan.total_requests == 3
    assert plan.credits_per_call == 30
    assert plan.total_credits == 90
    assert plan.requests_by_season_dp[(2024, "tuesday_0600_et")] == 1
    assert plan.requests_by_season_dp[(2024, "slot_close")] == 2
    assert "total_credits=90" in "\n".join(lines)


def test_tuesday_0600_et_is_tuesday_morning() -> None:
    from zoneinfo import ZoneInfo

    ts = tuesday_0600_et_for_week(2024, 1)
    et = ts.astimezone(ZoneInfo("America/New_York"))
    assert et.weekday() == 1
    assert et.hour == 6
    assert et.minute == 0


def test_historical_resumability_skips_completed(
    tmp_path: Path,
    team_map: dict[str, str],
) -> None:
    staged = tmp_path / "staged"
    raw = tmp_path / "raw_hist"
    kick = datetime(2024, 9, 7, 19, 0, tzinfo=UTC)
    with ParquetStore(staged) as store:
        store.write_partition("games", _games_rows(kick), {"season": 2024, "week": 1})

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200,
            content=json.dumps(
                _historical_envelope(
                    SAMPLE_PAYLOAD,
                    timestamp="2024-09-03T09:55:00Z",
                )
            ).encode(),
            headers={
                "x-requests-remaining": "5000",
                "x-requests-last": "30",
            },
        )

    transport = httpx.MockTransport(handler)
    client = OddsAPIClient(
        "test-key",
        books=["draftkings"],
        markets=["h2h", "spreads", "totals"],
        rate_limit_reserve=50,
        budget_kind="historical",
        historical_credit_ceiling=10_000,
        credits_per_historical_call=30,
        transport=transport,
    )
    cfg = load_config()
    r1 = run_historical_backfill(
        seasons=[2024],
        config=cfg,
        api_key="test-key",
        raw_root=raw,
        staged_root=staged,
        client=client,
        team_map=team_map,
        backfill_live_meta=False,
    )
    first_calls = calls["n"]
    assert first_calls >= 1
    assert r1.units_written >= 1
    assert is_unit_complete(raw, 2024, 1, "tuesday_0600_et")

    r2 = run_historical_backfill(
        seasons=[2024],
        config=cfg,
        api_key="test-key",
        raw_root=raw,
        staged_root=staged,
        client=client,
        team_map=team_map,
        skip_calibration=True,
        backfill_live_meta=False,
    )
    assert calls["n"] == first_calls
    assert r2.units_skipped >= 1
    assert r2.requests_made == 0


def test_historical_crash_mid_slot_does_not_rebill(
    tmp_path: Path,
    team_map: dict[str, str],
) -> None:
    staged = tmp_path / "staged"
    raw = tmp_path / "raw_hist"
    kick_a = datetime(2024, 9, 7, 16, 0, tzinfo=UTC)
    kick_b = datetime(2024, 9, 7, 20, 0, tzinfo=UTC)
    with ParquetStore(staged) as store:
        store.write_partition(
            "games",
            _games_rows(kick_a, kick_b),
            {"season": 2024, "week": 1},
        )
        plan = plan_historical_units(store, [2024], decision_points=["slot_close"])
    unit = plan.units[0]
    assert len(unit.request_times) == 2

    first_req = unit.request_times[0]
    archive_historical_response(
        raw,
        first_req,
        first_req - timedelta(minutes=5),
        json.dumps(_historical_envelope(SAMPLE_PAYLOAD)).encode(),
    )
    assert not is_unit_complete(raw, 2024, 1, "slot_close")

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200,
            content=json.dumps(
                _historical_envelope(
                    SAMPLE_PAYLOAD,
                    timestamp="2024-09-07T19:55:00Z",
                )
            ).encode(),
            headers={"x-requests-remaining": "5000", "x-requests-last": "30"},
        )

    transport = httpx.MockTransport(handler)
    client = OddsAPIClient(
        "test-key",
        books=["draftkings"],
        markets=["h2h", "spreads", "totals"],
        rate_limit_reserve=50,
        budget_kind="historical",
        historical_credit_ceiling=10_000,
        credits_per_historical_call=30,
        transport=transport,
    )
    mark_unit_complete(raw, 2024, 1, "tuesday_0600_et")
    run_historical_backfill(
        seasons=[2024],
        config=load_config(),
        api_key="test-key",
        raw_root=raw,
        staged_root=staged,
        client=client,
        team_map=team_map,
        skip_calibration=True,
        backfill_live_meta=False,
    )
    assert calls["n"] == 1
    assert is_unit_complete(raw, 2024, 1, "slot_close")


def test_calibration_gate_mismatch_aborts(
    tmp_path: Path,
    team_map: dict[str, str],
) -> None:
    staged = tmp_path / "staged"
    raw = tmp_path / "raw_hist"
    kick = datetime(2024, 9, 7, 19, 0, tzinfo=UTC)
    with ParquetStore(staged) as store:
        store.write_partition("games", _games_rows(kick), {"season": 2024, "week": 1})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=json.dumps(_historical_envelope(SAMPLE_PAYLOAD)).encode(),
            headers={"x-requests-remaining": "5000", "x-requests-last": "45"},
        )

    transport = httpx.MockTransport(handler)
    client = OddsAPIClient(
        "test-key",
        books=["draftkings"],
        markets=["h2h", "spreads", "totals"],
        rate_limit_reserve=50,
        budget_kind="historical",
        historical_credit_ceiling=10_000,
        credits_per_historical_call=30,
        transport=transport,
    )
    with pytest.raises(CalibrationError, match="Calibration failed"):
        run_historical_backfill(
            seasons=[2024],
            config=load_config(),
            api_key="test-key",
            raw_root=raw,
            staged_root=staged,
            client=client,
            team_map=team_map,
            backfill_live_meta=False,
        )


def test_cli_odds_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from ncaa_quant.cli import app
    from ncaa_quant.ingestion.odds_api import OddsRawCaptureResult

    runner = CliRunner()

    def fake_run(**_kwargs: object) -> OddsRawCaptureResult:
        return OddsRawCaptureResult(
            raw_path=tmp_path / "raw.json",
            captured_at=datetime(2024, 9, 1, tzinfo=UTC),
            bytes_written=42,
        )

    monkeypatch.setattr("ncaa_quant.ingestion.odds_api.run_odds_raw_capture", fake_run)
    result = runner.invoke(app, ["ingest", "odds", "--once"])
    assert result.exit_code == 0, result.output
    assert "raw archived bytes=42" in result.output
    assert "path=" in result.output


def test_cli_odds_requires_once() -> None:
    from typer.testing import CliRunner

    from ncaa_quant.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["ingest", "odds"])
    assert result.exit_code == 2


def test_cli_odds_historical_estimate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typer.testing import CliRunner

    import ncaa_quant.config as config_mod
    from ncaa_quant.cli import app

    staged = tmp_path / "staged"
    kick = datetime(2024, 9, 7, 19, 0, tzinfo=UTC)
    with ParquetStore(staged) as store:
        store.write_partition("games", _games_rows(kick), {"season": 2024, "week": 1})

    base = load_config()
    cfg = AppConfig(
        seed=base.seed,
        log_level=base.log_level,
        paths=base.paths.model_copy(update={"staged_dir": str(staged)}),
        data=base.data,
        ratings=base.ratings,
        betting=base.betting,
        pipeline=base.pipeline,
    )
    monkeypatch.setattr(config_mod, "load_config", lambda: cfg)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["ingest", "odds-historical", "--seasons", "2024", "--estimate"],
    )
    assert result.exit_code == 0, result.output
    assert "total_credits=" in result.output


def test_backfill_live_odds_metadata(tmp_path: Path, team_map: dict[str, str]) -> None:
    from ncaa_quant.ingestion.odds_api import backfill_live_odds_metadata

    captured = datetime(2024, 9, 1, 12, 0, 10, tzinfo=UTC)
    df = normalize_odds_payload(
        SAMPLE_PAYLOAD,
        captured_at=captured,
        ingested_at=captured,
        team_map=team_map,
    )
    # Simulate legacy rows without Task 5B columns.
    legacy = df.drop(columns=["snapshot_source", "decision_point", "n_books_available"])
    staged = tmp_path / "staged"
    with ParquetStore(staged) as store:
        # Write bypassing schema by using validate=False if available.
        store.write_partition(
            "odds_snapshots",
            _ensure_legacy_write(legacy),
            {"season": 2024, "week": 1},
            validate=False,
        )
        n = backfill_live_odds_metadata(store)
        assert n == 1
        out = store.read("odds_snapshots", filters={"season": 2024, "week": 1})
    assert (out["snapshot_source"] == "live").all()
    assert out["decision_point"].isna().all()
    assert (out["n_books_available"] >= 1).all()


def _ensure_legacy_write(df: pd.DataFrame) -> pd.DataFrame:
    """Return df as-is for validate=False writes."""
    return df


def test_coverage_and_reconcile_reports(
    tmp_path: Path,
    team_map: dict[str, str],
) -> None:
    from ncaa_quant.ingestion.odds_api import (
        coverage_report,
        reconcile_cfbd_close_vs_slot_close,
    )

    staged = tmp_path / "staged"
    kick = datetime(2024, 9, 7, 19, 0, tzinfo=UTC)
    games = _games_rows(kick)
    teams = pd.DataFrame(
        [
            {
                "team_id": 2,
                "season": 2024,
                "school": "Michigan",
                "conference": "Big Ten",
                "abbreviation": "MICH",
                "classification": "fbs",
                "source_version": "test",
                "event_time": kick,
                "ingested_at": kick,
            },
            {
                "team_id": 3,
                "season": 2024,
                "school": "Texas",
                "conference": "SEC",
                "abbreviation": "TEX",
                "classification": "fbs",
                "source_version": "test",
                "event_time": kick,
                "ingested_at": kick,
            },
        ]
    )
    lines = pd.DataFrame(
        [
            {
                "game_id": 1,
                "season": 2024,
                "week": 1,
                "book": "draftkings",
                "line_type": "close",
                "spread": -3.0,
                "total": 49.0,
                "home_ml": -150.0,
                "away_ml": 130.0,
                "source_version": "test",
                "event_time": kick,
                "ingested_at": kick,
            }
        ]
    )
    returned = datetime(2024, 9, 7, 18, 55, tzinfo=UTC)
    odds = normalize_odds_payload(
        SAMPLE_PAYLOAD,
        captured_at=returned,
        ingested_at=returned,
        team_map=team_map,
        snapshot_source="historical",
        decision_point="slot_close",
        event_time=returned,
    )
    with ParquetStore(staged) as store:
        store.write_partition("games", games, {"season": 2024, "week": 1})
        store.write_partition("teams", teams, {"season": 2024})
        store.write_partition("lines_historical", lines, {"season": 2024, "week": 1})
        write_odds_snapshots(store, odds)
        cov = coverage_report(store, [2024], config=load_config())
        report = reconcile_cfbd_close_vs_slot_close(store, [2024])
    assert any("slot_close" in line for line in cov)
    assert report.n_games >= 1
    assert len(report.spread_diffs) >= 1
    summary = "\n".join(report.summary_lines())
    assert "spread Δ" in summary


def test_estimate_with_remaining_quota(tmp_path: Path) -> None:
    staged = tmp_path / "staged"
    kick = datetime(2024, 9, 7, 19, 0, tzinfo=UTC)
    with ParquetStore(staged) as store:
        store.write_partition("games", _games_rows(kick), {"season": 2024, "week": 1})
        _plan, lines = estimate_historical_credits(
            store,
            [2024],
            config=load_config(),
            remaining_quota=20_000,
        )
    assert any("projected_remaining_after=" in line for line in lines)
    assert any("20000" in line or "remaining=20000" in line for line in lines)


def test_reconcile_empty_when_no_games(tmp_path: Path) -> None:
    from ncaa_quant.ingestion.odds_api import reconcile_cfbd_close_vs_slot_close

    with ParquetStore(tmp_path / "staged") as store:
        report = reconcile_cfbd_close_vs_slot_close(store, [2024])
    assert report.n_games == 0
    assert "no matched" in "\n".join(report.summary_lines()).lower()


def test_failure_hook_logs() -> None:
    from ncaa_quant.pipelines.odds import notify_ingest_odds_failure

    class _Run:
        id = "run-1"

    class _State:
        name = "Failed"
        message = "boom"

    notify_ingest_odds_failure(None, _Run(), _State())  # type: ignore[arg-type]


def test_ingest_odds_flow(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from ncaa_quant.ingestion.odds_api import OddsRawCaptureResult
    from ncaa_quant.pipelines.odds import ingest_odds_flow

    def fake_run(**_kwargs: object) -> OddsRawCaptureResult:
        return OddsRawCaptureResult(
            raw_path=tmp_path / "x.json",
            captured_at=datetime(2024, 9, 1, tzinfo=UTC),
            bytes_written=99,
        )

    monkeypatch.setattr("ncaa_quant.pipelines.odds.run_odds_raw_capture", fake_run)
    monkeypatch.setattr("ncaa_quant.pipelines.odds.configure_logging", lambda: "run")
    out = ingest_odds_flow.fn()
    assert out["bytes_written"] == 99
    assert out["raw_path"].endswith("x.json")


def test_run_odds_raw_capture(tmp_path: Path) -> None:
    body = json.dumps(SAMPLE_PAYLOAD).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=body,
            headers={"x-requests-remaining": "200"},
        )

    transport = httpx.MockTransport(handler)
    client = OddsAPIClient(
        "test-key",
        books=["draftkings"],
        markets=["h2h"],
        rate_limit_reserve=50,
        transport=transport,
    )
    result = run_odds_raw_capture(
        config=load_config(),
        api_key="test-key",
        raw_root=tmp_path / "raw",
        client=client,
        captured_at=datetime(2024, 9, 1, 15, 30, 0, tzinfo=UTC),
    )
    assert result.bytes_written == len(body)
    assert result.raw_path.is_file()
    assert result.raw_path.read_bytes() == body
    assert result.raw_path.parent.name == "2024-09-01"
