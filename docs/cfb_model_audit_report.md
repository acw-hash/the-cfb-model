# Audit Report — `acw-has/the-cfb-model` (NCAA Football Prediction System)

**Audit date:** 2026-08-06
**Artifacts audited:** `DESIGN.md` (583 lines), `ncaa_prediction_system_design.md` (byte-identical duplicate of DESIGN.md), `TASKS.md` (1,067 lines, 25+ implementation prompts), `_cursorrules`, `historical_odds_change_set.md`
**Artifacts NOT available:** any source code, tests, configs, data, CI history, or notes files. The repository as provided contains **specification only**.

---

## 0. Scope limitation (read first)

This audit was requested as a code audit. **No code exists in the provided repository snapshot.** Every finding below is therefore a finding against the *specification* — the document that governs all future implementation. This matters in two directions:

1. Several code-quality, QA, and production-readiness questions (race conditions, actual test coverage, actual error handling) are **unverifiable** and are scored as such.
2. Specification defects are *more* dangerous than code defects here, because the workflow explicitly instructs autonomous coding agents to treat `DESIGN.md` as non-negotiable authority (`_cursorrules`: "docs/DESIGN.md is the specification. Follow it."). A statistical error in the spec will be implemented faithfully, tested faithfully against the wrong definition, and pass every gate.

Overall verdict up front: this is an unusually sophisticated specification — top-decile for a project of this type. The two-stage state/mapping decomposition, walk-forward-only validation, point-in-time discipline, pre-registration culture, CLV-centric evaluation, and the honesty sections are genuinely professional-grade. But "be extremely critical" was the instruction, and the spec contains **several real statistical and methodological errors** — including two that would silently corrupt the system's headline metric (CLV) and one circular estimation procedure — plus a handful of internal contradictions an implementing agent cannot resolve on its own.

---

## 1. Findings

Findings are grouped by area. Severity reflects impact on the system's scientific validity and financial decisions, not merely code hygiene.

### 1.A Statistical & Mathematical Validity

---

**A-1. CLV is computed in a way that mechanically inflates it, independent of skill (two compounding defects)**

- **Severity:** Critical
- **Files:** `DESIGN.md` §2.7, §12, §1.6; `TASKS.md` Task 20; `historical_odds_change_set.md` §A3
- **Problem:** Two distinct defects:
  - *(a) Best-price bet vs. consensus close.* Edges and bets are computed "against the BEST available captured price across books" (§12, Task 20), but CLV is settled against the **consensus close** (§1.6, §2.7). Line shopping across books mechanically produces positive CLV against a consensus benchmark even for a model with zero predictive skill — you are systematically buying the outlier price and grading against the average. The change set's `n_books_available` stratification mitigates the *time-trend* bias but not this level bias. The primary success criterion ("mean CLV > 0, 95% CI excluding 0, ≥300 bets") is therefore achievable by a skill-free system with 4+ books, which defeats the entire purpose of CLV as the honest arbiter.
  - *(b) Probability-space CLV across moved spread/total lines.* The definition `implied_prob(close) − implied_prob(bet_line)` compares the de-vigged price of the *closing line* to the de-vigged price of the *bet line*. For moneylines this is correct. For spreads and totals, the line itself moves (−6.5 → −7): de-vigging the −7/−110 close yields the probability of covering **−7**, not the probability of your **−6.5** ticket. The difference conflates line movement with price movement and mis-measures CLV by the push-probability mass between the two numbers — largest exactly at key numbers (3, 7), where CLV signal concentrates. The system even builds the machinery to do this correctly (the key-number kernel and margin distribution can price any line) but the spec doesn't connect them.
