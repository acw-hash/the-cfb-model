"""Chaos test: killed ingestion → STALE predictions still publish; bets suppressed."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ncaa_quant.betting.filters import FilterReason
from ncaa_quant.config import AppConfig, PathsConfig, PipelineConfig
from ncaa_quant.pipelines.notifications import RecordingNotifier
from ncaa_quant.pipelines.predict import run_chaos_stale_publish


@pytest.fixture
def chaos_config(tmp_path) -> AppConfig:
    state = tmp_path / "state"
    return AppConfig(
        paths=PathsConfig(raw_dir=str(tmp_path / "raw")),
        pipeline=PipelineConfig(
            idempotency_dir=str(state / "idem"),
            dead_letter_dir=str(state / "dlq"),
            stale_odds_max_age_hours=24.0,
        ),
    )


def test_chaos_stale_publish_and_bet_suppression(chaos_config, tmp_path) -> None:
    """Acceptance-blocking: STALE stamp visible; zero accepted bets."""
    raw_root = tmp_path / "raw" / "odds_api"
    last_good = datetime.now(tz=UTC) - timedelta(hours=4)
    notifier = RecordingNotifier()

    result = run_chaos_stale_publish(
        raw_root=raw_root,
        season=2024,
        week=5,
        last_good_at=last_good,
        config=chaos_config,
        notifier=notifier,
    )

    assert result["ingest_failed"] is True

    assert result["stale"]["is_stale"] is True
    assert result["stale"]["combined_stamp"] is not None
    assert "STALE(odds" in result["stale"]["combined_stamp"]
    for pred in result["predictions"]:
        assert pred["is_stale"] is True
        assert pred["stale_stamp"] is not None
        assert "STALE(odds" in pred["stale_stamp"]

    assert result["n_candidates"] >= 1
    assert result["n_accepted"] == 0
    assert result["n_rejected"] >= 1
    assert len(result["stale_rejections"]) >= 1
    game_id, reasons = result["stale_rejections"][0]
    assert game_id == "g-chaos-1"
    assert FilterReason.STALE_INPUTS in reasons

    bet_alerts = [a for a in notifier.sent if a.kind.value == "new_bet_candidate"]
    assert bet_alerts == []
