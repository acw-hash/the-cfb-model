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

## A-6 / A-3 (part 1) — Kalman covariance consistency and the identifiability projection (done 2026-08-07)

Two independent defects in `ratings/state_space.py`, both fixed at the primitive
level so the joint-state rewrite (below) can build on them.

### A-6: clipped updates were shrinking the covariance as if fully observed

`kalman_update` winsorized the innovation at 2.5σ before moving the mean, but then
ran the Joseph covariance update with the **nominal** `R`. So the mean moved 2.5σ
while `P` shrank as though the entire residual had been informative. The concrete
consequence is overconfident November ratings: an early-season 60-point blowout was
treated as a high-precision measurement of team strength, when the filter had
explicitly decided to distrust most of it.

`effective_obs_noise` now applies §9.4's `R_eff = R · (|z|/σ)²` to clipped rows.
Rows and columns are scaled by the square root of the factor, so a correlated `R`
stays positive semi-definite; for the diagonal `R` in use this reduces to scaling
each variance. The gain and the Joseph update both use `R_eff`. The predictive
log-likelihood deliberately still uses the raw `S` — that is the honest density of
the observation that actually arrived, and inflating it would flatter the model's
likelihood-based tuning.

Behaviour now pinned by test: posterior variance rises monotonically with `|z|`
past the clip point and approaches the prior in the limit (a 25σ outlier teaches
almost nothing), with the closed-form value `272/289` checked at `z = 10`.
Parameter-recovery and calibrated-coverage tests pass unchanged, which is the
signal that mattered most — the inflation could easily have broken coverage.

### A-3 (part 1): the constraint projection

`project_league_mean_zero` implements §9.3 as an explicit projection,
`x ← (I−M)x` and `P ← (I−M)P(I−M)ᵀ`, not a zero-noise pseudo-observation, so the
constraint holds *exactly* after every update rather than approximately.

Worth stating why this matters: `off_h − def_a` identifies only differences. Add a
constant to every team's offense and every team's defense and no measurement
changes at all. That null direction is collinear with the league
scoring-environment state, so the filter has a ridge it can wander along, fitting
the data equally well while the absolute level drifts. The projection also removes
posterior *variance* along that direction — the filter should not claim
uncertainty about a quantity the data cannot speak to. Tests pin the group means
to zero, idempotence, zero variance along the all-ones direction with contrast
variance preserved, and the shift-invariance property.

## A-10 (part 1) — Simplex-constrained stacking (done 2026-08-07)

**What was wrong.** `fit_nnls_stack` ran `nnls(x, y)` and then `raw / total`.
NNLS minimizes over the non-negative *cone*; dividing by the sum slides along a
ray and generally lands somewhere other than the constrained minimizer. §5 rejects
this explicitly, and the audit asked for a demonstration rather than an assertion.

**The demonstration.** Two orthogonal members with `y = [0.2, 0.6]`. The
unconstrained non-negative solution is `(0.2, 0.6)`, which sums to 0.8 and so sits
off the simplex. Renormalizing gives `(0.25, 0.75)`; the true constrained optimum,
from `w1 − 0.2 = w2 − 0.6` with `w1 + w2 = 1`, is `(0.3, 0.7)`. Squared error
0.025 versus 0.020 — the rejected approach gives away 25% more error on a problem
small enough to check by hand.

`solve_simplex_least_squares` now solves the QP directly with SLSQP and an
analytic gradient, from a uniform start plus every vertex, keeping the best
objective. The problem is convex and 4-6 dimensional, so this is both cheap and
free of any dependence on the starting point. `renormalized_nnls_weights` is kept
in the module solely so the regression test can price the gap; a future reader
tempted to "simplify" back to renormalization will trip that test.

**One judgement call.** Degeneracy is still judged on the unconstrained NNLS
solve, not the constrained one. Under `Σw = 1` the solver always returns weights
summing to 1, which would look like a healthy fit even when no member carries any
signal — the constraint would hide exactly the condition the existing loud error
exists to catch.

