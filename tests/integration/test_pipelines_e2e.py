"""End-to-end fixture-week dry run for Prefect production flows (Task 24)."""

from __future__ import annotations

import pytest

from ncaa_quant.config import AppConfig, PipelineConfig
from ncaa_quant.pipelines.common import IdempotencyStore
from ncaa_quant.pipelines.notifications import AlertKind, RecordingNotifier
from ncaa_quant.pipelines.postgame import run_postgame_ingest
from ncaa_quant.pipelines.predict import run_fixture_week_publish
from ncaa_quant.pipelines.retrain import run_retrain_gate
from ncaa_quant.pipelines.settle import run_settle_clv
from ncaa_quant.pipelines.weekly import run_weekly_update


@pytest.fixture
def pipeline_config(tmp_path) -> AppConfig:
    state = tmp_path / "state"
    return AppConfig(
        pipeline=PipelineConfig(
            idempotency_dir=str(state / "idem"),
            dead_letter_dir=str(state / "dlq"),
        )
    )


def test_fixture_week_dry_run_end_to_end(pipeline_config) -> None:
    """Full fixture-week chain: postgame → weekly → predict → settle."""
    notifier = RecordingNotifier()
    season, week = 2024, 5

    post = run_postgame_ingest(
        season=season,
        week=week,
        slot="fixture",
        cfbd_fn=lambda **kw: {"partitions_written": 1, "rows_written": 10},
        quality_fn=lambda **kw: type(
            "Q", (), {"partitions_quarantined": 0, "hard_failure_count": 0}
        )(),
        config=pipeline_config,
    )
    assert post["season"] == season

    weekly = run_weekly_update(
        season=season,
        week=week,
        update_fn=lambda **kw: {
            "season": season,
            "week": week,
            "stage1_updated": True,
            "features_refreshed": True,
            "innovation_flags": [],
        },
        config=pipeline_config,
    )
    assert weekly["stage1_updated"] is True

    predict = run_fixture_week_publish(
        season=season,
        week=week,
        config=pipeline_config,
        notifier=notifier,
    )
    assert predict["n_candidates"] == 0
    assert predict["n_accepted"] == 0
    assert predict["stale"]["is_stale"] is False
    assert len(predict["predictions"]) == 2

    settle = run_settle_clv(
        season=season,
        week=week,
        recommendations=[],
        closes={},
        config=pipeline_config,
    )
    assert settle["n_settled"] == 0

    kinds = {a.kind for a in notifier.sent}
    assert AlertKind.NEW_BET_CANDIDATE not in kinds


def test_idempotent_rerun_fixture_week(pipeline_config) -> None:
    """Re-running predict_publish changes nothing (idempotency)."""
    out1 = run_fixture_week_publish(season=2024, week=6, config=pipeline_config)
    out2 = run_fixture_week_publish(season=2024, week=6, config=pipeline_config)
    assert out1["predictions"] == out2["predictions"]
    assert out1["n_accepted"] == out2["n_accepted"]

    store = IdempotencyStore(pipeline_config.pipeline.idempotency_dir)
    # Partition stamps published_at (run clock), not decision as_of.
    prefix = "predict_publish:2024-w6-tuesday_primary-"
    matching = [tok for tok in store._load() if tok.startswith(prefix)]  # noqa: SLF001
    assert len(matching) == 1


def test_retrain_gate_skips_non_gate_week(pipeline_config) -> None:
    out = run_retrain_gate(season=2024, week=3, config=pipeline_config)
    assert out["skipped"] is True
