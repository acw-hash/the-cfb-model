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
    OddsEventRef,
    RateLimitBudgetError,
    archive_historical_response,
    archive_raw_response,
    asof_tolerance_for,
    dedupe_snapshots,
    estimate_historical_credits,
    extract_odds_events,
    is_unit_complete,
    load_cfbd_schedule,
    load_team_name_map,
    make_game_key,
    mark_unit_complete,
    match_odds_events_to_cfbd,
    normalize_odds_payload,
    normalize_team_name,
    parse_historical_envelope,
    plan_historical_units,
    run_historical_backfill,
    run_odds_ingest,
    run_odds_raw_capture,
    saturday_0600_et_for_week,
    split_odds_by_line_sanity,
    tuesday_0600_et_for_week,
    within_asof_tolerance,
    write_odds_cfbd_crosswalk,
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
        n1, q1 = write_odds_snapshots(store, df)
        n2, q2 = write_odds_snapshots(store, df)
        all_rows = store.read("odds_snapshots")
    assert n1 == len(df)
    assert q1 == 0
    assert n2 == 0
    assert q2 == 0
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
                "event_time_estimated": True,
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
    # tue + sat + 2 distinct slot_close kicks
    assert plan.total_requests == 4
    assert plan.credits_per_call == 30
    assert plan.total_credits == 120
    assert plan.ceiling == 60000
    assert plan.requests_by_season_dp[(2024, "tuesday_0600_et")] == 1
    assert plan.requests_by_season_dp[(2024, "saturday_0600_et")] == 1
    assert plan.requests_by_season_dp[(2024, "slot_close")] == 2
    assert "total_credits=120" in "\n".join(lines)


def test_tuesday_0600_et_is_tuesday_morning() -> None:
    from zoneinfo import ZoneInfo

    ts = tuesday_0600_et_for_week(2024, 1)
    et = ts.astimezone(ZoneInfo("America/New_York"))
    assert et.weekday() == 1
    assert et.hour == 6
    assert et.minute == 0


def test_saturday_0600_et_is_saturday_morning() -> None:
    from zoneinfo import ZoneInfo

    ts = saturday_0600_et_for_week(2024, 1)
    et = ts.astimezone(ZoneInfo("America/New_York"))
    assert et.weekday() == 5
    assert et.hour == 6
    assert et.minute == 0


def test_saturday_0600_et_dst_fall_back() -> None:
    """Across Nov EST↔EDT boundary, Sat 06:00 ET shifts UTC by +1h."""
    # 2024 week containing Oct 29 (EDT) vs week containing Nov 5 (EST).
    # Labor Day 2024 = Sep 2 → week 9 Monday = Oct 28; week 10 Monday = Nov 4.
    before = saturday_0600_et_for_week(2024, 9)
    after = saturday_0600_et_for_week(2024, 10)
    assert before == datetime(2024, 11, 2, 10, 0, tzinfo=UTC)  # EDT UTC-4
    assert after == datetime(2024, 11, 9, 11, 0, tzinfo=UTC)  # EST UTC-5
    assert (after - before).total_seconds() == 7 * 24 * 3600 + 3600


