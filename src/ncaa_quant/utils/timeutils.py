"""UTC time helpers and CFB season/week conventions.

**NAIVE-DATETIME-FORBIDDEN:** every public function in this module rejects
timezone-naive ``datetime`` values. Callers must attach an explicit ``tzinfo``
(preferably UTC) at the system boundary via :func:`to_utc` or
:func:`assert_tz_aware`. Point-in-time joins use :func:`as_of_bound` so
``event_time < as_of`` comparisons are always UTC-aware.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


class NaiveDatetimeError(ValueError):
    """Raised when a timezone-naive datetime reaches a timeutils boundary."""


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
