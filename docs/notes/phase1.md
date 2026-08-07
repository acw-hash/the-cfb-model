# Phase 1 — Measurement-critical code to the amended spec

Bringing the code written 2026-08-04/05 up to the specification as amended by the
audit on 2026-08-06. Ordered by how much each item can move the numbers. Every
item carries a test that fails against the old behaviour.

## A-1 — CLV settlement and line translation (done 2026-08-07)

**What was wrong.** `betting/clv.py` differenced de-vigged prices with no notion
of which book closed or what line the ticket was on. Two biases, both positive:

- Bets are placed at the best price across books (§12) but CLV was graded against
  a consensus close, so line shopping was credited as forecasting skill.
- For spreads and totals it de-vigged the closing *price* at whatever line the
  book closed at, differencing probabilities of two different instruments. The
  error is the push mass between the numbers, largest exactly at 3 and 7.

**What it does now.**

- `settle()` prefers the close from the book that priced the bet. A close from a
  different book raises. Rows with no same-book close are settled against
  consensus, flagged `clv_settlement=fallback_consensus`, and excluded from every
  headline aggregate.
- `translate_close_to_bet_line()` implements §2.7's ladder and records
  `clv_method`: `alt_line_price` (the book's own quote at the ticket line) →
  `model_dist` (price the line gap with the model's predictive distribution and
  shift the closing probability by it) → `line_units` (report points of movement,
  no probability). `same_line` when nothing moved or the market is a moneyline.
- `model_dist` refuses to emit a probability outside [0, 1] rather than clamping,
  so a model that disagrees violently with the close fails loudly.
- `spread_cover_prob` / `total_cover_prob` price a line from a discrete
  distribution with pushes counted as half; production and tests share the math.
- `line_shopping_capture` is computed and stored, never folded into CLV.
- `summarize_settlements()` returns separate `same_book`, `fallback_consensus` and
  `line_units` blocks. The headline fields expose same-book probability-valued
  rows only, so a reader cannot quote a pooled number by accident.

**The two fixtures that prove it.**

*Line translation* (Task 20 item 7). Ticket on -6.5, book closes -7, price
unchanged at -110/-110. Price-only CLV sees identical prices and reports exactly
0.0. Correct value: our ticket needs a 7-point win, the close needs 8, so we hold
the better number by half the push mass on 7, i.e. `0.5 × P(margin = 7) =
0.5 × 0.08 = 0.04`. Implementation returns `p_close_fair = 0.54`, `clv = 0.04`,
`clv_method = model_dist`, `clv_line_units = 0.5`.

*Same-book settlement* (A-1a). A bet at a book that never moves has zero closing
value by construction. Bought at -105/-115 where consensus was -110/-110:

| settlement | CLV |
|---|---|
| same-book (truth) | 0.000000 |
| consensus (biased) | +0.010834 |

The spurious credit equals the magnitude of `line_shopping_capture`. A skill-free
system would have shown positive CLV under the old definition.

**Ambiguity found and recorded.** `line_shopping_capture` as §2.7 defines it is
*negative* when shopping yields value: a book offering our side more cheaply
holds a lower fair view of it than consensus, and that disagreement is why the
price was worth taking. The name implies the opposite sign. Implemented exactly
as specified rather than silently negated, documented at every point of use, and
recorded in [ADR 0006](../adr/0006-line-shopping-capture-sign.md).

**Verification.** `tests/unit/test_clv_line_translation.py` — 21 tests covering
both translation directions, the ladder priority order, sign conventions for
spreads and both total sides, refusal cases, and non-pooling of strata.
`make test`: 559 passed, coverage 80.84%.

**Not done here.** Nothing yet writes these fields into the walk-forward bet
frame — `edges.py` still needs to emit `line_shopping_capture` and
`n_books_available` onto recommendations, and the backtest bet layer needs to
supply `ClosingQuote`s and per-bet model distributions. That wiring lands with
the Phase 4 re-run, where it is observable end to end.
