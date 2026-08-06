"""Feature registry / builder / materialize / pit_audit tests (Task 9)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from ncaa_quant.features.builder import (
    AS_OF_COL,
    ENTITY_COL,
    VALUE_COL,
    FeatureBuilder,
    FeatureBuildError,
)
from ncaa_quant.features.materialize import (
    PartitionRef,
    duckdb_asof_join,
    dvc_add_argv,
    dvc_add_partition,
    materialize_partition,
    materialize_registry,
    read_partition,
)
from ncaa_quant.features.registry import (
    DependencyCycleError,
    FeatureSpec,
    RegistryError,
    load_registry,
    resolve_build_order,
)
from ncaa_quant.utils.timeutils import NaiveDatetimeError


def _spec(**overrides: Any) -> FeatureSpec:
    base: dict[str, Any] = {
        "name": "synthetic_mean",
        "version": "1",
        "dtype": "float64",
        "builder": "tests.helpers:SyntheticBuilder",
        "dependencies": ("raw:plays",),
        "as_of_semantics": "strict_lt_event_time",
        "null_policy": "allow",
        "lookback_window": "all_history",
        "hypothesis": "Mean of prior observations predicts next outcome.",
    }
    base.update(overrides)
    return FeatureSpec(
        name=str(base["name"]),
        version=str(base["version"]),
        dtype=str(base["dtype"]),
        builder=str(base["builder"]),
        dependencies=tuple(base["dependencies"]),
        as_of_semantics=str(base["as_of_semantics"]),
        null_policy=base["null_policy"],  # type: ignore[arg-type]
        lookback_window=str(base["lookback_window"]),
        hypothesis=str(base["hypothesis"]),
    )


def _history() -> pd.DataFrame:
    """Past + future observations for two entities (planted future rows)."""
    return pd.DataFrame(
        [
            {"entity_id": "A", "event_time": pd.Timestamp("2023-09-01T12:00:00Z"), "obs": 10.0},
            {"entity_id": "A", "event_time": pd.Timestamp("2023-09-08T12:00:00Z"), "obs": 12.0},
            # Future relative to as_of 2023-09-10 — planted leak bait.
            {"entity_id": "A", "event_time": pd.Timestamp("2023-09-15T12:00:00Z"), "obs": 100.0},
            {"entity_id": "B", "event_time": pd.Timestamp("2023-09-02T12:00:00Z"), "obs": 20.0},
            {"entity_id": "B", "event_time": pd.Timestamp("2023-09-16T12:00:00Z"), "obs": 200.0},
        ]
    )


class HonestMeanBuilder(FeatureBuilder):
    """Uses as_of filtering — ignores future rows when as_of precedes them."""

    def __init__(self, spec: FeatureSpec, history: pd.DataFrame) -> None:
        super().__init__(spec)
        self.history = history

    def compute(self, entity_ids: Sequence[Any], as_of: datetime) -> pd.DataFrame:
        eligible = self.filter_event_time(self.history, as_of)
        rows: list[dict[str, Any]] = []
        for entity_id in entity_ids:
            subset = eligible.loc[eligible[ENTITY_COL] == entity_id]
            value = float(subset["obs"].mean()) if not subset.empty else float("nan")
            rows.append({ENTITY_COL: entity_id, AS_OF_COL: as_of, VALUE_COL: value})
            # as_of_join per entity (multi-entity right frames hit a known
            # pandas merge_asof global-sort limitation in Task 3's helper —
            # flagged in docs/notes/09.md; do not patch asof.py here).
            entity_hist = self.history.loc[self.history[ENTITY_COL] == entity_id]
            if not entity_hist.empty:
                left = pd.DataFrame({ENTITY_COL: [entity_id]})
                _ = self.as_of_join(
                    left,
                    entity_hist,
                    on=ENTITY_COL,
                    ts_col="event_time",
                    as_of=as_of,
                )
        return pd.DataFrame(rows)


def _write_registry(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_load_default_registry_has_efficiency_features() -> None:
    """Task 10 registers efficiency features; registry is no longer empty."""
    registry = load_registry()
    names = registry.names()
    assert "adj_off_epa_std" in names
    assert "adj_def_epa_std" in names
    assert all(registry.get(n).hypothesis.strip() for n in names)


def test_registry_rejects_empty_hypothesis(tmp_path: Path) -> None:
    path = _write_registry(
        tmp_path / "registry.yaml",
        """
features:
  - name: bad
    version: "1"
    dtype: float64
    builder: mod:B
    dependencies: []
    as_of_semantics: strict_lt_event_time
    null_policy: allow
    lookback_window: season_to_date
    hypothesis: ""
