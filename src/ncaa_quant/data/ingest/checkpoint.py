"""Persistent ``(season, dataset, page)`` checkpoint for CFBD backfill resume."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def checkpoint_key(season: int, dataset: str, page: int) -> str:
    """Canonical key for one resumable unit."""
    return f"{int(season)}|{dataset}|{int(page)}"


@dataclass
class BackfillCheckpoint:
    """On-disk resume state. Survives process death."""

    path: Path
    completed: set[str] = field(default_factory=set)
    meta: dict[str, Any] = field(default_factory=dict)

    def is_done(self, season: int, dataset: str, page: int) -> bool:
        return checkpoint_key(season, dataset, page) in self.completed

    def mark(self, season: int, dataset: str, page: int) -> None:
        self.completed.add(checkpoint_key(season, dataset, page))
        self.meta["updated_at"] = datetime.now(tz=UTC).isoformat()
        save_checkpoint(self)


def load_checkpoint(path: Path | str) -> BackfillCheckpoint:
    """Load checkpoint from ``path``, or empty if missing/corrupt."""
    p = Path(path)
    if not p.exists():
        return BackfillCheckpoint(path=p)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return BackfillCheckpoint(path=p)
    completed = set(str(x) for x in raw.get("completed", []))
    meta = {k: v for k, v in raw.items() if k != "completed"}
    return BackfillCheckpoint(path=p, completed=completed, meta=meta)


def save_checkpoint(state: BackfillCheckpoint) -> None:
    """Atomically persist checkpoint JSON."""
    state.path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "completed": sorted(state.completed),
        **state.meta,
        "updated_at": datetime.now(tz=UTC).isoformat(),
    }
    tmp = state.path.with_suffix(state.path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(state.path)


def mark_complete(
    state: BackfillCheckpoint,
    season: int,
    dataset: str,
    page: int,
) -> None:
    """Mark one unit complete and flush to disk."""
    state.mark(season, dataset, page)
