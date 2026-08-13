"""Human gates: model promotion and bet confirmation (DESIGN §9.8, §16 item 3)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from ncaa_quant.betting.clv import RecommendationRecord
from ncaa_quant.config import AppConfig, load_config
from ncaa_quant.utils.logging import get_logger

log = get_logger(__name__)

ConfirmationStatus = Literal["pending", "confirmed", "void"]


class VoidReason(StrEnum):
    """Why a bet confirmation was voided."""

    LINE_MOVED = "line_moved_past_threshold"
    STALE_INPUTS = "stale_inputs"
    MANUAL = "manual_void"


@dataclass(frozen=True, slots=True)
class BetConfirmationView:
    """Human-readable bet confirmation row (§16 item 3)."""

    recommendation_id: str
    game_id: str
    side: str
    market: str
    line_at_recommendation: float | None
    current_line: float | None
    line_move_points: float | None
    status: ConfirmationStatus
    void_reason: VoidReason | None
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommendation_id": self.recommendation_id,
            "game_id": self.game_id,
            "side": self.side,
            "market": self.market,
            "line_at_recommendation": self.line_at_recommendation,
            "current_line": self.current_line,
            "line_move_points": self.line_move_points,
            "status": self.status,
            "void_reason": self.void_reason,
            "message": self.message,
        }


def line_move_points(
    line_at_recommendation: float | None,
    current_line: float | None,
) -> float | None:
    """Absolute line move in points; None when either line is missing."""
    if line_at_recommendation is None or current_line is None:
        return None
    return abs(float(current_line) - float(line_at_recommendation))


def evaluate_bet_confirmation(
    recommendation: RecommendationRecord,
    *,
    current_line: float | None,
    is_stale: bool = False,
    config: AppConfig | None = None,
) -> BetConfirmationView:
    """Evaluate whether a recommendation is still confirmable.

    §16 item 3: void if the line moved past its threshold. Spread/total bets
    compare ``bet_line`` to ``current_line``; moneylines skip line-move void.
    """
    cfg = config or load_config()
    threshold = float(cfg.pipeline.bet_line_move_void_points)
    rec_line = recommendation.bet_line
    market = recommendation.market
    move = line_move_points(rec_line, current_line)

    if is_stale:
        return BetConfirmationView(
            recommendation_id=recommendation.recommendation_id,
            game_id=recommendation.game_id,
            side=recommendation.side,
            market=market,
            line_at_recommendation=rec_line,
            current_line=current_line,
            line_move_points=move,
            status="void",
            void_reason=VoidReason.STALE_INPUTS,
            message="VOID — inputs are STALE; no bet permitted.",
        )

    if (
        market in ("spread", "total")
        and rec_line is not None
        and current_line is not None
        and move is not None
        and move > threshold + 1e-9
    ):
        return BetConfirmationView(
            recommendation_id=recommendation.recommendation_id,
            game_id=recommendation.game_id,
            side=recommendation.side,
            market=market,
            line_at_recommendation=rec_line,
            current_line=current_line,
            line_move_points=move,
            status="void",
            void_reason=VoidReason.LINE_MOVED,
            message=(
                f"VOID — line moved {move:.1f} pts (threshold {threshold:.1f}): "
                f"recommended {rec_line:+.1f}, current {current_line:+.1f}."
            ),
        )

    line_display = f"{rec_line:+.1f}" if rec_line is not None else "ML"
    cur_display = f"{current_line:+.1f}" if current_line is not None else "ML"
    return BetConfirmationView(
        recommendation_id=recommendation.recommendation_id,
        game_id=recommendation.game_id,
        side=recommendation.side,
        market=market,
        line_at_recommendation=rec_line,
        current_line=current_line,
        line_move_points=move,
        status="pending",
        void_reason=None,
        message=(
            f"PENDING — line at recommendation: {line_display}; "
            f"current: {cur_display}. Confirm manually after roster/news check."
        ),
    )


def format_bet_confirmation_report(views: list[BetConfirmationView]) -> str:
    """Render the bet-confirmation queue for human review."""
    lines = ["=== Bet confirmation queue ==="]
    for v in views:
        lines.append(
            f"{v.recommendation_id} {v.game_id} {v.side} "
            f"rec_line={v.line_at_recommendation} cur_line={v.current_line} "
            f"status={v.status} — {v.message}"
        )
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class PromotionGateDecision:
    """Result of the model promotion human gate (§9.8 Mon 06:00)."""

    candidate_version: str
    approved: bool
    force: bool
    reason: str
    comparison_report_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_version": self.candidate_version,
            "approved": self.approved,
            "force": self.force,
            "reason": self.reason,
            "comparison_report_path": self.comparison_report_path,
        }


def evaluate_promotion_gate(
    *,
    candidate_version: str,
    gate_passed: bool,
    manual_approve: bool,
    force: bool = False,
    comparison_report_path: str | None = None,
) -> PromotionGateDecision:
    """Human gate wrapper — promotion requires gate pass AND manual approval.

    ``force=True`` is the explicit human override path from Task 22 registry.
    """
    if force:
        return PromotionGateDecision(
            candidate_version=candidate_version,
            approved=True,
            force=True,
            reason="human force override",
            comparison_report_path=comparison_report_path,
        )
    if not gate_passed:
        return PromotionGateDecision(
            candidate_version=candidate_version,
            approved=False,
            force=False,
            reason="automatic gate failed — candidate archived",
            comparison_report_path=comparison_report_path,
        )
    if not manual_approve:
        return PromotionGateDecision(
            candidate_version=candidate_version,
            approved=False,
            force=False,
            reason="awaiting manual approval (Mon 06:00 gate)",
            comparison_report_path=comparison_report_path,
        )
    log.info("promotion_gate_approved", candidate=candidate_version)
    return PromotionGateDecision(
        candidate_version=candidate_version,
        approved=True,
        force=False,
        reason="gate passed and manually approved",
        comparison_report_path=comparison_report_path,
    )
