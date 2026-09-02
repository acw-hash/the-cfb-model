"""Tests for wall-clock slot_close polling scheduler (V4-B1a)."""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from ncaa_quant.config import load_config
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.features.market_lines import SLOT_CLOSE_LEAD
from ncaa_quant.ingestion.odds_api import load_team_name_map
from ncaa_quant.ingestion.slot_close_capture import (
    LIVE_CREDITS_PER_SLOT,
    KickoffSlot,
    is_slot_capture_complete,
    is_slot_missed,
    slot_close_instant,
)
from ncaa_quant.pipelines.slot_close_schedule import (
    DEFAULT_SLOT_CLOSE_POLL_INTERVAL,
    copy_slots_to_staged,
    count_poll_invocations_per_day,
    execute_slot_close_poll,
    poll_lock_path,
    read_poll_outcome_ledger,
    remap_slots_to_calendar_day,
    simulate_saturday_polling,
    slots_on_calendar_day,
    stage_simulation_games,
)
from tests.unit.test_slot_close_capture import (
    AUBURN_PAYLOAD,
    CAPTURE_AT,
    KICKOFF,
    _mock_client,
    _stage_fixture_week,
)

BUSIEST_SATURDAY_PROFILE = datetime(2026, 9, 19, tzinfo=UTC)
SIMULATION_ANCHOR_SATURDAY = datetime(2024, 9, 7, tzinfo=UTC)


def test_no_op_poll_zero_credits(tmp_path: Path) -> None:
    staged = tmp_path / "staged"
    raw = tmp_path / "raw"
    _stage_fixture_week(staged)
    poll = execute_slot_close_poll(
        seasons=[2024],
        as_of=CAPTURE_AT - timedelta(minutes=10),
        config=load_config(),
        raw_root=raw,
        staged_root=staged,
    )
    assert poll.outcome == "no_op"
    assert poll.due_count == 0
    assert poll.credits_spent == 0


def test_poll_captures_due_slot(tmp_path: Path, team_map: dict[str, str]) -> None:
    staged = tmp_path / "staged"
    raw = tmp_path / "raw"
    _stage_fixture_week(staged)

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            content=json.dumps(AUBURN_PAYLOAD).encode(),
            headers={"x-requests-remaining": "99990", "x-requests-last": "3"},
        )

    client = _mock_client(httpx.MockTransport(handler))
    poll = execute_slot_close_poll(
        seasons=[2024],
        as_of=CAPTURE_AT,
        config=load_config(),
        raw_root=raw,
        staged_root=staged,
        client=client,
        team_map=team_map,
    )
    assert poll.outcome == "captured"
    assert poll.captured == 1
    assert poll.credits_spent == LIVE_CREDITS_PER_SLOT


def test_poll_writes_outcome_ledger(tmp_path: Path, team_map: dict[str, str]) -> None:
    staged = tmp_path / "staged"
    raw = tmp_path / "raw"
    _stage_fixture_week(staged)

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            content=json.dumps(AUBURN_PAYLOAD).encode(),
            headers={"x-requests-remaining": "99990", "x-requests-last": "3"},
        )

    client = _mock_client(httpx.MockTransport(handler))
    poll = execute_slot_close_poll(
        seasons=[2024],
        as_of=CAPTURE_AT,
        config=load_config(),
        raw_root=raw,
        staged_root=staged,
        client=client,
        team_map=team_map,
    )
    rows = read_poll_outcome_ledger(raw, day=poll.as_of)
    assert rows
    last = rows[-1]
    assert last["outcome"] == "captured"
    assert last["slots"][0]["status"] == "captured"


def test_concurrent_poll_skipped(tmp_path: Path) -> None:
    staged = tmp_path / "staged"
    raw = tmp_path / "raw"
    _stage_fixture_week(staged)
    lock = poll_lock_path(raw)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("held\n", encoding="utf-8")

    poll = execute_slot_close_poll(
        seasons=[2024],
        as_of=CAPTURE_AT,
        config=load_config(),
        raw_root=raw,
        staged_root=staged,
    )
    assert poll.outcome == "skipped_concurrent"
    assert poll.credits_spent == 0


