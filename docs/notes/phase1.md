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

## A-4 — Distributional PIT recalibration (done 2026-08-07)

**What was wrong.** Three independent per-market isotonic/Platt maps (ML,
ATS@close, OU@close) with nothing tying them together. `P(home wins)` and
`P(home covers 0)` are the *same event*, and separate maps can move them in
opposite directions, which breaks the §2.2 internal-consistency guarantee that
the whole bivariate-simulation design exists to provide.

A second defect in the same function: σ for the OOF probabilities was built from
`|y − μ|`, the very residual being calibrated against. The label was inside the
predictive distribution that was supposed to score it.

**What it does now.** `models/pit_calibration.py` implements the amended §2.6:
one monotone map per predictive distribution, fit on PIT values
(Kuleshov et al. 2018). `R` is the empirical CDF of the OOF PIT values; reporting
`F̃ = R ∘ F` makes the PIT uniform by construction, and because `R` is monotone,
`F̃` is a valid CDF. Every derived probability is read off that one recalibrated
distribution, so coherence is structural rather than hoped for.

- Isotonic-on-PIT when there are enough distinct OOF values; parametric Beta map
  as §2.6's thin-data fallback (`Beta(1,1)` is uniform, so the identity is inside
  the family).
- `assert_market_free_target` refuses `ats_close` / `ou_close` / `ml` as fitting
  targets, so the closing line cannot re-enter the fundamental stack through the
  calibration layer after §4 kept it out of the features.
- σ now comes from the fitted σ-heads.
- Gate is time-ordered: fit on the earlier 70% of OOF, gate on the later 30%,
  refit on all rows only if held-out PIT uniformity improved. Isotonic-on-PIT is
  perfect in sample by construction, so an in-sample gate would always pass.
- `ProductionEnsemblePredictor._apply_calibrator` routes `ml` and `ats_close` to
  the margin map and `ou_close` to the total map. That routing *is* the coherence
  guarantee.
- `models/calibrate.py` keeps its reliability/Cox machinery but now carries a
  warning banner that it is diagnostics only.

**Fixed a silent no-op while here.** The calibration fit was wrapped in
`contextlib.suppress(Exception)`, so any failure left an empty report that looked
identical to "ran and found nothing to fix". It now records a reason string
(`margin_pit_skipped`) naming the row counts or the exception. This is what
surfaced that the 12-game `_games()` fixture is below `_MIN_OOF_ROWS`, so the σ,
calibration and CQR layers were all being skipped on it — meaning the existing
`test_production_predict_emits_varying_sigma` never exercised calibration at all.
The new integration test uses a 112-game fixture where the OOF frame actually
forms (58 OOF rows, both maps fit).

**The property test that matters.**
`test_moneyline_equals_cover_at_zero_after_calibration` asserts the two
probabilities agree to 1e-12 *post*-calibration, which is where A-4 said the check
belonged. Also pinned: cover probability stays monotone in the line, two-way
probabilities still sum to 1, outputs never reach 0 or 1, and quantile-level
inversion round-trips.

Thresholds in the PIT tests are stated as multiples of the KS critical value
`1.36/√n` rather than hand-picked constants, after a first pass asserted
`> 0.15` against a true value of 0.1245 — an arbitrary threshold that happened to
be wrong. The overconfident-forecaster fixture sits at roughly 6× critical;
recalibration brings it under 1×.

**Verification.** `tests/unit/test_pit_calibration.py` (22 tests) plus the
integration test. `make test`: 594 passed, coverage 81.00%.

**Not done here.** Quantile columns and CQR bands are still read off the raw
distribution rather than through `transform_quantile_level`, so conformal
intervals do not yet inherit the recalibration. That belongs with A-9 (ACI
conformal), which is the next integrity item.
