"""FeatureBuilder abstract base class (DESIGN §4.1 / §15 item 9).

Signature: ``build(entity_ids, as_of: datetime) -> DataFrame``.

Cardinal rules enforced here:
- ``as_of`` must be timezone-aware (NAIVE-DATETIME-FORBIDDEN).
- All historical joins go through :mod:`ncaa_quant.data.asof` — no direct
  storage reads from builder code paths provided by this base class.
- Output is validated against the registered dtype and null policy.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import pandas as pd  # type: ignore[import-untyped]

from ncaa_quant.data.asof import as_of_join
from ncaa_quant.features.registry import FeatureSpec, NullPolicy
from ncaa_quant.utils.timeutils import assert_tz_aware, to_utc

ENTITY_COL = "entity_id"
AS_OF_COL = "as_of"
VALUE_COL = "value"


class FeatureBuildError(ValueError):
    """Raised when a builder output violates its registry contract."""


class FeatureBuilder(ABC):
    """Abstract feature builder.

    Subclasses implement :meth:`compute` only. Callers invoke :meth:`build`,
    which asserts ``as_of`` awareness, delegates to ``compute``, then validates
    dtype / null policy.

    Data access
    -----------
    Use :meth:`as_of_join` or :meth:`filter_event_time` for historical facts.
    Do **not** call ``ParquetStore.read`` / DuckDB directly from builders —
    materializers inject frames; builders join them as-of.
    """

    def __init__(self, spec: FeatureSpec) -> None:
        self.spec = spec

    def build(self, entity_ids: Sequence[Any], as_of: datetime) -> pd.DataFrame:
        """Compute feature rows for ``entity_ids`` as of ``as_of`` (UTC).

        Returns
        -------
        pd.DataFrame
            Columns: ``entity_id``, ``as_of``, ``value`` (and optionally
            ``is_missing`` when ``null_policy == "indicator"``). Units and
            semantics are feature-specific; ``as_of`` is timezone-aware UTC.
        """
        assert_tz_aware(as_of)
        as_of_utc = to_utc(as_of)
        frame = self.compute(list(entity_ids), as_of_utc)
        self.validate_output(frame)
        return frame

    @abstractmethod
    def compute(self, entity_ids: Sequence[Any], as_of: datetime) -> pd.DataFrame:
        """Feature logic. ``as_of`` is already tz-aware UTC when called from build."""

    def as_of_join(
        self,
        left: pd.DataFrame,
        right: pd.DataFrame,
        on: str | Sequence[str],
        ts_col: str,
        as_of: datetime | str,
    ) -> pd.DataFrame:
        """Sole sanctioned entity join against historical facts (strict ``<``)."""
        return as_of_join(left, right, on=on, ts_col=ts_col, as_of=as_of)

    def filter_event_time(
        self,
        frame: pd.DataFrame,
        as_of: datetime,
        *,
        ts_col: str = "event_time",
    ) -> pd.DataFrame:
        """Return rows with ``ts_col < as_of`` (strict; same bound as as_of_join).

        Prefer :meth:`as_of_join` for entity-keyed history. This helper is for
        whole-frame eligibility cuts (e.g. pit_audit source restriction) that
        share the exclusive ``<`` contract of :mod:`ncaa_quant.data.asof`.
        """
        assert_tz_aware(as_of)
        if frame.empty:
            return frame.copy()
        if ts_col not in frame.columns:
            msg = f"ts_col {ts_col!r} missing from frame"
            raise FeatureBuildError(msg)
        bound = pd.Timestamp(to_utc(as_of))
        ts = pd.to_datetime(frame[ts_col], utc=True)
        return frame.loc[ts < bound].copy().reset_index(drop=True)

    def validate_output(self, frame: pd.DataFrame) -> None:
        """Validate ``frame`` against registered dtype and null policy."""
        required = {ENTITY_COL, AS_OF_COL, VALUE_COL}
        missing = required - set(frame.columns)
        if missing:
            msg = f"{self.spec.name}: output missing columns {sorted(missing)}"
            raise FeatureBuildError(msg)

        if frame.empty:
            return

        self._validate_as_of_column(frame[AS_OF_COL])
        self._validate_dtype(frame[VALUE_COL])
        self._validate_null_policy(frame)

    def _validate_as_of_column(self, series: pd.Series) -> None:
        sample = series.dropna()
        if sample.empty:
            return
        for value in sample:
            ts = value if isinstance(value, datetime) else pd.Timestamp(value).to_pydatetime()
            assert_tz_aware(ts)

    def _validate_dtype(self, series: pd.Series) -> None:
        expected = self.spec.dtype.lower()
        actual = str(series.dtype).lower()
        if expected in actual:
            return
        # Accept common aliases (e.g. float64 vs Float64, int64 vs Int64).
        aliases = {
            "float": ("float", "float64", "float32", "float16"),
            "float64": ("float64", "float"),
            "int": ("int", "int64", "int32", "int16", "int8"),
            "int64": ("int64", "int"),
            "bool": ("bool", "boolean"),
            "string": ("string", "object", "str"),
            "object": ("object", "string"),
        }
        allowed = aliases.get(expected, (expected,))
        if any(a in actual for a in allowed):
            return
        # object columns holding Python floats are ok for float64 when non-null
        # values are numeric — materializers may not cast yet.
        if expected.startswith("float") and series.dropna().map(_is_number).all():
            return
        if expected.startswith("int") and series.dropna().map(_is_int_like).all():
            return
        msg = (
            f"{self.spec.name}: value dtype {actual!r} does not match "
            f"registered dtype {self.spec.dtype!r}"
        )
        raise FeatureBuildError(msg)

    def _validate_null_policy(self, frame: pd.DataFrame) -> None:
        policy: NullPolicy = self.spec.null_policy
        nulls = int(frame[VALUE_COL].isna().sum())
        if policy == "forbid" and nulls > 0:
            msg = f"{self.spec.name}: null_policy=forbid but {nulls} null value(s)"
            raise FeatureBuildError(msg)
        if policy == "indicator":
            if "is_missing" not in frame.columns:
                msg = f"{self.spec.name}: null_policy=indicator requires is_missing column"
                raise FeatureBuildError(msg)
            # is_missing must be True exactly where value is null.
            mismatch = (frame[VALUE_COL].isna() != frame["is_missing"].astype(bool)).sum()
            if mismatch:
                msg = (
                    f"{self.spec.name}: is_missing must match value.isna() "
                    f"({int(mismatch)} mismatched row(s))"
                )
                raise FeatureBuildError(msg)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_int_like(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