def test_overlapping_polls_do_not_double_charge(
    tmp_path: Path,
    team_map: dict[str, str],
) -> None:
    staged = tmp_path / "staged"
    raw = tmp_path / "raw"
    slot = _stage_fixture_week(staged)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        calls["n"] += 1
        return httpx.Response(
            200,
            content=json.dumps(AUBURN_PAYLOAD).encode(),
            headers={"x-requests-remaining": "99990", "x-requests-last": "3"},
        )

    client = _mock_client(httpx.MockTransport(handler))
    cfg = load_config()
    first = execute_slot_close_poll(
        seasons=[2024],
        as_of=CAPTURE_AT,
        config=cfg,
        raw_root=raw,
        staged_root=staged,
        client=client,
        team_map=team_map,
    )
    second = execute_slot_close_poll(
        seasons=[2024],
        as_of=CAPTURE_AT + timedelta(minutes=1),
        config=cfg,
        raw_root=raw,
        staged_root=staged,
        client=client,
        team_map=team_map,
    )
    assert first.credits_spent == LIVE_CREDITS_PER_SLOT
    assert second.credits_spent == 0
    assert calls["n"] == 1
    assert is_slot_capture_complete(raw, slot)


def test_poll_lock_released_after_slow_run(tmp_path: Path, team_map: dict[str, str]) -> None:
    staged = tmp_path / "staged"
    raw = tmp_path / "raw"
    _stage_fixture_week(staged)
    started = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        started.set()
        time.sleep(0.05)
        return httpx.Response(
            200,
            content=json.dumps(AUBURN_PAYLOAD).encode(),
            headers={"x-requests-remaining": "99990", "x-requests-last": "3"},
        )

    client = _mock_client(httpx.MockTransport(handler))
    cfg = load_config()
    thread = threading.Thread(
        target=lambda: execute_slot_close_poll(
            seasons=[2024],
            as_of=CAPTURE_AT,
            config=cfg,
            raw_root=raw,
            staged_root=staged,
            client=client,
            team_map=team_map,
        ),
    )
    thread.start()
    assert started.wait(timeout=2.0)
    overlap = execute_slot_close_poll(
        seasons=[2024],
        as_of=CAPTURE_AT,
        config=cfg,
        raw_root=raw,
        staged_root=staged,
    )
    assert overlap.outcome == "skipped_concurrent"
    thread.join(timeout=5.0)
    assert not poll_lock_path(raw).is_file()


def test_late_poll_inside_window_still_captures(
    tmp_path: Path,
    team_map: dict[str, str],
) -> None:
    staged = tmp_path / "staged"
    raw = tmp_path / "raw"
    slot = _stage_fixture_week(staged)
    late = KICKOFF - timedelta(minutes=1)

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            content=json.dumps(AUBURN_PAYLOAD).encode(),
            headers={"x-requests-remaining": "99990", "x-requests-last": "3"},
        )

    client = _mock_client(httpx.MockTransport(handler))
    poll = execute_slot_close_poll(
        seasons=[2024],
        as_of=late,
        config=load_config(),
        raw_root=raw,
        staged_root=staged,
        client=client,
        team_map=team_map,
    )
    assert poll.captured == 1
    assert is_slot_capture_complete(raw, slot)


def test_poll_after_kickoff_records_missed(tmp_path: Path) -> None:
    staged = tmp_path / "staged"
    raw = tmp_path / "raw"
    slot = _stage_fixture_week(staged)
    poll = execute_slot_close_poll(
        seasons=[2024],
        as_of=KICKOFF + timedelta(seconds=30),
        config=load_config(),
        raw_root=raw,
        staged_root=staged,
    )
    assert poll.missed_recorded >= 1
    assert is_slot_missed(raw, slot)
    assert poll.credits_spent == 0