def test_unknown_decision_point_in_plan_raises(tmp_path: Path) -> None:
    staged = tmp_path / "staged"
    kick = datetime(2024, 9, 7, 19, 0, tzinfo=UTC)
    with ParquetStore(staged) as store:
        store.write_partition("games", _games_rows(kick), {"season": 2024, "week": 1})
        with pytest.raises(ValueError, match="Unknown decision point"):
            plan_historical_units(
                store,
                [2024],
                decision_points=["thursday_0600_et"],
            )


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
    """Archived-but-unstaged slot replays from disk at zero credits."""
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

    # Both slots archived (as after a mid-unit crash); neither staged yet.
    for req in unit.request_times:
        returned = req - timedelta(minutes=5)
        archive_historical_response(
            raw,
            req,
            returned,
            json.dumps(
                _historical_envelope(
                    SAMPLE_PAYLOAD,
                    timestamp=returned.strftime("%Y-%m-%dT%H:%M:%SZ"),
                )
            ).encode(),
        )
    assert not is_unit_complete(raw, 2024, 1, "slot_close")

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise AssertionError("archived slots must not hit the API")

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
    mark_unit_complete(raw, 2024, 1, "saturday_0600_et")
    result = run_historical_backfill(
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
    assert calls["n"] == 0
    assert result.requests_made == 0
    assert result.rows_written > 0
    assert is_unit_complete(raw, 2024, 1, "slot_close")
    with ParquetStore(staged) as store:
        odds = store.read("odds_snapshots", filters={"season": 2024, "week": 1})
    assert not odds.empty
    assert (odds["snapshot_source"] == "historical").all()
    assert (odds["decision_point"] == "slot_close").all()


def test_line_sanity_quarantine_split_does_not_raise(
    tmp_path: Path,
    team_map: dict[str, str],
) -> None:
    """Bad book lines land in the sidecar; good rows stage; write does not raise."""
    captured = datetime(2024, 9, 7, 19, 55, tzinfo=UTC)
    payload = [
        {
            "id": "evt_good",
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
                    ],
                },
                {
                    "key": "williamhill_us",
                    "title": "William Hill",
                    "markets": [
                        {
                            "key": "spreads",
                            "outcomes": [
                                {"name": "Michigan Wolverines", "price": -1667, "point": -600.0},
                                {"name": "Texas Longhorns", "price": -100000, "point": 600.0},
                            ],
                        }
                    ],
                },
                {
                    "key": "fanduel",
                    "title": "FanDuel",
                    "markets": [
                        {
                            "key": "totals",
                            "outcomes": [
                                {"name": "Over", "price": -1667, "point": 17.5},
                                {"name": "Under", "price": 750, "point": 17.5},
                            ],
                        }
                    ],
                },
            ],
        }
    ]
    df = normalize_odds_payload(
        payload,
        captured_at=captured,
        ingested_at=captured,
        team_map=team_map,
        snapshot_source="historical",
        decision_point="slot_close",
        event_time=captured,
    )
    good, bad = split_odds_by_line_sanity(df)
    assert len(bad) == 4
    assert set(bad["quarantine_reason"]) == {
        "spread_out_of_bounds",
        "total_out_of_bounds",
    }
    assert len(good) == 4

    staged = tmp_path / "staged"
    raw_path = tmp_path / "raw" / "archive.json"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_text("{}", encoding="utf-8")
    with ParquetStore(staged) as store:
        written, quarantined = write_odds_snapshots(
            store,
            df,
            raw_archive_path=raw_path,
            requested_at=captured,
        )
        odds = store.read("odds_snapshots")
    assert written == 4
    assert quarantined == 4
    assert len(odds) == 4
    assert (odds["book"] == "draftkings").all()

    q_path = staged / "odds_snapshots_quarantine" / "season=2024" / "week=1" / "part.parquet"
    assert q_path.is_file()
    qdf = pd.read_parquet(q_path)
    assert len(qdf) == 4
    assert set(qdf["quarantine_reason"]) == {
        "spread_out_of_bounds",
        "total_out_of_bounds",
    }
    assert (qdf["raw_archive_path"] == str(raw_path)).all()
    assert qdf["requested_at"].notna().all()
    assert (pd.to_datetime(qdf["event_time"], utc=True) == pd.Timestamp(captured)).all()
    assert (qdf["decision_point"] == "slot_close").all()


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
    from ncaa_quant.ingestion.odds_api import OddsIngestResult

    runner = CliRunner()

    def fake_run(**_kwargs: object) -> OddsIngestResult:
        return OddsIngestResult(
            raw_path=tmp_path / "raw.json",
            rows_written=12,
            rows_fetched=12,
            captured_at=datetime(2024, 9, 1, tzinfo=UTC),
        )

    monkeypatch.setattr("ncaa_quant.ingestion.odds_api.run_odds_ingest", fake_run)
    result = runner.invoke(app, ["ingest", "odds", "--once"])
    assert result.exit_code == 0, result.output
    assert "wrote 12 new rows (fetched 12)" in result.output
    assert "raw=" in result.output


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
    from ncaa_quant.ingestion.odds_api import OddsIngestResult
    from ncaa_quant.pipelines.odds import ingest_odds_flow

    def fake_run(**_kwargs: object) -> OddsIngestResult:
        return OddsIngestResult(
            raw_path=tmp_path / "x.json",
            rows_written=7,
            rows_fetched=7,
            captured_at=datetime(2024, 9, 1, tzinfo=UTC),
        )

    monkeypatch.setattr("ncaa_quant.pipelines.odds.run_odds_ingest", fake_run)
    monkeypatch.setattr("ncaa_quant.pipelines.odds.configure_logging", lambda: "run")
    out = ingest_odds_flow.fn()
    assert out["rows_written"] == 7
    assert out["rows_fetched"] == 7
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


