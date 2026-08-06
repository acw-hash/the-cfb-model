"""Parquet-on-disk store with DuckDB query layer.

Game-grained tables are partitioned by ``(season, week)``; reference tables by
``(season)``. Writes are atomic (temp file + ``os.replace``) and idempotent:
rewriting a partition with byte-identical normalized data is a no-op that
leaves the on-disk bytes unchanged.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

import duckdb
import pandas as pd  # type: ignore[import-untyped]
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from ncaa_quant.data.schemas import (
    GAME_GRAINED_TABLES,
    REFERENCE_TABLES,
    SCHEMA_REGISTRY,
    validate_table,
)

WriteMode = Literal["overwrite", "append"]

_PART_FILENAME = "part.parquet"
_HASH_CHUNK = 1 << 20


class PartitionError(ValueError):
    """Invalid partition keys for a table."""


class ParquetStore:
    """Hive-style Parquet dataset under ``root`` with DuckDB SQL access."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(database=":memory:")

    def close(self) -> None:
        """Close the DuckDB connection."""
        self._con.close()

    def __enter__(self) -> ParquetStore:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def write_partition(
        self,
        table: str,
        df: pd.DataFrame,
        partition: Mapping[str, int],
        mode: WriteMode = "overwrite",
        *,
        validate: bool = True,
    ) -> Path:
        """Write ``df`` to ``table`` under ``partition``.

        Parameters
        ----------
        table:
            Registered table name (see :data:`SCHEMA_REGISTRY`).
        df:
            Rows for this partition only (partition columns may be present).
        partition:
            ``{"season": Y, "week": W}`` for game-grained tables, or
            ``{"season": Y}`` for reference tables.
        mode:
            ``overwrite`` replaces the partition file; ``append`` concatenates
            with any existing partition rows then overwrites atomically.
        validate:
            When True (default), run the pandera schema before writing.
        """
        self._assert_known_table(table)
        self._validate_partition(table, partition)

        frame = df.copy()
        if validate:
            frame = validate_table(table, frame)

        if mode == "append":
            existing = self._read_partition_file(table, partition)
            if existing is not None and not existing.empty:
                frame = pd.concat([existing, frame], ignore_index=True)

        frame = _normalize_for_write(frame)
        target = self._partition_path(table, partition)
        target.parent.mkdir(parents=True, exist_ok=True)

        tmp = target.parent / f".{_PART_FILENAME}.tmp.{uuid.uuid4().hex}"
        try:
            _write_parquet_deterministic(frame, tmp)
            if target.exists() and _file_sha256(tmp) == _file_sha256(target):
                tmp.unlink(missing_ok=True)
                return target
            os.replace(tmp, target)
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
        return target

    def read(
        self,
        table: str,
        filters: Mapping[str, Any] | None = None,
    ) -> pd.DataFrame:
        """Read ``table`` rows, optionally filtered by partition/column equality.

        ``filters`` values may be scalars or sequences (IN-list). Partition
        keys (``season``, ``week``) prune directories; other keys filter after
        read.
        """
        self._assert_known_table(table)
        filters = dict(filters or {})
        paths = self._matching_paths(table, filters)
        if not paths:
            return pd.DataFrame()

        frames = [pd.read_parquet(path) for path in paths]
        out = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]

        for key, value in filters.items():
            if key in {"season", "week"}:
                continue
            if key not in out.columns:
                msg = f"filter column {key!r} not in table {table!r}"
                raise KeyError(msg)
            if isinstance(value, (list, tuple, set, frozenset)):
                out = out[out[key].isin(list(value))]
            else:
                out = out[out[key] == value]
        return out.reset_index(drop=True)

    def query(self, sql: str) -> pd.DataFrame:
        """Run DuckDB SQL against registered Parquet table views."""
        self._register_views()
        return self._con.execute(sql).df()

    def _assert_known_table(self, table: str) -> None:
        if table not in SCHEMA_REGISTRY:
            known = ", ".join(sorted(SCHEMA_REGISTRY))
            msg = f"unknown table {table!r}; known: {known}"
            raise KeyError(msg)

    def _validate_partition(self, table: str, partition: Mapping[str, int]) -> None:
        if "season" not in partition:
            raise PartitionError("partition requires 'season'")
        if table in GAME_GRAINED_TABLES:
            if "week" not in partition:
                raise PartitionError(f"game-grained table {table!r} requires partition key 'week'")
            unexpected = set(partition) - {"season", "week"}
        elif table in REFERENCE_TABLES:
            if "week" in partition:
                raise PartitionError(f"reference table {table!r} is partitioned by season only")
            unexpected = set(partition) - {"season"}
        else:  # pragma: no cover — guarded by SCHEMA_REGISTRY
            unexpected = set(partition)
        if unexpected:
            msg = f"unexpected partition keys: {sorted(unexpected)}"
            raise PartitionError(msg)

    def _partition_dir(self, table: str, partition: Mapping[str, int]) -> Path:
        path = self.root / table / f"season={int(partition['season'])}"
        if table in GAME_GRAINED_TABLES:
            path = path / f"week={int(partition['week'])}"
        return path

    def _partition_path(self, table: str, partition: Mapping[str, int]) -> Path:
        return self._partition_dir(table, partition) / _PART_FILENAME

    def _read_partition_file(self, table: str, partition: Mapping[str, int]) -> pd.DataFrame | None:
        path = self._partition_path(table, partition)
        if not path.exists():
            return None
        return pd.read_parquet(path)

    def _matching_paths(self, table: str, filters: Mapping[str, Any]) -> list[Path]:
        table_root = self.root / table
        if not table_root.exists():
            return []

        season_filter = filters.get("season")
        week_filter = filters.get("week")
        seasons = _as_value_set(season_filter) if season_filter is not None else None
        weeks = _as_value_set(week_filter) if week_filter is not None else None

        paths: list[Path] = []
        for season_dir in sorted(table_root.glob("season=*")):
            season_val = _parse_hive_value(season_dir.name, "season")
            if seasons is not None and season_val not in seasons:
                continue
            if table in REFERENCE_TABLES:
                part = season_dir / _PART_FILENAME
                if part.exists():
                    paths.append(part)
                continue
            for week_dir in sorted(season_dir.glob("week=*")):
                week_val = _parse_hive_value(week_dir.name, "week")
                if weeks is not None and week_val not in weeks:
                    continue
                part = week_dir / _PART_FILENAME
                if part.exists():
                    paths.append(part)
        return paths

    def _register_views(self) -> None:
        for table in SCHEMA_REGISTRY:
            table_root = self.root / table
            pattern = str(table_root / "**" / _PART_FILENAME).replace("\\", "/")
            if not any(table_root.rglob(_PART_FILENAME)):
                # Empty relation with no files — skip; SQL on missing tables errors.
                continue
            self._con.execute(
                f"""
                CREATE OR REPLACE VIEW "{table}" AS
                SELECT * FROM read_parquet('{pattern}', hive_partitioning := true)
                """
            )


def _normalize_for_write(df: pd.DataFrame) -> pd.DataFrame:
    """Stable column order + row order for byte-identical rewrites."""
    ordered = df.reindex(columns=sorted(df.columns))
    if ordered.empty:
        return ordered.reset_index(drop=True)
    return ordered.sort_values(list(ordered.columns), kind="mergesort").reset_index(drop=True)


def _write_parquet_deterministic(df: pd.DataFrame, path: Path) -> None:
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, path, compression="snappy")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_HASH_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _parse_hive_value(dirname: str, key: str) -> int:
    prefix = f"{key}="
    if not dirname.startswith(prefix):
        msg = f"expected hive directory {prefix}*, got {dirname!r}"
        raise ValueError(msg)
    return int(dirname[len(prefix) :])


def _as_value_set(value: Any) -> set[Any]:
    if isinstance(value, (list, tuple, set, frozenset, Sequence)) and not isinstance(
        value, (str, bytes)
    ):
        return set(value)
    return {value}
