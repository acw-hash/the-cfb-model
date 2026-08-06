"""Closing-line value (CLV) tracking and weekly settlement (DESIGN §2.7, §12).

CLV is computed in **proportionally de-vigged probability space** on the bet
side:

    CLV = fair_prob(close)_side − fair_prob(bet_line)_side

Positive CLV means the closing market assigned a higher fair probability to our
side than the price we bet — i.e. the line moved in our favor (we got a better
number than close).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import numpy as np

from ncaa_quant.betting.devig import DEFAULT_DEVIG_METHOD, DevigMethod, fair_prob_on_side

CloseDefinition = Literal["odds_api_consensus", "cfbd_close"]


@dataclass(frozen=True, slots=True)
class RecommendationRecord:
    """Stored fields for every bet recommendation (pre-settlement)."""

    recommendation_id: str
    game_id: str
    season: int
    week: int
    side: str
    """Human label for the bet side (e.g. team id, 'over', 'under')."""

    bet_side_american: float
    bet_other_american: float
    """Two-way American prices at recommendation time (our side + opposite)."""

    recommended_at: datetime
    close_definition: CloseDefinition
    """Which close definition will be used at settlement (§2.7 / §3.4)."""


@dataclass(frozen=True, slots=True)
class SettledBet:
    """Recommendation plus close prices and CLV."""

    recommendation: RecommendationRecord
    close_side_american: float
    close_other_american: float
    p_bet_fair: float
    p_close_fair: float
    clv: float
    """``p_close_fair − p_bet_fair`` on the bet side (proportional de-vig)."""


@dataclass(frozen=True, slots=True)
class WeeklyClvReport:
    """Weekly CLV settlement summary."""

    season: int
    week: int
    n_bets: int
    mean_clv: float
    pct_positive: float
    clv_values: tuple[float, ...]
    ci_low: float
    ci_high: float
    """Normal approx 95% CI on the mean (or nan if n < 2)."""

    close_definition: CloseDefinition


class ClvError(ValueError):
    """Raised when CLV cannot be computed honestly."""


def compute_clv(
    bet_side_american: float,
    bet_other_american: float,
    close_side_american: float,
    close_other_american: float,
    *,
    method: DevigMethod = DEFAULT_DEVIG_METHOD,
    bet_line_source_row_id: str | None = None,
    close_line_source_row_id: str | None = None,
) -> tuple[float, float, float]:
    """CLV in de-vigged probability space on the bet side.

    Returns ``(p_bet_fair, p_close_fair, clv)`` where
    ``clv = p_close_fair − p_bet_fair``.

    DESIGN §2.7 specifies proportional de-vig for the CLV label; the default
    ``method`` is therefore proportional. Other methods are available for
    sensitivity checks only.

    Raises
    ------
    ClvError
        When bet-time and closing prices resolve to the same source row
        (CLV identically zero by construction — §7.2 item 7 forbids this).
    """
    if (
        bet_line_source_row_id is not None
        and close_line_source_row_id is not None
        and bet_line_source_row_id == close_line_source_row_id
    ):
        msg = (
            "CLV degeneracy: bet-time and closing prices resolve to the same "
            f"source row ({bet_line_source_row_id}); refusing to report CLV≡0 "
            "by construction"
        )
        raise ClvError(msg)
    # Identical American quotes on both legs with no distinct row ids still
    # collapse CLV to zero when the instrument is the same price vector.
    if (
        float(bet_side_american) == float(close_side_american)
        and float(bet_other_american) == float(close_other_american)
        and bet_line_source_row_id is None
        and close_line_source_row_id is None
    ):
        # Ambiguous without provenance — callers that know row identity must
        # pass ids. Price-identity alone is not always degenerate (same number,
        # different books/times). No raise here.
        pass

    p_bet = fair_prob_on_side(bet_side_american, bet_other_american, method=method)
    p_close = fair_prob_on_side(close_side_american, close_other_american, method=method)
    return float(p_bet), float(p_close), float(p_close - p_bet)


def assert_distinct_line_sources(
    *,
    bet_source_row_id: str,
    close_source_row_id: str,
) -> None:
    """Raise :class:`ClvError` when bet-time and close share one source row."""
    if bet_source_row_id == close_source_row_id:
        msg = (
            "CLV degeneracy: bet-time and closing prices resolve to the same "
            f"source row ({bet_source_row_id}); refusing to report CLV≡0 "
            "by construction"
        )
        raise ClvError(msg)


def settle_recommendation(
    recommendation: RecommendationRecord,
    close_side_american: float,
    close_other_american: float,
    *,
    method: DevigMethod = DEFAULT_DEVIG_METHOD,
) -> SettledBet:
    """Attach consensus close and CLV to a stored recommendation."""
    p_bet, p_close, clv = compute_clv(
        recommendation.bet_side_american,
        recommendation.bet_other_american,
        close_side_american,
        close_other_american,
        method=method,
    )
    return SettledBet(
        recommendation=recommendation,
        close_side_american=float(close_side_american),
        close_other_american=float(close_other_american),
        p_bet_fair=p_bet,
        p_close_fair=p_close,
        clv=clv,
    )


def settle_week(
    recommendations: Sequence[RecommendationRecord],
    closes: dict[str, tuple[float, float]],
    *,
    season: int,
    week: int,
    method: DevigMethod = DEFAULT_DEVIG_METHOD,
    close_definition: CloseDefinition = "odds_api_consensus",
) -> tuple[list[SettledBet], WeeklyClvReport]:
    """Weekly settlement job: join closes, compute CLVs, summarize.

    Parameters
    ----------
    recommendations:
        Stored recommendations for this week (must include line-at-bet).
    closes:
        Map ``recommendation_id → (close_side_american, close_other_american)``.
    """
    settled: list[SettledBet] = []
    for rec in recommendations:
        if rec.season != season or rec.week != week:
            continue
        if rec.recommendation_id not in closes:
            continue
        side_am, other_am = closes[rec.recommendation_id]
        settled.append(settle_recommendation(rec, side_am, other_am, method=method))

    clvs = np.asarray([s.clv for s in settled], dtype=float)
    n = int(clvs.size)
    if n == 0:
        report = WeeklyClvReport(
            season=season,
            week=week,
            n_bets=0,
            mean_clv=float("nan"),
            pct_positive=float("nan"),
            clv_values=(),
            ci_low=float("nan"),
            ci_high=float("nan"),
            close_definition=close_definition,
        )
        return settled, report

    mean = float(np.mean(clvs))
    pct_pos = float(np.mean(clvs > 0.0))
    if n >= 2:
        se = float(np.std(clvs, ddof=1) / np.sqrt(n))
        ci_low, ci_high = mean - 1.96 * se, mean + 1.96 * se
    else:
        ci_low = ci_high = float("nan")

    report = WeeklyClvReport(
        season=season,
        week=week,
        n_bets=n,
        mean_clv=mean,
        pct_positive=pct_pos,
        clv_values=tuple(float(x) for x in clvs.tolist()),
        ci_low=float(ci_low),
        ci_high=float(ci_high),
        close_definition=close_definition,
    )
    return settled, report
