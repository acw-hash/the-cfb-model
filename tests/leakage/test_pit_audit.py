"""Leakage suite: feature-store PIT audit (DESIGN §4.7 / §14).

Planted-leak test: a builder that reads future rows fails the audit; the same
feature computed with the as-of guard passes.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import pytest

from ncaa_quant.features.builder import AS_OF_COL, ENTITY_COL, VALUE_COL, FeatureBuilder
from ncaa_quant.features.materialize import materialize_partition, read_partition
from ncaa_quant.features.pit_audit import PitAuditError, assert_partition_pit_clean, audit_partition
from ncaa_quant.features.registry import FeatureSpec


def _spec() -> FeatureSpec:
    return FeatureSpec(
        name="synthetic_mean",
        version="1",
        dtype="float64",
        builder="tests.helpers:SyntheticBuilder",
        dependencies=("raw:plays",),
        as_of_semantics="strict_lt_event_time",
        null_policy="allow",
        lookback_window="all_history",
        hypothesis="Mean of prior observations predicts next outcome.",
    )


def _history() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"entity_id": "A", "event_time": pd.Timestamp("2023-09-01T12:00:00Z"), "obs": 10.0},
            {"entity_id": "A", "event_time": pd.Timestamp("2023-09-08T12:00:00Z"), "obs": 12.0},
            {"entity_id": "A", "event_time": pd.Timestamp("2023-09-15T12:00:00Z"), "obs": 100.0},
            {"entity_id": "B", "event_time": pd.Timestamp("2023-09-02T12:00:00Z"), "obs": 20.0},
            {"entity_id": "B", "event_time": pd.Timestamp("2023-09-16T12:00:00Z"), "obs": 200.0},
        ]
    )


class HonestMeanBuilder(FeatureBuilder):
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
        return pd.DataFrame(rows)


class LeakyMeanBuilder(FeatureBuilder):
    def __init__(self, spec: FeatureSpec, history: pd.DataFrame) -> None:
        super().__init__(spec)
        self.history = history

    def compute(self, entity_ids: Sequence[Any], as_of: datetime) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        for entity_id in entity_ids:
            subset = self.history.loc[self.history[ENTITY_COL] == entity_id]
            value = float(subset["obs"].mean()) if not subset.empty else float("nan")
            rows.append({ENTITY_COL: entity_id, AS_OF_COL: as_of, VALUE_COL: value})
        return pd.DataFrame(rows)


def test_pit_audit_catches_planted_leak(tmp_path: Any) -> None:
    history = _history()
    as_of = datetime(2023, 9, 10, 12, 0, 0, tzinfo=UTC)
    leaky = LeakyMeanBuilder(_spec(), history)
    result = materialize_partition(
        leaky,
        entity_ids=["A", "B"],
        as_of=as_of,
        season=2023,
        week=2,
        output_root=tmp_path / "leaky",
    )
    stored = read_partition(tmp_path / "leaky", result.partition)

    audit = audit_partition(stored, leaky, history, sample_size=10, seed=0)
    assert audit.passed is False
    assert audit.mismatches

    with pytest.raises(PitAuditError, match="PIT audit failed"):
        assert_partition_pit_clean(stored, leaky, history, sample_size=10, seed=0)


def test_pit_audit_passes_with_asof_guard(tmp_path: Any) -> None:
    history = _history()
    as_of = datetime(2023, 9, 10, 12, 0, 0, tzinfo=UTC)
    honest = HonestMeanBuilder(_spec(), history)
    result = materialize_partition(
        honest,
        entity_ids=["A", "B"],
        as_of=as_of,
        season=2023,
        week=2,
        output_root=tmp_path / "honest",
    )
    stored = read_partition(tmp_path / "honest", result.partition)

    a_val = float(stored.loc[stored[ENTITY_COL] == "A", VALUE_COL].iloc[0])
    assert a_val == pytest.approx(11.0)

    audit = assert_partition_pit_clean(stored, honest, history, sample_size=10, seed=0)
    assert audit.passed is True
    assert audit.n_checked == 2