def _schedule_row(
    *,
    game_id: int,
    season: int,
    home: str,
    away: str,
    start: datetime,
) -> dict[str, object]:
    return {
        "game_id": game_id,
        "season": season,
        "home_team": home,
        "away_team": away,
        "start_date": start,
    }


def test_postponed_game_keeps_single_cfbd_key_and_continuous_history(
    team_map: dict[str, str],
) -> None:
    """AUDIT-6.3 / Task 4: one-day postpone → one CFBD id, continuous snapshots."""
    cfbd_id = 401628999
    original_kick = datetime(2024, 9, 7, 19, 0, tzinfo=UTC)
    postponed_kick = original_kick + timedelta(days=1)
    schedule = pd.DataFrame(
        [
            _schedule_row(
                game_id=cfbd_id,
                season=2024,
                home="Michigan",
                away="Texas",
                start=original_kick,
            )
        ]
    )
    event_id = "evt-postpone-1"
    pre = OddsEventRef(
        odds_event_id=event_id,
        game_key=make_game_key(2024, "Michigan", "Texas", original_kick.date()),
        season=2024,
        home_team="Michigan",
        away_team="Texas",
        kickoff=original_kick,
    )
    post = OddsEventRef(
        odds_event_id=event_id,
        game_key=make_game_key(2024, "Michigan", "Texas", postponed_kick.date()),
        season=2024,
        home_team="Michigan",
        away_team="Texas",
        kickoff=postponed_kick,
    )
    ingested = datetime(2024, 9, 6, 12, 0, tzinfo=UTC)
    first = match_odds_events_to_cfbd([pre], schedule, ingested_at=ingested)
    assert len(first) == 1
    assert first.iloc[0]["match_status"] == "matched"
    assert int(first.iloc[0]["game_id"]) == cfbd_id

    # Second pull after postpone: same odds_event_id, new commence / game_key.
    second = match_odds_events_to_cfbd(
        [post],
        schedule,
        existing=first,
        ingested_at=ingested + timedelta(hours=20),
    )
    assert len(second) == 1
    assert second.iloc[0]["match_status"] == "matched"
    assert int(second.iloc[0]["game_id"]) == cfbd_id
    assert second.iloc[0]["game_key"] != first.iloc[0]["game_key"]

    # Snapshot history across the postpone shares one canonical game_id.
    t0 = datetime(2024, 9, 6, 12, 0, tzinfo=UTC)
    t1 = datetime(2024, 9, 7, 8, 0, tzinfo=UTC)
    payload_pre = [
        {
            **SAMPLE_PAYLOAD[0],
            "id": event_id,
            "commence_time": original_kick.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    ]
    payload_post = [
        {
            **SAMPLE_PAYLOAD[0],
            "id": event_id,
            "commence_time": postponed_kick.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    ]
    ids = {event_id: cfbd_id}
    snap_pre = normalize_odds_payload(
        payload_pre,
        captured_at=t0,
        ingested_at=t0,
        team_map=team_map,
        event_game_ids=ids,
    )
    snap_post = normalize_odds_payload(
        payload_post,
        captured_at=t1,
        ingested_at=t1,
        team_map=team_map,
        event_game_ids=ids,
    )
    history = pd.concat([snap_pre, snap_post], ignore_index=True)
    assert set(history["game_id"].dropna().astype(int)) == {cfbd_id}
    assert history["game_key"].nunique() == 2
    assert history["captured_at"].nunique() == 2


def test_ambiguous_match_is_quarantined_never_guessed() -> None:
    kick = datetime(2024, 9, 7, 19, 0, tzinfo=UTC)
    schedule = pd.DataFrame(
        [
            _schedule_row(
                game_id=1,
                season=2024,
                home="Michigan",
                away="Texas",
                start=kick,
            ),
            _schedule_row(
                game_id=2,
                season=2024,
                home="Michigan",
                away="Texas",
                start=kick + timedelta(hours=12),
            ),
        ]
    )
    ev = OddsEventRef(
        odds_event_id="ambig",
        game_key="2024:Michigan:Texas:2024-09-07",
        season=2024,
        home_team="Michigan",
        away_team="Texas",
        kickoff=kick + timedelta(hours=6),
    )
    out = match_odds_events_to_cfbd([ev], schedule, ingested_at=kick)
    assert out.iloc[0]["match_status"] == "quarantined"
    assert pd.isna(out.iloc[0]["game_id"])


def test_raw_archive_body_has_no_api_key(tmp_path: Path) -> None:
    """Raw archival stores response body only — never request metadata with apiKey."""
    secret = "SUPER_SECRET_ODDS_KEY_DO_NOT_LEAK"
    body = json.dumps(SAMPLE_PAYLOAD).encode()
    assert secret.encode() not in body
    path = archive_raw_response(
        tmp_path,
        datetime(2024, 9, 1, 12, 0, tzinfo=UTC),
        body,
    )
    archived = path.read_bytes()
    assert archived == body
    assert b"apiKey" not in archived
    assert secret.encode() not in archived


def test_crosswalk_write_and_load_schedule(
    tmp_path: Path,
    team_map: dict[str, str],
) -> None:
    kick = datetime(2024, 9, 7, 19, 0, tzinfo=UTC)
    staged = tmp_path / "staged"
    with ParquetStore(staged) as store:
        store.write_partition(
            "games",
            pd.DataFrame(
                [
                    {
                        "game_id": 401628331,
                        "season": 2024,
                        "week": 1,
                        "season_type": "regular",
                        "start_date": kick,
                        "home_team_id": 130,
                        "away_team_id": 251,
                        "home_points": None,
                        "away_points": None,
                        "neutral_site": False,
                        "conference_game": False,
                        "venue_id": None,
                        "completed": False,
                        "event_time_estimated": True,
                        "source_version": "test",
                        "event_time": kick,
                        "ingested_at": kick,
                    }
                ]
            ),
            {"season": 2024, "week": 1},
        )
        store.write_partition(
            "teams",
            pd.DataFrame(
                [
                    {
                        "team_id": 130,
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
                        "team_id": 251,
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
            ),
            {"season": 2024},
        )
        schedule = load_cfbd_schedule(store, [2024], team_map)
        assert len(schedule) == 1
        assert int(schedule.iloc[0]["game_id"]) == 401628331
        events = extract_odds_events(SAMPLE_PAYLOAD, team_map)
        crosswalk = match_odds_events_to_cfbd(events, schedule, ingested_at=kick)
        n = write_odds_cfbd_crosswalk(store, crosswalk)
        assert n == 1
        saved = store.read("odds_cfbd_game_crosswalk", filters={"season": 2024})
        assert int(saved.iloc[0]["game_id"]) == 401628331
        assert saved.iloc[0]["match_status"] == "matched"


def test_bare_odds_fbs_aliases_map_to_cfbd_schools(team_map: dict[str, str]) -> None:
    """Odds bare strings from 5b-verify unmatched list → CFBD ``teams.school``."""
    assert normalize_team_name("Appalachian State", team_map) == "App State"
    assert normalize_team_name("UMass", team_map) == "Massachusetts"
    assert normalize_team_name("UMASS Minutemen", team_map) == "Massachusetts"
    assert normalize_team_name("UMass Minutemen", team_map) == "Massachusetts"
    assert normalize_team_name("Southern Mississippi", team_map) == "Southern Miss"
    assert normalize_team_name("Southern Miss Golden Eagles", team_map) == "Southern Miss"
    assert normalize_team_name("Sam Houston State", team_map) == "Sam Houston"
    assert normalize_team_name("Sam Houston Bearkats", team_map) == "Sam Houston"


def test_team_name_map_targets_resolve_against_staged_cfbd() -> None:
    """Every ``odds_api`` map TARGET must exist in staged CFBD ``teams.school``.

    Makes alias-direction bugs (target is an Odds string, not a CFBD school)
    impossible to reintroduce unnoticed when staged CFBD teams are present.
    """
    staged = Path("data/staged/teams")
    if not staged.is_dir():
        pytest.skip("staged CFBD teams not present")
    team_map = load_team_name_map(Path("configs/team_names.yaml"))
    schools: set[str] = set()
    with ParquetStore(Path("data/staged")) as store:
        for season_dir in sorted(staged.glob("season=*")):
            try:
                season = int(season_dir.name.split("=", 1)[1])
            except ValueError:
                continue
            teams = store.read("teams", filters={"season": season})
            if teams.empty:
                continue
            schools |= set(teams["school"].astype(str))
    assert schools, "staged teams present but school set empty"
    missing = sorted({tgt for tgt in team_map.values() if tgt not in schools})
    assert missing == [], f"map targets missing from CFBD teams.school: {missing}"


def test_sam_houston_state_match_requires_schedule_presence(
    tmp_path: Path,
    team_map: dict[str, str],
) -> None:
    """Sam Houston State alias must not invent matches without a CFBD schedule row.

    FCS years (pre-2023) stay gated by schedule presence + ±36h kickoff tolerance;
    the alias alone is insufficient.
    """
    assert normalize_team_name("Sam Houston State", team_map) == "Sam Houston"
    kick = datetime(2021, 9, 4, 19, 0, tzinfo=UTC)
    events = [
        OddsEventRef(
            odds_event_id="sam-houston-fcs",
            game_key=make_game_key(2021, "Sam Houston", "Texas", kick.date()),
            season=2021,
            home_team="Texas",
            away_team="Sam Houston",
            kickoff=kick,
        )
    ]
    empty = pd.DataFrame(columns=["game_id", "season", "home_team", "away_team", "start_date"])
    out = match_odds_events_to_cfbd(events, empty, ingested_at=kick)
    assert out.iloc[0]["match_status"] == "unmatched"
    assert pd.isna(out.iloc[0]["game_id"])

    schedule = pd.DataFrame(
        [
            {
                "game_id": 401299999,
                "season": 2021,
                "home_team": "Texas",
                "away_team": "Sam Houston",
                "start_date": kick,
            }
        ]
    )
    hit = match_odds_events_to_cfbd(events, schedule, ingested_at=kick)
    assert hit.iloc[0]["match_status"] == "matched"
    assert int(hit.iloc[0]["game_id"]) == 401299999


def test_preview_crosswalk_game_key_regression_flags_remap(
    tmp_path: Path,
    team_map: dict[str, str],
) -> None:
    from ncaa_quant.ingestion.odds_api import preview_crosswalk_game_key_regression

    kick = datetime(2024, 9, 7, 19, 0, tzinfo=UTC)
    staged = tmp_path / "staged"
    with ParquetStore(staged) as store:
        store.write_partition(
            "odds_cfbd_game_crosswalk",
            pd.DataFrame(
                [
                    {
                        "odds_event_id": "e1",
                        "game_id": 1,
                        "game_key": "2024:UMass:Michigan:2024-09-07",
                        "season": 2024,
                        "home_team": "Michigan",
                        "away_team": "UMass",
                        "kickoff": kick,
                        "kickoff_delta_hours": 0.0,
                        "match_status": "matched",
                        "source_version": "test",
                        "event_time": kick,
                        "ingested_at": kick,
                    }
                ]
            ),
            {"season": 2024},
        )
        failures = preview_crosswalk_game_key_regression(store, [2024], team_map)
    assert len(failures) == 1
    assert failures[0].old_game_key.startswith("2024:UMass:")
    assert "Massachusetts" in failures[0].new_game_key


def test_replay_historical_from_archives_zero_api(
    tmp_path: Path,
    team_map: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Archive-only replay rewrites historical rows without creating an HTTP client."""
    from ncaa_quant.ingestion import odds_api as odds_mod
    from ncaa_quant.ingestion.odds_api import (
        archive_historical_response,
        replay_historical_from_archives,
    )

    staged = tmp_path / "staged"
    raw = tmp_path / "raw_hist"
    kick = datetime(2024, 9, 7, 19, 0, tzinfo=UTC)
    req = datetime(2024, 9, 7, 18, 55, tzinfo=UTC)
    returned = datetime(2024, 9, 7, 18, 50, tzinfo=UTC)

    games = pd.DataFrame(
        [
            {
                "game_id": 401628331,
                "season": 2024,
                "week": 1,
                "season_type": "regular",
                "start_date": kick,
                "home_team_id": 1,
                "away_team_id": 2,
                "home_points": None,
                "away_points": None,
                "neutral_site": False,
                "conference_game": False,
                "venue_id": None,
                "completed": False,
                "event_time_estimated": True,
                "source_version": "test",
                "event_time": kick,
                "ingested_at": kick,
            }
        ]
    )
    teams = pd.DataFrame(
        [
            {
                "team_id": 1,
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
                "team_id": 2,
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

    with ParquetStore(staged) as store:
        store.write_partition("games", games, {"season": 2024, "week": 1})
        store.write_partition("teams", teams, {"season": 2024})
        # Seed a wrong-key historical row that wipe+replay must replace.
        bad = normalize_odds_payload(
            [
                {
                    **SAMPLE_PAYLOAD[0],
                    "home_team": "Appalachian State",
                    "away_team": "Texas Longhorns",
                    "id": "wrong-key-evt",
                }
            ],
            captured_at=returned,
            ingested_at=returned,
            team_map=team_map,
            snapshot_source="historical",
            decision_point="slot_close",
            event_time=returned,
        )
        bad["season"] = 2024
        bad["week"] = 1
        write_odds_snapshots(store, bad)

    envelope = _historical_envelope(
        SAMPLE_PAYLOAD,
        timestamp=returned.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    archive_historical_response(
        raw,
        req,
        returned,
        json.dumps(envelope).encode("utf-8"),
    )

    base = load_config()
    cfg = AppConfig(
        seed=base.seed,
        log_level=base.log_level,
        paths=base.paths.model_copy(
            update={"staged_dir": str(staged), "raw_dir": str(tmp_path / "raw")}
        ),
        data=base.data.model_copy(
            update={
                "odds_historical_decision_points": ["slot_close"],
            }
        ),
        ratings=base.ratings,
        betting=base.betting,
        pipeline=base.pipeline,
    )

    def _boom(*_a: object, **_k: object) -> None:
        raise AssertionError("HTTP client must not be constructed during archive replay")

    monkeypatch.setattr(odds_mod, "OddsAPIClient", _boom)

    result = replay_historical_from_archives(
        [2024],
        config=cfg,
        raw_root=raw,
        staged_root=staged,
        team_map=team_map,
    )
    assert result.archives_replayed == 1
    assert result.rows_written >= 1
    with ParquetStore(staged) as store:
        odds = store.read("odds_snapshots", filters={"season": 2024, "week": 1})
        hist = odds[odds["snapshot_source"] == "historical"]
        assert not hist.empty
        assert (hist["game_key"] == "2024:Michigan:Texas:2024-09-07").all()
        cw = store.read("odds_cfbd_game_crosswalk", filters={"season": 2024})
        assert (cw["match_status"] == "matched").all()


@pytest.mark.live
def test_live_odds_ingest_once_writes_raw_and_parquet() -> None:
    """Live network smoke; excluded from CI / default ``make test`` via -m 'not live'."""
    from ncaa_quant.config import load_secrets

    key = load_secrets().odds_api_key.get_secret_value()
    if not key:
        pytest.skip("ODDS_API_KEY not configured")

    result = run_odds_ingest()
    assert result.raw_path.is_file()
    assert result.raw_path.stat().st_size > 0
    assert b"apiKey" not in result.raw_path.read_bytes()
    assert result.rows_fetched > 0
    # Second call in the same minute must not duplicate staged rows.
    again = run_odds_ingest(captured_at=result.captured_at)
    assert again.rows_written == 0
