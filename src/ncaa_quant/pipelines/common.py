"""Shared Prefect flow infrastructure: idempotency, DLQ, partition keys (DESIGN §10)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ncaa_quant.config import AppConfig, load_config
from ncaa_quant.utils.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PartitionKey:
    """Idempotency key for a flow task: (source, partition)."""

    source: str
    partition: str

    def token(self) -> str:
        return f"{self.source}:{self.partition}"


class IdempotencyStore:
    """Filesystem idempotency ledger keyed by :class:`PartitionKey`."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._path = self.root / "idempotency.json"

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self._path.is_file():
            return {}
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            msg = f"invalid idempotency ledger: {self._path}"
            raise TypeError(msg)
        return payload

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        self._path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def is_done(self, key: PartitionKey) -> bool:
        return key.token() in self._load()

    def result(self, key: PartitionKey) -> dict[str, Any] | None:
        entry = self._load().get(key.token())
        if entry is None:
            return None
        raw = entry.get("result")
        return raw if isinstance(raw, dict) else None

    def mark_done(self, key: PartitionKey, result: dict[str, Any]) -> None:
        data = self._load()
        token = key.token()
        if token in data:
            log.info("idempotency_skip", key=token)
            return
        data[token] = {
            "completed_at": datetime.now(tz=UTC).isoformat(),
            "result": result,
        }
        self._save(data)
        log.info("idempotency_marked", key=token)


class DeadLetterQueue:
    """Append-only poisoned-partition queue (DESIGN §10)."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def enqueue(
        self,
        key: PartitionKey,
        error: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> Path:
        stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        safe = key.token().replace(":", "_").replace("/", "_")
        path = self.root / f"{stamp}_{safe}.json"
        payload = {
            "key": asdict(key),
            "error": error,
            "context": context or {},
            "enqueued_at": datetime.now(tz=UTC).isoformat(),
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        log.error("dead_letter_enqueued", key=key.token(), path=str(path))
        return path


def stable_result_hash(payload: dict[str, Any]) -> str:
    """Deterministic hash for comparing idempotent re-run outputs."""
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def pipeline_state_dirs(config: AppConfig | None = None) -> tuple[Path, Path]:
    """Return (idempotency_dir, dead_letter_dir) from config."""
    cfg = config or load_config()
    return Path(cfg.pipeline.idempotency_dir), Path(cfg.pipeline.dead_letter_dir)


def run_idempotent(
    key: PartitionKey,
    fn: Any,
    *,
    config: AppConfig | None = None,
    on_poison: bool = True,
) -> dict[str, Any]:
    """Execute ``fn()`` once per partition; return cached result on re-run.

    On exception, optionally enqueue to the dead-letter queue and re-raise.
    """
    idem_root, dlq_root = pipeline_state_dirs(config)
    store = IdempotencyStore(idem_root)
    if store.is_done(key):
        cached = store.result(key)
        if cached is not None:
            return cached

    try:
        result = fn()
        if not isinstance(result, dict):
            msg = "idempotent task must return a dict"
            raise TypeError(msg)
        store.mark_done(key, result)
        return result
    except Exception as exc:
        if on_poison:
            DeadLetterQueue(dlq_root).enqueue(key, str(exc))
        raise
