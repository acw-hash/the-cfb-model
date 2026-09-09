# Finding — `rating_uncertainty` is a signed feature fitted on unsigned evidence

Date: 2026-09-08. Season 2026, week 1, n=99 graded.
Status: root cause established, **no fix applied**. Decision required.
Working dumps: `data/tmp/epistemic_mix_coherence_w1.json`,
`gap_region_quantile_diag.json`, `mono_constraint_ablation.json`,
`mu_decomposition_failures.json`.

## Summary

The ENet member of the margin stack carries a large positive coefficient on
`rating_uncertainty` (coef_z +4.05, raw slope ≈ +66 margin points per unit).
On games where a team has no rating history, this single term contributes the
majority of the published point forecast — +59.6 of BYU–Utah Tech's +76.7 ENet
μ. It is what pushes published μ above its own q90 on 15 of 49 pooled-prior
games and 0 of 50 full-history games.

The feature is the sum of Kalman posterior SDs on `off_epa` for both teams
(`production_stack.py` 444–446), defaulting to 1.0 for a missing side. It
measures **how little the model knows**, and the ENet has learned to read that
as a large home margin.

It is not a defect to remove. Dropping it makes all 17 coherence failures
coherent and moves week-1 MAE from **16.27 to 24.39**. The feature is buying
substantial accuracy.

## The structural flaw

In the training frame (n=4943), 555 rows have a side absent from
`filter_history`. **All 555 are the visitor. Home-absent n = 0.**

So "uncertainty is high" and "the home team wins big" always co-occurred in
training. The ENet fused them into one fact. `rating_uncertainty` is
symmetric — it sums both teams' SDs and cannot distinguish which side is
uncertain — but it was fitted on evidence that was entirely one-sided. It
carries a direction it has no way to justify.

It works when the uncertain visitor is genuinely bad. It fails when the
uncertain team is good, or at home, and it cannot tell the difference because
it is not measuring the opponent at all.

Week 1 2026 shows both faces. The three largest misses in the original
handoff were FBS home favorites over FCS visitors that lost outright — Utah
State +40.3 (lost by 12 to Idaho State), Bowling Green +32.7 (lost by 7 to
Tarleton State), Charlotte +31.8 (lost by 2 to The Citadel). Same feature,
same direction, wrong answer.

## Extrapolation range

| slice | n | mean | p50 | p90 | p99 | max |
|---|---|---|---|---|---|---|
| training, overall | 4943 | 0.182 | 0.166 | 0.237 | 0.361 | 0.447 |
| training, both in filter_history | 4388 | 0.173 | 0.164 | 0.193 | 0.315 | 0.447 |
| training, either absent | 555 | 0.254 | 0.247 | 0.337 | 0.413 | 0.416 |
| **week-1 coherent games** | — | **0.53** | | | | |
| **week-1 coherence failures** | — | **1.10** | | | | |

Week-1 values run about 6× the training mean and well past the training max,
because preseason posteriors are wide. Production is extrapolating an
unconstrained linear term far outside its fitted range, at 0.616 stack weight.

## Decomposition (17 post-mix coherence failures)

| component | mean |
|---|---|
| μ LGBM member | 20.3 |
| μ ENet member | 66.8 |
| μ stacked (pre-mix) | 49.0 |
| μ post-mix | 44.0 |
| q90 | 41.6 |
| realized margin | 44.4 |
| excess (stack − LGBM) | +28.6 = w_enet × (ENet − LGBM) |
| mix shift | −5.0 |

Stack weights: LGBM 0.384, ENet 0.616. The ENet is the excess on 17/17. The
epistemic mix *reduces* it and never adds to it.

Note that realized margin (44.4) is closer to the inflated μ (44.0) than to
q90 (41.6). On these games the proxy was right. That is what makes this a
judgment call rather than a bug fix.

## What was ruled out along the way

Each of these was a live hypothesis, tested and discarded:

- **Snapshot age / grading precedence.** Paired re-grade, Aug 27 vs Sept 1,
  91 identical games: mean AE delta −0.28, 95% CI [−0.77, 0.16]. No effect.
  (Weak evidence — only 8 games were played between the snapshots.)
- **Pooled prior hurting point accuracy.** It does not. Absent-side MAE 15.56
  vs full-history 16.99.
- **MAE worse than a naive baseline.** False. Model 16.29 vs constant home +3
  at 28.38, best hindsight constant (+20) at 19.67.
- **Epistemic mix causing the incoherence.** It reduces it: 0 games flip
  inside→outside, 17 flip outside→inside.
- **Sparse training data in the extreme-gap tail.** No. 900–1200 training
  games per gap region; q90 sits at ECDF ≈0.94 of realized margins there.
- **Unstable quantile boosters.** No. 0/17 crossings, raw equals sorted.
- **Monotone constraints on the μ head.** No. The constrained head was never
  outside the band (17/17 inside); unconstrained μ moves *down*, away from
  q90, and week-1 MAE worsens 19.93 → 21.33. Realized margins also steepen
  through the top gap decile rather than flattening, so the constraint is
  right about the physics.
- **`ensemble_weight_dampen = 0.7` (§9.7) being related.** It is not — that is
  a monthly temporal EMA on weight updates (`new = 0.7·old + 0.3·fit`), not
  member dampening. Still unimplemented in `src/`; remains its own item.

## Options

1. **Leave it.** Accept the incoherence; the gate suppresses the interval and
   now records `null_reason: incoherent_margin_interval`. Point accuracy
   preserved. The failure mode remains live and will misfire whenever high
   uncertainty stops meaning "weak FCS visitor."
2. **Sign the feature.** Replace the symmetric sum with a signed differential
   (home SD − away SD, or similar), so it goes negative when the uncertain
   team is at home. Fixes the structural flaw rather than the symptom.
   Requires a refit and a walkforward eval.
3. **Constrain or dampen the ENet member** at extreme feature values, or cap
   `rating_uncertainty` at its training p99. Bounds the extrapolation without
   changing the fit.
4. **Drop the feature.** Fixes coherence completely, costs ~8 MAE points.
   Not recommended on this evidence.

Options 2 and 3 are modeling changes and need an eval, not a patch.

## Do not

- Widen the quantile band to restore coherence. The band is correct — q90
  sits where realized margins are, and the boosters are stable and ordered.
  Widening would hide the disagreement rather than resolve it.
- Shift the band by the mix delta. That translates rather than inflates and
  leaves the interval too narrow; CQR conformalization sits downstream of
  both.
- Read week-1 point accuracy as validation of the feature. It was right on
  average because week 1 is mostly FBS-over-FCS. That is the condition under
  which the proxy holds.

## Open questions

- Does the same coefficient appear in prior-season champion fits, or is it
  specific to this one?
- What does the feature do in later weeks, once posteriors narrow and
  `rating_uncertainty` returns toward its training range?
- Should `filter_history` carry 2026 rows at all? It currently spans
  2014–2025, which is why preseason uncertainty is so wide. Related to
  W-RATINGS-WIRE (`team_ratings_2026.json` shipping empty).

## Standing note

The coherence gate is currently the only thing preventing a published
interval that excludes the model's own point estimate. It fired 15 times in
week 1 and refused. Until one of the options above lands, it is load-bearing.
