"""Closing-line value (CLV) tracking and weekly settlement (DESIGN §2.7, §12).

CLV is computed in **proportionally de-vigged probability space** on the bet
side, and two things about *which* prices enter that difference are load-bearing:

**Settlement book.** CLV settles against the closing price at the **same book**
whose price was bet. Bets are placed at the best price across books (§12), so
settling against a consensus close would credit line-shopping as if it were
forecasting skill — a skill-free system with several books would show positive
CLV. Rows that cannot be settled same-book are flagged
``clv_settlement=fallback_consensus`` and never pooled with ``same_book`` rows.

**Same line.** For spreads and totals the *line* moves, not just the price. A
ticket on -6.5 is not the same instrument as the -7 the book closed at, so
de-vigging the closing price and differencing it against the bet price prices the
wrong ticket. The error is exactly the push mass between the two numbers, which
is largest at the key numbers (3, 7) where CLV signal concentrates. Both
probabilities must therefore refer to **the bettor's actual ticket line**; see
:func:`translate_close_to_bet_line` for the translation ladder and ``clv_method``.

Moneylines have no line to translate and settle as ``same_line``.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import numpy as np

from ncaa_quant.betting.devig import DEFAULT_DEVIG_METHOD, DevigMethod, fair_prob_on_side

CloseDefinition = Literal["odds_api_consensus", "cfbd_close"]

Market = Literal["moneyline", "spread", "total"]
TotalSide = Literal["over", "under"]

ClvSettlement = Literal["same_book", "fallback_consensus"]
"""Which book's close settled the row. Never pool the two (§2.7, §7.2 item 7)."""

ClvMethod = Literal["same_line", "alt_line_price", "model_dist", "line_units"]
"""How the close was expressed at the bet line. ``line_units`` is not a probability."""

PROBABILITY_VALUED_METHODS: frozenset[str] = frozenset(
    {"same_line", "alt_line_price", "model_dist"}
)
"""Methods whose CLV is a probability difference and may enter §1.6's criterion."""

CoverProbFn = Callable[[float], float]
"""Model probability that the **bet side** covers, as a function of the line."""


class ClvError(ValueError):
    """Raised when CLV cannot be computed honestly."""


# ---------------------------------------------------------------------------
# Model cover probabilities (used for the model_dist translation)
# ---------------------------------------------------------------------------


def _pmf_items(pmf: Mapping[float, float]) -> tuple[np.ndarray, np.ndarray]:
    if not pmf:
        raise ClvError("empty distribution")
    xs = np.asarray(list(pmf.keys()), dtype=float)
    ps = np.asarray(list(pmf.values()), dtype=float)
    if not np.all(np.isfinite(xs)) or not np.all(np.isfinite(ps)):
        raise ClvError("distribution contains non-finite values")
    if np.any(ps < 0.0):
        raise ClvError("distribution has negative mass")
    total = float(ps.sum())
    if not math.isclose(total, 1.0, abs_tol=1e-6):
        raise ClvError(f"distribution must sum to 1, got {total}")
    return xs, ps


def spread_cover_prob(pmf: Mapping[float, float], line: float) -> float:
    """P(bet side covers ``line``) from a discrete margin distribution.

    ``pmf`` maps the **bet side's** margin (their points minus opponent's) to
    probability; ``line`` is the number on the bet side's ticket (-6.5 for a
    favourite laying 6.5, +3.5 for a dog taking 3.5).

    Cover is ``margin + line > 0`` and push is ``margin + line == 0``. Pushes
    count as half, the standard convention for pricing a spread as a two-way
    probability: stake is returned, so the ticket is worth half a win.
    """
    xs, ps = _pmf_items(pmf)
    shifted = xs + float(line)
    win = float(ps[shifted > 0.0].sum())
    push = float(ps[shifted == 0.0].sum())
    return win + 0.5 * push


def total_cover_prob(pmf: Mapping[float, float], line: float, side: TotalSide) -> float:
    """P(bet side covers ``line``) from a discrete total-points distribution.

    Over covers above the line, under covers below it; pushes count as half, as
    in :func:`spread_cover_prob`.
    """
    xs, ps = _pmf_items(pmf)
    line_f = float(line)
    push = float(ps[xs == line_f].sum())
    if side == "over":
        return float(ps[xs > line_f].sum()) + 0.5 * push
    if side == "under":
        return float(ps[xs < line_f].sum()) + 0.5 * push
    raise ClvError(f"unknown total side: {side!r}")


