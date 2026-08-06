"""Tests for UTC timeutils, season/week boundaries, and DST."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from ncaa_quant.utils.timeutils import (
    NaiveDatetimeError,
    as_of_bound,
    assert_tz_aware,
    season_of,
    to_utc,
    week_of,
)

EASTERN = ZoneInfo("America/New_York")


def test_naive_datetime_forbidden() -> None:
    naive = datetime(2024, 9, 1, 12, 0, 0)
    with pytest.raises(NaiveDatetimeError, match="NAIVE-DATETIME-FORBIDDEN"):
        assert_tz_aware(naive)
    with pytest.raises(NaiveDatetimeError):
        to_utc(naive)


def test_to_utc_and_as_of_bound() -> None:
    eastern = datetime(2024, 10, 5, 12, 0, tzinfo=EASTERN)
    utc = to_utc(eastern)
    assert utc.tzinfo == UTC
    assert utc.hour == 16  # EDT is UTC-4
    bound = as_of_bound(eastern)
    assert bound == utc


def test_january_bowl_maps_to_prior_season() -> None:
    """Acceptance: a January 2 bowl game belongs to the prior season."""
    kickoff = datetime(2025, 1, 2, 20, 0, tzinfo=UTC)
    assert season_of(kickoff) == 2024


def test_season_boundaries() -> None:
    assert season_of(datetime(2024, 8, 31, tzinfo=UTC)) == 2024
    assert season_of(datetime(2024, 12, 15, tzinfo=UTC)) == 2024
    assert season_of(datetime(2025, 1, 15, tzinfo=UTC)) == 2024
    assert season_of(datetime(2025, 3, 1, tzinfo=UTC)) == 2025
    assert season_of(datetime(2025, 9, 1, tzinfo=UTC)) == 2025


def test_week_of_week_zero_and_week_one() -> None:
    # Labor Day 2024 = Monday Sep 2 → Week 1.
    week1 = datetime(2024, 9, 2, 18, 0, tzinfo=UTC)
    assert week_of(week1, 2024) == 1
    # Late August before Labor Day → Week 0.
    week0 = datetime(2024, 8, 24, 18, 0, tzinfo=UTC)
    assert week_of(week0, 2024) == 0


def test_week_of_rejects_season_mismatch() -> None:
    ts = datetime(2025, 1, 2, tzinfo=UTC)
    with pytest.raises(ValueError, match="does not match"):
        week_of(ts, 2025)


def test_dst_spring_forward_conversion() -> None:
    # 2024-03-10 02:00 EST skipped; 12:00 EDT = 16:00 UTC.
    eastern = datetime(2024, 3, 10, 12, 0, tzinfo=EASTERN)
    utc = to_utc(eastern)
    assert utc == datetime(2024, 3, 10, 16, 0, tzinfo=UTC)


def test_dst_fall_back_conversion() -> None:
    # 2024-11-03 12:00 EST = 17:00 UTC.
    eastern = datetime(2024, 11, 3, 12, 0, tzinfo=EASTERN)
    utc = to_utc(eastern)
    assert utc == datetime(2024, 11, 3, 17, 0, tzinfo=UTC)
