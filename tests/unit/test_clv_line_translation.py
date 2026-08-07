"""CLV settlement per amended DESIGN §2.7 / Task 20 item 6-7 (audit A-1).

Two defects are pinned here:

1. Bets are placed at the best price across books but CLV was settled against a
   consensus close, which credits line shopping as forecasting skill.
2. Probability CLV was differenced across *different lines* when the spread or
   total moved, which prices a ticket nobody holds. The error is the push mass
   between the two numbers — largest at key numbers.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest

from ncaa_quant.betting.clv import (
    ClosingQuote,
    ClvError,
    RecommendationRecord,
    compute_clv,
    compute_line_shopping_capture,
    line_units_clv,
    settle,
    settle_week,
    spread_cover_prob,
    spread_cover_prob_fn,
    summarize_settlements,
    total_cover_prob,
    total_cover_prob_fn,
)

# A margin distribution with 8% mass exactly on 7, the key number that makes the
# -6.5 -> -7 translation matter. Everything else is spread over non-key margins
# so the hand computation below stays exact.
MARGIN_PMF: dict[float, float] = {
    -10.0: 0.20,
    -3.0: 0.14,
    0.0: 0.04,
    3.0: 0.14,
    7.0: 0.08,
    10.0: 0.20,
    14.0: 0.20,
}

TOTAL_PMF: dict[float, float] = {
    41.0: 0.30,
    48.0: 0.10,
    52.0: 0.25,
    59.0: 0.35,
}


def _spread_rec(**overrides: object) -> RecommendationRecord:
    base: dict[str, object] = {
        "recommendation_id": "r1",
        "game_id": "g1",
        "season": 2024,
        "week": 5,
        "side": "HOME",
        "bet_side_american": -110,
        "bet_other_american": -110,
        "recommended_at": datetime(2024, 10, 1, tzinfo=UTC),
        "close_definition": "odds_api_consensus",
        "book": "pinnacle",
        "market": "spread",
        "bet_line": -6.5,
    }
    base.update(overrides)
    return RecommendationRecord(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Model cover probabilities
# ---------------------------------------------------------------------------


def test_spread_cover_prob_counts_pushes_as_half() -> None:
    # Laying 6.5: covers on margin >= 7 -> 0.08 + 0.20 + 0.20 = 0.48, no push.
    assert spread_cover_prob(MARGIN_PMF, -6.5) == pytest.approx(0.48)
    # Laying 7: covers on margin >= 8 -> 0.40; margin == 7 pushes -> +0.04.
    assert spread_cover_prob(MARGIN_PMF, -7.0) == pytest.approx(0.44)
    # Taking 6.5 as a dog is the complement of laying 6.5 here (no push mass).
    assert spread_cover_prob(MARGIN_PMF, -6.5) + spread_cover_prob(
        {-m: p for m, p in MARGIN_PMF.items()}, 6.5
    ) == pytest.approx(1.0)


def test_total_cover_prob_orients_by_side() -> None:
    # Over 48: totals above 48 -> 0.25 + 0.35 = 0.60; 48 pushes -> +0.05.
    assert total_cover_prob(TOTAL_PMF, 48.0, "over") == pytest.approx(0.65)
    # Under 48: totals below -> 0.30; push -> +0.05.
    assert total_cover_prob(TOTAL_PMF, 48.0, "under") == pytest.approx(0.35)
    assert total_cover_prob(TOTAL_PMF, 48.0, "over") + total_cover_prob(
        TOTAL_PMF, 48.0, "under"
    ) == pytest.approx(1.0)


def test_cover_prob_rejects_a_distribution_that_is_not_one() -> None:
    with pytest.raises(ClvError, match="sum to 1"):
        spread_cover_prob({0.0: 0.3, 7.0: 0.3}, -3.5)


# ---------------------------------------------------------------------------
# The audit fixture: -6.5 -> -7 at unchanged -110 / -110
# ---------------------------------------------------------------------------


def test_moved_line_translation_differs_from_naive_price_only_clv() -> None:
    """Task 20 item 7: the fixture that must fail a price-only implementation.

    We hold -6.5; the book closed at -7 with the price unchanged at -110/-110.
    Price-only CLV sees two identical prices and reports exactly zero, which
    claims we captured nothing. In fact our ticket needs a 7-point win and the
    close needs 8, so we hold the better number by the half of the push mass on
    7: 0.5 * P(margin == 7) = 0.5 * 0.08 = 0.04.
    """
    rec = _spread_rec()
    close = ClosingQuote(side_american=-110, other_american=-110, book="pinnacle", line=-7.0)

    _p_bet_naive, _p_close_naive, naive_clv = compute_clv(-110, -110, -110, -110)
    assert naive_clv == pytest.approx(0.0)

    bet = settle(rec, same_book_close=close, model_cover_prob=spread_cover_prob_fn(MARGIN_PMF))

    assert bet.clv_method == "model_dist"
    assert bet.clv_settlement == "same_book"
    assert bet.p_bet_fair == pytest.approx(0.5)
    # Market level 0.5 at -7, shifted by P(cover -6.5) - P(cover -7) = 0.48 - 0.44.
    assert bet.p_close_fair == pytest.approx(0.54)
    assert bet.clv == pytest.approx(0.04)
    assert bet.clv_line_units == pytest.approx(0.5)

    # The whole point: the corrected value is not the naive one.
    assert not math.isclose(bet.clv, naive_clv, abs_tol=1e-9)
    assert bet.is_headline


def test_translation_sign_flips_when_the_line_moves_against_us() -> None:
    """We hold -7 and the book closes -6.5: later bettors got the better number."""
    rec = _spread_rec(bet_line=-7.0)
    close = ClosingQuote(side_american=-110, other_american=-110, book="pinnacle", line=-6.5)

    bet = settle(rec, same_book_close=close, model_cover_prob=spread_cover_prob_fn(MARGIN_PMF))

    assert bet.clv == pytest.approx(-0.04)
    assert bet.clv_line_units == pytest.approx(-0.5)


def test_alt_line_price_takes_priority_over_the_model(caplog: pytest.LogCaptureFixture) -> None:
    """A real quote at the ticket line beats inferring the shift from the model."""
    rec = _spread_rec()
    close = ClosingQuote(
        side_american=-110,
        other_american=-110,
        book="pinnacle",
        line=-7.0,
        alt_side_american=-125,
        alt_other_american=105,
    )

    bet = settle(rec, same_book_close=close, model_cover_prob=spread_cover_prob_fn(MARGIN_PMF))

    assert bet.clv_method == "alt_line_price"
    q_side, q_other = 125.0 / 225.0, 100.0 / 205.0
    assert bet.p_close_fair == pytest.approx(q_side / (q_side + q_other))


def test_line_units_when_no_translation_is_possible() -> None:
    """Without an alt line or a model, points are the only honest unit."""
    rec = _spread_rec()
    close = ClosingQuote(side_american=-110, other_american=-110, book="pinnacle", line=-7.0)

    bet = settle(rec, same_book_close=close)

    assert bet.clv_method == "line_units"
    assert math.isnan(bet.clv)
    assert bet.clv_line_units == pytest.approx(0.5)
    assert not bet.is_headline


def test_unmoved_line_settles_as_same_line() -> None:
    rec = _spread_rec()
    close = ClosingQuote(side_american=-130, other_american=110, book="pinnacle", line=-6.5)

    bet = settle(rec, same_book_close=close)

    assert bet.clv_method == "same_line"
    assert bet.clv > 0.0
    assert bet.clv_line_units == pytest.approx(0.0)


def test_totals_translate_on_the_over_and_under_sides() -> None:
    over = RecommendationRecord(
        recommendation_id="o1",
        game_id="g1",
        season=2024,
        week=5,
        side="over",
        bet_side_american=-110,
        bet_other_american=-110,
        recommended_at=datetime(2024, 10, 1, tzinfo=UTC),
        close_definition="odds_api_consensus",
        book="pinnacle",
        market="total",
        bet_line=48.0,
        total_side="over",
    )
    close = ClosingQuote(side_american=-110, other_american=-110, book="pinnacle", line=52.0)

    bet = settle(
        over, same_book_close=close, model_cover_prob=total_cover_prob_fn(TOTAL_PMF, "over")
    )

    # An over bought at 48 is better than a 52 close: fewer points needed.
    shift = total_cover_prob(TOTAL_PMF, 48.0, "over") - total_cover_prob(TOTAL_PMF, 52.0, "over")
    assert shift > 0.0
    assert bet.clv == pytest.approx(0.5 + shift - 0.5)
    assert bet.clv_line_units == pytest.approx(4.0)


def test_line_units_sign_conventions() -> None:
    assert line_units_clv(market="spread", bet_line=-6.5, close_line=-7.0) == pytest.approx(0.5)
    assert line_units_clv(market="spread", bet_line=3.5, close_line=3.0) == pytest.approx(0.5)
    assert line_units_clv(
        market="total", bet_line=48.0, close_line=52.0, total_side="over"
    ) == pytest.approx(4.0)
    assert line_units_clv(
        market="total", bet_line=52.0, close_line=48.0, total_side="under"
    ) == pytest.approx(4.0)
    with pytest.raises(ClvError, match="no line to translate"):
        line_units_clv(market="moneyline", bet_line=0.0, close_line=0.0)
    with pytest.raises(ClvError, match="total_side"):
        line_units_clv(market="total", bet_line=48.0, close_line=52.0)


def test_translation_refuses_to_leave_the_unit_interval() -> None:
    """A model that disagrees violently with the close must not fake a probability."""
    rec = _spread_rec(bet_line=-6.5)
    close = ClosingQuote(side_american=-110, other_american=-110, book="pinnacle", line=40.0)

    with pytest.raises(ClvError, match=r"left \[0, 1\]"):
        settle(rec, same_book_close=close, model_cover_prob=lambda line: 1.0 if line < 0 else 0.0)


# ---------------------------------------------------------------------------
# Same-book settlement and line shopping
# ---------------------------------------------------------------------------


def test_same_book_settlement_requires_the_same_book() -> None:
    rec = _spread_rec()
    close = ClosingQuote(side_american=-110, other_american=-110, book="draftkings", line=-6.5)

    with pytest.raises(ClvError, match="matching books"):
        settle(rec, same_book_close=close)


def test_missing_same_book_close_falls_back_and_is_flagged() -> None:
    rec = _spread_rec()
    consensus = ClosingQuote(side_american=-130, other_american=110, line=-6.5)

    bet = settle(rec, consensus_close=consensus)

    assert bet.clv_settlement == "fallback_consensus"
    assert not bet.is_headline


def test_settle_without_any_close_raises() -> None:
    with pytest.raises(ClvError, match="no close available"):
        settle(_spread_rec())


def test_line_shopping_capture_measures_the_books_disagreement_with_consensus() -> None:
    """We bought our side at -105 where consensus was -110.

    Sign convention, which is easy to misread: the metric is
    ``implied_prob(best) − implied_prob(consensus)`` per §2.7, so buying our side
    *cheaper* than consensus makes it **negative** — that book's fair view of our
    side is below consensus, which is exactly why its price was worth taking.
    See ADR 0006.
    """
    rec = _spread_rec(
        bet_side_american=-105,
        bet_other_american=-115,
        consensus_side_american=-110,
        consensus_other_american=-110,
    )
    close = ClosingQuote(side_american=-110, other_american=-110, book="pinnacle", line=-6.5)

    capture = compute_line_shopping_capture(rec)
    bet = settle(rec, same_book_close=close)

    q_best_side, q_best_other = 105.0 / 205.0, 115.0 / 215.0
    expected = q_best_side / (q_best_side + q_best_other) - 0.5
    assert capture == pytest.approx(expected)
    assert capture < 0.0
    assert bet.line_shopping_capture == pytest.approx(expected)
    # Shopping value must not leak into CLV, which compares bet price to close.
    assert bet.clv != pytest.approx(capture)


def test_consensus_settlement_credits_a_skill_free_bet_and_same_book_does_not() -> None:
    """The exact bias audit A-1(a) describes, pinned as a regression test.

    A bet whose book never moves has zero closing-line value by construction.
    Settling it against a consensus close instead reports positive CLV anyway,
    purely because we bought the outlier price and graded against the average.
    The spurious credit equals the magnitude of ``line_shopping_capture``.
    """
    rec = _spread_rec(
        bet_side_american=-105,
        bet_other_american=-115,
        consensus_side_american=-110,
        consensus_other_american=-110,
    )
    # Our book closes exactly where we bet: no movement, so no value.
    same_book_close = ClosingQuote(
        side_american=-105, other_american=-115, book="pinnacle", line=-6.5
    )
    consensus_close = ClosingQuote(side_american=-110, other_american=-110, line=-6.5)

    truth = settle(rec, same_book_close=same_book_close)
    biased = settle(rec, consensus_close=consensus_close)

    assert truth.clv == pytest.approx(0.0)
    assert biased.clv > 0.0
    assert biased.clv == pytest.approx(-compute_line_shopping_capture(rec))

    # And the two can never be pooled into one headline number.
    report = summarize_settlements([truth, biased], season=2024, week=5)
    assert report.n_bets == 1
    assert report.mean_clv == pytest.approx(0.0)


def test_line_shopping_capture_is_nan_without_a_consensus_price() -> None:
    assert math.isnan(compute_line_shopping_capture(_spread_rec()))


# ---------------------------------------------------------------------------
# Aggregation must not pool strata
# ---------------------------------------------------------------------------


def test_summary_never_pools_same_book_with_fallback_or_line_units() -> None:
    same_line_close = ClosingQuote(
        side_american=-130, other_american=110, book="pinnacle", line=-6.5
    )
    moved_close = ClosingQuote(side_american=-110, other_american=-110, book="pinnacle", line=-7.0)

    head = settle(_spread_rec(recommendation_id="head"), same_book_close=same_line_close)
    fallback = settle(
        _spread_rec(recommendation_id="fb"),
        consensus_close=ClosingQuote(side_american=-150, other_american=130, line=-6.5),
    )
    units = settle(_spread_rec(recommendation_id="units"), same_book_close=moved_close)

    report = summarize_settlements([head, fallback, units], season=2024, week=5)

    assert report.n_bets == 1
    assert report.mean_clv == pytest.approx(head.clv)
    assert report.same_book is not None and report.same_book.n_bets == 1
    assert report.fallback_consensus is not None and report.fallback_consensus.n_bets == 1
    assert report.line_units is not None and report.line_units.n_bets == 1
    # The fallback row has the largest CLV; if it leaked into the headline the
    # mean would move.
    assert fallback.clv > head.clv
    assert report.mean_clv < fallback.clv


def test_settle_week_routes_same_book_fallback_and_model_translation() -> None:
    moved = _spread_rec(recommendation_id="moved")
    unmoved = _spread_rec(recommendation_id="unmoved")
    orphan = _spread_rec(recommendation_id="orphan")

    settled, report = settle_week(
        [moved, unmoved, orphan],
        {
            "moved": ClosingQuote(
                side_american=-110, other_american=-110, book="pinnacle", line=-7.0
            ),
            "unmoved": (-130, 110),
        },
        season=2024,
        week=5,
        consensus_closes={
            "orphan": ClosingQuote(side_american=-110, other_american=-110, line=-6.5)
        },
        model_cover_probs={"moved": spread_cover_prob_fn(MARGIN_PMF)},
    )

    by_id = {s.recommendation.recommendation_id: s for s in settled}
    assert by_id["moved"].clv_method == "model_dist"
    assert by_id["moved"].clv == pytest.approx(0.04)
    assert by_id["unmoved"].clv_method == "same_line"
    assert by_id["orphan"].clv_settlement == "fallback_consensus"
    assert report.n_bets == 2  # orphan excluded from the headline


def test_settle_week_skips_recommendations_with_no_close_at_all() -> None:
    settled, report = settle_week([_spread_rec()], {}, season=2024, week=5)

    assert settled == []
    assert report.n_bets == 0