def spread_cover_prob_fn(pmf: Mapping[float, float]) -> CoverProbFn:
    """Bind a margin distribution into a :data:`CoverProbFn`."""
    return lambda line: spread_cover_prob(pmf, line)


def total_cover_prob_fn(pmf: Mapping[float, float], side: TotalSide) -> CoverProbFn:
    """Bind a total distribution and bet side into a :data:`CoverProbFn`."""
    return lambda line: total_cover_prob(pmf, line, side)


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RecommendationRecord:
    """Stored fields for every bet recommendation (pre-settlement).

    Per §12, a recommendation must persist enough to settle honestly later: the
    book that priced it, the line on the ticket, the bet-time price, and the
    bet-time consensus price (for ``line_shopping_capture``).
    """

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

    book: str = ""
    """Book that supplied the bet price; settlement looks for this book's close."""

    market: Market = "moneyline"
    bet_line: float | None = None
    """Ticket line, signed for the bet side (spread) or the total number."""

    total_side: TotalSide | None = None
    """Required when ``market == 'total'`` to orient the line translation."""

    consensus_side_american: float | None = None
    consensus_other_american: float | None = None
    """Bet-time consensus prices, for ``line_shopping_capture`` (§2.7)."""

    n_books_available: int = 0
    """Books quoting at bet time; Task 21 stratifies shopping value by this."""

    bet_line_source_row_id: str | None = None
    """Stable id of the snapshot / CFBD row that priced this bet at recommendation time.

    Required for settlement: :func:`settle` refuses CLV when this or the close
    ``source_row_id`` is missing, and raises when the two resolve to the same row
    (§7.2 item 7 / Task 23-FIX P0-2).
    """


@dataclass(frozen=True, slots=True)
class ClosingQuote:
    """A closing two-way quote, optionally with an alternate-line price."""

    side_american: float
    other_american: float
    book: str = ""
    line: float | None = None
    """Line the book actually closed at (None for moneyline)."""

    alt_side_american: float | None = None
    alt_other_american: float | None = None
    """Closing price at the *bet* line, when the book quoted that alt line."""

    source_row_id: str | None = None


@dataclass(frozen=True, slots=True)
class SettledBet:
    """Recommendation plus close prices, translation method, and CLV."""

    recommendation: RecommendationRecord
    close_side_american: float
    close_other_american: float
    p_bet_fair: float
    p_close_fair: float
    clv: float
    """``p_close_fair − p_bet_fair`` on the bet side, both at the ticket line.

    ``nan`` when ``clv_method == 'line_units'`` (no probability is available).
    """

    clv_settlement: ClvSettlement = "same_book"
    clv_method: ClvMethod = "same_line"
    clv_line_units: float = float("nan")
    """Points the close moved toward the bet. Positive means we hold the better number."""

    line_shopping_capture: float = float("nan")
    """Bet-time best-vs-consensus fair-probability gap. Not CLV; never pooled into it.

    Negative when we bought our side more cheaply than consensus — see ADR 0006.
    """

    close_line: float | None = None

    @property
    def is_headline(self) -> bool:
        """True when this row may enter the §1.6 primary CLV criterion."""
        return self.clv_settlement == "same_book" and self.clv_method in PROBABILITY_VALUED_METHODS


@dataclass(frozen=True, slots=True)
class ClvBlock:
    """CLV summary for one stratum. Strata are never pooled."""

    label: str
    n_bets: int
    mean_clv: float
    pct_positive: float
    clv_values: tuple[float, ...]
    ci_low: float
    ci_high: float


@dataclass(frozen=True, slots=True)
class WeeklyClvReport:
    """Weekly CLV settlement summary, stratified per §2.7.

    ``n_bets`` / ``mean_clv`` / ``pct_positive`` are the **headline** figures and
    cover same-book, probability-valued rows only. Fallback-consensus and
    line-unit rows are reported in their own blocks and deliberately excluded, so
    a reader cannot accidentally quote a pooled number.
    """

    season: int
    week: int
    n_bets: int
    mean_clv: float
    pct_positive: float
    clv_values: tuple[float, ...]
    ci_low: float
    ci_high: float
    close_definition: CloseDefinition
    same_book: ClvBlock | None = None
    fallback_consensus: ClvBlock | None = None
    line_units: ClvBlock | None = None
    mean_line_shopping_capture: float = float("nan")


