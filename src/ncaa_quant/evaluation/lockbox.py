"""Lockbox season enforcement (DESIGN §7.2 item 9, audit A-11).

Season **2025** is reserved. It is excluded from all development, HPO, ablation
and promotion evaluation, and may be read at most once per calendar year for a
confirmatory report, with the read logged in ``docs/lockbox_access.md``.

The reason a code-level guard exists rather than a documented convention: the
promotion gate re-tests candidates against the same walk-forward seasons at
p < 0.10 over the project's life, so evaluation-set reuse accumulates silently.
A season that is never touched during development is the only instrument that
survives that. Pre-registration constrains hypotheses, not set reuse.

**2025 is not a virgin holdout.** `docs/notes/D7.md` used 2025 weeks 1-4 as a
holdout for the week-interaction refinement on 2026-08-06, hours before the
lockbox designation was written. That read was legitimate at the time and is
recorded in ``docs/lockbox_access.md``; it is not undone by this module. What this
module guarantees is that no *further* development read happens by accident.
"""

from __future__ import annotations

from collections.abc import Iterable

LOCKBOX_SEASON: int = 2025
"""Reserved for confirmatory reads only (§7.2 item 9)."""

HPO_TIEBREAK_SEASON: int = 2024
"""Task 18 quarantine tiebreak. Must differ from the lockbox (§7.2 item 9)."""


class LockboxViolation(RuntimeError):
    """Raised when a development-time evaluation would read the lockbox season."""


def lockbox_free(seasons: Iterable[int]) -> tuple[int, ...]:
    """Drop the lockbox season from ``seasons`` (sorted, unique)."""
    return tuple(sorted({int(s) for s in seasons if int(s) != LOCKBOX_SEASON}))


def contains_lockbox(seasons: Iterable[int]) -> bool:
    """True when ``seasons`` includes the lockbox season."""
    return any(int(s) == LOCKBOX_SEASON for s in seasons)


def assert_lockbox_excluded(
    seasons: Iterable[int],
    *,
    context: str,
    confirmatory_read: bool = False,
) -> None:
    """Raise unless ``seasons`` excludes the lockbox season.

    Parameters
    ----------
    seasons:
        Seasons the caller is about to evaluate on.
    context:
        What is being run, quoted back in the error so the operator knows which
        config to fix.
    confirmatory_read:
        Set only for a deliberate, logged, once-per-year confirmatory read. It is
        an explicit argument rather than a config flag so that permitting a
        lockbox read is always a visible act in code review.
    """
    if confirmatory_read or not contains_lockbox(seasons):
        return
    raise LockboxViolation(
        f"{context} would evaluate on lockbox season {LOCKBOX_SEASON}, which DESIGN "
        f"§7.2 item 9 reserves for confirmatory reads. Drop it from the season list "
        f"(allowed: {lockbox_free(seasons)}), or, if this really is the annual "
        f"confirmatory read, pass confirmatory_read=True and append a row to "
        f"docs/lockbox_access.md recording date, reader, purpose, git SHA and summary."
    )


def assert_tiebreak_differs_from_lockbox() -> None:
    """Guard §7.2 item 9's requirement that the two designations stay distinct."""
    if HPO_TIEBREAK_SEASON == LOCKBOX_SEASON:
        raise LockboxViolation(
            "the HPO quarantine-tiebreak season must differ from the lockbox season; "
            "using one season for both consumes the lockbox on the first HPO run"
        )


__all__ = [
    "HPO_TIEBREAK_SEASON",
    "LOCKBOX_SEASON",
    "LockboxViolation",
    "assert_lockbox_excluded",
    "assert_tiebreak_differs_from_lockbox",
    "contains_lockbox",
    "lockbox_free",
]
