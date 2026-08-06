"""Point-in-time as-of joins.

**Direct ``pandas.DataFrame.merge`` (or equivalent) on entity ids alone is
forbidden outside this module.** Every entity join against historical facts
must go through :func:`as_of_join` so that only rows with
``right[ts_col] < as_of`` are visible — the cardinal leakage guard of the
system (DESIGN §4.1 / §4.7).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import pandas as pd  # type: ignore[import-untyped]

from ncaa_quant.utils.timeutils import NaiveDatetimeError, assert_tz_aware, to_utc


class AsOfJoinError(ValueError):
    """Raised when as-of join inputs are missing or temporally invalid."""


def as_of_join(
    left: pd.DataFrame,
    right: pd.DataFrame,
    on: str | Sequence[str],
    ts_col: str,
    as_of: datetime | str,
) -> pd.DataFrame:
    """For each ``left`` row, attach the latest eligible ``right`` row.

    Eligibility: matching ``on`` keys and ``right[ts_col] < as_of`` (strict).
    Rows with ``right[ts_col] == as_of`` are **excluded** — a leakage guard.

    Parameters
    ----------
    left:
        Driving frame (e.g. games to score).
    right:
        Historical facts carrying ``ts_col`` (typically ``event_time``).
    on:
        Entity key column name(s) shared by both frames.
    ts_col:
        Timestamp column on ``right`` (and the exclusive bound source when
        ``as_of`` is a column name on ``left``).
    as_of:
        Either a timezone-aware :class:`~datetime.datetime` applied to every
        left row, or the name of a timezone-aware column on ``left`` holding
        per-row exclusive upper bounds.

    Returns
    -------
    pd.DataFrame
        ``left`` columns plus non-key columns from the matched ``right`` row
        (suffix ``_right`` on collisions other than ``on`` / ``ts_col``).
        Unmatched left rows are retained with null right columns.
    """
    if ts_col not in right.columns:
        msg = f"ts_col {ts_col!r} missing from right frame"
        raise AsOfJoinError(msg)

    keys = _normalize_join_keys(on)
    for key in keys:
        if key not in left.columns:
            msg = f"join key {key!r} missing from left frame"
            raise AsOfJoinError(msg)
        if key not in right.columns:
            msg = f"join key {key!r} missing from right frame"
            raise AsOfJoinError(msg)

    _assert_series_tz_aware(right[ts_col], where=f"right[{ts_col!r}]")

    left_work = left.copy()
    left_work["_asof_row_id"] = range(len(left_work))
    bound_col = "_as_of_bound"
    if isinstance(as_of, str):
        if as_of not in left_work.columns:
            msg = f"as_of column {as_of!r} missing from left frame"
            raise AsOfJoinError(msg)
        _assert_series_tz_aware(left_work[as_of], where=f"left[{as_of!r}]")
        left_work[bound_col] = _to_utc_series(left_work[as_of])
    else:
        assert_tz_aware(as_of)
        left_work[bound_col] = pd.Timestamp(to_utc(as_of))

    right_work = right.copy()
    right_work[ts_col] = _to_utc_series(right_work[ts_col])
    # merge_asof requires identical datetime units on both sides.
    left_work[bound_col] = _to_utc_series(left_work[bound_col])

    # Stable ordering for merge_asof; allow_exact_matches=False ⇒ strict '<'.
    by_key: str | list[str] = keys if len(keys) > 1 else keys[0]
    sort_left = keys + [bound_col]
    sort_right = keys + [ts_col]
    left_sorted = left_work.sort_values(sort_left, kind="mergesort")
    right_sorted = right_work.sort_values(sort_right, kind="mergesort")

    merged = pd.merge_asof(
        left_sorted,
        right_sorted,
        left_on=bound_col,
        right_on=ts_col,
        by=by_key,
        direction="backward",
        allow_exact_matches=False,
        suffixes=("", "_right"),
    )

    merged = merged.sort_values("_asof_row_id", kind="mergesort")
    return merged.drop(columns=[bound_col, "_asof_row_id"]).reset_index(drop=True)


def _normalize_join_keys(on: str | Sequence[str]) -> list[str]:
    if isinstance(on, str):
        return [on]
    return [str(key) for key in on]


def _to_utc_series(series: pd.Series) -> pd.Series:
    """Normalize a datetime series to UTC-aware ``datetime64[ns, UTC]``."""
    converted = pd.to_datetime(series, utc=True)
    # Force nanosecond unit so scalar bounds match column dtypes in merge_asof.
    return converted.astype("datetime64[ns, UTC]")


def _assert_series_tz_aware(series: pd.Series, *, where: str) -> None:
    """Raise if ``series`` is missing, empty-nonissue, or timezone-naive."""
    if series.empty:
        return
    dtype = series.dtype
    if not isinstance(dtype, pd.DatetimeTZDtype):
        # object/datetime64[ns] without tz — inspect values.
        sample = series.dropna()
        if sample.empty:
            return
        first = sample.iloc[0]
        if isinstance(first, pd.Timestamp):
            if first.tzinfo is None:
                msg = f"NAIVE-DATETIME-FORBIDDEN: {where} is tz-naive"
                raise NaiveDatetimeError(msg)
            # Mixed object timestamps — verify all aware.
            for value in sample:
                ts = value if isinstance(value, pd.Timestamp) else pd.Timestamp(value)
                if ts.tzinfo is None:
                    msg = f"NAIVE-DATETIME-FORBIDDEN: {where} contains tz-naive values"
                    raise NaiveDatetimeError(msg)
            return
        if isinstance(first, datetime):
            assert_tz_aware(first)
            for value in sample:
                assert_tz_aware(value)
            return
        msg = f"{where} must be timezone-aware datetimes, got dtype {dtype}"
        raise AsOfJoinError(msg)
