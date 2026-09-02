"""Frozen §1 baseline selection convention (prereg Amendment 1, rule (a)).

Used only to stamp ``RecommendationRecord`` at recommendation time. This is
**not** the §12 accept filter (``configs/betting.yaml`` ``min_edge_sides``).
"""

from __future__ import annotations

from typing import Final

BASELINE_CONVENTION_MIN_WEEK: Final = 2

# Frozen §1 public edge bar (S3/S5 ``THRESHOLD``). Intentionally **not**
# ``configs/betting.yaml`` ``min_edge_sides`` (0.025).
BASELINE_CONVENTION_MIN_EDGE_SIDES: Final = 0.05

# ``BetCandidate.market`` uses ``"side"``; ``RecommendationRecord.market`` uses
# ``"spread"`` for the same instrument.
_BASELINE_SIDE_MARKETS: Final = frozenset({"side", "spread"})


def compute_baseline_convention_eligibility(
    *,
    week: int,
    edge: float,
    market: str,
) -> tuple[bool, tuple[str, ...]]:
    """Return ``(eligible, exclusion_axes)`` for the frozen §1 convention.

    Axes are exhaustive and ordered: ``week_lt_2``, ``edge_lt_0.05``,
    ``market_not_side``.
    """
    axes: list[str] = []
    if int(week) < BASELINE_CONVENTION_MIN_WEEK:
        axes.append("week_lt_2")
    if float(edge) < BASELINE_CONVENTION_MIN_EDGE_SIDES:
        axes.append("edge_lt_0.05")
    if str(market) not in _BASELINE_SIDE_MARKETS:
        axes.append("market_not_side")
    return (len(axes) == 0, tuple(axes))