- **Why it matters:** CLV is the pre-registered primary success criterion and the promotion-gate metric. Both defects bias it *positive*. The system's central "honest measurement apparatus" claim fails.
- **Recommended fix:** (1) Settle CLV against the closing price **at the same book where the bet was priced**; report best-price-vs-consensus separately as "line-shopping capture," which is real value but not model skill. (2) For spreads/totals, translate the bet line and close to a common number using the empirical margin distribution (or the book's own alternate-line prices where captured) before differencing probabilities; alternatively report CLV in *line units* (points of closing movement toward the bet) as a companion metric with a documented points→prob conversion. (3) Re-state the §1.6 success criterion accordingly.
- **Confidence:** High (the definitions as written are unambiguous, and the bias direction is arithmetic, not judgment).

---

**A-2. Preseason prior weight fitting is circular as specified**

- **Severity:** High
- **Files:** `DESIGN.md` §9.6; `TASKS.md` Task 15
- **Problem:** Prior weights are to be "fit by regressing *next-season early ratings* on candidate predictors over 2015–2024." But early-season Kalman posteriors are, by construction, dominated by the priors that initialized them (§9.6 itself says priors dominate Weeks 1–3 and reach 50/50 around games 5–7). Regressing prior-dominated posteriors on prior components largely recovers whatever weights were used to build the priors — a self-confirming loop. The fitted weights and their reported standard errors (Task 15 acceptance) would be quantitatively meaningless.
- **Why it matters:** The priors govern Weeks 1–5 predictions each season — the softest market window and the place the spec expects real edge. A circular fit means the prior system is never actually validated against reality.
- **Recommended fix:** Fit weights against a **prior-free target**: e.g., regress each team's *end-of-season* (or ≥8-games) posterior from a filter run with **diffuse initialization** on the preseason predictors of that season. Equivalently, maximize the one-step-ahead predictive likelihood of *actual game observations* in Weeks 1–4 with respect to the prior weights (an empirical-Bayes fit against data, not against posteriors). Task 15's out-of-sample R² acceptance should target realized early-season *game outcomes/observations*, not "realized early-season ratings."
- **Confidence:** High.

---

**A-3. State-space level identifiability is unaddressed; the scoring-environment drift state is collinear with it**

- **Severity:** High
- **Files:** `DESIGN.md` §9.2–§9.3; `TASKS.md` Task 14
- **Problem:** The measurement equation `obs_epa_h = off_h − def_a + hfa_off + ε` identifies only *differences* of the form (off − def). Adding a constant c to every team's offense and every team's defense leaves all measurement predictions unchanged: the league-wide offensive/defensive *levels* are unidentified. The spec then adds a league-level "season scoring-environment drift" state that is exactly collinear with this unidentified direction. Without a constraint, the filter's levels random-walk arbitrarily, the drift state absorbs noise unpredictably, and the covariance in the unidentified direction grows without bound.
- **Why it matters:** Margin predictions survive (differences cancel), but **totals do not** — totals depend on the sum of offensive levels net of defensive levels, which lives partly in the unidentified subspace. Totals are the market the spec explicitly targets first (§16 item 6). Also, cross-season comparability of ratings and the 2023 clock-rule regime handling both depend on a well-defined league level.
- **Recommended fix:** Impose a sum-to-zero (league-mean-zero) constraint on off and def states each update (project the state and covariance onto the constraint surface, or include the constraint as a pseudo-observation with tiny noise), and let the *single* scoring-environment state carry the level — making it identified as the league-mean EPA environment. Add a Task 14 test: run the filter on synthetic data, add a constant to all initial states, and assert predictions and constrained states are invariant.
- **Confidence:** High on the math; Medium-High on practical impact magnitude (depends on Q in the null direction).

---

**A-4. Per-market isotonic calibration destroys the internal-consistency guarantee the architecture was built to provide — an internal contradiction**

- **Severity:** High
- **Files:** `DESIGN.md` §2.2, §2.6, §5.2 (Level 2); `TASKS.md` Task 19
- **Problem:** §2.2's core argument for modeling the joint score distribution is that all bet probabilities derive from one distribution and "cannot contradict each other." Level 2 then applies **separate isotonic maps per market** (ML, ATS@close, OU@close). After per-market recalibration, calibrated P(ML win) no longer equals calibrated P(cover at spread 0); calibrated ATS probabilities at adjacent spreads need not be monotone in the spread across the isotonic seams. Task 19's own property test ("P(win) derived from margin distribution equals P(cover at 0)") will fail — or worse, will be run pre-calibration and pass while the shipped (post-calibration) probabilities are inconsistent. Additionally, ATS-vs-close probabilities cluster tightly near 0.5, where isotonic regression on a few thousand OOF points is a fragile step function.
- **Why it matters:** Inconsistent probabilities across markets re-open exactly the incoherence (§2.2's "70% to win / 45% to cover −1") the design exists to prevent; edges computed from inconsistent probabilities produce contradictory bet recommendations on the same game.
- **Recommended fix:** Calibrate the **distribution**, not the derived markets: apply PIT-based distributional recalibration (e.g., isotonic on the margin/total PIT values, i.e., Kuleshov-style quantile recalibration) so a single monotone map recalibrates all derived probabilities coherently; then verify per-market reliability as *diagnostics*. If per-market maps are retained for pragmatic reasons, document the inconsistency bound, enforce a reconciliation step, and move Task 19's consistency property test to run on the **post-calibration** outputs.
- **Confidence:** High.

---

**A-5. σ-head trained on |OOF residual| without the half-normal scaling factor**

- **Severity:** Medium
- **Files:** `DESIGN.md` §2.4, §5.2 item 7; `TASKS.md` Task 17 item 3
- **Problem:** A model regressing absolute residuals estimates E[|r|]. Under approximate normality, E[|r|] = σ·√(2/π) ≈ 0.798σ. Used directly as σ, predictive SDs are understated ~20%, intervals under-cover, tail probabilities (blowout covers, alt lines) are materially wrong, and Kelly stakes are inflated. The spec never mentions the √(π/2) correction (or the alternative of training on squared residuals for σ², or using the NGBoost σ as the scale anchor).
- **Why it matters:** Every probability the betting layer consumes flows through σ. Downstream isotonic/conformal layers would partially mask the bug — which is worse, because it would hide a 20% systematic error inside opaque correction layers instead of fixing it at the source.
- **Recommended fix:** Specify: σ̂ = √(π/2) · f(|r|), or train on r² with a target of σ², or fit a proper scale model (Gamma deviance on r²). Add a unit test on synthetic heteroskedastic normal data asserting recovered σ is unbiased within tolerance.
- **Confidence:** High that the spec as written omits it; the implementing agent may or may not know the correction.

---

**A-6. Winsorized-innovation Kalman update without a covariance correction understates posterior uncertainty**

- **Severity:** Medium
- **Files:** `DESIGN.md` §9.5; `TASKS.md` Task 14 item 4
- **Problem:** Clipping the innovation at ±2.5σ while performing the *standard* covariance update P ← (I − KH)P is internally inconsistent: the state moves as if a partially-informative observation arrived, but the variance shrinks as if a fully-informative one did. Repeated over a season, posterior SDs are biased low, the Kalman gain becomes too small, and the filter under-reacts later — the opposite failure mode from the one winsorization targets. The spec's own coverage test (Task 14: 95% band covers 93–97%) may catch this in aggregate, but only in the simulation regime, not under real blowout frequency.
- **Recommended fix:** Use a proper robustified update: either (a) a Huberized filter where clipped observations also get inflated effective R (equivalently, downweight via R ← R·(|z|/2.5)² when |z|>2.5), or (b) an explicit Student-t measurement likelihood via a Gaussian scale-mixture step. Both are one-line-ish changes; document which is used. Add a test that a clipped update shrinks variance *less* than an unclipped one.
- **Confidence:** High on the inconsistency; Medium on real-world magnitude.

---

**A-7. Key-number discretization kernel must be conditional on the predicted margin, not pooled**

- **Severity:** Medium
- **Files:** `DESIGN.md` §2.3; `TASKS.md` Task 19 item 6
- **Problem:** The spec learns "an empirical margin-distribution kernel … from historical residuals" and reallocates mass to key margins. Key-number mass is strongly conditional on the expected margin: games near pick'em land on ±3 at several times the rate of 20-point spreads. A pooled unconditional kernel over- or under-allocates key-number mass exactly where ATS pricing is most sensitive (spreads of 2.5–3.5, 6.5–7.5).
- **Recommended fix:** Learn the kernel conditional on μ_M (bucketed or via a smooth model of P(M = k | μ_M, σ_M)); at minimum condition on |μ_M| buckets. Add a validation comparing empirical exact-margin frequencies by predicted-spread bucket to kernel output.
- **Confidence:** High.

---

**A-8. The "shifted-label" leakage test has the wrong null hypothesis**

- **Severity:** Medium
- **Files:** `DESIGN.md` §14 (Testing); `TASKS.md` Task 16 item 6
- **Problem:** "A model given future features to predict PAST games must score approximately at chance." False: team strength is persistent, so future-derived ratings *legitimately* predict past games far above chance. The test as specified will fail on a perfectly leak-free system, and the natural "fix" — loosening the threshold until it passes — makes it meaningless.
- **Why it matters:** A leakage suite containing a test with a wrong null erodes trust in the suite and invites threshold-fiddling culture, the exact failure mode the project's pre-registration ethos guards against.
- **Recommended fix:** Replace with well-posed tests: (a) the existing pit_audit recomputation (correct); (b) a *label-permutation* test — shuffle outcomes within week and assert models trained on shuffled labels score ≈ chance OOS; (c) a *feature-timestamp* static-analysis check; (d) a "prophecy" test — deliberately plant a future-derived feature and assert pit_audit and information-set audit both catch it.
- **Confidence:** High.

---

**A-9. Split conformal "distribution-free guarantee" is overclaimed under temporal shift**

- **Severity:** Medium
- **Files:** `DESIGN.md` §2.6; `TASKS.md` Task 19 item 4
- **Problem:** Split conformal's coverage guarantee requires exchangeability between calibration and test points. A trailing-2-seasons calibration set vs. a new season with rule changes (2023 clock rules), portal-era drift, and scoring-environment movement is not exchangeable. Coverage is approximate at best; calling it a "distribution-free guarantee layer" misstates what is delivered — notable in a document that otherwise polices its own claims carefully.
- **Recommended fix:** Rephrase as "approximate finite-sample coverage under mild drift"; prefer weighted conformal or Adaptive Conformal Inference (online coverage tracking with step-size updates) which is designed for exactly this setting and costs little. Keep the "alert on divergence" idea — it's good.
- **Confidence:** High on theory; Medium on practical coverage error size.

---

**A-10. Ensemble variance assembly risks double counting; NNLS "sum to 1" is not what NNLS does**

- **Severity:** Medium
- **Files:** `DESIGN.md` §5.2; `TASKS.md` Task 19 items 1–2
- **Problem:** Two issues. (1) "Ensemble σ from law-of-total-variance across members + σ-head": member disagreement (epistemic) plus a σ-head trained on total OOF residuals (which already includes aleatoric *and* the ensemble's epistemic error) plus the Stage-1 posterior mixture (§2.6) triple-counts overlapping variance components; predictive variance will be biased high in an uncontrolled way, then squashed back by calibration — layered opaque corrections again. (2) Plain NNLS does not produce weights summing to 1; Task 19 states both "non-negative least squares" and "weights summing to 1" as if they coincide. Post-hoc normalization of NNLS weights is not the solution of the simplex-constrained problem, and neither handles member bias without an intercept decision.
- **Recommended fix:** (1) Define the variance decomposition once: aleatoric from the σ-head (trained on residuals of the *stacked* mean, with the A-5 scaling), epistemic from member disagreement and rating-posterior draws — and validate total variance via PIT/coverage on held-out seasons before any conformal correction. (2) Specify simplex-constrained least squares (solve with `scipy.optimize.lsq_linear`-style projection or quadratic programming), state the intercept policy, and test that weights are on the simplex by construction.
- **Confidence:** High.

---

**A-11. Repeated promotion tests at p < 0.10 with no multiplicity control, and no untouched final holdout**

- **Severity:** Medium-High
- **Files:** `DESIGN.md` §8 item 7, §7.2; `TASKS.md` Tasks 18, 22, 23
- **Problem:** The champion/challenger gate re-tests candidates against the *same* walk-forward seasons (2019, 2021–2025) at p < 0.10, repeatedly, over the project's life. With mid-season gates, offseason retrains, and six research sprints, dozens of looks at the same seasons accumulate; at α = 0.10 per look, spurious promotions are near-certain within a few years. Pre-registration constrains *hypotheses* but not the *reuse of the evaluation set*. The only quarantine mechanism is a single season used as an HPO tiebreak — which is itself consumed by that use. There is no season structurally reserved for final judgment.
- **Recommended fix:** (a) Reserve one season (e.g., most recent completed) as a lockbox touched at most once per year for a confirmatory read, with the access logged. (b) Track a promotion ledger and apply a simple alpha-spending or Bonferroni-within-year rule to the gate. (c) Treat live forward performance (the paper-trade season, §16 item 2) as the true confirmatory instrument and say so in §1.6.
- **Confidence:** High on the statistics; Medium on how fast it bites in practice.

---

**A-12. Calibrating the "fundamental" model on ATS@close targets contaminates its market independence; SP+ prior-anchor contradiction**

- **Severity:** Medium
- **Files:** `DESIGN.md` §0.2, §2.6, §3.1 (SP+ row), §9.6
- **Problem:** (1) The fundamental stack is defined by having *no market features*, yet its Level-2 calibration targets are defined relative to the **closing spread/total** (ATS@close, OU@close). Fitting isotonic maps to market-relative outcomes injects market information into the fundamental model's published probabilities, weakening the stated purpose ("rating integrity … honest self-evaluation"). (2) §3.1 says SP+ should be used "as prior anchor & benchmark, keep OUT of fundamental model features" — but the §9.6 prior blend contains no SP+ term. Either §3.1's "prior anchor" language is stale (then remove it) or SP+ enters the priors (then the fundamental ratings are anchored to SP+, and the independence claim needs qualification). An implementing agent cannot resolve this contradiction.
- **Recommended fix:** Calibrate the fundamental stack only on market-free functionals (moneyline from P(M>0); distributional PIT recalibration per A-4), and evaluate ATS@close as a *diagnostic*. Resolve the SP+ contradiction explicitly in the spec, one way or the other.
- **Confidence:** High on the contradiction; Medium on materiality of the calibration contamination.

---

**A-13. ρ(M,T) as a single global constant**

- **Severity:** Low
- **Files:** `DESIGN.md` §2.3; `TASKS.md` Task 19 item 5
- **Problem:** Margin–total correlation is context-dependent (large favorites, tempo mismatches, weather). A single ρ misprices joint products (team totals, ML/OU parlays on the roadmap) and slightly distorts ATS-OU consistency in extreme games.
- **Recommended fix:** Estimate ρ conditional on |spread| bucket and tempo, report the profile; keep global ρ if the conditional estimates are flat within noise (document the check).
- **Confidence:** Medium.

---

**A-14. Training-set size is overstated ~2× ("20,000 rows" / "20k-game training set")**

- **Severity:** Low
- **Files:** `DESIGN.md` §0.3, §9.7
- **Problem:** 2014–2025 FBS is ≈ 800–870 games/season → roughly 9,500–10,500 games, not 20,000. The n-vs-p reasoning (120–180 features), HPO budgets, and "~60 games add <1%" arithmetic all lean on n; at n≈10k the conclusions still hold, but the document should not contain a 2× arithmetic error in its foundational data-regime argument.
- **Recommended fix:** Correct the figures; re-check any derived claims (the <1% weekly-increment claim becomes ~0.6–0.7% — still supports the design).
- **Confidence:** High.

### 1.B Machine Learning Methodology

---

**B-1. Joint vs. per-team covariance is ambiguous, and the "optimal schedule propagation" claim depends on it**

- **Severity:** High
- **Files:** `DESIGN.md` §9.2, §5.1 (GNN row); `TASKS.md` Task 14
- **Problem:** §9.2 defines the state "per team i … All states carry full covariance." It never says whether the filter maintains the **joint league state** (~134 teams × 4–7 dims ≈ 540–940 dims with full cross-team covariance) or independent per-team blocks. The distinction is not cosmetic: cross-team covariance is *how* information propagates through the schedule graph (beating a common opponent updates beliefs about transitive opponents). §5.1 rejects GNNs on the grounds that "the Kalman layer already propagates information through the schedule graph optimally under its model" — a claim that is **true only for the joint filter** and false for block-diagonal per-team filters. A joint 940-dim filter is computationally fine (weekly updates of a ~10⁶-entry covariance are trivial on this hardware) but is a very different implementation from what "per team i" suggests, and the epistemic-uncertainty sampling (§2.6, 50 draws) must then draw from the joint posterior, not independent marginals.
- **Recommended fix:** Amend §9.2 to mandate the joint league-state filter with full cross-team covariance (state the dimension and cost), or explicitly accept the block-diagonal approximation and delete/weaken the §5.1 optimality claim and revisit the GNN rejection. Specify that posterior draws for epistemic uncertainty use the joint covariance restricted to the two teams in the game.
- **Confidence:** High that the ambiguity exists and matters; the fix direction (joint filter) is clearly correct at this scale.

---

**B-2. `game_key = (season, home_team, away_team, kickoff_date)` is fragile to reschedules and cross-source matching**

- **Severity:** Medium-High
- **Files:** `TASKS.md` Task 4 item 3; `historical_odds_change_set.md`
- **Problem:** Games get postponed by a day, moved to neutral sites (hurricanes), or flipped home/away. A date-bearing natural key means a Saturday→Sunday move creates a *new* key, orphaning all prior odds snapshots for that game — silently severing the line-movement history the whole odds layer exists to capture. The Odds API and CFBD also disagree on kickoff timestamps around midnight UTC.
- **Recommended fix:** Use CFBD's stable game id as the canonical key; map Odds API events to it via (normalized team pair + kickoff within a ±36h window), with a persisted crosswalk table, ambiguity → quarantine not guess. Keep the derived natural key only as a fallback matcher. Add a test fixture with a postponed game asserting snapshot continuity.
- **Confidence:** High (this class of bug is near-universal in multi-source sports pipelines; the spec itself calls team-name mismatch "the #1 integration bug" but stops one step short).

---

**B-3. Era/rule-change covariates are under-specified for totals (2023 clock rules)**

- **Severity:** Medium
- **Files:** `DESIGN.md` §4.5, §14 risk register
- **Problem:** The 2023 clock-rule change (no clock stop on first downs) reduced plays/game and totals materially — a step change inside the training window. The production feature list has week/month/portal-era but no season-level scoring-era covariate; the risk register hand-waves "era features." The intended absorber is the league scoring-environment state — which is currently unidentified (A-3). Totals models trained across 2014–2025 without either fix will carry a systematic pre/post-2023 bias.
- **Recommended fix:** Fix A-3 so the environment state is identified and feed its posterior into Stage 2 as a feature; additionally add an explicit rule-era categorical (pre-2023 / post-2023 clock era) so the trees can express the step directly. Verify via a per-era totals-bias slice in Task 21.
- **Confidence:** High on the mechanism; the empirical size of the 2023 shift is well documented.

---

**B-4. HPO objective averaging and season weighting under-specified**

- **Severity:** Low
- **Files:** `DESIGN.md` §6; `TASKS.md` Task 18
- **Problem:** "Mean walk-forward validation loss … averaged over the last 3 validation seasons" — unweighted season means over seasons with different game counts (and one possibly COVID-adjacent) give unequal per-game influence; also unstated whether the mean is over per-game losses or per-season means of losses (these differ).
- **Recommended fix:** Specify per-game pooled loss with season-level reporting, or per-season means with explicit rationale; exclude/flag 2020 consistently with §7.2 item 5.
- **Confidence:** High that it's ambiguous; Low impact.

---

**B-5. Monotone constraints exist only on LightGBM members; the stacked ensemble output is unconstrained**

- **Severity:** Low
- **Files:** `DESIGN.md` §5.2; `TASKS.md` Tasks 17, 19
- **Problem:** XGBoost/CatBoost/ENet/NGBoost members are unconstrained; a non-negative combination of constrained and unconstrained members is unconstrained. The "sanity guarantee" (rating increase never decreases predicted margin) is not actually delivered at the system output.
- **Recommended fix:** Either apply monotone constraints to XGB/CatBoost too (both support them), or demote the claim to "regularization on the primary member" and add an ensemble-level monotonicity *monitoring* test (perturbation check on rating-diff features, warn not fail).
- **Confidence:** High.

### 1.C Data Quality

---

**C-1. `event_time = kickoff + game duration estimate` is anti-conservative and violates the project's own conservatism rule**

- **Severity:** Medium
- **Files:** `TASKS.md` Task 5 item 4
- **Problem:** Task 5 instructs: game results get `event_time = kickoff + duration estimate` (actual completion timestamp "if available"), while separately stating the rule "assign the most conservative (latest) defensible time" for endpoints with no timestamp. An underestimated duration (OT games, weather delays) stamps results as knowable *before* they were — formal leakage. Weekly Tuesday as-of makes it mostly moot, but Saturday-night rating updates and T−6h/T−1h refreshes operate near this boundary, and pit_audit will certify correctness against a wrong timestamp.
- **Recommended fix:** Prefer CFBD's actual completion/last-play timestamp; where estimating, use a deliberately generous upper bound (e.g., kickoff + 5h, + more for OT flags) and record `event_time_estimated=True`. Add a validator: no play-by-play row's derived event_time earlier than its game's last recorded play clock time.
- **Confidence:** High on principle; Low-Medium on realized impact.

---

**C-2. Dependency specification is incomplete for downstream tasks (agent will hit walls or improvise)**

- **Severity:** Medium
- **Files:** `TASKS.md` Task 1 item 2 vs. Tasks 9, 19, 21, 25+; `DESIGN.md` §10, §13
- **Problem:** Task 1's pinned dependency list omits packages later tasks require: **dvc** (F10, Task 9 DVC hooks), **scipy** (NNLS/optimization, Task 19), **shap** (SHAP summaries, Tasks 18/21), a plotting/templating stack for HTML reports (jinja2, plotly or similar; matplotlib alone won't produce the specified interactive HTML), **numpyro/jax** (monthly Bayesian cross-check §9.1, sprint R6), **pymc-bart** (R4). `_cursorrules` forbids adding dependencies without asking — so agents will stall or smuggle.
- **Recommended fix:** Amend Task 1's list (production: dvc, scipy, shap, jinja2, plotly; research extra group: numpyro, jax, pymc-bart) and note which are in the dev/research extras.
- **Confidence:** High.

---

**C-3. Duplicate authority documents will diverge**

- **Severity:** Low
- **Files:** `DESIGN.md`, `ncaa_prediction_system_design.md` (byte-identical today); `historical_odds_change_set.md`
- **Problem:** Two identical copies of the spec exist, and a change-set document instructs edits "to `docs/DESIGN.md`" only. First applied edit forks the authority; `_cursorrules` points agents at `docs/DESIGN.md`, but a human or agent reading the other file gets a stale spec. The change set itself is also *unapplied* — the current DESIGN.md does not yet contain the §A1–A5 addenda it mandates "before running any further tasks."
- **Recommended fix:** Delete the duplicate (or make it a symlink/stub pointing at DESIGN.md); apply the change set to DESIGN.md/TASKS.md now and delete or archive the change-set file; record the merge in an ADR.
- **Confidence:** High.

---

**C-4. ET-defined decision points vs. UTC-only engineering rule need explicit reconciliation**

- **Severity:** Low
- **Files:** `historical_odds_change_set.md` Part B item 1; `_cursorrules`; `TASKS.md` Task 2
- **Problem:** Decision points are specified in ET ("Tuesday 06:00 ET") while the codebase mandates UTC-aware timestamps everywhere. ET↔UTC crossing DST transitions (early November — mid-season!) shifts the UTC decision time by an hour; if any component stores the decision point as fixed UTC, the pre-registered snapshot schedule and the walk-forward as-of silently diverge across the DST boundary, breaking snapshot↔decision-point matching for one or two weeks a season.
- **Recommended fix:** Specify decision points as (America/New_York local time, converted per-date with zoneinfo); add DST-week fixtures to Task 2 and Task 5B tests; store both the named decision point and resolved UTC instant on every row (the change set already stores the name — good).
- **Confidence:** High on the hazard; the spec's DST tests suggest awareness, but the linkage to decision points is unstated.

---

**C-5. Odds/CFBD reconciliation and quality gates are strong (positive finding, one gap)**

- **Severity:** Low
- **Files:** `historical_odds_change_set.md` §A3, Part C
- **Problem (gap):** The reconciliation compares CFBD close vs. snapshot close but no validator checks that the *live* capture cadence actually achieved its 6×/day schedule (silent cron death = quietly thinning snapshot coverage, discovered months later). Freshness monitoring (§14) covers "ingestion freshness" generically; snapshot-cadence completeness deserves an explicit expectation (expected vs. actual snapshots per game-week).
- **Recommended fix:** Add a coverage expectation: per (game, decision-window), assert snapshot count ≥ expected minus tolerance; alert on shortfall within 24h.
- **Confidence:** High.

### 1.D Code Quality / QA (specification-level, since no code exists)

---

**D-1. Repository contains no implementation: all engineering claims are unverified**

- **Severity:** Critical (as a status finding, not a design flaw)
- **Files:** entire repo
- **Problem:** Zero source files, tests, configs, CI runs, or notes. Coverage gates, leakage suites, idempotent storage, STALE mode — all exist only as intentions. Per the project's own working agreement ("verify the acceptance criteria yourself … Do not take its word"), the honest current state is: nothing is done.
- **Why it matters:** Any external claim about this system's performance or safety is currently unsupported. Also, per Task 4/§3.4, **every day without the live odds capture running is permanently lost data** — the spec's own most-time-sensitive item is, as far as this snapshot shows, not running.
- **Recommended fix:** Execute Task 1 → 2 → 4a immediately (the spec's own minimum viable odds-capture path); commit the notes files as evidence trail.
- **Confidence:** High (limited only by the possibility that code exists outside the provided snapshot).

---

**D-2. "Bit-for-bit reproducibility" NFR is contradicted by GPU training and asynchronous parallel HPO elsewhere in the same spec**

- **Severity:** Medium
- **Files:** `DESIGN.md` §1.4, §6; `TASKS.md` Tasks 16, 18
- **Problem:** §1.4 demands any prediction be "regenerable bit-for-bit." §6 mandates XGBoost/CatBoost trials on `device=cuda` (GPU tree construction uses non-deterministic atomics unless deterministic modes are forced, with caveats by version) and 4-way asynchronous Optuna parallelism (TPE trial *sequence* depends on completion order → different suggested params across runs). Task 16's determinism test will pass for the harness and fail the moment HPO or GPU members are in the loop, and the agent will face an unresolvable spec conflict.
- **Recommended fix:** Scope the bit-for-bit guarantee precisely: inference and the walk-forward replay given *fixed model artifacts* are bit-for-bit; **training/HPO** are "reproducible to logged artifacts" (every trial's params/seed logged; final refits run single-threaded CPU or with deterministic flags where supported; the champion artifact hash is the reproducibility anchor). Amend §1.4 and Task 18 accordingly.
- **Confidence:** High.

---

**D-3. MLflow-on-SQLite under 4-way process parallelism; live-API tests in acceptance paths**

- **Severity:** Low-Medium
- **Files:** `TASKS.md` Tasks 1, 4, 18; `DESIGN.md` §10
- **Problem:** (1) Four concurrent trial processes logging params/metrics/artifacts to an MLflow SQLite backend will hit `database is locked` under bursty logging; the spec chose journal storage for *Optuna* but SQLite for MLflow. (2) Task 4's acceptance requires a live-API smoke run; nothing states that CI must exclude live-network tests, and CI as specified runs the full pytest suite — flaky CI plus quota burn.
- **Recommended fix:** MLflow with local file-store backend or batched logging (or a single logging writer process); mark live tests `@pytest.mark.live` and exclude from CI by default.
- **Confidence:** High.

---

**D-4. `_cursorrules` file hygiene**

- **Severity:** Low
- **Files:** `_cursorrules`
- **Problem:** CRLF line endings (repo standard elsewhere unstated), and the Style section's final line runs directly into the `# Environment` header without a newline in the source — cosmetic, but this is the file that governs agent behavior; parsing glitches in it have outsized cost. Also it lives at repo root as `_cursorrules` while tooling conventionally expects `.cursorrules`.
- **Recommended fix:** Normalize to LF, fix the missing newline, rename per tool convention, add `.gitattributes`.
- **Confidence:** High on the artifacts observed; Medium on whether the rename matters for the user's tooling.

### 1.E Production Readiness

---

**E-1. Single-machine, single-operator SPOF with no backup/DR specification**

- **Severity:** Medium
- **Files:** `DESIGN.md` §10, §11 (hardware assumption)
- **Problem:** The design is intentionally single-workstation — fine — but the unbackfillable odds archive lives on the same NVMe as everything else, and the spec's only remote mention is "DVC remote = local NAS or S3-compatible bucket" as an aside. Disk failure or house-level event destroys the one dataset money can't re-buy (live snapshots; historical snapshots cost real credits to re-pull). No backup cadence, no restore test, no runbook.
- **Recommended fix:** Mandate: raw odds archive replicated off-machine (S3-class, versioned, lifecycle-policied) within 24h of capture; quarterly restore drill written into the runbooks task (Task 24 item 6); the weekly manifest includes a backup-freshness check with alerting.
- **Confidence:** High.

---

**E-2. Secrets/security posture is adequate for scope, with two small gaps**

- **Severity:** Low
- **Files:** `TASKS.md` Tasks 1, 2; `DESIGN.md` §10
- **Problem:** Env-only secrets, redaction processor, detect-private-key pre-commit: good. Gaps: (1) raw API archival "verbatim" may persist responses whose *request URLs* (if logged alongside) embed API keys — the redaction rule covers log keys by name but the raw-archive pathway is exempted by design; specify that request metadata stored with raw payloads is scrubbed. (2) MLflow/Prefect servers bind locally with no auth — fine until any port-forwarding/tailnet exposure; one sentence in the runbooks should forbid exposing them unauthenticated.
- **Recommended fix:** As above; both are one-line spec amendments plus a test for (1).
- **Confidence:** Medium-High.

---

**E-3. Innovation-flag threshold is so strict it will essentially never fire**

- **Severity:** Low
- **Files:** `DESIGN.md` §9.5; `TASKS.md` Task 14 item 5
- **Problem:** Three consecutive same-signed >2σ standardized innovations has probability ≈ (0.0228)³ ≈ 1.2×10⁻⁵ per window under a correct model; across ~134 teams × ~10 windows/season, expected false flags ≈ 0.02/season — meaning the monitoring channel is effectively dead, and *true* regime changes (which produce, say, 1.0–1.5σ persistent drift) also won't trip it. A monitor that never fires provides false comfort.
- **Recommended fix:** Use a CUSUM on standardized innovations per team (the spec already uses CUSUM for model-level drift — apply the same tool here) tuned to an expected false-alarm rate of ~1–2 flags/week league-wide; keep the 3×2σ rule as a "loud" tier.
- **Confidence:** High on the arithmetic; threshold tuning is judgment.

---

## 2. Scores (0–100)

Two columns because the honest answer differs by object. "Design" scores the specification as the governing scientific artifact, incorporating the findings above. "Implementation" scores what verifiably exists in the repository today.

| Dimension | Design | Implementation | Rationale (design column) |
|---|---|---|---|
| Statistical Soundness | **76** | n/a (no code) | Excellent validation philosophy (walk-forward only, market baselines, pre-registration, CLV-centric) undercut by the CLV measurement defects (A-1), prior-fitting circularity (A-2), calibration-consistency contradiction (A-4), and evaluation-set reuse (A-11). |
| Mathematical Correctness | **72** | n/a | Breakeven/CI arithmetic checks out; but identifiability (A-3), σ scaling (A-5), robust-filter covariance (A-6), NNLS constraint (A-10), unconditional key-number kernel (A-7), and the 2× data-size error (A-14) are real mathematical defects in the governing document. |
| ML Methodology | **85** | n/a | Two-stage architecture, nested HPO isolation enforced at the API level, GBDT-for-tabular reasoning, ablation A2 as a falsification test, quantile+conformal stack — genuinely strong. Deductions: B-1 ambiguity on the core engine, per-market calibration design, monotonicity claim not delivered at ensemble level. |
| Data Quality | **86** | n/a | As-of-join discipline, raw-archive-before-parse, pit_audit-first testing, quarantine flow, the historical-odds change set's timestamp discipline (returned vs. requested) — best-in-class for this scale. Deductions: game_key fragility (B-2), anti-conservative event_time rule (C-1), snapshot-cadence gap (C-5), unapplied change set (C-3). |
| Software Engineering | **82** | **0–5** | Spec-level engineering (typed configs, pandera boundaries, leakage CI suite, feature-signature contracts, golden tests) is excellent. Implementation column: nothing exists to score beyond the docs themselves. Deductions on design: dependency gaps (C-2), reproducibility contradiction (D-2), doc duplication (C-3). |
| Production Readiness | **68** | **0** | Good orchestration/failure-mode design (STALE mode, idempotency, dead-letter, chaos tests) but: no backup/DR for unbackfillable data (E-1), reproducibility NFR unachievable as stated (D-2), monitoring channel that can't fire (E-3), MLflow concurrency (D-3). Implementation: not deployed, not running, and the clock is ticking on unbackfillable odds data. |

Composite (design): **≈ 78/100** — a strong specification with a small number of high-consequence defects, all fixable on paper before they become code.

---

## 3. Prioritized Action Plan (ordered by impact)

1. **Stand up live odds capture now** (Tasks 1 → 2 → 4a per the spec's own fast path). Every day of delay is permanent data loss; nothing else on this list loses value by waiting a week. *(D-1)*
2. **Fix the CLV definition before any bet-layer code exists** — same-book settlement + line-translation for spreads/totals; restate §1.6. This is the headline metric; a biased definition poisons every future decision. *(A-1)*
3. **Apply the historical-odds change set to DESIGN.md/TASKS.md and delete the duplicate spec** — the authority documents must be singular and current before agents run more tasks. *(C-3)*
4. **Amend §9.2/§9.3: joint league-state filter + sum-to-zero identifiability constraint + identified scoring-environment state.** This settles A-3, B-1, and most of B-3 in one spec edit, before Task 14 is built. *(A-3, B-1, B-3)*
5. **Rewrite the prior-fitting procedure to a non-circular target** (diffuse-init late-season ratings or predictive-likelihood fit). *(A-2)*
6. **Replace per-market isotonic with distributional (PIT/quantile) recalibration**, and move consistency property tests post-calibration. *(A-4)*
7. **Specify the variance pipeline exactly once** — σ-head scaling (√(π/2)), robust-filter covariance correction, single non-overlapping aleatoric/epistemic decomposition, simplex-constrained stacking. *(A-5, A-6, A-10)*
8. **Adopt a stable game-key strategy** (CFBD id canonical + crosswalk matching for odds sources) with a postponed-game fixture. *(B-2)*
9. **Add evaluation-integrity guardrails:** lockbox season, promotion ledger with alpha control, conditional key-number kernel, corrected leakage-test suite, conformal claim rephrased/upgraded to adaptive. *(A-7, A-8, A-9, A-11)*
10. **Close the engineering spec gaps:** dependency list, reproducibility scope, live-test CI markers, MLflow backend, decision-point DST semantics, event_time conservatism, snapshot-cadence expectation, off-machine backup of the raw odds archive, CUSUM innovation monitoring. *(C-1, C-2, C-4, C-5, D-2, D-3, E-1, E-3)*
11. **Resolve the SP+ prior-anchor contradiction and the fundamental-model calibration-target policy** in writing. *(A-12)*
12. **Then, and only then, resume the Task sequence** (3, 5, 5B, 6, …) against the amended spec.

---

## 4. Copy-paste prompts for a coding agent

Each prompt is self-contained, one per improvement, in execution order, with built-in verification. They assume the working agreement in `_cursorrules` (one fresh session per prompt; verify acceptance yourself; commit per task). Prompts 1–3 are spec-editing tasks — deliberately, because the spec is the current codebase.

---

**Prompt 1 — Consolidate spec authority and apply the pending change set**

```
TASK AUDIT-1: Spec consolidation. Read docs/DESIGN.md, TASKS.md, and
historical_odds_change_set.md in full before editing anything.

You are editing documentation only. Do not create or modify any code.

1. Apply every edit in historical_odds_change_set.md Parts A, B, and C to
   docs/DESIGN.md and TASKS.md exactly as written: add the §3.2 table row, the two
   §3.4 warnings, the §2.7 closing-line definition replacement, §7.2 item 8, the
   §4.5 availability contract, insert TASK 5B between Tasks 5 and 6, and apply the
   Part C edits to Tasks 4, 7, 16, 20, 21, 23 (including ablation A6).
2. Delete ncaa_prediction_system_design.md. First verify it is byte-identical to
   DESIGN.md (diff them and show the empty output); if it is NOT identical, stop
   and report the differences instead of deleting.
3. Move historical_odds_change_set.md to docs/adr/0002-historical-odds-source.md
   with a one-paragraph preamble stating it has been applied and on what date.
4. Fix _cursorrules: normalize line endings to LF, ensure a blank line before the
   "## Environment" header, and add a .gitattributes enforcing LF for *.md and
   text files. If the repository's Cursor tooling expects the filename
   .cursorrules, rename it and state that you did.

Verification before you finish:
- grep DESIGN.md for "n_books_available", "previous_timestamp", and "Line-source
  regime" and show the matches — all three must now exist in DESIGN.md.
- grep TASKS.md for "TASK 5B" and "A6" and show the matches.
- Confirm ncaa_prediction_system_design.md no longer exists.
- Show `file _cursorrules` (or equivalent) proving LF endings.

Write docs/notes/audit-1.md listing every section you touched.
```

---

**Prompt 2 — Correct the CLV specification (definition-level fix, before any betting code)**

```
TASK AUDIT-2: CLV definition repair. Read docs/DESIGN.md §1.6, §2.7, §12, §7.2 and
TASKS.md Task 20 before editing. Documentation edits only; no code.

Problem being fixed: (a) bets priced at the best price across books but CLV settled
against a consensus close mechanically inflates CLV with zero model skill;
(b) probability-space CLV that de-vigs the closing PRICE at the closing LINE does
not price the bettor's actual ticket when the spread/total line has moved.

Edit DESIGN.md and Task 20 to specify:
1. CLV is settled against the closing price AT THE SAME BOOK whose price was used
   to place/recommend the bet. If that book's close is missing, the row is flagged
   clv_settlement=fallback_consensus and reported separately, never pooled.
2. A separate metric, line_shopping_capture, defined as implied_prob(best captured
   price at bet time) − implied_prob(consensus price at bet time), reported
   alongside CLV so the two sources of value are never conflated.
3. For spread and total bets, CLV must compare probabilities OF THE SAME LINE:
   translate the closing market to the bet's line using, in priority order:
   (i) the book's captured alternate-line price at the bet line, if snapshots
   include it; (ii) the model's own margin/total distribution evaluated at both
   lines to convert the line difference into probability; (iii) if neither is
   possible, report CLV in line units (points of close movement toward the bet)
   with the conversion method recorded per row. The method used is stored on every
   settlement row as clv_method.
4. Restate §1.6's primary success criterion to reference same-book,
   line-translated CLV, and add: line_shopping_capture is excluded from the skill
   criterion.
5. Add to Task 20's tests: a fixture where the line moves from -6.5 to -7 with
   unchanged -110 prices, hand-compute the correct line-translated CLV using a
   given margin distribution, and assert the naive price-only CLV differs — the
   test must demonstrate the bug the definition change prevents.

Verification: show the new §2.7 text in full; show the Task 20 test description;
grep for any remaining instance of settling CLV against "consensus close" outside
the fallback path and fix or justify each. docs/notes/audit-2.md.
```

---

**Prompt 3 — Amend the state-space spec: joint filter, identifiability, era handling**

```
TASK AUDIT-3: State-space spec amendments. Read docs/DESIGN.md §9.2–§9.6, §5.1,
§4.5 and TASKS.md Task 14 in full. Documentation edits only; no code.

Three defects to fix in the spec:

1. JOINT STATE. §9.2 currently reads as per-team states with "full covariance",
   which is ambiguous. Amend it to mandate a single joint league state vector
   (all ~134 teams × state dims, plus league-level states) with full cross-team
   covariance, updated jointly per game (both teams' four measurement equations in
   one update). State the dimension (~540 for v1) and note the per-update cost is
   trivial at this size. Amend §2.6 so epistemic-uncertainty draws sample the two
   game teams' ratings from the JOINT posterior block (preserving their
   covariance), not independent marginals. In §5.1, the claim that the Kalman
   layer propagates schedule information "optimally" is now justified — add a
   footnote that this holds only because the filter is joint.

2. IDENTIFIABILITY. The measurement model off_h − def_a identifies only
   differences; adding a constant to all offenses and defenses is unobservable,
   and the "season scoring-environment drift" state is collinear with that null
   direction. Amend §9.3 to require: offensive and defensive states are
   constrained to league-mean zero after every update (constraint projection or a
   zero-noise pseudo-observation — specify projection), and the single league
   scoring-environment state carries the level, making it identified as the
   league-mean efficiency environment. Add to Task 14's tests: an invariance test
   — shift all initial states by a constant, run the filter, assert constrained
   states and all predictions are unchanged to numerical tolerance.

3. ERA COVARIATE. Add to §4.5's situational features an explicit rule-era
   categorical (at minimum: pre-2023 vs post-2023 clock rules), with hypothesis
   text referencing the 2023 clock-rule change's effect on plays and totals. Add
   the identified scoring-environment posterior mean as a Stage-2 feature. Add to
   Task 21's slice analysis: totals bias per rule era.

Verification: show the amended §9.2, §9.3 text; show the new Task 14 invariance
test description; grep §4.5 for the era feature. docs/notes/audit-3.md.
```

---

**Prompt 4 — Amend prior fitting, calibration architecture, and variance pipeline in the spec**

```
TASK AUDIT-4: Statistical spec repairs (priors, calibration, variance). Read
docs/DESIGN.md §2.4, §2.6, §5.2, §9.5, §9.6 and TASKS.md Tasks 14, 15, 17, 19.
Documentation edits only; no code.

1. PRIOR FITTING (circularity). §9.6/Task 15 currently fit prior weights by
   regressing next-season EARLY ratings on prior components — but early ratings
   are prior-dominated, so the regression recovers the assumed weights. Rewrite:
   weights are fit by regressing each season's LATE-SEASON (≥8 games) posterior
   ratings FROM A DIFFUSE-INITIALIZATION FILTER RUN on the preseason predictors,
   over 2015–2024; alternatively (state as the preferred upgrade) by maximizing
   Weeks 1–4 one-step-ahead predictive likelihood with respect to the weights.
   Task 15's out-of-sample acceptance must score priors against realized GAME
   OBSERVATIONS or diffuse-run late ratings, never against prior-initialized
   early posteriors. Add a test: fitting against prior-initialized early ratings
   (the old way) on synthetic data recovers the planted PRIOR weights even when
   the true generative weights differ — demonstrating the circularity the change
   prevents.

2. CALIBRATION (consistency). §5.2 Level 2 / Task 19 currently apply separate
   isotonic maps per market, which breaks the §2.2 internal-consistency guarantee.
   Replace with distributional recalibration: a single monotone map fit on the
   PIT values of the margin distribution (and one for total) on OOF predictions,
   so ALL derived market probabilities recalibrate coherently. Per-market
   reliability diagrams remain as diagnostics only. Move Task 19's consistency
   property test (P(win) == P(cover at 0)) to run on POST-calibration outputs.
   For the fundamental stack, calibration targets must be market-free (moneyline/
   distribution only); ATS@close reliability is reported as a diagnostic, not fit.
   Also resolve the SP+ contradiction: state explicitly that SP+ appears in the
   §9.6 prior blend for the MARKET-AWARE stack only [or: nowhere in priors —
   choose one, record the decision in an ADR], and reconcile §3.1's wording.

3. VARIANCE PIPELINE. Specify exactly once, in §5.2: (a) the σ-head is trained on
   absolute residuals of the STACKED mean and multiplied by sqrt(pi/2) to be an
   unbiased σ estimate under normality (or trained on squared residuals targeting
   σ² — pick one, document it); (b) total predictive variance = σ-head aleatoric
   variance + member-disagreement variance + Stage-1 posterior-draw variance,
   with an explicit statement that the σ-head is fit on residuals that EXCLUDE
   the epistemic components already counted (fit it on OOF residuals net of the
   member-mean, and say so); (c) the §9.5 winsorized Kalman update must inflate
   the effective observation noise for clipped observations
   (R_eff = R * (|z|/2.5)^2 when |z| > 2.5) so the covariance update is
   consistent with the dampened state update; (d) Level-1 stacking is
   simplex-constrained least squares (weights ≥ 0, sum to 1, solved as a
   constrained QP), not plain NNLS with post-hoc normalization; state the
   no-intercept decision and its rationale.
   Add required tests to Tasks 14/17/19: σ unbiasedness on synthetic
   heteroskedastic data; clipped update shrinks variance strictly less than an
   unclipped one; stacking weights on the simplex by construction.

Verification: show the rewritten §9.6 fitting paragraph, the new Level-2 text,
and the §5.2 variance paragraph in full. docs/notes/audit-4.md.
```

---

**Prompt 5 — Evaluation-integrity amendments (lockbox, multiplicity, leakage tests, conformal, key numbers)**

```
TASK AUDIT-5: Evaluation-integrity spec amendments. Read docs/DESIGN.md §2.3,
§2.6, §7, §8 item 7, §14 and TASKS.md Tasks 16, 18, 19, 22, 23. Documentation
edits only; no code.

1. LOCKBOX SEASON. Add to §7.2: one season (the most recent completed season at
   time of writing — name it) is a lockbox, excluded from ALL development,
   HPO, ablation, and promotion evaluations; it may be read at most once per
   calendar year for a confirmatory report, and every read is logged in
   docs/lockbox_access.md. The Task 18 quarantine-tiebreak season must be a
   DIFFERENT season from the lockbox. Update Task 23's season list accordingly.

2. PROMOTION MULTIPLICITY. Amend §8 item 7 / Task 22: the registry maintains a
   promotion-attempt ledger; within each calendar year the promotion alpha is
   Bonferroni-adjusted by the number of attempts recorded that year (document the
   exact rule); the comparison report prints the attempt count and adjusted
   threshold. Live forward performance (paper-trade per §16 item 2) is named in
   §1.6 as the confirmatory instrument for the success criteria.

3. LEAKAGE SUITE. In §14 and Task 16, DELETE the shifted-label test as specified
   ("future features predicting past games must score at chance" — this null is
   wrong: strength persistence makes future features legitimately predictive of
   past games). Replace with: (a) within-week label permutation test — models
   trained on permuted labels must score ≈ chance out-of-sample; (b) a planted
   prophecy test — a deliberately future-leaking feature is added in a test
   fixture and BOTH pit_audit and the information-set audit must catch it;
   (c) the existing pit_audit and information-set audit, unchanged.

4. CONFORMAL. In §2.6 and Task 19, replace "distribution-free guarantee layer"
   with accurate language: split-conformal coverage is guaranteed under
   exchangeability, which season-over-season drift violates; the layer therefore
   provides approximate coverage, monitored weekly. Specify Adaptive Conformal
   Inference (online alpha adjustment) as the production variant, with the
   trailing-2-season split-conformal as the initializer.

5. KEY NUMBERS. In §2.3 and Task 19 item 6: the discretization kernel is learned
   CONDITIONAL on the predicted margin (buckets of mu_M at minimum; a smooth
   model of P(M=k | mu_M, sigma_M) preferred). Add a validation: empirical
   exact-margin frequencies by predicted-spread bucket vs kernel output.

6. Fix the data-size figures in §0.3 and §9.7: 2014–2025 FBS is roughly 10k
   games, not 20k; recompute the "~60 games add <1%" statement and keep it only
   if still true (it is: ~0.6–0.7%).

Verification: show each replaced passage before/after; show the new §7.2 lockbox
text; grep for "20,000" and "20k" and confirm both are corrected.
docs/notes/audit-5.md.
```

---

**Prompt 6 — Engineering spec closure (dependencies, reproducibility scope, keys, timestamps, ops)**

```
TASK AUDIT-6: Engineering spec closure. Read TASKS.md Tasks 1, 2, 4, 5, 5B, 7, 18
and docs/DESIGN.md §1.4, §6, §10. Documentation edits only; no code.

1. DEPENDENCIES. Amend Task 1's dependency list: add dvc, scipy, shap, jinja2,
   plotly to the main group; create a "research" extra with numpyro, jax,
   pymc-bart. Note that _cursorrules forbids un-approved dependency additions, so
   this amendment is the approval.

2. REPRODUCIBILITY SCOPE. Amend §1.4: bit-for-bit reproducibility applies to
   inference and walk-forward replay GIVEN fixed model artifacts (pinned by hash);
   training and HPO are reproducible-to-artifacts (every trial's params and seed
   logged; final champion refits run with deterministic settings — CPU or
   framework deterministic mode — and the artifact hash is the anchor). Amend §6
   and Task 18 to note asynchronous parallel TPE is not run-order deterministic
   and that this is why the artifact, not the search, is the reproducibility unit.
   Task 16's determinism test scope is updated to match.

3. GAME KEY. Amend Task 4 item 3 and Task 5: the canonical game key is CFBD's
   stable game id. Odds API events are matched to it via normalized team pair +
   kickoff within ±36h, persisted in a crosswalk table; ambiguous matches are
   quarantined, never guessed. The derived (season, teams, date) key is retained
   only as a matcher input. Add a required test fixture: a game postponed by one
   day retains a single key and continuous snapshot history.

4. EVENT-TIME CONSERVATISM. Amend Task 5 item 4: game-result event_time uses the
   actual completion timestamp when available; otherwise kickoff + a deliberately
   generous upper bound (5h; more when OT is flagged), with
   event_time_estimated=True recorded. The "most conservative (latest) defensible
   time" rule applies to results too, not only timestamp-free endpoints.

5. DECISION-POINT TIME SEMANTICS. Amend Task 5B item 1 and Task 2: decision
   points are defined in America/New_York local time and resolved to UTC per
   date via zoneinfo; DST-transition weeks (early November) get explicit test
   fixtures in both tasks; every snapshot row stores both the decision-point name
   and the resolved UTC instant.

6. OPS. Amend §10 and Tasks 1/7/24: (a) MLflow backend is a local file store (or
   a single-writer logging pattern) — not SQLite under 4-way parallel logging;
   (b) tests requiring live network are marked pytest.mark.live and excluded
   from CI; (c) add a data-quality expectation: per (game, decision window),
   captured snapshot count >= expected minus tolerance, alerting within 24h of a
   cadence shortfall; (d) add to §10 and Task 24's runbooks: the raw odds archive
   is replicated off-machine (versioned S3-class target) within 24h of capture,
   with a quarterly restore drill; MLflow/Prefect UIs must never be exposed
   off-host without auth; raw-archive request metadata is scrubbed of API keys,
   with a test; (e) replace the §9.5 3-consecutive->2-sigma team flag with a
   per-team CUSUM on standardized innovations tuned to ~1-2 expected flags per
   week league-wide, keeping the 3x2-sigma rule as a loud tier.

Verification: show the amended Task 1 dependency block, the new §1.4 text, the
game-key paragraph, and grep Task 5B for "zoneinfo". docs/notes/audit-6.md.
```

---

**Prompt 7 — Execute the minimum viable odds-capture path (first code)**

```
TASK AUDIT-7: Execute TASKS.md Task 1 (repository scaffold) exactly as written in
the AMENDED TASKS.md (post AUDIT-1..6), then STOP.

Follow _cursorrules. Implement only Task 1's deliverables. Before finishing,
verify and show output for every acceptance item:
- make install && make lint && make typecheck && make test all pass
- docker build . succeeds (build only — per _cursorrules, do not run it)
- python -m ncaa_quant.cli --help lists all command groups
- The dependency list matches the AUDIT-6 amendment (grep pyproject.toml for
  dvc, scipy, shap and show the lines)

Write docs/notes/01.md. Commit as feat(task01): repository scaffold.
Do not proceed to Task 2.
```

---

**Prompt 8 — Config/utilities, then live capture online**

```
TASK AUDIT-8: Execute TASKS.md Task 2 exactly as written (amended), verify all
acceptance items yourself with shown output (config precedence test, secret-
redaction test, the January-2-bowl-maps-to-prior-season test, DST fixtures per
AUDIT-6 item 5), write docs/notes/02.md, commit.

Then, in the same session per the spec's own fast-path note ("1 -> 2 -> 4a"),
implement Task 4 deliverable 2 ONLY (raw archival: fetch the live Odds API
response and write it verbatim to data/raw/odds_api/{date}/{captured_at}.json,
with retries and the rate-limit reserve guard) plus a minimal cron/Prefect
schedule at 6x/day, and START IT. Show me: one raw file on disk with its
timestamp, and the schedule registered. Normalization, dedupe, and the rest of
Task 4 come next session — the point is that from today, no snapshot is lost.

docs/notes/02.md and docs/notes/04a.md. Commit separately:
feat(task02) and feat(task04a-raw-capture).
```

---

**Prompt 9 — Storage/schemas with the audited key and boundary semantics**

```
TASK AUDIT-9: Execute TASKS.md Task 3 exactly as written (amended). Pay specific
attention to the audit-driven requirements:
- game identity follows the AUDIT-6 canonical-key rule (CFBD game id canonical;
  crosswalk table schema included even though population comes later)
- as_of_join boundary semantics: right.event_time == as_of is EXCLUDED — show
  the passing boundary test output
- partition rewrite idempotency: show identical file hashes from two writes

Verify every acceptance item with shown output. docs/notes/03.md. Commit.
Do not start Task 4's remainder.
```

---

**Prompt 10 — Complete the odds ingester against the amended spec**

```
TASK AUDIT-10: Complete TASKS.md Task 4 (amended) on top of the AUDIT-8 raw
capture: normalization to the odds_snapshots schema (including snapshot_source,
decision_point, n_books_available per the applied change set), dedupe,
team-name normalization map in configs/ with fixtures, Prefect deployment
migration, CLI --once.

Audit-specific verifications to show:
- the postponed-game fixture: one key, continuous snapshot history (AUDIT-6.3)
- raw-archival-before-parse: make the parser throw, show the raw file survives
- running twice in the same minute does not duplicate rows
- live tests are marked pytest.mark.live and CI config excludes them

docs/notes/04.md. Commit. The live capture schedule must remain running
throughout — prove it by showing a snapshot captured during your session.
```

---

**Prompt 11 — Continue the amended task sequence**

```
TASK AUDIT-11 (repeatable template): Execute the next unstarted task in the
amended TASKS.md order (5, 5B, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18,
19, 20, 21, 22, 23, 24), one task per session, exactly as written.

Standing audit obligations layered on every task:
- Verify every acceptance item yourself and paste the actual output; if any
  fails, fix or report — never claim done on assertion.
- For Tasks 14/15/17/19/20: the audit-mandated tests from AUDIT-3/4/5 (filter
  shift-invariance; circularity demonstration; sigma unbiasedness; clipped-update
  variance; simplex weights; post-calibration consistency; conditional
  key-number validation; line-translated CLV fixture) are acceptance-blocking.
  List each with its pass/fail in the notes file.
- For Task 16: the information-set audit and the AUDIT-5 leakage suite replace
  the deleted shifted-label test; the line-source fallback ladder is logged per
  game per the applied change set.
- For Task 23: report every number the acceptance demands without spin, including
  the A2 ablation delta and the A6 snapshot-vs-CFBD delta; a lockbox-season read
  is FORBIDDEN in this task.
- Any spec ambiguity: stop and record it in docs/adr/, per _cursorrules.

docs/notes/NN.md per task. One commit per task.
```

---

## 5. Closing assessment

The specification's philosophy is right: state/mapping separation, walk-forward-only evaluation, point-in-time correctness as the cardinal rule, CLV as the arbiter, pre-registration, and an unusual willingness to define failure conditions (ablation A2, the §16 uncertainty list). Most retail sports-model projects fail on exactly the disciplines this document gets right.

What the audit adds is that four of the load-bearing quantitative definitions — CLV settlement, prior-weight fitting, calibration architecture, and the state-space measurement model — contain errors or contradictions that would survive the project's own (excellent) test regime, because the tests would faithfully verify the wrong definition. All four are cheap to fix now and expensive to fix after Tasks 14–23 are built on them. Fix the spec first (Prompts 1–6), get the odds capture running (Prompts 7–8), then build.
