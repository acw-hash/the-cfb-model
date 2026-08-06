"""Fractional Kelly staking with hard-coded safety caps (DESIGN §12).

Production staking uses quarter-Kelly (25% of full) and never more than 1.5% of
bankroll per bet. Those ceilings are enforced in code via module constants;
configuration may only tighten them, never loosen past the hard caps.

Full Kelly is exposed for reporting only — :func:`recommended_stake` never
returns a full-Kelly fraction.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ncaa_quant.betting.devig import american_to_decimal
from ncaa_quant.config import BettingConfig

# Hard caps from DESIGN §12 — not overridable above these values.
HARD_MAX_STAKE_PCT: float = 0.015
HARD_MAX_KELLY_FRACTION: float = 0.25


class KellyError(ValueError):
    """Invalid Kelly / staking inputs."""


@dataclass(frozen=True, slots=True)
class StakeResult:
    """Staking decision for one bet candidate."""

    full_kelly_fraction: float
    """Uncapped full-Kelly fraction of bankroll (reporting only)."""

    fractional_kelly_before_cap: float
    """``kelly_fraction * full_kelly`` before exposure / hard stake caps."""

    stake_fraction: float
    """Fraction of bankroll actually recommended (after all caps)."""

    stake_amount: float
    """Absolute stake in bankroll currency units."""

    capped_by_hard_max: bool
    capped_by_config: bool
    capped_by_weekly: bool
    capped_by_team: bool


def full_kelly(p_win: float, american_odds: float) -> float:
    """Full Kelly fraction of bankroll for a binary bet at American odds.

    ``f* = (b p − q) / b`` where ``b = decimal − 1``, ``q = 1 − p``.
    Negative edges return 0 (never bet). For reporting — not for staking.
    """
    p = float(p_win)
    if not 0.0 <= p <= 1.0:
        raise KellyError(f"p_win must be in [0, 1], got {p}")
    decimal = american_to_decimal(american_odds)
    b = decimal - 1.0
    if b <= 0.0:
        raise KellyError(f"decimal odds must be > 1, got {decimal}")
    q = 1.0 - p
    f_star = (b * p - q) / b
    return float(max(0.0, f_star))


def _effective_kelly_fraction(config: BettingConfig) -> float:
    """Config fraction clamped to the hard max (never full Kelly for staking)."""
    return min(float(config.kelly_fraction), HARD_MAX_KELLY_FRACTION)


def _effective_max_stake_pct(config: BettingConfig) -> float:
    """Config stake cap clamped to the hard 1.5% ceiling."""
    return min(float(config.max_stake_pct), HARD_MAX_STAKE_PCT)


def recommended_stake(
    p_win: float,
    american_odds: float,
    bankroll: float,
    config: BettingConfig,
    *,
    weekly_exposure_so_far: float = 0.0,
    team_exposure_so_far: float = 0.0,
) -> StakeResult:
    """Fractional Kelly stake with hard + config + exposure caps.

    Parameters
    ----------
    p_win:
        Calibrated win probability for the bet side.
    american_odds:
        Actual available American price.
    bankroll:
        Current bankroll (currency units); stake_amount = fraction * bankroll.
    config:
        Betting thresholds from ``configs/betting.yaml``.
    weekly_exposure_so_far:
        Sum of stake fractions already committed this week (0–1 of bankroll).
    team_exposure_so_far:
        Sum of stake fractions already on this team this week.
    """
    if bankroll <= 0.0:
        raise KellyError(f"bankroll must be positive, got {bankroll}")
    if weekly_exposure_so_far < 0.0 or team_exposure_so_far < 0.0:
        raise KellyError("exposures so far must be non-negative")

    f_full = full_kelly(p_win, american_odds)
    frac = _effective_kelly_fraction(config)
    before_cap = frac * f_full

    # Config stake ceiling is itself clamped to the hard 1.5% — config alone
    # can never authorize a larger stake than HARD_MAX_STAKE_PCT.
    hard_cap = HARD_MAX_STAKE_PCT
    config_cap = _effective_max_stake_pct(config)
    weekly_room = max(0.0, float(config.max_weekly_exposure) - weekly_exposure_so_far)
    team_room = max(0.0, float(config.max_exposure_per_team) - team_exposure_so_far)

    stake_frac = min(before_cap, config_cap, hard_cap, weekly_room, team_room)
    stake_frac = max(0.0, float(stake_frac))

    # Flag which constraints bind (a stake can hit several at once).
    capped_by_config = before_cap > config_cap + 1e-15 and abs(stake_frac - config_cap) < 1e-12
    capped_by_hard = before_cap > hard_cap + 1e-15 and stake_frac <= hard_cap + 1e-15
    capped_by_weekly = before_cap > weekly_room + 1e-15 and abs(stake_frac - weekly_room) < 1e-12
    capped_by_team = before_cap > team_room + 1e-15 and abs(stake_frac - team_room) < 1e-12

    return StakeResult(
        full_kelly_fraction=float(f_full),
        fractional_kelly_before_cap=float(before_cap),
        stake_fraction=stake_frac,
        stake_amount=float(stake_frac * bankroll),
        capped_by_hard_max=capped_by_hard,
        capped_by_config=capped_by_config,
        capped_by_weekly=capped_by_weekly,
        capped_by_team=capped_by_team,
    )


@dataclass(frozen=True, slots=True)
class ExposureState:
    """Running weekly / per-team exposure tracker (fractions of bankroll)."""

    weekly_total: float = 0.0
    per_team: dict[str, float] | None = None

    def __post_init__(self) -> None:
        if self.per_team is None:
            object.__setattr__(self, "per_team", {})

    def team_total(self, team_id: str) -> float:
        assert self.per_team is not None
        return float(self.per_team.get(team_id, 0.0))

    def with_bet(self, team_ids: Sequence[str], stake_fraction: float) -> ExposureState:
        """Return a new state after allocating ``stake_fraction`` to ``team_ids``."""
        assert self.per_team is not None
        updated = dict(self.per_team)
        for tid in team_ids:
            updated[tid] = float(updated.get(tid, 0.0) + stake_fraction)
        return ExposureState(
            weekly_total=float(self.weekly_total + stake_fraction),
            per_team=updated,
        )