""",
    )
    with pytest.raises(RegistryError, match="hypothesis"):
        load_registry(path)


def test_registry_rejects_missing_hypothesis_key(tmp_path: Path) -> None:
    path = _write_registry(
        tmp_path / "registry.yaml",
        """
features:
  - name: bad
    version: "1"
    dtype: float64
    builder: mod:B
    dependencies: []
    as_of_semantics: strict_lt_event_time
    null_policy: allow
    lookback_window: season_to_date
""",
    )
    with pytest.raises(RegistryError, match="hypothesis"):
        load_registry(path)


def test_dependency_cycle_raises(tmp_path: Path) -> None:
    path = _write_registry(
        tmp_path / "registry.yaml",
        """
features:
  - name: a
    version: "1"
    dtype: float64
    builder: mod:A
    dependencies: [b]
    as_of_semantics: strict_lt_event_time
    null_policy: allow
    lookback_window: season_to_date
    hypothesis: "A depends on B."
  - name: b
    version: "1"
    dtype: float64
    builder: mod:B
    dependencies: [a]
    as_of_semantics: strict_lt_event_time
    null_policy: allow
    lookback_window: season_to_date
    hypothesis: "B depends on A."
""",
    )
    registry = load_registry(path)
    with pytest.raises(DependencyCycleError, match="cycle"):
        resolve_build_order(registry)


def test_resolve_build_order_topo(tmp_path: Path) -> None:
    path = _write_registry(
        tmp_path / "registry.yaml",
        """
features:
  - name: child
    version: "1"
    dtype: float64
    builder: mod:C
    dependencies: [parent, raw:games]
    as_of_semantics: strict_lt_event_time
    null_policy: allow
    lookback_window: season_to_date
    hypothesis: "Child needs parent."
  - name: parent
    version: "1"
    dtype: float64
    builder: mod:P
    dependencies: [raw:plays]
    as_of_semantics: strict_lt_event_time
    null_policy: allow
    lookback_window: season_to_date
    hypothesis: "Parent is base signal."
""",
    )
    registry = load_registry(path)
    assert resolve_build_order(registry) == ["parent", "child"]


def test_builder_rejects_naive_as_of() -> None:
    builder = HonestMeanBuilder(_spec(), _history())
    with pytest.raises(NaiveDatetimeError):
        builder.build(["A"], datetime(2023, 9, 10, 12, 0, 0))


def test_builder_null_policy_forbid() -> None:
    spec = _spec(null_policy="forbid")
    builder = HonestMeanBuilder(spec, pd.DataFrame(columns=["entity_id", "event_time", "obs"]))
    with pytest.raises(FeatureBuildError, match="forbid"):
        builder.build(["Z"], datetime(2023, 9, 10, 12, 0, 0, tzinfo=UTC))


# ---------------------------------------------------------------------------
# Materialization
# ---------------------------------------------------------------------------


def test_incremental_materialization_skips_unchanged(tmp_path: Path) -> None:
    history = _history()
    as_of = datetime(2023, 9, 10, 12, 0, 0, tzinfo=UTC)
    calls = {"n": 0}

    class CountingBuilder(HonestMeanBuilder):
        def compute(self, entity_ids: Sequence[Any], as_of: datetime) -> pd.DataFrame:
            calls["n"] += 1
            return super().compute(entity_ids, as_of)

    builder = CountingBuilder(_spec(), history)
    r1 = materialize_partition(
        builder,
        entity_ids=["A", "B"],
        as_of=as_of,
        season=2023,
        week=2,
        output_root=tmp_path,
    )
    assert r1.skipped is False
    assert calls["n"] == 1

    r2 = materialize_partition(
        builder,
        entity_ids=["A", "B"],
        as_of=as_of,
        season=2023,
        week=2,
        output_root=tmp_path,
    )
    assert r2.skipped is True
    assert calls["n"] == 1
    assert r2.content_hash == r1.content_hash

    stored = read_partition(
        tmp_path,
        PartitionRef(feature="synthetic_mean", version="1", season=2023, week=2),
    )
    assert set(stored[ENTITY_COL]) == {"A", "B"}


def test_dvc_add_argv() -> None:
    argv = dvc_add_argv(Path("data/features/x"))
    assert argv[:2] == ["dvc", "add"]
    assert Path(argv[2]) == Path("data/features/x")
    assert dvc_add_partition(Path("data/features/x"), run=False) == argv


def test_duckdb_asof_excludes_equal_timestamp() -> None:
    left = pd.DataFrame(
        {
            "entity_id": ["A"],
            "as_of": [pd.Timestamp("2023-09-10T12:00:00Z")],
        }
    )
    right = pd.DataFrame(
        [
            {"entity_id": "A", "event_time": pd.Timestamp("2023-09-08T12:00:00Z"), "obs": 1.0},
            {"entity_id": "A", "event_time": pd.Timestamp("2023-09-10T12:00:00Z"), "obs": 99.0},
        ]
    )
    out = duckdb_asof_join(left, right, on="entity_id", ts_col="event_time", as_of="as_of")
    assert float(out.iloc[0]["obs"]) == 1.0


def test_duckdb_asof_scalar_bound() -> None:
    left = pd.DataFrame({"entity_id": ["A"]})
    right = pd.DataFrame(
        [
            {"entity_id": "A", "event_time": pd.Timestamp("2023-09-08T12:00:00Z"), "obs": 1.0},
            {"entity_id": "A", "event_time": pd.Timestamp("2023-09-12T12:00:00Z"), "obs": 99.0},
        ]
    )
    out = duckdb_asof_join(
        left,
        right,
        on="entity_id",
        ts_col="event_time",
        as_of=datetime(2023, 9, 10, 12, 0, 0, tzinfo=UTC),
    )
    assert float(out.iloc[0]["obs"]) == 1.0


def test_materialize_registry_dependency_order(tmp_path: Path) -> None:
    path = _write_registry(
        tmp_path / "registry.yaml",
        """
