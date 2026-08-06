"""Incremental feature materialization (DESIGN §4.7 / §15 item 9).

Writes Parquet partitions under ``data/features/`` keyed by
``(feature, version, season, week)``, skipping unchanged partitions via a
sidecar content hash. DVC hooks are thin wrappers around ``dvc add`` — see
``docs/feature_store.md``.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd  # type: ignore[import-untyped]
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from ncaa_quant.features.builder import AS_OF_COL, ENTITY_COL, VALUE_COL, FeatureBuilder
from ncaa_quant.features.registry import FeatureRegistry, FeatureSpec, resolve_build_order
from ncaa_quant.utils.timeutils import assert_tz_aware, to_utc

_PART_FILENAME = "part.parquet"
_META_FILENAME = "meta.json"
_HASH_CHUNK = 1 << 20


class MaterializeError(ValueError):
    """Feature materialization failure."""


@dataclass(frozen=True)
class PartitionRef:
    """Identity of one materialized feature partition."""

    feature: str
    version: str
    season: int
    week: int

    def relative_dir(self) -> Path:
        return (
            Path(self.feature) / f"v{self.version}" / f"season={self.season}" / f"week={self.week}"
        )


@dataclass(frozen=True)
class MaterializeResult:
    """Outcome of a single partition materialization attempt."""

    partition: PartitionRef
    path: Path
    skipped: bool
    content_hash: str
    n_rows: int


BuilderFactory = Callable[[FeatureSpec], FeatureBuilder]


def partition_dir(root: Path | str, ref: PartitionRef) -> Path:
    """Absolute directory for a feature partition under ``root``."""
    return Path(root) / ref.relative_dir()


def partition_parquet_path(root: Path | str, ref: PartitionRef) -> Path:
    """Path to ``part.parquet`` for ``ref``."""
    return partition_dir(root, ref) / _PART_FILENAME


def materialize_partition(
    builder: FeatureBuilder,
    *,
    entity_ids: Sequence[Any],
    as_of: datetime,
    season: int,
    week: int,
    output_root: Path | str,
    force: bool = False,
) -> MaterializeResult:
    """Build one ``(season, week)`` partition and write it under ``output_root``.

    Skips the builder call when an existing partition's ``meta.json`` matches
    the current feature ``spec_hash`` and on-disk content hash (unless
    ``force=True``).
    """
    assert_tz_aware(as_of)
    as_of_utc = to_utc(as_of)
    spec = builder.spec
    ref = PartitionRef(
        feature=spec.name,
        version=spec.version,
        season=int(season),
        week=int(week),
    )
    out_dir = partition_dir(output_root, ref)
    part_path = out_dir / _PART_FILENAME
    meta_path = out_dir / _META_FILENAME
    spec_hash = spec.spec_hash()

    if not force and _is_unchanged(part_path, meta_path, spec_hash=spec_hash):
        meta = _read_meta(meta_path)
        return MaterializeResult(
            partition=ref,
            path=part_path,
            skipped=True,
            content_hash=str(meta["content_hash"]),
            n_rows=int(meta.get("n_rows", 0)),
        )

    frame = builder.build(entity_ids, as_of_utc)
    frame = _annotate_partition(frame, season=season, week=week, feature=spec.name)
    out_dir.mkdir(parents=True, exist_ok=True)
    content_hash = _write_partition_atomic(frame, part_path)
    meta = {
        "feature": spec.name,
        "version": spec.version,
        "season": int(season),
        "week": int(week),
        "spec_hash": spec_hash,
        "content_hash": content_hash,
        "n_rows": int(len(frame)),
        "as_of": as_of_utc.isoformat(),
    }
    _write_json_atomic(meta_path, meta)
    return MaterializeResult(
        partition=ref,
        path=part_path,
        skipped=False,
        content_hash=content_hash,
        n_rows=len(frame),
    )


def materialize_registry(
    registry: FeatureRegistry,
    *,
    builder_factory: BuilderFactory,
    entity_ids: Sequence[Any],
    as_of: datetime,
    season: int,
    week: int,
    output_root: Path | str,
    force: bool = False,
    feature_names: Sequence[str] | None = None,
) -> list[MaterializeResult]:
    """Materialize features in dependency order for one ``(season, week)``."""
    order = resolve_build_order(registry)
    if feature_names is not None:
        wanted = set(feature_names)
        order = [name for name in order if name in wanted]
        missing = wanted - set(order)
        if missing:
            msg = f"unknown features: {sorted(missing)}"
            raise MaterializeError(msg)

    results: list[MaterializeResult] = []
    for name in order:
        spec = registry.get(name)
        builder = builder_factory(spec)
        results.append(
            materialize_partition(
                builder,
                entity_ids=entity_ids,
                as_of=as_of,
                season=season,
                week=week,
                output_root=output_root,
                force=force,
            )
        )
    return results


def read_partition(root: Path | str, ref: PartitionRef) -> pd.DataFrame:
    """Load a materialized partition DataFrame."""
    path = partition_parquet_path(root, ref)
    if not path.is_file():
        msg = f"feature partition not found: {path}"
        raise MaterializeError(msg)
    return pd.read_parquet(path)


def duckdb_asof_join(
    left: pd.DataFrame,
    right: pd.DataFrame,
    *,
    on: str | Sequence[str],
    ts_col: str,
    as_of: datetime | str,
) -> pd.DataFrame:
    """DuckDB ASOF join with strict ``right.ts < as_of`` (DESIGN §4.7).

    Mirrors :func:`ncaa_quant.data.asof.as_of_join` semantics for querying
    materialized feature / history frames via DuckDB. Prefer the pandas helper
    inside builders; use this for store-level SQL paths.
    """
    keys = [on] if isinstance(on, str) else list(on)
    left_work = left.copy()
    left_work["_asof_row_id"] = range(len(left_work))
    if isinstance(as_of, str):
        if as_of not in left_work.columns:
            msg = f"as_of column {as_of!r} missing from left frame"
            raise MaterializeError(msg)
        left_work["_as_of_bound"] = pd.to_datetime(left_work[as_of], utc=True)
    else:
        assert_tz_aware(as_of)
        left_work["_as_of_bound"] = pd.Timestamp(to_utc(as_of))

    right_work = right.copy()
    right_work[ts_col] = pd.to_datetime(right_work[ts_col], utc=True)

    con = duckdb.connect(database=":memory:")
    try:
        con.register("left_df", left_work)
        con.register("right_df", right_work)
        on_sql = " AND ".join(f'l."{k}" = r."{k}"' for k in keys)
        # Project left columns + non-key right columns (suffix collisions avoided
        # by selecting explicitly).
        right_extras = [c for c in right_work.columns if c not in keys]
        right_select = ", ".join(f'r."{c}" AS "{c}"' for c in right_extras)
        if right_select:
            right_select = ", " + right_select
        sql = f"""
            SELECT l.* EXCLUDE (_as_of_bound)
                   {right_select}
            FROM left_df l
            ASOF LEFT JOIN right_df r
              ON {on_sql}
             AND r."{ts_col}" < l._as_of_bound
            ORDER BY l._asof_row_id
        """
        out = con.execute(sql).df()
    finally:
        con.close()

    return out.drop(columns=["_asof_row_id"], errors="ignore").reset_index(drop=True)


def dvc_add_argv(path: Path | str) -> list[str]:
    """Return ``dvc add`` argv for a feature partition path (caller executes)."""
    return ["dvc", "add", str(Path(path))]


def dvc_add_partition(
    path: Path | str,
    *,
    run: bool = False,
) -> list[str]:
    """Build (and optionally run) ``dvc add`` for a feature output path.

    DVC is an operator tool, not a Python dependency — when ``run=True`` this
    shells out to ``dvc`` on ``PATH``. Default is argv-only so tests and CI
    without DVC stay green. Workflow: ``docs/feature_store.md``.
    """
    argv = dvc_add_argv(path)
    if run:
        subprocess.run(argv, check=True)
    return argv


def _annotate_partition(
    frame: pd.DataFrame,
    *,
    season: int,
    week: int,
    feature: str,
) -> pd.DataFrame:
    out = frame.copy()
    out["season"] = int(season)
    out["week"] = int(week)
    out["feature"] = feature
    # Stable column order for deterministic parquet bytes.
    preferred = [ENTITY_COL, AS_OF_COL, VALUE_COL, "is_missing", "season", "week", "feature"]
    ordered = [c for c in preferred if c in out.columns]
    ordered.extend(sorted(c for c in out.columns if c not in ordered))
    return out.reindex(columns=ordered)


def _is_unchanged(part_path: Path, meta_path: Path, *, spec_hash: str) -> bool:
    if not part_path.is_file() or not meta_path.is_file():
        return False
    try:
        meta = _read_meta(meta_path)
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return False
    if meta.get("spec_hash") != spec_hash:
        return False
    return meta.get("content_hash") == _file_sha256(part_path)


def _read_meta(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        msg = f"meta.json must be an object: {path}"
        raise MaterializeError(msg)
    return {str(k): v for k, v in payload.items()}


def _write_partition_atomic(frame: pd.DataFrame, target: Path) -> str:
    """Write parquet atomically; return content sha256."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f".{_PART_FILENAME}.tmp.{uuid.uuid4().hex}"
    try:
        table = pa.Table.from_pandas(_normalize_frame(frame), preserve_index=False)
        pq.write_table(table, tmp, compression="snappy")
        digest = _file_sha256(tmp)
        if target.exists() and _file_sha256(target) == digest:
            tmp.unlink(missing_ok=True)
            return digest
        os.replace(tmp, target)
        return digest
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    tmp = path.parent / f".{_META_FILENAME}.tmp.{uuid.uuid4().hex}"
    try:
        tmp.write_text(json.dumps(dict(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _normalize_frame(df: pd.DataFrame) -> pd.DataFrame:
    ordered = df.reindex(columns=sorted(df.columns))
    if ordered.empty:
        return ordered.reset_index(drop=True)
    return ordered.sort_values(list(ordered.columns), kind="mergesort").reset_index(drop=True)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_HASH_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()
