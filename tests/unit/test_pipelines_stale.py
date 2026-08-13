"""Unit tests for STALE mode helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ncaa_quant.config import AppConfig, PipelineConfig
from ncaa_quant.pipelines.stale import (
    find_last_good_capture,
    format_stale_stamp,
    resolve_stale_context,
    stamp_predictions,
)


def test_format_stale_stamp() -> None:
    s = format_stale_stamp("odds", timedelta(hours=4.25))
    assert s.startswith("STALE(odds,")
    assert "4.2h" in s


def test_find_last_good_capture(tmp_path) -> None:
    day = tmp_path / "2024-09-01"
    day.mkdir(parents=True)
    (day / "20240901T100000000000Z.json").write_text("[]\n")
    (day / "20240901T120000000000Z.json").write_text("[]\n")
    ts = find_last_good_capture(tmp_path)
    assert ts is not None
    assert ts.hour == 12


def test_resolve_stale_context_on_failure(tmp_path) -> None:
    day = tmp_path / "2024-09-01"
    day.mkdir(parents=True)
    last = datetime(2024, 9, 1, 10, 0, tzinfo=UTC)
    (day / "20240901T100000000000Z.json").write_text("[]\n")
    now = last + timedelta(hours=3)
    ctx = resolve_stale_context(
        ingest_failed=True,
        raw_root=tmp_path,
        now=now,
        config=AppConfig(pipeline=PipelineConfig(stale_odds_max_age_hours=6.0)),
    )
    assert ctx.is_stale
    assert ctx.combined_stamp is not None
    assert "STALE(odds" in ctx.combined_stamp


def test_stamp_predictions_applies_stale(tmp_path) -> None:
    day = tmp_path / "2024-09-01"
    day.mkdir(parents=True)
    (day / "20240901T100000000000Z.json").write_text("[]\n")
    now = datetime(2024, 9, 1, 13, 0, tzinfo=UTC)
    cfg = AppConfig(pipeline=PipelineConfig(stale_odds_max_age_hours=6.0))
    ctx = resolve_stale_context(ingest_failed=True, raw_root=tmp_path, now=now, config=cfg)
    stamped = stamp_predictions(
        [{"game_id": "g1", "mu_margin": 1.0, "sigma_margin": 14.0}],
        ctx,
    )
    assert stamped[0].is_stale
    assert stamped[0].stale_stamp is not None
    assert "STALE(odds" in stamped[0].stale_stamp
