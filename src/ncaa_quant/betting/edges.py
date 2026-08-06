"""Edge and expected-value computation vs captured book prices.

Edge is calibrated model probability minus de-vigged market probability on the
same side, evaluated at the **best available** captured American price across
books (line shopping is alpha per DESIGN §16 item 5 / §12).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ncaa_quant.betting.devig import (
    DEFAULT_DEVIG_METHOD,
    DevigError,
    DevigMethod,
    american_to_decimal,
    american_to_raw_implied,
    devig_two_way,
)


class EdgeError(ValueError):
    """Invalid edge / EV inputs."""


@dataclass(frozen=True, slots=True)
class BookPrice:
    """One book's two-way American prices for a side market.

    ``side_american`` is the price for the side we might bet;
    ``other_american`` is the opposing side (needed for two-way de-vig).
    """

    book: str
    side_american: float
    other_american: float


@dataclass(frozen=True, slots=True)
class EdgeResult:
    """Edge vs best book for one side."""

    book: str
    side_american: float
    other_american: float
    p_model: float
    p_market: float
    edge: float
    decimal_odds: float
    expected_value: float


def best_price(prices: Sequence[BookPrice]) -> BookPrice:
    """Select the best available price for the bet side (highest decimal odds).

    Line shopping: among captured books, take the most favorable American odds
    for the side under consideration. Ties broken by first occurrence.
    """
    if not prices:
        raise EdgeError("need at least one book price")
    best = prices[0]
    best_dec = american_to_decimal(best.side_american)
    for p in prices[1:]:
        dec = american_to_decimal(p.side_american)
        if dec > best_dec:
            best = p
            best_dec = dec
    return best


def compute_edge(
    p_model_calibrated: float,
    prices: Sequence[BookPrice],
    *,
    method: DevigMethod = DEFAULT_DEVIG_METHOD,
) -> EdgeResult:
    """``edge = p_model_calibrated − p_market_devigged`` at best book.

    Parameters
    ----------
    p_model_calibrated:
        Calibrated model probability for this side in [0, 1].
    prices:
        Captured two-way prices across books for this market/side.
    method:
        De-vig method (default proportional per DESIGN §2.7).
    """
    p_model = float(p_model_calibrated)
    if not 0.0 <= p_model <= 1.0:
        raise EdgeError(f"p_model must be in [0, 1], got {p_model}")

    best = best_price(prices)
    p_side, _p_other = devig_two_way(
        best.side_american,
        best.other_american,
        method=method,
    )
    edge = p_model - p_side
    dec = american_to_decimal(best.side_american)
    ev = expected_value(p_model, best.side_american)
    return EdgeResult(
        book=best.book,
        side_american=float(best.side_american),
        other_american=float(best.other_american),
        p_model=p_model,
        p_market=float(p_side),
        edge=float(edge),
        decimal_odds=float(dec),
        expected_value=float(ev),
    )


def expected_value(p_win: float, american_odds: float) -> float:
    """Expected value per unit stake at the actual available American price.

    ``EV = p * decimal − 1`` (profit units per 1 unit staked).
    """
    p = float(p_win)
    if not 0.0 <= p <= 1.0:
        raise EdgeError(f"p_win must be in [0, 1], got {p}")
    try:
        dec = american_to_decimal(american_odds)
    except DevigError as exc:
        raise EdgeError(str(exc)) from exc
    return p * dec - 1.0


def expected_value_from_raw_implied(p_win: float, raw_implied: float) -> float:
    """EV when the offered price is expressed as a raw (vigged) implied prob."""
    q = float(raw_implied)
    if q <= 0.0 or q >= 1.0:
        raise EdgeError(f"raw implied must be in (0, 1), got {q}")
    # decimal = 1 / raw_implied for a single quoted price
    return float(p_win) / q - 1.0


def market_fair_prob(
    side_american: float,
    other_american: float,
    *,
    method: DevigMethod = DEFAULT_DEVIG_METHOD,
) -> float:
    """Convenience: fair prob of ``side`` after de-vig."""
    p_side, _ = devig_two_way(side_american, other_american, method=method)
    return p_side


# Re-export for callers that only import edges.
__all__ = [
    "BookPrice",
    "EdgeError",
    "EdgeResult",
    "american_to_raw_implied",
    "best_price",
    "compute_edge",
    "expected_value",
    "expected_value_from_raw_implied",
    "market_fair_prob",
]
