"""Unit tests for the betting layer (Task 20 / DESIGN §12, §2.7)."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from ncaa_quant.betting.clv import (
    RecommendationRecord,
    compute_clv,
    settle_recommendation,
    settle_week,
)
from ncaa_quant.betting.devig import (
    DEFAULT_DEVIG_METHOD,
    american_to_decimal,
    american_to_raw_implied,
    devig,
    multiplicative_devig,
    proportional_devig,
    shin_devig,
)
from ncaa_quant.betting.edges import BookPrice, best_price, compute_edge, expected_value
from ncaa_quant.betting.filters import BetCandidate, FilterReason, evaluate_filters
from ncaa_quant.betting.kelly import (
    HARD_MAX_KELLY_FRACTION,
    HARD_MAX_STAKE_PCT,
    full_kelly,
    recommended_stake,
)
from ncaa_quant.config import BettingConfig

# ---------------------------------------------------------------------------
# De-vig — hand-computed fixtures
# ---------------------------------------------------------------------------


def test_american_to_raw_implied_hand_computed() -> None:
    # −110 → 110/210 = 0.523809...
    assert american_to_raw_implied(-110) == pytest.approx(110.0 / 210.0)
    # +150 → 100/250 = 0.4
    assert american_to_raw_implied(150) == pytest.approx(0.4)
    # −200 → 200/300 = 2/3
    assert american_to_raw_implied(-200) == pytest.approx(2.0 / 3.0)


def test_proportional_devig_hand_computed() -> None:
    # Classic −110/−110: raw = (110/210, 110/210), sum = 220/210
    # fair each = 0.5
    q = [110.0 / 210.0, 110.0 / 210.0]
    p = proportional_devig(q)
    assert p[0] == pytest.approx(0.5)
    assert p[1] == pytest.approx(0.5)
    assert float(p.sum()) == pytest.approx(1.0)

    # −150 / +130: q1=150/250=0.6, q2=100/230≈0.434783
    q1, q2 = 0.6, 100.0 / 230.0
    p = proportional_devig([q1, q2])
    s = q1 + q2
    assert p[0] == pytest.approx(q1 / s)
    assert p[1] == pytest.approx(q2 / s)
    assert float(p.sum()) == pytest.approx(1.0)


def test_multiplicative_devig_hand_computed() -> None:
    # Equal raw probs → power method must still yield 0.5/0.5
    p = multiplicative_devig([0.55, 0.55])
    assert p[0] == pytest.approx(0.5)
    assert p[1] == pytest.approx(0.5)

    # Asymmetric: sum(q^c)=1; verify numerically
    q = np.array([0.6, 100.0 / 230.0])
    p = multiplicative_devig(q)
    assert float(p.sum()) == pytest.approx(1.0)
    assert np.all(p > 0.0)
    # Power method ≠ proportional when asymmetric
    prop = proportional_devig(q)
    assert not np.allclose(p, prop)


def test_shin_devig_hand_computed() -> None:
    # Equal two-way market → Shin returns 0.5/0.5 by symmetry
    p = shin_devig([0.55, 0.55])
    assert p[0] == pytest.approx(0.5)
    assert p[1] == pytest.approx(0.5)
    assert float(p.sum()) == pytest.approx(1.0)

    # Two-way asymmetric: Shin differs from proportional (hand-checked).
    q2 = np.array([0.6, 100.0 / 230.0])
    p2 = shin_devig(q2)
    prop2 = proportional_devig(q2)
    assert float(p2.sum()) == pytest.approx(1.0)
    assert np.all(p2 > 0.0)
    assert not np.allclose(p2, prop2, atol=1e-4)
    # Approx values from the Shin inversion at this market:
    assert p2[0] == pytest.approx(0.5826087, abs=1e-5)

    # Three-way asymmetric: also differs from proportional.
    q3 = np.array([0.55, 0.35, 0.30])
    p3 = shin_devig(q3)
    prop3 = proportional_devig(q3)
    assert float(p3.sum()) == pytest.approx(1.0)
    assert not np.allclose(p3, prop3, atol=1e-4)


def test_default_devig_method_is_proportional() -> None:
    assert DEFAULT_DEVIG_METHOD == "proportional"
    q = [0.55, 0.55]
    assert np.allclose(devig(q), proportional_devig(q))


# ---------------------------------------------------------------------------
# Edge / EV
# ---------------------------------------------------------------------------


def test_best_price_line_shops() -> None:
    prices = [
        BookPrice("dk", -110, -110),
        BookPrice("fd", -105, -115),  # better for side
        BookPrice("mgm", -115, -105),
    ]
    best = best_price(prices)
    assert best.book == "fd"
    assert best.side_american == -105


def test_edge_and_ev_hand_computed() -> None:
    # Model 0.55; market −110/−110 → fair 0.5; edge = 0.05
    prices = [BookPrice("dk", -110, -110)]
    result = compute_edge(0.55, prices)
    assert result.p_market == pytest.approx(0.5)
    assert result.edge == pytest.approx(0.05)
    # EV = 0.55 * (1 + 100/110) − 1 = 0.55 * (210/110) − 1
    expected_ev = 0.55 * (210.0 / 110.0) - 1.0
    assert result.expected_value == pytest.approx(expected_ev)
    assert expected_value(0.55, -110) == pytest.approx(expected_ev)


def test_edge_uses_best_book() -> None:
    prices = [
        BookPrice("dk", -110, -110),
        BookPrice("fd", -105, -115),
    ]
    result = compute_edge(0.55, prices)
    assert result.book == "fd"
    assert result.side_american == -105


# ---------------------------------------------------------------------------
# Kelly
# ---------------------------------------------------------------------------


def test_full_kelly_even_money_closed_form() -> None:
    # Even money (+100): f* = 2p − 1
    assert full_kelly(0.6, 100) == pytest.approx(0.2)
    assert full_kelly(0.5, 100) == pytest.approx(0.0)
    assert full_kelly(0.4, 100) == pytest.approx(0.0)


def test_full_kelly_minus_110_closed_form() -> None:
    # b = 100/110; f* = (b p − (1−p)) / b
    p = 0.55
    b = 100.0 / 110.0
    expected = (b * p - (1.0 - p)) / b
    assert full_kelly(p, -110) == pytest.approx(expected)


def test_recommended_stake_quarter_kelly() -> None:
    cfg = BettingConfig()
    # Even money, p=0.6 → full=0.2 → quarter=0.05 → capped at 1.5%
    result = recommended_stake(0.6, 100, bankroll=10_000.0, config=cfg)
    assert result.full_kelly_fraction == pytest.approx(0.2)
    assert result.fractional_kelly_before_cap == pytest.approx(0.05)
    assert result.stake_fraction == pytest.approx(HARD_MAX_STAKE_PCT)
    assert result.capped_by_hard_max is True
    assert result.stake_amount == pytest.approx(150.0)


def test_recommended_stake_respects_tighter_config_cap() -> None:
    cfg = BettingConfig(max_stake_pct=0.01)
    result = recommended_stake(0.6, 100, bankroll=10_000.0, config=cfg)
    assert result.stake_fraction == pytest.approx(0.01)
    assert result.capped_by_config is True
    assert result.stake_amount == pytest.approx(100.0)


def test_staking_cap_cannot_be_bypassed_by_configuration() -> None:
    """Acceptance: max_stake_pct / kelly_fraction config cannot exceed hard caps."""
    # Try to force full Kelly and 100% bankroll via config.
    cfg = BettingConfig(kelly_fraction=1.0, max_stake_pct=1.0)
    result = recommended_stake(0.9, 100, bankroll=10_000.0, config=cfg)
    assert result.stake_fraction <= HARD_MAX_STAKE_PCT + 1e-15
    assert result.stake_fraction == pytest.approx(HARD_MAX_STAKE_PCT)
    # Effective fraction used for staking is capped at quarter-Kelly.
    assert result.fractional_kelly_before_cap == pytest.approx(
        HARD_MAX_KELLY_FRACTION * result.full_kelly_fraction
    )
    # Full Kelly is still reported for inspection.
    assert result.full_kelly_fraction == pytest.approx(full_kelly(0.9, 100))
    assert result.full_kelly_fraction > result.stake_fraction


def test_weekly_and_team_exposure_limits() -> None:
    cfg = BettingConfig(max_weekly_exposure=0.02, max_exposure_per_team=0.01)
    # p=0.9 at +100 → full=0.8 → quarter=0.2 → hard-capped to 0.015,
    # then team room = 0.01 − 0.005 = 0.005
    result = recommended_stake(
        0.9,
        100,
        bankroll=1_000.0,
        config=cfg,
        weekly_exposure_so_far=0.0,
        team_exposure_so_far=0.005,
    )
    assert result.stake_fraction == pytest.approx(0.005)
    assert result.capped_by_team is True


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def _candidate(**overrides: object) -> BetCandidate:
    base: dict[str, object] = {
        "game_id": "g1",
        "market": "side",
        "edge": 0.04,
        "expected_value": 0.05,
        "is_stale": False,
        "qb_status_known": True,
        "is_bowl": False,
        "model_market_residual_points": 2.0,
        "team_ids": ("TEAM_A",),
    }
    base.update(overrides)
    return BetCandidate(**base)  # type: ignore[arg-type]


def test_filter_passes_when_all_clear() -> None:
    cfg = BettingConfig()
    result = evaluate_filters(_candidate(), cfg)
    assert result.accepted is True
    assert result.reasons == (FilterReason.PASS,)


def test_filter_edge_too_small_sides() -> None:
    cfg = BettingConfig()
    result = evaluate_filters(_candidate(edge=0.02), cfg)
    assert result.accepted is False
    assert FilterReason.EDGE_TOO_SMALL in result.reasons


def test_filter_edge_too_small_totals() -> None:
    cfg = BettingConfig()
    result = evaluate_filters(_candidate(market="total", edge=0.029), cfg)
    assert FilterReason.EDGE_TOO_SMALL in result.reasons
    result_ok = evaluate_filters(_candidate(market="total", edge=0.03), cfg)
    assert result_ok.accepted is True


def test_filter_stale() -> None:
    cfg = BettingConfig()
    result = evaluate_filters(_candidate(is_stale=True), cfg)
    assert FilterReason.STALE_INPUTS in result.reasons


def test_filter_qb_unknown() -> None:
    cfg = BettingConfig()
    result = evaluate_filters(_candidate(qb_status_known=False), cfg)
    assert FilterReason.QB_STATUS_UNKNOWN in result.reasons


def test_filter_bowl_stricter_threshold() -> None:
    cfg = BettingConfig()  # sides 2.5% × 1.5 = 3.75%
    # 3% edge passes regular, fails bowl
    regular = evaluate_filters(_candidate(edge=0.03, is_bowl=False), cfg)
    bowl = evaluate_filters(_candidate(edge=0.03, is_bowl=True), cfg)
    assert regular.accepted is True
    assert bowl.accepted is False
    assert FilterReason.EDGE_TOO_SMALL in bowl.reasons
    assert bowl.min_edge_applied == pytest.approx(0.025 * 1.5)


def test_filter_max_bets_per_week() -> None:
    cfg = BettingConfig(max_bets_per_week=2)
    result = evaluate_filters(_candidate(), cfg, bets_this_week=2)
    assert FilterReason.MAX_BETS_PER_WEEK in result.reasons


def test_filter_model_market_disagreement() -> None:
    cfg = BettingConfig(min_model_market_agreement=7.0)
    result = evaluate_filters(_candidate(model_market_residual_points=8.0), cfg)
    assert FilterReason.MODEL_MARKET_DISAGREE in result.reasons


def test_filter_flags_can_be_disabled() -> None:
    cfg = BettingConfig(no_bet_on_stale=False, no_bet_on_qb_unknown=False)
    result = evaluate_filters(_candidate(is_stale=True, qb_status_known=False), cfg)
    assert result.accepted is True


# ---------------------------------------------------------------------------
# CLV — both directions
# ---------------------------------------------------------------------------


def test_clv_raises_on_same_source_row() -> None:
    from ncaa_quant.betting.clv import ClvError, assert_distinct_line_sources

    with pytest.raises(ClvError, match="same source row"):
        compute_clv(
            -110,
            -110,
            -110,
            -110,
            bet_line_source_row_id="cfbd:2019:1:close",
            close_line_source_row_id="cfbd:2019:1:close",
        )
    with pytest.raises(ClvError, match="same source row"):
        assert_distinct_line_sources(
            bet_source_row_id="row_a",
            close_source_row_id="row_a",
        )


def test_clv_line_moved_in_our_favor() -> None:
    """Bet −110/−110 (fair 0.5); close −130/+110 → our side more favorite → +CLV."""
    p_bet, p_close, clv = compute_clv(
        bet_side_american=-110,
        bet_other_american=-110,
        close_side_american=-130,
        close_other_american=110,
    )
    assert p_bet == pytest.approx(0.5)
    # Hand: q_side=130/230, q_other=100/210; p_close = q_side/(q_side+q_other)
    q_s, q_o = 130.0 / 230.0, 100.0 / 210.0
    expected_p_close = q_s / (q_s + q_o)
    assert p_close == pytest.approx(expected_p_close)
    assert p_close > p_bet
    assert clv == pytest.approx(expected_p_close - 0.5)
    assert clv > 0.0


def test_clv_line_moved_against_us() -> None:
    """Bet −110/−110; close +110/−130 → our side became dog → −CLV."""
    p_bet, p_close, clv = compute_clv(
        bet_side_american=-110,
        bet_other_american=-110,
        close_side_american=110,
        close_other_american=-130,
    )
    assert p_bet == pytest.approx(0.5)
    q_s, q_o = 100.0 / 210.0, 130.0 / 230.0
    expected_p_close = q_s / (q_s + q_o)
    assert p_close == pytest.approx(expected_p_close)
    assert p_close < p_bet
    assert clv == pytest.approx(expected_p_close - 0.5)
    assert clv < 0.0


def test_weekly_settlement_job() -> None:
    rec = RecommendationRecord(
        recommendation_id="r1",
        game_id="g1",
        season=2024,
        week=5,
        side="HOME",
        bet_side_american=-110,
        bet_other_american=-110,
        recommended_at=datetime(2024, 10, 1, tzinfo=UTC),
        close_definition="odds_api_consensus",
    )
    settled, report = settle_week(
        [rec],
        {"r1": (-130, 110)},
        season=2024,
        week=5,
    )
    assert len(settled) == 1
    assert settled[0].clv > 0.0
    assert report.n_bets == 1
    assert report.mean_clv == pytest.approx(settled[0].clv)
    assert report.pct_positive == pytest.approx(1.0)
    assert report.close_definition == "odds_api_consensus"

    one = settle_recommendation(rec, -130, 110)
    assert one.clv == pytest.approx(settled[0].clv)


def test_american_to_decimal() -> None:
    assert american_to_decimal(-110) == pytest.approx(210.0 / 110.0)
    assert american_to_decimal(150) == pytest.approx(2.5)


def test_devig_errors_and_helpers() -> None:
    from ncaa_quant.betting.devig import (
        DevigError,
        decimal_to_raw_implied,
        fair_prob_on_side,
    )

    assert decimal_to_raw_implied(2.0) == pytest.approx(0.5)
    with pytest.raises(DevigError):
        decimal_to_raw_implied(1.0)
    with pytest.raises(DevigError):
        american_to_decimal(0)
    with pytest.raises(DevigError):
        proportional_devig([0.5])
    assert fair_prob_on_side(-110, -110) == pytest.approx(0.5)
    assert float(devig([0.55, 0.55], method="shin").sum()) == pytest.approx(1.0)


def test_edge_errors() -> None:
    from ncaa_quant.betting.edges import (
        EdgeError,
        expected_value_from_raw_implied,
        market_fair_prob,
    )

    with pytest.raises(EdgeError):
        best_price([])
    with pytest.raises(EdgeError):
        compute_edge(1.5, [BookPrice("dk", -110, -110)])
    with pytest.raises(EdgeError):
        expected_value(-0.1, -110)
    assert expected_value_from_raw_implied(0.55, 0.5) == pytest.approx(0.1)
    assert market_fair_prob(-110, -110) == pytest.approx(0.5)


def test_kelly_errors_and_exposure_state() -> None:
    from ncaa_quant.betting.kelly import ExposureState, KellyError

    with pytest.raises(KellyError):
        full_kelly(1.5, 100)
    with pytest.raises(KellyError):
        recommended_stake(0.6, 100, bankroll=0.0, config=BettingConfig())
    state = ExposureState()
    nxt = state.with_bet(["A", "B"], 0.01)
    assert nxt.weekly_total == pytest.approx(0.01)
    assert nxt.team_total("A") == pytest.approx(0.01)
    assert nxt.team_total("B") == pytest.approx(0.01)


def test_filter_exposure_and_ev() -> None:
    cfg = BettingConfig(max_weekly_exposure=0.05, max_exposure_per_team=0.02)
    weekly = evaluate_filters(
        _candidate(),
        cfg,
        weekly_exposure_so_far=0.049,
        proposed_stake_fraction=0.01,
    )
    assert FilterReason.MAX_WEEKLY_EXPOSURE in weekly.reasons
    team = evaluate_filters(
        _candidate(team_ids=("T1",)),
        cfg,
        team_exposure_so_far={"T1": 0.019},
        proposed_stake_fraction=0.01,
    )
    assert FilterReason.MAX_TEAM_EXPOSURE in team.reasons
    ev = evaluate_filters(_candidate(expected_value=-0.01), cfg)
    assert FilterReason.NON_POSITIVE_EV in ev.reasons


def test_settle_week_skips_missing_and_wrong_week() -> None:
    from ncaa_quant.betting.clv import settle_week

    rec_ok = RecommendationRecord(
        recommendation_id="r1",
        game_id="g1",
        season=2024,
        week=5,
        side="HOME",
        bet_side_american=-110,
        bet_other_american=-110,
        recommended_at=datetime(2024, 10, 1, tzinfo=UTC),
        close_definition="odds_api_consensus",
    )
    rec_other_week = RecommendationRecord(
        recommendation_id="r2",
        game_id="g2",
        season=2024,
        week=6,
        side="AWAY",
        bet_side_american=-110,
        bet_other_american=-110,
        recommended_at=datetime(2024, 10, 8, tzinfo=UTC),
        close_definition="odds_api_consensus",
    )
    settled, report = settle_week(
        [rec_ok, rec_other_week],
        {},  # missing close for r1
        season=2024,
        week=5,
    )
    assert settled == []
    assert report.n_bets == 0
    assert np.isnan(report.mean_clv)
