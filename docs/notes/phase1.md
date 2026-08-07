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

## A-11 — Lockbox enforcement in code (done 2026-08-07)

Taken out of plan order and done early, because every run after this point risks
touching 2025 and the mistake is irreversible.

**What was wrong.** The lockbox was documentation only. `DEFAULT_TEST_SEASONS`
included 2025, and so did **every** `task23_*` config — the exact configs the
Phase 4 re-run would have used. Nothing in code would have objected.

**What it does now.** `evaluation/lockbox.py` holds `LOCKBOX_SEASON`,
`assert_lockbox_excluded()` and `LockboxViolation`.
`WalkForwardConfig.validate_ablations()` calls it over `all_replay_seasons()`, so
test, continuity *and* warm-up roles are all guarded, and a violating run fails
before it spends compute. Permitting a read takes an explicit
`lockbox_confirmatory_read=True`, which is deliberately an argument rather than a
config default so that allowing it is visible in review.

Configs corrected: `task23_full.yaml`, all seven runs in `task23_run_set.yaml`,
A6's snapshot-regime list, and `configs/eval/encompassing.yaml`. The odds purchase
spec still buys 2025 — the lockbox restricts *reading* the season, not acquiring
its data, and buying now means a later confirmatory read needs no repurchase.

`HISTORICAL_CANONICAL_SEASONS` preserves the lockbox-inclusive list that the
frozen D2-D7 canonical frames were built on, so those archived SHAs stay
reproducible. It is named to be impossible to reach for by accident.

**Finding: 2025 is not a virgin holdout.** D7 used 2025 weeks 1-4 as its
pre-registered confirmatory holdout on 2026-08-06, hours before the lockbox
designation was written — legitimate then, but it means the season has already
been looked at, and the largest early-week effect in the whole D7 table
(`b2 = 0.376`) came from it. `docs/lockbox_access.md` now records that read
instead of claiming "none yet", and states that a future confirmatory report on
2025 is weaker evidence than a first read would have been. A register that only
logs the reads we feel good about is not a register.

**Verification.** `tests/unit/test_lockbox.py` — 12 tests. The one that earns its
keep is `test_shipped_ablation_configs_exclude_the_lockbox`, which walks every
shipped config; it failed on first run and caught `task23_full.yaml`.
`make test`: 571 passed, coverage 80.86%.
