"""Tests for CFBD backfill checkpoint + quota pacing (Task 23-FIX-BACKFILL)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from ncaa_quant.data.ingest.checkpoint import (
    checkpoint_key,
    load_checkpoint,
    mark_complete,
    save_checkpoint,
)
from ncaa_quant.data.ingest.quota import (
    CfbdQuotaStatus,
    fetch_quota_status,
    next_resume_at,
    sleep_until_quota_window,
    wait_for_quota,
)


def test_checkpoint_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "ckpt.json"
    state = load_checkpoint(path)
    assert not state.is_done(2024, "games", 0)
    mark_complete(state, 2024, "games", 0)
    mark_complete(state, 2024, "plays", 3)

    reloaded = load_checkpoint(path)
    assert reloaded.is_done(2024, "games", 0)
    assert reloaded.is_done(2024, "plays", 3)
    assert not reloaded.is_done(2025, "games", 0)
    assert checkpoint_key(2024, "plays", 3) in reloaded.completed


def test_checkpoint_survives_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "ckpt.json"
    path.write_text("{not-json", encoding="utf-8")
    state = load_checkpoint(path)
    assert state.completed == set()


def test_checkpoint_atomic_save(tmp_path: Path) -> None:
    path = tmp_path / "ckpt.json"
    state = load_checkpoint(path)
    state.completed.add(checkpoint_key(2019, "roster", 0))
    save_checkpoint(state)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "2019|roster|0" in raw["completed"]
    assert "updated_at" in raw


def _info_payload(
    *,
    remaining: int,
    reset_at: datetime,
    monthly_limit: int = 1000,
) -> dict[str, object]:
    return {
        "patronLevel": 0,
        "tierName": "Free",
        "monthlyLimit": monthly_limit,
        "remainingCalls": remaining,
        "usedCalls": monthly_limit - remaining,
        "resetAt": reset_at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "sharedPool": True,
        "products": ["cfb"],
        "features": {},
    }


def test_fetch_quota_status_parses_info() -> None:
    reset = datetime(2026, 9, 1, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/info"
        return httpx.Response(200, json=_info_payload(remaining=42, reset_at=reset))

    transport = httpx.MockTransport(handler)
    status = fetch_quota_status("fake-key", transport=transport)
    assert status.remaining_calls == 42
    assert status.tier_name == "Free"
    assert status.monthly_limit == 1000
    assert status.reset_at == reset
    assert status.exhausted is False


def test_sleep_until_quota_window_logs_and_sleeps() -> None:
    reset = datetime(2026, 9, 1, tzinfo=UTC)
    status = CfbdQuotaStatus(
        patron_level=0,
        tier_name="Free",
        monthly_limit=1000,
        remaining_calls=0,
        used_calls=1000,
        reset_at=reset,
        observed_at=datetime(2026, 8, 5, tzinfo=UTC),
        raw={},
    )
    slept: list[float] = []
    resume = sleep_until_quota_window(
        status,
        now=datetime(2026, 8, 5, 12, 0, 0, tzinfo=UTC),
        sleep_fn=slept.append,
    )
    assert resume == next_resume_at(status)
    assert len(slept) == 1
    assert slept[0] > 20 * 24 * 3600  # ~late August → Sept 1


def test_wait_for_quota_recovers_after_reset() -> None:
    reset = datetime(2026, 8, 5, 12, 0, 0, tzinfo=UTC)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        remaining = 0 if calls["n"] == 1 else 500
        return httpx.Response(200, json=_info_payload(remaining=remaining, reset_at=reset))

    transport = httpx.MockTransport(handler)
    slept: list[float] = []
    status = wait_for_quota(
        "fake-key",
        min_remaining=10,
        sleep_fn=slept.append,
        transport=transport,
        max_wait=timedelta(hours=1),
    )
    assert status.remaining_calls == 500
    assert slept  # paused at least once
