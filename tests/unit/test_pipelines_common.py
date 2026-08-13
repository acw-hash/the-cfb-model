"""Unit tests for pipeline idempotency and dead-letter queue."""

from __future__ import annotations

import contextlib

from ncaa_quant.pipelines.common import (
    IdempotencyStore,
    PartitionKey,
    run_idempotent,
    stable_result_hash,
)


def test_idempotency_skips_rerun(tmp_path) -> None:
    cfg = _cfg(tmp_path)
    key = PartitionKey("test", "p1")
    calls = {"n": 0}

    def fn() -> dict[str, str]:
        calls["n"] += 1
        return {"v": "one"}

    out1 = run_idempotent(key, fn, config=cfg)
    out2 = run_idempotent(key, fn, config=cfg)
    assert out1 == out2 == {"v": "one"}
    assert calls["n"] == 1
    store = IdempotencyStore(cfg.pipeline.idempotency_dir)
    assert store.is_done(key)


def test_dead_letter_on_poison(tmp_path) -> None:
    key = PartitionKey("test", "bad")

    def boom() -> dict[str, str]:
        msg = "poison"
        raise RuntimeError(msg)

    dlq = tmp_path / "dlq"
    with contextlib.suppress(RuntimeError):
        run_idempotent(key, boom, config=_cfg(tmp_path, dlq=dlq))
    files = list(dlq.glob("*.json"))
    assert len(files) == 1


def test_stable_result_hash_deterministic() -> None:
    a = stable_result_hash({"b": 1, "a": 2})
    b = stable_result_hash({"a": 2, "b": 1})
    assert a == b


def _cfg(tmp_path, dlq=None):
    from ncaa_quant.config import AppConfig, PipelineConfig

    return AppConfig(
        pipeline=PipelineConfig(
            idempotency_dir=str(tmp_path / "idem"),
            dead_letter_dir=str(dlq or tmp_path / "dlq"),
        )
    )