Also pinned: weights on the simplex by construction across random problems, pure
noise driven under 3% weight, determinism across repeated calls, and the no-intercept
property (two members both biased 10+ points low must yield a stack that is still
biased low, because correcting the level is Level-2's job).

**Verification.** `tests/unit/test_simplex_stacking.py` (10 tests).
`make test`: 620 passed, coverage 81.05%.

**Still open in A-10.** The single non-overlapping variance decomposition
(`σ²_pred = σ²_aleatoric + σ²_members + σ²_Stage-1` with no double counting) is
untouched; it needs the joint league state to supply a real `σ²_Stage-1`, so it is
bundled with the state-space task.

## A-2 — Non-circular preseason prior fitting (done 2026-08-07)

**What was wrong.** `fit_prior_weights` regressed each season's `early_rating` on
the six preseason predictors. But early ratings come from a filter *initialized
with those very priors*, so the target is prior-dominated and the regression
largely recovers the weights that were assumed. The reported R² was high for
exactly the wrong reason, and the priors that govern Weeks 1-5 — the softest market
window, where the system expects its edge — were never validated against anything.

**What it does now.** The fitting target must be prior-free. `diffuse_late_ratings`
runs the filter with **no preseason states** and `prior_var = 100.0` (2500x the
standard 0.04), then takes each team's posterior after at least 8 games. Regressing
those on the preseason predictors is an honest test of whether the predictors
forecast anything, because the prior had no hand in producing them.

`fit_prior_weights` now **refuses** `early_rating` unless
`allow_circular_target=True`. The escape hatch exists only so the demonstration
test can show the failure; `FittedPriorWeights.target_column` is recorded on every
fit, with a `target_is_circular` property, because an R² is uninterpretable without
knowing what it was scored against. `out_of_sample_r2` follows the fitted target by
default, so Task 15's acceptance criterion now scores priors against prior-free
ratings.

**The demonstration.** `test_circular_target_recovers_the_assumed_weights` plants a
world where `talent` drives the rating and `last_regressed` does nothing, then
gives the prior the opposite belief. Fitting against early ratings returns the
*assumed* weights (`last_regressed ≈ 0.9`, `talent ≈ 0.0`) with R² > 0.99. Fitting
against diffuse-run late ratings returns the truth (`talent ≈ 0.9`). The two
disagree by more than 0.5 on which predictor matters at all.

A second test makes the point harder: with priors that predict *nothing*, the
circular target still scores above 0.99 out of sample — the prior predicts the
prior — while the honest target correctly reports under 0.05.

**One correction during the work.** The no-skill test first asserted in-sample R²
below 0.1 and measured 0.109. That was my threshold being wrong rather than the
code: six predictors on 160 rows fit that much noise in sample. Rewritten to use
out-of-sample R², which is the honest yardstick and makes the contrast sharper.

**Three existing tests updated.** `test_priors.py` had synthetic frames whose target
was generated from the predictors but named `early_rating`. They were testing
reproducibility, roundtrip and the OOS helper, not circularity, so the column is
renamed to `late_rating` to say what it actually is. The roundtrip test now attaches
a prior-free target explicitly via `attach_late_target`.

**Verification.** `tests/unit/test_prior_circularity.py` (13 tests), including an
end-to-end diffuse filter run that recovers a planted strength ordering with
correlation above 0.8. `make test`: 633 passed, coverage 81.12%.

**Not done here.** The spec's preferred upgrade — maximizing Weeks 1-4
one-step-ahead predictive likelihood with respect to the weights — is not
implemented. The diffuse-late-ratings route is the audit's primary recommendation
and is what ships; the likelihood route would be a strict improvement and is worth
its own task once the joint filter lands, since it needs the filter's likelihood to
be trustworthy.

### Still open: the joint league state (A-3 part 2 / B-1)

The projection primitive exists but is **not yet wired into the filter**, because
wiring it requires the joint state it is meant to constrain. `run_filter` still
keeps `teams: dict[str, GaussianState]` — per-team marginals — and `update_game`
says so plainly in its own docstring: "cross-covariance is discarded for storage —
standard independent-team approximation".

That approximation contradicts three separate spec claims:

1. §9.2's single joint league state with full cross-team covariance.
2. §5.1's footnote that schedule information propagates optimally, which holds
   "only because the filter is joint". With cross-covariance discarded after every
   game, beating Team C tells the filter nothing about Team A that played C
   earlier, beyond C's own marginal — so opponent adjustments do not flow through
   the league graph.
3. §2.6's epistemic draws, which are specified to draw both teams' blocks jointly
   "preserving their cross-team covariance — not independent marginal draws".
   There is currently no cross-team covariance to preserve, so those draws are
   independent whatever the code intends.

Scope of the remaining work, deliberately left as its own task rather than rushed:
a `LeagueState` holding one mean vector and one full covariance with a position
registry, dynamic team admission, per-team process noise and season regression
applied to slices of the joint covariance, a `scoring_env` league dimension to
absorb the level the projection removes, the projection applied after every
measurement update, and the end-to-end shift-invariance acceptance test from
§15 item 14. Runtime needs checking against the "<5 min for 2014-2025" criterion:
roughly 540 dims means a 540x540 covariance and a rank-5 update per game, which
should be comfortable, but it needs measuring rather than assuming.

## A-8 � Leakage suite with well-posed nulls (done 2026-08-07)

**What was wrong.** The shifted-label test asserted that future features predicting
past games must score at chance. Strength persists, so a November rating is
legitimately informative about a September game. A leak-free system fails that
null, inviting threshold-fiddling.

**What it does now.**

- `evaluation/leakage.py` implements three falsifiable checks:
  1. Within-week label permutation (train on shuffled labels ? OOS at chance).
  2. Planted prophecy (outcome-derived feature must be caught).
  3. As-of sensitivity (features must change when `as_of` moves).
- `tests/leakage/test_shifted_label.py` deleted.
- `run_shifted_label_test` retained as a **cheater detector only**, with
  `null_is_invalid=True`. Scoring at chance is not a gate; beating chance is still
  worth investigating.
- Production leakage test (`test_task22b`) gates on information-set + prophecy
  audit; shifted-label is diagnostic.

**Verification.** `tests/leakage/test_label_permutation.py` plus updated walkforward /
task22b / diagnostics tests. Targeted suite: 63 passed.

## A-7 � Conditional key-number kernel (done 2026-08-07)

**What was wrong.** A single pooled residual-offset kernel over- or under-allocated
key-number mass exactly where ATS pricing is most sensitive. Pick''em games land
on �3 far more often than 20-point spreads; the pooled fit cannot express that.

**What it does now.** `fit_key_number_kernel` returns a
`ConditionalKeyNumberKernel` by default, with `|�|` buckets at edges
`(0, 3.5, 7.5, 14.5, 21.5, 8)`. Thin buckets fall back to the pooled fit.
`discrete_margin_pmf` / `sample_discrete_margins` resolve the bucket for each
game''s �. Pass `mu_abs_edges=None` for the pre-A-7 pooled behaviour.
`validate_key_number_kernel` compares empirical exact-margin frequencies to
kernel output by predicted-spread bucket � the A-7 acceptance check.

**Verification.** `tests/unit/test_conditional_key_numbers.py` plants a world
where pick''em piles on +3 and blowouts on +14; the conditional fit separates
them and beats the pooled L1 error on the validation report.

## A-11 (part 2) � Promotion ledger + Bonferroni a (done 2026-08-07)

**What was wrong.** The gate re-tested at a = 0.10 with no memory of prior looks.
Repeated mid-season / offseason attempts make spurious promotion near-certain.

**What it does now.** `registry/promotion_ledger.py` is an append-only JSONL
ledger under the registry root. `promote()` (default) consults it, applies
`a_adj = a0 / k` for the k-th attempt this calendar year, records every attempt
(pass or fail), and prints attempt count + adjusted threshold on the comparison
report. `apply_multiplicity=False` remains for cold-start / test seeding.

**Verification.** `tests/unit/test_promotion_ledger.py` � Bonferroni arithmetic,
ledger integrity, a tightening across real promotes, and a pinned p=0.06 case
that passes at a=0.10 but fails at a=0.05.

## A-9 � Adaptive Conformal Inference (done 2026-08-07)

**What was wrong.** The module claimed a "distribution-free guarantee layer".
Split conformal needs exchangeability; season-over-season drift violates it.

**What it does now.** Module docstring states approximate coverage under mild
drift. `AdaptiveCQR` / `fit_adaptive_cqr` initialize from the trailing-2-season
split CQR fit and update `a_t ? clip(a_t + ?(a_target - err_t))` online.
Misses lower a (widen); persistent coverage raises a (tighten).
`run_aci_stream` replays a holdout for diagnostics.

**Not wired into** `ProductionEnsemblePredictor.predict` yet � that lands with
the Phase 4 path where PIT-recalibrated quantiles and ACI should share one
interval path (A-4 note).
