"""UTC time helpers and CFB season/week conventions.

**NAIVE-DATETIME-FORBIDDEN:** every public function in this module rejects
timezone-naive ``datetime`` values. Callers must attach an explicit ``tzinfo``
(preferably UTC) at the system boundary via :func:`to_utc` or
:func:`assert_tz_aware`. Point-in-time joins use :func:`as_of_bound` so
``event_time < as_of`` comparisons are always UTC-aware.

**Decision points (AUDIT-6 / Task 5B contract):** named production decision
points are defined as wall-clock times in ``America/New_York`` and resolved to
an aware UTC instant per local calendar date via :mod:`zoneinfo`. Use
:func:`resolve_decision_point`. Kickoff-relative points such as ``slot_close``
(slot minus 5 minutes) are *not* wall-clock-on-a-date and are resolved by the
odds ingester, not here.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta
from typing import Final
from zoneinfo import ZoneInfo

# Contract shared with Task 5B historical backfill and DESIGN §9.8.
DECISION_POINT_TZ: Final[ZoneInfo] = ZoneInfo("America/New_York")

# Wall-clock (hour, minute) in America/New_York for named decision points.
# ``slot_close`` is intentionally absent — it requires a kickoff instant.
WALL_CLOCK_DECISION_POINTS: Final[Mapping[str, tuple[int, int]]] = {
    "tuesday_0600_et": (6, 0),
    "thursday_0600_et": (6, 0),
    "saturday_0600_et": (6, 0),
    "sunday_0600_et": (6, 0),
    "monday_0600_et": (6, 0),
    "saturday_2330_et": (23, 30),
}


class NaiveDatetimeError(ValueError):
    """Raised when a timezone-naive datetime reaches a timeutils boundary."""


class UnknownDecisionPointError(ValueError):
    """Raised when ``decision_point_name`` is not a registered wall-clock point."""


def assert_tz_aware(ts: datetime) -> datetime:
    """Guard used at API boundaries — naive datetimes are forbidden.

    NAIVE-DATETIME-FORBIDDEN: raise :class:`NaiveDatetimeError` if ``ts`` has
    no ``tzinfo``.
    """
    if ts.tzinfo is None or ts.tzinfo.utcoffset(ts) is None:
        msg = f"NAIVE-DATETIME-FORBIDDEN: expected timezone-aware datetime, got {ts!r}"
        raise NaiveDatetimeError(msg)
    return ts


def to_utc(ts: datetime) -> datetime:
    """Convert an aware datetime to UTC. Rejects naive inputs."""
    assert_tz_aware(ts)
    return ts.astimezone(UTC)


def as_of_bound(ts: datetime) -> datetime:
    """Normalize ``as_of`` to an exclusive UTC upper bound for PIT queries.

    Returns the UTC-aware instant such that feature/storage joins should keep
    rows with ``event_time < as_of_bound(ts)``.
    """
    return to_utc(ts)


def resolve_decision_point(decision_point_name: str, local_date: date) -> datetime:
    """Resolve ``(decision_point_name, local_date)`` → aware UTC instant.

    ``local_date`` is interpreted in ``America/New_York``. The named point's
    wall-clock time on that date is constructed with :class:`zoneinfo.ZoneInfo`
    so DST transitions (EST↔EDT) are handled by the tz database — never by a
    fixed UTC offset.

    Parameters
    ----------
    decision_point_name:
        Key in :data:`WALL_CLOCK_DECISION_POINTS` (e.g. ``tuesday_0600_et``).
        Caller must pass the calendar date that already falls on the intended
        weekday (e.g. a Tuesday for ``tuesday_0600_et``).
    local_date:
        Civil date in America/New_York (not UTC).

    Returns
    -------
    datetime
        Timezone-aware UTC instant for that decision point.
    """
    try:
        hour, minute = WALL_CLOCK_DECISION_POINTS[decision_point_name]
    except KeyError as exc:
        known = ", ".join(sorted(WALL_CLOCK_DECISION_POINTS))
        msg = f"Unknown decision point {decision_point_name!r}; known wall-clock: {known}"
        raise UnknownDecisionPointError(msg) from exc

    local_dt = datetime.combine(
        local_date,
        time(hour=hour, minute=minute),
        tzinfo=DECISION_POINT_TZ,
    )
    return to_utc(local_dt)


def season_of(ts: datetime) -> int:
    """Return the CFB season year for an aware timestamp.

    Convention (CFB / CFBD-aligned):
    - A season labeled ``Y`` covers games from August of calendar year ``Y``
      through January of calendar year ``Y + 1`` (bowls / CFP).
    - Therefore early-January bowl games belong to the **prior** season
      (e.g. 2025-01-02 → season 2024).
    - February–July are treated as the upcoming season ``Y`` (calendar year).
    """
    utc = to_utc(ts)
    if utc.month == 1:
        return utc.year - 1
    if utc.month >= 8:
        return utc.year
    return utc.year


def week_of(ts: datetime, season: int) -> int:
    """Return the CFB week number for ``ts`` within ``season``.

    Convention (documented approximation of the CFBD calendar):
    - Weeks are Monday–Sunday in **UTC**.
    - Labor Day is the first Monday of September of ``season``.
    - **Week 1** is the UTC week whose Monday is that Labor Day.
    - Any game before Week 1's Monday is **Week 0** (CFBD "Week Zero" bucket;
      clamped — there is no negative week number).
    - Postseason (December / January) continues sequential week numbers.

    Parameters
    ----------
    ts:
        Aware kickoff (or event) timestamp.
    season:
        Season label as returned by :func:`season_of` (must match ``ts``).
    """
    utc = to_utc(ts)
    expected = season_of(utc)
    if expected != season:
        msg = f"timestamp season {expected} does not match provided season {season}"
        raise ValueError(msg)

    week1_monday = _labor_day_monday(season)
    # Monday 00:00 UTC of the week containing `utc`.
    days_since_monday = utc.weekday()  # Monday=0
    this_monday = utc.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=days_since_monday
    )
    delta_weeks = (this_monday.date() - week1_monday.date()).days // 7
    return max(0, delta_weeks + 1)


def _labor_day_monday(year: int) -> datetime:
    """First Monday of September ``year``, as UTC midnight."""
    # September 1..7 contains exactly one Monday.
    for day in range(1, 8):
        candidate = datetime(year, 9, day, tzinfo=UTC)
        if candidate.weekday() == 0:
            return candidate
    msg = f"Labor Day Monday not found for {year}"  # pragma: no cover
    raise RuntimeError(msg)
