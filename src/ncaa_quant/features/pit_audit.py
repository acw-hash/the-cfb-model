"""Feature-store point-in-time audit (DESIGN §4.7 / §14 / §15 item 9).

Given a materialized feature partition, re-derive a random sample of rows using
ONLY history with ``event_time <`` the row's ``as_of``, and assert equality with
the stored value. This is the repository's primary leakage harness.

Note: :mod:`ncaa_quant.quality.pit_audit` covers ingestion temporal contracts
(Task 7). This module audits *feature recomputation* against the as-of bound.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, cast

import pandas as pd  # type: ignore[import-untyped]

from ncaa_quant.features.builder import (
    AS_OF_COL,
    ENTITY_COL,
    VALUE_COL,
    FeatureBuilder,
)
from ncaa_quant.utils.seeding import set_global_seed
from ncaa_quant.utils.timeutils import assert_tz_aware, to_utc


class PitAuditError(AssertionError):
    """Raised when stored feature values disagree with as-of recomputation."""


@dataclass
class PitAuditMismatch:
    """One sampled row that failed the PIT equality check."""

    entity_id: Any
    as_of: datetime
    stored_value: Any
    recomputed_value: Any
    message: str


@dataclass
class PitAuditResult:
    """Summary of a partition PIT audit."""

    n_sampled: int
    n_checked: int
    mismatches: list[PitAuditMismatch] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.mismatches


def audit_partition(
    stored: pd.DataFrame,
    builder: FeatureBuilder,
    history: pd.DataFrame,
    *,
    sample_size: int = 20,
    seed: int = 0,
    entity_col: str = ENTITY_COL,
    as_of_col: str = AS_OF_COL,
    value_col: str = VALUE_COL,
    history_ts_col: str = "event_time",
    rtol: float = 1e-9,
    atol: float = 1e-9,
) -> PitAuditResult:
    """Recompute a random sample of ``stored`` rows under a strict as-of cut.

    For each sampled row the audit:
    1. Restricts ``history`` to ``event_time < as_of``
    2. Injects that frame into ``builder.history`` (required attribute)
    3. Calls ``builder.build([entity_id], as_of)``
    4. Asserts the recomputed ``value`` equals the stored value

    A builder that closed over future rows at materialization time will disagree
    with the as-of-restricted recompute and fail the audit.
    """
    if stored.empty:
        return PitAuditResult(n_sampled=0, n_checked=0)

    for col in (entity_col, as_of_col, value_col):
        if col not in stored.columns:
            msg = f"stored partition missing column {col!r}"
            raise PitAuditError(msg)
    if history_ts_col not in history.columns:
        msg = f"history missing timestamp column {history_ts_col!r}"
        raise PitAuditError(msg)
    if not hasattr(builder, "history"):
        msg = (
            "pit_audit requires builder.history to be injectable so the harness "
            "can restrict event_time < as_of during recompute"
        )
        raise PitAuditError(msg)

    set_global_seed(seed)
    n = min(int(sample_size), len(stored))
    sample = stored.sample(n=n, random_state=seed).reset_index(drop=True)

    mismatches: list[PitAuditMismatch] = []
    checked = 0
    history_holder = cast(Any, builder)
    original_history = history_holder.history
    try:
        for _, row in sample.iterrows():
            as_of_utc = _row_as_of(row[as_of_col])
            entity_id = row[entity_col]
            stored_value = row[value_col]

            eligible = builder.filter_event_time(history, as_of_utc, ts_col=history_ts_col)
            history_holder.history = eligible
            out = builder.build([entity_id], as_of_utc)
            recomputed = _value_for_entity(out, entity_id)
            checked += 1
            if not _values_equal(stored_value, recomputed, rtol=rtol, atol=atol):
                mismatches.append(
                    PitAuditMismatch(
                        entity_id=entity_id,
                        as_of=as_of_utc,
                        stored_value=stored_value,
                        recomputed_value=recomputed,
                        message=(
                            f"entity={entity_id!r} as_of={as_of_utc.isoformat()}: "
                            f"stored={stored_value!r} recomputed={recomputed!r}"
                        ),
                    )
                )
    finally:
        history_holder.history = original_history

    return PitAuditResult(n_sampled=n, n_checked=checked, mismatches=mismatches)


def assert_partition_pit_clean(
    stored: pd.DataFrame,
    builder: FeatureBuilder,
    history: pd.DataFrame,
    **kwargs: Any,
) -> PitAuditResult:
    """Run :func:`audit_partition` and raise :class:`PitAuditError` on mismatch."""
    result = audit_partition(stored, builder, history, **kwargs)
    if not result.passed:
        detail = "; ".join(m.message for m in result.mismatches[:5])
        msg = f"PIT audit failed: {len(result.mismatches)}/{result.n_checked} mismatches. {detail}"
        raise PitAuditError(msg)
    return result


def _row_as_of(value: Any) -> datetime:
    if isinstance(value, datetime):
        assert_tz_aware(value)
        return to_utc(value)
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        msg = f"NAIVE-DATETIME-FORBIDDEN: stored as_of is tz-naive: {value!r}"
        raise PitAuditError(msg)
    return to_utc(ts.to_pydatetime())


def _value_for_entity(frame: pd.DataFrame, entity_id: Any) -> Any:
    if frame.empty or ENTITY_COL not in frame.columns:
        return None
    match = frame.loc[frame[ENTITY_COL] == entity_id]
    if match.empty:
        return None
    return match.iloc[0][VALUE_COL]


def _values_equal(left: Any, right: Any, *, rtol: float, atol: float) -> bool:
    if left is None and right is None:
        return True
    left_na = left is None or (isinstance(left, float) and math.isnan(left)) or pd.isna(left)
    right_na = right is None or (isinstance(right, float) and math.isnan(right)) or pd.isna(right)
    if left_na and right_na:
        return True
    if left_na or right_na:
        return False
    if isinstance(left, (float, int)) and isinstance(right, (float, int)):
        return math.isclose(float(left), float(right), rel_tol=rtol, abs_tol=atol)
    return bool(left == right)