features:
  - name: parent
    version: "1"
    dtype: float64
    builder: mod:P
    dependencies: [raw:plays]
    as_of_semantics: strict_lt_event_time
    null_policy: allow
    lookback_window: season_to_date
    hypothesis: "Parent is base signal."
  - name: child
    version: "1"
    dtype: float64
    builder: mod:C
    dependencies: [parent]
    as_of_semantics: strict_lt_event_time
    null_policy: allow
    lookback_window: season_to_date
    hypothesis: "Child needs parent."
""",
    )
    registry = load_registry(path)
    history = _history()
    as_of = datetime(2023, 9, 10, 12, 0, 0, tzinfo=UTC)
    order: list[str] = []

    def factory(spec: FeatureSpec) -> FeatureBuilder:
        order.append(spec.name)
        return HonestMeanBuilder(
            FeatureSpec(
                name=spec.name,
                version=spec.version,
                dtype=spec.dtype,
                builder=spec.builder,
                dependencies=spec.dependencies,
                as_of_semantics=spec.as_of_semantics,
                null_policy=spec.null_policy,
                lookback_window=spec.lookback_window,
                hypothesis=spec.hypothesis,
            ),
            history,
        )

    results = materialize_registry(
        registry,
        builder_factory=factory,
        entity_ids=["A"],
        as_of=as_of,
        season=2023,
        week=1,
        output_root=tmp_path / "out",
    )
    assert order == ["parent", "child"]
    assert len(results) == 2
    assert all(not r.skipped for r in results)


def test_builder_indicator_null_policy() -> None:
    spec = _spec(null_policy="indicator")

    class IndicatorBuilder(FeatureBuilder):
        def compute(self, entity_ids: Sequence[Any], as_of: datetime) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {
                        ENTITY_COL: entity_ids[0],
                        AS_OF_COL: as_of,
                        VALUE_COL: float("nan"),
                        "is_missing": True,
                    }
                ]
            )

    out = IndicatorBuilder(spec).build(["Z"], datetime(2023, 9, 10, 12, 0, 0, tzinfo=UTC))
    assert bool(out.iloc[0]["is_missing"]) is True


def test_force_rematerialize(tmp_path: Path) -> None:
    history = _history()
    as_of = datetime(2023, 9, 10, 12, 0, 0, tzinfo=UTC)
    calls = {"n": 0}

    class CountingBuilder(HonestMeanBuilder):
        def compute(self, entity_ids: Sequence[Any], as_of: datetime) -> pd.DataFrame:
            calls["n"] += 1
            return super().compute(entity_ids, as_of)

    builder = CountingBuilder(_spec(), history)
    materialize_partition(
        builder,
        entity_ids=["A"],
        as_of=as_of,
        season=2023,
        week=2,
        output_root=tmp_path,
    )
    materialize_partition(
        builder,
        entity_ids=["A"],
        as_of=as_of,
        season=2023,
        week=2,
        output_root=tmp_path,
        force=True,
    )
    assert calls["n"] == 2


# PIT audit planted-leak cases live in tests/leakage/test_pit_audit.py (cardinal
# leakage suite). Registry/materialize coverage above stays in unit/.
