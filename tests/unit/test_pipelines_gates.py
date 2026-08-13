"""Unit tests for bet confirmation and promotion gates."""

from __future__ import annotations

from datetime import UTC, datetime

from ncaa_quant.betting.clv import RecommendationRecord
from ncaa_quant.config import AppConfig, PipelineConfig
from ncaa_quant.pipelines.gates import (
    evaluate_bet_confirmation,
    evaluate_promotion_gate,
    format_bet_confirmation_report,
)


def _rec(*, bet_line: float | None = -3.5) -> RecommendationRecord:
    return RecommendationRecord(
        recommendation_id="r1",
        game_id="g1",
        season=2024,
        week=5,
        side="HOME",
        bet_side_american=-110,
        bet_other_american=-110,
        recommended_at=datetime(2024, 10, 1, tzinfo=UTC),
        close_definition="odds_api_consensus",
        market="spread",
        bet_line=bet_line,
        bet_line_source_row_id="snap:1",
    )


def test_bet_confirmation_void_on_line_move() -> None:
    cfg = AppConfig(pipeline=PipelineConfig(bet_line_move_void_points=0.5))
    view = evaluate_bet_confirmation(_rec(bet_line=-3.5), current_line=-4.5, config=cfg)
    assert view.status == "void"
    assert view.line_at_recommendation == -3.5
    assert view.current_line == -4.5
    assert "VOID" in view.message


def test_bet_confirmation_pending_when_line_stable() -> None:
    cfg = AppConfig(pipeline=PipelineConfig(bet_line_move_void_points=0.5))
    view = evaluate_bet_confirmation(_rec(bet_line=-3.5), current_line=-3.5, config=cfg)
    assert view.status == "pending"
    assert "line at recommendation" in view.message.lower()


def test_bet_confirmation_void_on_stale() -> None:
    cfg = AppConfig()
    view = evaluate_bet_confirmation(_rec(), current_line=-3.5, is_stale=True, config=cfg)
    assert view.status == "void"


def test_bet_confirmation_report_format() -> None:
    cfg = AppConfig()
    views = [
        evaluate_bet_confirmation(_rec(), current_line=-3.5, config=cfg),
    ]
    report = format_bet_confirmation_report(views)
    assert "Bet confirmation queue" in report
    assert "rec_line=-3.5" in report


def test_promotion_gate_requires_manual_approve() -> None:
    d = evaluate_promotion_gate(
        candidate_version="v2",
        gate_passed=True,
        manual_approve=False,
    )
    assert not d.approved
    d2 = evaluate_promotion_gate(
        candidate_version="v2",
        gate_passed=True,
        manual_approve=True,
    )
    assert d2.approved


def test_promotion_gate_force_override() -> None:
    d = evaluate_promotion_gate(
        candidate_version="v2",
        gate_passed=False,
        manual_approve=False,
        force=True,
    )
    assert d.approved and d.force