# ---------------------------------------------------------------------------
# Same-line CLV primitive
# ---------------------------------------------------------------------------


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
    """CLV in de-vigged probability space when both prices refer to one line.

    Returns ``(p_bet_fair, p_close_fair, clv)`` where
    ``clv = p_close_fair − p_bet_fair``.

    This is the primitive for moneylines and for sides/totals whose line did not
    move. It is **not** valid on its own against a moved spread or total line —
    use :func:`settle` so the close is translated to the ticket line first.

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


# ---------------------------------------------------------------------------
# Line translation and shopping capture
# ---------------------------------------------------------------------------


def line_units_clv(
    *,
    market: Market,
    bet_line: float,
    close_line: float,
    total_side: TotalSide | None = None,
) -> float:
    """Points of closing movement toward the bet (positive = we hold value).

    A spread ticket is better than the close when its number is higher on the
    bet side (-6.5 beats -7; +3.5 beats +3). An over is better on a lower total,
    an under on a higher one.
    """
    if market == "spread":
        return float(bet_line) - float(close_line)
    if market == "total":
        if total_side == "over":
            return float(close_line) - float(bet_line)
        if total_side == "under":
            return float(bet_line) - float(close_line)
        raise ClvError("total bets require total_side to orient line movement")
    raise ClvError(f"{market!r} has no line to translate")


def translate_close_to_bet_line(
    recommendation: RecommendationRecord,
    close: ClosingQuote,
    *,
    model_cover_prob: CoverProbFn | None = None,
    method: DevigMethod = DEFAULT_DEVIG_METHOD,
) -> tuple[ClvMethod, float, float]:
    """Express the closing market at the bet's line.

    Returns ``(clv_method, p_close_at_bet_line, clv_line_units)``. The
    probability is ``nan`` for ``line_units``; the line-unit value is ``nan``
    when no translation was needed.

    Ladder per §2.7, in priority order:

    1. ``alt_line_price`` — the book's own closing price at the bet line. Most
       direct: it is a real quote for the actual ticket.
    2. ``model_dist`` — price the line difference with the model's predictive
       distribution and shift the closing probability by it. The market supplies
       the level, the model only supplies the increment between two numbers.
    3. ``line_units`` — no probability is defensible, so report movement in
       points and keep it out of probability-space aggregates.
    """
    rec = recommendation
    p_close_at_close_line = fair_prob_on_side(
        close.side_american, close.other_american, method=method
    )

    if rec.market == "moneyline" or rec.bet_line is None or close.line is None:
        return "same_line", float(p_close_at_close_line), float("nan")

    if float(close.line) == float(rec.bet_line):
        return "same_line", float(p_close_at_close_line), 0.0

    units = line_units_clv(
        market=rec.market,
        bet_line=float(rec.bet_line),
        close_line=float(close.line),
        total_side=rec.total_side,
    )

    if close.alt_side_american is not None and close.alt_other_american is not None:
        p_alt = fair_prob_on_side(close.alt_side_american, close.alt_other_american, method=method)
        return "alt_line_price", float(p_alt), float(units)

    if model_cover_prob is not None:
        shift = float(model_cover_prob(float(rec.bet_line))) - float(
            model_cover_prob(float(close.line))
        )
        p_translated = float(p_close_at_close_line) + shift
        if not 0.0 <= p_translated <= 1.0:
            raise ClvError(
                "line-translated closing probability left [0, 1] "
                f"({p_translated:.6f}); model distribution and closing price disagree "
                "too strongly to translate honestly"
            )
        return "model_dist", p_translated, float(units)

    return "line_units", float("nan"), float(units)


def compute_line_shopping_capture(
    recommendation: RecommendationRecord,
    *,
    method: DevigMethod = DEFAULT_DEVIG_METHOD,
) -> float:
    """``implied_prob(best@bet_time) − implied_prob(consensus@bet_time)`` (§2.7).

    Execution value from shopping books, measured entirely at bet time. It is
    reported next to CLV and never folded into it: shopping is real value, but it
    is not evidence that the model predicted anything.

    Sign, which reads backwards from the name: buying our side *cheaper* than
    consensus yields a **negative** value, because the book we bought from holds a
    lower fair view of our side than consensus does — which is precisely why its
    price was worth taking. The magnitude equals the CLV that consensus
    settlement would spuriously credit to a bet whose own book never moves. ADR
    0006 records this; the formula is left exactly as §2.7 specifies.
    """
    rec = recommendation
    if rec.consensus_side_american is None or rec.consensus_other_american is None:
        return float("nan")
    p_best = fair_prob_on_side(rec.bet_side_american, rec.bet_other_american, method=method)
    p_consensus = fair_prob_on_side(
        rec.consensus_side_american, rec.consensus_other_american, method=method
    )
    return float(p_best - p_consensus)


# ---------------------------------------------------------------------------
# Settlement
# ---------------------------------------------------------------------------


def _require_distinct_line_sources(
    recommendation: RecommendationRecord,
    close: ClosingQuote,
) -> tuple[str, str]:
    """Return ``(bet_id, close_id)`` or raise — never skip the §7.2 item 7 guard."""
    bet_id = recommendation.bet_line_source_row_id
    close_id = close.source_row_id
    if bet_id is None or str(bet_id).strip() == "":
        raise ClvError(
            f"recommendation {recommendation.recommendation_id!r} is missing "
            "bet_line_source_row_id; refusing to settle CLV without the source-row guard"
        )
    if close_id is None or str(close_id).strip() == "":
        raise ClvError(
            f"close for recommendation {recommendation.recommendation_id!r} is missing "
            "source_row_id; refusing to settle CLV without the source-row guard"
        )
    bet_s = str(bet_id)
    close_s = str(close_id)
    assert_distinct_line_sources(bet_source_row_id=bet_s, close_source_row_id=close_s)
    return bet_s, close_s


def settle(
    recommendation: RecommendationRecord,
    *,
    same_book_close: ClosingQuote | None = None,
    consensus_close: ClosingQuote | None = None,
    model_cover_prob: CoverProbFn | None = None,
    method: DevigMethod = DEFAULT_DEVIG_METHOD,
) -> SettledBet:
    """Settle one recommendation per §2.7.

    Prefers the close from the book that priced the bet. Falls back to consensus
    only when that book has no close, and marks the row so it stays out of
    headline aggregates.

    Every settlement threads bet-time and close ``source_row_id`` values into
    :func:`assert_distinct_line_sources` (and :func:`compute_clv` on
    ``same_line`` rows). Missing either id raises — the guard is never skipped.
    """
    rec = recommendation
    if same_book_close is not None:
        close = same_book_close
        settlement: ClvSettlement = "same_book"
        if close.book and rec.book and close.book != rec.book:
            raise ClvError(
                f"same-book settlement requires matching books: bet at {rec.book!r}, "
                f"close from {close.book!r}"
            )
    elif consensus_close is not None:
        close = consensus_close
        settlement = "fallback_consensus"
    else:
        raise ClvError(
            f"no close available for recommendation {rec.recommendation_id!r}; cannot settle CLV"
        )

    bet_id, close_id = _require_distinct_line_sources(rec, close)

    clv_method, p_close, units = translate_close_to_bet_line(
        rec, close, model_cover_prob=model_cover_prob, method=method
    )
    if clv_method == "same_line":
        # Thread IDs through compute_clv so the primitive and settle() share one guard.
        p_bet, p_close, clv = compute_clv(
            rec.bet_side_american,
            rec.bet_other_american,
            close.side_american,
            close.other_american,
            method=method,
            bet_line_source_row_id=bet_id,
            close_line_source_row_id=close_id,
        )
    else:
        p_bet = fair_prob_on_side(rec.bet_side_american, rec.bet_other_american, method=method)
        clv = float(p_close - p_bet) if clv_method in PROBABILITY_VALUED_METHODS else float("nan")

    return SettledBet(
        recommendation=rec,
        close_side_american=float(close.side_american),
        close_other_american=float(close.other_american),
        p_bet_fair=float(p_bet),
        p_close_fair=float(p_close),
        clv=clv,
        clv_settlement=settlement,
        clv_method=clv_method,
        clv_line_units=float(units),
        line_shopping_capture=compute_line_shopping_capture(rec, method=method),
        close_line=None if close.line is None else float(close.line),
    )


def settle_recommendation(
    recommendation: RecommendationRecord,
    close_side_american: float,
    close_other_american: float,
    *,
    close_source_row_id: str,
    method: DevigMethod = DEFAULT_DEVIG_METHOD,
) -> SettledBet:
    """Settle against a same-book close quoted at the ticket's line.

    Convenience wrapper for moneylines and unmoved lines; anything involving a
    moved spread or total must go through :func:`settle` with a
    :class:`ClosingQuote` so the line is translated.

    ``close_source_row_id`` is required so the source-row guard cannot be skipped.
    """
    return settle(
        recommendation,
        same_book_close=ClosingQuote(
            side_american=float(close_side_american),
            other_american=float(close_other_american),
            book=recommendation.book,
            line=recommendation.bet_line,
            source_row_id=str(close_source_row_id),
        ),
        method=method,
    )


def _block(label: str, values: Sequence[float]) -> ClvBlock:
    arr = np.asarray(values, dtype=float)
    n = int(arr.size)
    if n == 0:
        return ClvBlock(
            label=label,
            n_bets=0,
            mean_clv=float("nan"),
            pct_positive=float("nan"),
            clv_values=(),
            ci_low=float("nan"),
            ci_high=float("nan"),
        )
    mean = float(np.mean(arr))
    if n >= 2:
        se = float(np.std(arr, ddof=1) / np.sqrt(n))
        ci_low, ci_high = mean - 1.96 * se, mean + 1.96 * se
    else:
        ci_low = ci_high = float("nan")
    return ClvBlock(
        label=label,
        n_bets=n,
        mean_clv=mean,
        pct_positive=float(np.mean(arr > 0.0)),
        clv_values=tuple(float(x) for x in arr.tolist()),
        ci_low=float(ci_low),
        ci_high=float(ci_high),
    )


def summarize_settlements(
    settled: Sequence[SettledBet],
    *,
    season: int,
    week: int,
    close_definition: CloseDefinition = "odds_api_consensus",
) -> WeeklyClvReport:
    """Stratify settled rows into non-pooled blocks (§2.7, §7.3).

    The headline fields carry same-book probability-valued rows only. Mixing in
    fallback-consensus rows would reintroduce the shopping bias the same-book
    rule exists to remove, and mixing in line-unit rows would add points to
    probabilities.
    """
    headline = [s.clv for s in settled if s.is_headline]
    fallback = [
        s.clv
        for s in settled
        if s.clv_settlement == "fallback_consensus" and s.clv_method in PROBABILITY_VALUED_METHODS
    ]
    units = [s.clv_line_units for s in settled if s.clv_method == "line_units"]
    shopping = np.asarray([s.line_shopping_capture for s in settled], dtype=float)

    head_block = _block("same_book", headline)
    return WeeklyClvReport(
        season=season,
        week=week,
        n_bets=head_block.n_bets,
        mean_clv=head_block.mean_clv,
        pct_positive=head_block.pct_positive,
        clv_values=head_block.clv_values,
        ci_low=head_block.ci_low,
        ci_high=head_block.ci_high,
        close_definition=close_definition,
        same_book=head_block,
        fallback_consensus=_block("fallback_consensus", fallback),
        line_units=_block("line_units", units),
        mean_line_shopping_capture=(
            float(np.nanmean(shopping))
            if shopping.size and np.any(np.isfinite(shopping))
            else float("nan")
        ),
    )


def settle_week(
    recommendations: Sequence[RecommendationRecord],
    closes: Mapping[str, tuple[float, float] | ClosingQuote],
    *,
    season: int,
    week: int,
    method: DevigMethod = DEFAULT_DEVIG_METHOD,
    close_definition: CloseDefinition = "odds_api_consensus",
    consensus_closes: Mapping[str, ClosingQuote] | None = None,
    model_cover_probs: Mapping[str, CoverProbFn] | None = None,
) -> tuple[list[SettledBet], WeeklyClvReport]:
    """Weekly settlement job: join closes, translate lines, summarize.

    Parameters
    ----------
    recommendations:
        Stored recommendations for this week (must include line-at-bet).
    closes:
        Map ``recommendation_id → ClosingQuote`` from the **same book** as the
        bet. A bare ``(side, other)`` tuple is accepted for moneylines and
        unmoved lines.
    consensus_closes:
        Fallback closes for recommendations absent from ``closes``. Rows settled
        this way are flagged and excluded from headline aggregates.
    model_cover_probs:
        Per-recommendation model cover probability, enabling ``model_dist``
        translation when the book quoted no alternate line.
    """
    settled: list[SettledBet] = []
    for rec in recommendations:
        if rec.season != season or rec.week != week:
            continue

        raw = closes.get(rec.recommendation_id)
        same_book: ClosingQuote | None
        if raw is None:
            same_book = None
        elif isinstance(raw, ClosingQuote):
            same_book = raw
        else:
            raise ClvError(
                f"settle_week close for {rec.recommendation_id!r} must be a ClosingQuote "
                "with source_row_id; bare (side, other) tuples cannot thread the CLV "
                "source-row guard"
            )

        fallback = None if consensus_closes is None else consensus_closes.get(rec.recommendation_id)
        if same_book is None and fallback is None:
            continue

        cover_fn = (
            None if model_cover_probs is None else model_cover_probs.get(rec.recommendation_id)
        )
        settled.append(
            settle(
                rec,
                same_book_close=same_book,
                consensus_close=fallback,
                model_cover_prob=cover_fn,
                method=method,
            )
        )

    report = summarize_settlements(
        settled, season=season, week=week, close_definition=close_definition
    )
    return settled, report