def test_saturday_simulation_fixture_slate(tmp_path: Path, team_map: dict[str, str]) -> None:
    staged = tmp_path / "staged"
    raw = tmp_path / "raw"
    base = SIMULATION_ANCHOR_SATURDAY.replace(hour=16, minute=0, second=0, microsecond=0)
    slots = tuple(
        KickoffSlot(
            season=2024,
            week=1,
            kickoff=base + timedelta(hours=i),
            game_ids=(401856636 + i,),
        )
        for i in range(4)
    )
    stage_simulation_games(staged, slots)
    skip_at = {slot_close_instant(slots[1].kickoff)}
    result = simulate_saturday_polling(
        slots,
        calendar_day=base,
        staged_root=staged,
        raw_root=raw,
        seasons=[2024],
        skip_poll_at=skip_at,
        fail_slot_id_once=slots[2].slot_id,
        team_map=team_map,
    )
    assert result.missed_slots == 0
    assert result.captured_slots == len(slots)
    assert result.credits_spent == len(slots) * LIVE_CREDITS_PER_SLOT
    assert result.api_errors_injected == 1
    assert result.skipped_polls == 1


def _odds_display_name(school: str, team_map: dict[str, str]) -> str:
    for odds_name, canonical in team_map.items():
        if canonical == school:
            return odds_name
    return school


def test_2026_busiest_saturday_simulation(tmp_path: Path, team_map: dict[str, str]) -> None:
    source = Path("data/staged")
    if not (source / "games" / "season=2026").is_dir():
        pytest.skip("2026 staged games not present")
    staged = tmp_path / "staged"
    raw = tmp_path / "raw"
    with ParquetStore(source) as store:
        profile = slots_on_calendar_day(store, 2026, calendar_day=BUSIEST_SATURDAY_PROFILE)
        if len(profile) < 10:
            pytest.skip("insufficient Saturday slots in staged 2026 games")
        slots = remap_slots_to_calendar_day(profile, SIMULATION_ANCHOR_SATURDAY)
        copy_slots_to_staged(store, staged, slots)

    def payload_for_slot(slot: KickoffSlot) -> bytes:
        with ParquetStore(staged) as store:
            games = store.read("games", filters={"season": slot.season, "week": slot.week})
            game = games.loc[games["game_id"] == slot.game_ids[0]].iloc[0]
            teams = store.read("teams", filters={"season": slot.season})
            home = teams.loc[teams["team_id"] == int(game.home_team_id), "school"].iloc[0]
            away = teams.loc[teams["team_id"] == int(game.away_team_id), "school"].iloc[0]
        kick = slot.kickoff.strftime("%Y-%m-%dT%H:%M:%SZ")
        body = [
            {
                "id": f"evt_{slot.slot_id}",
                "sport_key": "americanfootball_ncaaf",
                "commence_time": kick,
                "home_team": _odds_display_name(str(home), team_map),
                "away_team": _odds_display_name(str(away), team_map),
                "bookmakers": AUBURN_PAYLOAD[0]["bookmakers"],
            }
        ]
        return json.dumps(body).encode()

    mid_slot = slots[len(slots) // 2]
    skip_at = {slot_close_instant(mid_slot.kickoff)}
    result = simulate_saturday_polling(
        slots,
        calendar_day=SIMULATION_ANCHOR_SATURDAY,
        staged_root=staged,
        raw_root=raw,
        seasons=[2026],
        skip_poll_at=skip_at,
        fail_slot_id_once=slots[0].slot_id,
        capture_handler=payload_for_slot,
        team_map=team_map,
    )
    assert result.missed_slots == 0
    assert result.captured_slots == len(slots)
    assert result.credits_spent == len(slots) * LIVE_CREDITS_PER_SLOT


def test_count_poll_invocations_per_day() -> None:
    assert (
        count_poll_invocations_per_day(
            poll_interval=DEFAULT_SLOT_CLOSE_POLL_INTERVAL,
        )
        == 720
    )


def test_capture_window_uses_slot_close_lead() -> None:
    assert slot_close_instant(KICKOFF) == KICKOFF - SLOT_CLOSE_LEAD


@pytest.fixture
def team_map() -> dict[str, str]:
    return load_team_name_map(Path("configs/team_names.yaml"))
