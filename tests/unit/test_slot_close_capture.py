"""Tests for forward kickoff-aligned slot_close capture (V4-B1)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd
import pytest

from ncaa_quant.betting.clv import build_recommendation_record, settle
from ncaa_quant.config import load_config
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.features.market_lines import SLOT_CLOSE_LEAD
from ncaa_quant.ingestion.odds_api import (
    OddsAPIClient,
    load_team_name_map,
    run_odds_ingest,
)
from ncaa_quant.ingestion.slot_close_capture import (
    DECISION_POINT_SLOT_CLOSE,
    LIVE_CREDITS_PER_SLOT,
    KickoffSlot,
    closing_quote_from_slot_close,
    credit_accounting_report,
    derive_kickoff_slots,
    filter_payload_to_slot,
    is_slot_capture_complete,
    is_slot_missed,
    mark_slot_missed,
    plan_forward_slot_captures,
    record_missed_slots,
    run_slot_close_capture,
    slot_close_instant,
)

KICKOFF = datetime(2024, 9, 7, 19, 0, tzinfo=UTC)
CAPTURE_AT = slot_close_instant(KICKOFF)


def _event(
    *,
    event_id: str,
    home: str,
    away: str,
    kickoff: datetime,
    books: tuple[str, ...] = ("fanduel", "draftkings"),
) -> dict[str, object]:
    home_line = -7.5
    away_line = 7.5
    bookmakers: list[dict[str, object]] = []
    for book in books:
        bookmakers.append(
            {
                "key": book,
                "title": book,
                "markets": [
                    {
                        "key": "spreads",
                        "outcomes": [
                            {
                                "name": home,
                                "price": -102 if book == "fanduel" else -110,
                                "point": home_line,
                            },
                            {
                                "name": away,
                                "price": -118 if book == "fanduel" else -110,
                                "point": away_line,
                            },
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
                            {"name": home, "price": -280},
                            {"name": away, "price": 230},
                        ],
                    },
                ],
            }
        )
    return {
        "id": event_id,
        "sport_key": "americanfootball_ncaaf",
        "commence_time": kickoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "home_team": home,
        "away_team": away,
        "bookmakers": bookmakers,
    }


AUBURN_PAYLOAD = [
    _event(
        event_id="evt_auburn",
        home="Auburn Tigers",
        away="Baylor Bears",
        kickoff=KICKOFF,
    )
]


def _games_rows(
    *,
    season: int = 2024,
    week: int = 1,
    game_id: int = 401856636,
    kickoff: datetime = KICKOFF,
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "game_id": game_id,
                "season": season,
                "week": week,
                "season_type": "regular",
                "start_date": kickoff,
                "home_team_id": 2,
                "away_team_id": 3,
                "home_points": None,
                "away_points": None,
                "neutral_site": False,
                "conference_game": False,
                "venue_id": None,
                "completed": False,
                "event_time_estimated": True,
                "source_version": "test",
                "event_time": kickoff,
                "ingested_at": kickoff,
            }
        ]
    )


def _teams_rows(*, season: int = 2024, kickoff: datetime = KICKOFF) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "team_id": 2,
                "season": season,
                "school": "Auburn",
                "conference": "SEC",
                "abbreviation": "AUB",
                "classification": "fbs",
                "source_version": "test",
                "event_time": kickoff,
                "ingested_at": kickoff,
            },
            {
                "team_id": 3,
                "season": season,
                "school": "Baylor",
                "conference": "Big 12",
                "abbreviation": "BAY",
                "classification": "fbs",
                "source_version": "test",
                "event_time": kickoff,
                "ingested_at": kickoff,
            },
        ]
    )


@pytest.fixture
def team_map() -> dict[str, str]:
    return load_team_name_map(Path("configs/team_names.yaml"))


def _mock_client(transport: httpx.MockTransport) -> OddsAPIClient:
    cfg = load_config()
    return OddsAPIClient(
        "test-key",
        books=cfg.data.odds_books,
        markets=cfg.data.odds_markets,
        regions=cfg.data.odds_regions,
        rate_limit_reserve=cfg.data.odds_rate_limit_reserve,
        budget_kind="live",
        transport=transport,
    )


def _stage_fixture_week(
    staged: Path,
    *,
    season: int = 2024,
    week: int = 1,
    game_id: int = 401856636,
    kickoff: datetime = KICKOFF,
) -> KickoffSlot:
    with ParquetStore(staged) as store:
        store.write_partition(
            "games",
            _games_rows(season=season, week=week, game_id=game_id, kickoff=kickoff),
            {"season": season, "week": week},
        )
        store.write_partition(
            "teams",
            _teams_rows(season=season, kickoff=kickoff),
            {"season": season},
        )
        slots = derive_kickoff_slots(store, season, week)
    assert len(slots) == 1
    return slots[0]


def test_derive_kickoff_slots_groups_shared_kickoff(tmp_path: Path) -> None:
    staged = tmp_path / "staged"
    kick_a = datetime(2024, 9, 7, 16, 0, tzinfo=UTC)
    kick_b = datetime(2024, 9, 7, 19, 0, tzinfo=UTC)
    rows = []
    for i, kick in enumerate((kick_a, kick_b, kick_a), start=1):
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
    with ParquetStore(staged) as store:
        store.write_partition("games", pd.DataFrame(rows), {"season": 2024, "week": 1})
        slots = derive_kickoff_slots(store, 2024, 1)
    assert len(slots) == 2
    assert slots[0].capture_at == kick_a - SLOT_CLOSE_LEAD
    assert slots[1].capture_at == kick_b - SLOT_CLOSE_LEAD


def test_filter_payload_to_slot_matches_kickoff_only() -> None:
    other_kick = KICKOFF + timedelta(hours=3)
    payload = [
        _event(event_id="a", home="Auburn Tigers", away="Baylor Bears", kickoff=KICKOFF),
        _event(
            event_id="b",
            home="Michigan Wolverines",
            away="Texas Longhorns",
            kickoff=other_kick,
        ),
    ]
    kept = filter_payload_to_slot(payload, kickoff=KICKOFF)
    assert len(kept) == 1
    assert kept[0]["id"] == "a"


def test_slot_close_capture_stages_per_book_and_source_row_id(
    tmp_path: Path,
    team_map: dict[str, str],
) -> None:
    staged = tmp_path / "staged"
    raw = tmp_path / "raw"
    slot = _stage_fixture_week(staged)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200,
            content=json.dumps(AUBURN_PAYLOAD).encode(),
            headers={"x-requests-remaining": "99990", "x-requests-last": "3"},
        )

    client = _mock_client(httpx.MockTransport(handler))
    result = run_slot_close_capture(
        slot,
        raw_root=raw,
        staged_root=staged,
        captured_at=CAPTURE_AT,
        client=client,
        team_map=team_map,
    )
    assert result.status == "captured"
    assert result.credits_charged == LIVE_CREDITS_PER_SLOT
    assert calls["n"] == 1

    with ParquetStore(staged) as store:
        odds = store.read("odds_snapshots", filters={"season": 2024, "week": 1})
    assert (odds["decision_point"] == DECISION_POINT_SLOT_CLOSE).all()
    assert (odds["snapshot_source"] == "live").all()
    assert set(odds["book"].str.casefold()) >= {"fanduel", "draftkings"}
    assert odds["snapshot_id"].notna().all()
    fd = odds.loc[odds["book"] == "fanduel"]
    assert not fd.empty
    assert float(fd.loc[fd["side"] == "Auburn", "line"].iloc[0]) == -7.5


def test_slot_close_capture_idempotent_no_double_charge(
    tmp_path: Path,
    team_map: dict[str, str],
) -> None:
    staged = tmp_path / "staged"
    raw = tmp_path / "raw"
    slot = _stage_fixture_week(staged)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200,
            content=json.dumps(AUBURN_PAYLOAD).encode(),
            headers={"x-requests-remaining": "99990", "x-requests-last": "3"},
        )

    client = _mock_client(httpx.MockTransport(handler))
    run_slot_close_capture(
        slot,
        raw_root=raw,
        staged_root=staged,
        captured_at=CAPTURE_AT,
        client=client,
        team_map=team_map,
    )
    second = run_slot_close_capture(
        slot,
        raw_root=raw,
        staged_root=staged,
        captured_at=CAPTURE_AT + timedelta(seconds=30),
        client=client,
        team_map=team_map,
    )
    assert calls["n"] == 1
    assert second.skipped_reason in {"capture_marker", "staged_rows_present"}
    assert second.credits_charged == 0


def test_missed_slot_recorded_not_backfilled(
    tmp_path: Path,
    team_map: dict[str, str],
) -> None:
    staged = tmp_path / "staged"
    raw = tmp_path / "raw"
    slot = _stage_fixture_week(staged)
    after_kick = KICKOFF + timedelta(minutes=1)

    missed = run_slot_close_capture(
        slot,
        raw_root=raw,
        staged_root=staged,
        captured_at=after_kick,
        team_map=team_map,
    )
    assert missed.status == "missed"
    assert is_slot_missed(raw, slot)

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200,
            content=json.dumps(AUBURN_PAYLOAD).encode(),
            headers={"x-requests-remaining": "99990", "x-requests-last": "3"},
        )

    client = _mock_client(httpx.MockTransport(handler))
    retry = run_slot_close_capture(
        slot,
        raw_root=raw,
        staged_root=staged,
        captured_at=CAPTURE_AT,
        client=client,
        team_map=team_map,
    )
    assert calls["n"] == 0
    assert retry.status == "missed"
    assert retry.skipped_reason == "missed_marker"


def test_partial_book_coverage_still_stages_available_books(
    tmp_path: Path,
    team_map: dict[str, str],
) -> None:
    staged = tmp_path / "staged"
    raw = tmp_path / "raw"
    slot = _stage_fixture_week(staged)
    partial_payload = [
        _event(
            event_id="evt_auburn",
            home="Auburn Tigers",
            away="Baylor Bears",
            kickoff=KICKOFF,
            books=("fanduel",),
        )
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=json.dumps(partial_payload).encode(),
            headers={"x-requests-remaining": "99990", "x-requests-last": "3"},
        )

    client = _mock_client(httpx.MockTransport(handler))
    run_slot_close_capture(
        slot,
        raw_root=raw,
        staged_root=staged,
        captured_at=CAPTURE_AT,
        client=client,
        team_map=team_map,
    )
    with ParquetStore(staged) as store:
        odds = store.read("odds_snapshots", filters={"season": 2024, "week": 1})
    books = set(odds.loc[odds["decision_point"] == DECISION_POINT_SLOT_CLOSE, "book"])
    assert books == {"fanduel"}


def test_kickoff_moved_after_derivation_uses_current_schedule(
    tmp_path: Path,
    team_map: dict[str, str],
) -> None:
    staged = tmp_path / "staged"
    raw = tmp_path / "raw"
    old_kick = KICKOFF
    new_kick = KICKOFF + timedelta(hours=2)
    with ParquetStore(staged) as store:
        store.write_partition(
            "games",
            _games_rows(kickoff=old_kick),
            {"season": 2024, "week": 1},
        )
        store.write_partition("teams", _teams_rows(), {"season": 2024})
        old_slots = derive_kickoff_slots(store, 2024, 1)
        store.write_partition(
            "games",
            _games_rows(kickoff=new_kick),
            {"season": 2024, "week": 1},
        )
        new_slots = derive_kickoff_slots(store, 2024, 1)

    assert old_slots[0].kickoff != new_slots[0].kickoff
    payload_new = [
        _event(
            event_id="evt_auburn",
            home="Auburn Tigers",
            away="Baylor Bears",
            kickoff=new_kick,
        )
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=json.dumps(payload_new).encode(),
            headers={"x-requests-remaining": "99990", "x-requests-last": "3"},
        )

    client = _mock_client(httpx.MockTransport(handler))
    run_slot_close_capture(
        new_slots[0],
        raw_root=raw,
        staged_root=staged,
        captured_at=slot_close_instant(new_kick),
        client=client,
        team_map=team_map,
    )
    mark_slot_missed(
        raw,
        old_slots[0],
        reason="kickoff_rescheduled",
        as_of=new_kick + timedelta(minutes=1),
    )
    assert is_slot_missed(raw, old_slots[0])
    assert is_slot_capture_complete(raw, new_slots[0])


def test_settlement_integration_auburn_fanduel_ticket(
    tmp_path: Path,
    team_map: dict[str, str],
) -> None:
    staged = tmp_path / "staged"
    raw = tmp_path / "raw"
    slot = _stage_fixture_week(staged, game_id=401856636)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=json.dumps(AUBURN_PAYLOAD).encode(),
            headers={"x-requests-remaining": "99990", "x-requests-last": "3"},
        )

    client = _mock_client(httpx.MockTransport(handler))
    run_slot_close_capture(
        slot,
        raw_root=raw,
        staged_root=staged,
        captured_at=CAPTURE_AT,
        client=client,
        team_map=team_map,
    )

    bet_at = datetime(2026, 9, 1, 20, 38, 58, tzinfo=UTC)
    rec = build_recommendation_record(
        recommendation_id="401856636:side",
        game_id="401856636",
        season=2026,
        week=1,
        side="Auburn",
        edge=0.1472,
        bet_side_american=-102.0,
        bet_other_american=-118.0,
        recommended_at=bet_at,
        close_definition="odds_api_consensus",
        bet_line_source_row_id="bet-snapshot-auburn-fanduel-w1",
        book="fanduel",
        market="spread",
        bet_line=-7.5,
        n_books_available=4,
    )

    with ParquetStore(staged) as store:
        close = closing_quote_from_slot_close(
            store,
            game_id="401856636",
            book="fanduel",
            home_team="Auburn",
            away_team="Baylor",
            bet_on="Auburn",
            bet_line_home=-7.5,
            season=2024,
            week=1,
        )
    assert close is not None
    assert close.source_row_id
    assert close.book == "fanduel"

    settled = settle(rec, same_book_close=close)
    assert settled.clv_method == "same_line"
    assert settled.clv_settlement == "same_book"
    assert settled.is_headline is True
    assert settled.clv == settled.p_close_fair - settled.p_bet_fair


def test_wall_clock_ingest_does_not_tag_slot_close(
    tmp_path: Path,
    team_map: dict[str, str],
) -> None:
    staged = tmp_path / "staged"
    raw = tmp_path / "raw"
    _stage_fixture_week(staged)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=json.dumps(AUBURN_PAYLOAD).encode(),
            headers={"x-requests-remaining": "99990", "x-requests-last": "3"},
        )

    client = _mock_client(httpx.MockTransport(handler))
    run_odds_ingest(
        raw_root=raw,
        staged_root=staged,
        captured_at=KICKOFF + timedelta(hours=1),
        client=client,
        team_map=team_map,
    )
    with ParquetStore(staged) as store:
        odds = store.read("odds_snapshots", filters={"season": 2024, "week": 1})
    assert odds["decision_point"].isna().all()


def test_2026_credit_accounting_matches_v4_projection() -> None:
    staged_root = Path("data/staged")
    if not (staged_root / "games" / "season=2026").is_dir():
        pytest.skip("2026 staged games not present")
    with ParquetStore(staged_root) as store:
        report = credit_accounting_report(store, [2026], max_week=15)
    assert report.total_slots == 263
    assert report.total_credits == 789
    assert report.slots_by_season_week[(2026, 1)] == 37


def test_record_missed_slots_marks_past_kickoff(tmp_path: Path) -> None:
    staged = tmp_path / "staged"
    raw = tmp_path / "raw"
    slot = _stage_fixture_week(staged)
    with ParquetStore(staged) as store:
        n = record_missed_slots(
            store,
            raw,
            [2024],
            as_of=KICKOFF + timedelta(minutes=5),
        )
    assert n == 1
    assert is_slot_missed(raw, slot)


def test_plan_forward_slot_captures_respects_due_window(tmp_path: Path) -> None:
    staged = tmp_path / "staged"
    raw = tmp_path / "raw"
    slot = _stage_fixture_week(staged)
    with ParquetStore(staged) as store:
        before = plan_forward_slot_captures(
            store,
            [2024],
            as_of=CAPTURE_AT - timedelta(minutes=1),
            raw_root=raw,
        )
        due = plan_forward_slot_captures(
            store,
            [2024],
            as_of=CAPTURE_AT,
            raw_root=raw,
        )
    assert before == ()
    assert due == (slot,)
