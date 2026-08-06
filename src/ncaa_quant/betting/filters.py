"""Bet filters from DESIGN §12 / ``configs/betting.yaml``.

All thresholds are configurable via :class:`~ncaa_quant.config.BettingConfig`.
Defaults match the repo ``configs/betting.yaml``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from ncaa_quant.config import BettingConfig

MarketKind = Literal["side", "total"]


class FilterReason(StrEnum):
    """Why a candidate was rejected (empty if accepted)."""

    PASS = "pass"
    EDGE_TOO_SMALL = "edge_too_small"
    STALE_INPUTS = "stale_inputs"
    QB_STATUS_UNKNOWN = "qb_status_unknown"
    MODEL_MARKET_DISAGREE = "model_market_disagree"
    MAX_BETS_PER_WEEK = "max_bets_per_week"
    MAX_WEEKLY_EXPOSURE = "max_weekly_exposure"
    MAX_TEAM_EXPOSURE = "max_team_exposure"
    NON_POSITIVE_EV = "non_positive_ev"


@dataclass(frozen=True, slots=True)
class BetCandidate:
    """One potential bet after edge/EV computation."""

    game_id: str
    market: MarketKind
    edge: float
    expected_value: float
    is_stale: bool
    qb_status_known: bool
    is_bowl: bool
    model_market_residual_points: float
    """``|model_line − market_line|`` in points (spread or total)."""

    team_ids: tuple[str, ...] = ()
    """Teams whose exposure this bet counts against (usually one or both)."""


@dataclass(frozen=True, slots=True)
class FilterResult:
    """Accept / reject decision with reasons."""

    accepted: bool
    reasons: tuple[FilterReason, ...] = field(default_factory=tuple)
    min_edge_applied: float = 0.0


def _min_edge_for(candidate: BetCandidate, config: BettingConfig) -> float:
    base = (
        float(config.min_edge_sides)
        if candidate.market == "side"
        else float(config.min_edge_totals)
    )
    if candidate.is_bowl:
        return base * float(config.bowl_edge_multiplier)
    return base


def evaluate_filters(
    candidate: BetCandidate,
    config: BettingConfig,
    *,
    bets_this_week: int = 0,
    weekly_exposure_so_far: float = 0.0,
    team_exposure_so_far: dict[str, float] | None = None,
    proposed_stake_fraction: float = 0.0,
) -> FilterResult:
    """Apply all §12 filters; return pass/fail with reason codes.

    Parameters
    ----------
    candidate:
        Bet under consideration.
    config:
        Thresholds from ``configs/betting.yaml``.
    bets_this_week:
        Count of already-accepted recommendations this week.
    weekly_exposure_so_far / team_exposure_so_far:
        Current exposure fractions of bankroll.
    proposed_stake_fraction:
        Stake that would be added if accepted (for exposure checks).
    """
    reasons: list[FilterReason] = []
    team_exp = team_exposure_so_far or {}
    min_edge = _min_edge_for(candidate, config)

    if candidate.edge < min_edge:
        reasons.append(FilterReason.EDGE_TOO_SMALL)

    if config.no_bet_on_stale and candidate.is_stale:
        reasons.append(FilterReason.STALE_INPUTS)

    if config.no_bet_on_qb_unknown and not candidate.qb_status_known:
        reasons.append(FilterReason.QB_STATUS_UNKNOWN)

    if candidate.model_market_residual_points > float(config.min_model_market_agreement):
        reasons.append(FilterReason.MODEL_MARKET_DISAGREE)

    if bets_this_week >= int(config.max_bets_per_week):
        reasons.append(FilterReason.MAX_BETS_PER_WEEK)

    if weekly_exposure_so_far + proposed_stake_fraction > float(config.max_weekly_exposure) + 1e-15:
        reasons.append(FilterReason.MAX_WEEKLY_EXPOSURE)

    for tid in candidate.team_ids:
        current = float(team_exp.get(tid, 0.0))
        if current + proposed_stake_fraction > float(config.max_exposure_per_team) + 1e-15:
            reasons.append(FilterReason.MAX_TEAM_EXPOSURE)
            break

    if candidate.expected_value <= 0.0:
        reasons.append(FilterReason.NON_POSITIVE_EV)

    if reasons:
        return FilterResult(accepted=False, reasons=tuple(reasons), min_edge_applied=min_edge)
    return FilterResult(
        accepted=True,
        reasons=(FilterReason.PASS,),
        min_edge_applied=min_edge,
    )


def default_betting_config() -> BettingConfig:
    """Defaults matching ``configs/betting.yaml`` / :class:`BettingConfig`."""
    return BettingConfig()
