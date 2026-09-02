"""Tests for frozen §1 baseline-convention eligibility stamping."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ncaa_quant.betting.baseline_convention import (
    BASELINE_CONVENTION_MIN_EDGE_SIDES,
    compute_baseline_convention_eligibility,
)
from ncaa_quant.betting.clv import (
    ClosingQuote,
    ClvError,
    RecommendationRecord,
    build_recommendation_record,
    settle,
)


def test_baseline_eligible_ticket() -> None:
    eligible, axes = compute_baseline_convention_eligibility(
        week=5, edge=0.10, market="spread"
    )
    assert eligible is True
    assert axes == ()

    rec = build_recommendation_record(
        recommendation_id="r-eligible",
        game_id="g1",
        season=2026,
        week=5,
        side="HOME",
        edge=0.10,
        bet_side_american=-110,
        bet_other_american=-110,
        recommended_at=datetime(2026, 9, 30, tzinfo=UTC),
        close_definition="odds_api_consensus",
        bet_line_source_row_id="snap:1",
        market="spread",
    )
    assert rec.baseline_convention_eligible is True
    assert rec.baseline_convention_exclusion_axes == ()


def test_baseline_week_one_ticket() -> None:
    eligible, axes = compute_baseline_convention_eligibility(
        week=1, edge=0.1472, market="spread"
    )
    assert eligible is False
    assert axes == ("week_lt_2",)


def test_baseline_sub_threshold_edge_ticket() -> None:
    eligible, axes = compute_baseline_convention_eligibility(
        week=5, edge=0.04, market="spread"
    )
    assert eligible is False
    assert axes == ("edge_lt_0.05",)
    assert BASELINE_CONVENTION_MIN_EDGE_SIDES == 0.05


def test_baseline_two_axis_failure_ordered() -> None:
    eligible, axes = compute_baseline_convention_eligibility(
        week=1, edge=0.04, market="total"
    )
    assert eligible is False
    assert axes == ("week_lt_2", "edge_lt_0.05", "market_not_side")


def test_recommendation_record_rejects_inconsistent_baseline_fields() -> None:
    with pytest.raises(ClvError, match="empty exclusion_axes"):
        RecommendationRecord(
            recommendation_id="bad",
            game_id="g1",
            season=2026,
            week=5,
            side="HOME",
            bet_side_american=-110,
            bet_other_american=-110,
            recommended_at=datetime(2026, 9, 30, tzinfo=UTC),
            close_definition="odds_api_consensus",
            baseline_convention_eligible=True,
            baseline_convention_exclusion_axes=("week_lt_2",),
        )


def test_settle_passes_baseline_fields_through_unchanged() -> None:
    rec = build_recommendation_record(
        recommendation_id="r1",
        game_id="g1",
        season=2026,
        week=1,
        side="Auburn",
        edge=0.1472,
        bet_side_american=-102,
        bet_other_american=-118,
        recommended_at=datetime(2026, 9, 1, tzinfo=UTC),
        close_definition="odds_api_consensus",
        bet_line_source_row_id="snap:bet",
        market="spread",
        bet_line=-7.5,
        book="fanduel",
    )
    settled = settle(
        rec,
        same_book_close=ClosingQuote(
            side_american=-105,
            other_american=-115,
            book="fanduel",
            line=-7.5,
            source_row_id="snap:close",
        ),
    )
    assert settled.recommendation is rec
    assert settled.recommendation.baseline_convention_eligible is False
    assert settled.recommendation.baseline_convention_exclusion_axes == ("week_lt_2",)


def test_w1_step5_survivors_dry_run() -> None:
    """Frozen W1 step-5 survivors (Auburn 0.1472, Duke 0.1048) — week 1 only."""
    cases = (
        ("Auburn", 0.1472),
        ("Duke", 0.1048),
    )
    for side, edge in cases:
        eligible, axes = compute_baseline_convention_eligibility(
            week=1, edge=edge, market="spread"
        )
        assert eligible is False
        assert axes == ("week_lt_2",), f"{side} edge={edge}"
