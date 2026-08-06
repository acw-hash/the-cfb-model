"""Quarantine flow: persist validation_results and query partition status.

``validation_results`` is owned by the quality layer (not registered in
``SCHEMA_REGISTRY``) so Task 7 stays inside ``src/ncaa_quant/quality/``. Layout::

    {staged_dir}/validation_results/season={Y}/[week={W}/]part.parquet

Downstream consumers must call :func:`is_quarantined` and skip those partitions.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import pandas as pd  # type: ignore[import-untyped]
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

Status = Literal["PASSED", "QUARANTINED", "FLAGGED"]

_PART = "part.parquet"
_VALIDATION_TABLE = "validation_results"


@dataclass(frozen=True)
class ValidationRecord:
    """One expectation outcome for a partition."""

    run_id: str
    table: str
    season: int
    week: int | None
    status: Status
    expectation: str
    severity: str
    message: str
    sample_rows: list[dict[str, Any]]
    n_failures: int
    validated_at: datetime


def validation_root(staged_dir: Path | str) -> Path:
    """Root directory for the validation_results sidecar table."""
    return Path(staged_dir) / _VALIDATION_TABLE


def is_quarantined(
    staged_dir: Path | str,
    table: str,
    *,
    season: int,
    week: int | None = None,
) -> bool:
    """Return True if the latest run marked this partition ``QUARANTINED``.

    Downstream feature/rating code must skip quarantined partitions rather than
    crash. Absence of validation rows means "not yet validated" (not quarantined).
    """
    results = load_validation_results(staged_dir, table=table, season=season, week=week)
    if results.empty:
        return False
    # Latest run_id by validated_at.
    latest_run = results.sort_values("validated_at").iloc[-1]["run_id"]
    latest = results[results["run_id"] == latest_run]
    return bool((latest["status"] == "QUARANTINED").any())


def load_validation_results(
    staged_dir: Path | str,
    *,
    table: str | None = None,
    season: int | None = None,
    week: int | None = None,
) -> pd.DataFrame:
    """Read validation_results rows, optionally filtered."""
    root = validation_root(staged_dir)
    if not root.exists():
        return pd.DataFrame()
    paths = sorted(root.rglob(_PART))
    if not paths:
        return pd.DataFrame()
    frames = [pd.read_parquet(p) for p in paths]
    out = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    if table is not None:
        out = out[out["table"] == table]
    if season is not None:
        out = out[out["season"] == season]
    if week is not None:
        out = out[out["week"] == week]
    return out.reset_index(drop=True)


def write_validation_records(
    staged_dir: Path | str,
    records: list[ValidationRecord],
) -> list[Path]:
    """Append/overwrite validation result partitions for ``records``."""
    if not records:
        return []

    rows = [_record_to_row(r) for r in records]
    frame = pd.DataFrame(rows)
    written: list[Path] = []
    for (season, week, table), group in frame.groupby(
        ["season", "week", "table"], dropna=False, sort=True
    ):
        season_i = int(season)
        week_i = None if pd.isna(week) else int(week)
        path = _partition_path(staged_dir, season=season_i, week=week_i)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Merge with existing rows for other tables/runs in the same partition dir.
        existing = pd.read_parquet(path) if path.exists() else pd.DataFrame()
        if not existing.empty:
            # Drop prior rows for this table from older content in same file, keep others.
            existing = existing[existing["table"] != table]
            combined = pd.concat([existing, group], ignore_index=True)
        else:
            combined = group
        _atomic_write_parquet(combined, path)
        written.append(path)
    return written


def partition_status_from_findings(
    *,
    hard_failures: int,
    flags: int,
) -> Status:
    """Map failure/flag counts to partition status."""
    if hard_failures > 0:
        return "QUARANTINED"
    if flags > 0:
        return "FLAGGED"
    return "PASSED"


def _partition_path(staged_dir: Path | str, *, season: int, week: int | None) -> Path:
    base = validation_root(staged_dir) / f"season={int(season)}"
    if week is not None:
        base = base / f"week={int(week)}"
    return base / _PART


def _record_to_row(record: ValidationRecord) -> dict[str, Any]:
    return {
        "run_id": record.run_id,
        "table": record.table,
        "season": record.season,
        "week": record.week,
        "status": record.status,
        "expectation": record.expectation,
        "severity": record.severity,
        "message": record.message,
        "sample_rows_json": json.dumps(record.sample_rows, default=str),
        "n_failures": record.n_failures,
        "validated_at": record.validated_at,
    }


def _atomic_write_parquet(df: pd.DataFrame, path: Path) -> None:
    tmp = path.parent / f".{_PART}.tmp.{uuid.uuid4().hex}"
    try:
        table = pa.Table.from_pandas(df, preserve_index=False)
        pq.write_table(table, tmp, compression="snappy")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def new_run_id(now: datetime | None = None) -> str:
    """Generate a run identifier ``YYYYMMDDTHHMMSSZ_<shortuuid>``."""
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}_{uuid.uuid4().hex[:8]}"


def utcnow() -> datetime:
    """Timezone-aware UTC now."""
    return datetime.now(UTC)
