# W9-G — ATS grading defects: align CI sample; drop invented missing-σ p

**Date:** 2026-08-17  
**Status:** DONE — regrade, not a refit. No walk-forward, no promotion, no
site restamp.  
**Authority:** `docs/notes/webapp-w9r.md` Phase 0.3; `docs/notes/23-reval.md`;
ADR 0014; DESIGN §1.6.

Two stacked defects in the W9-A ATS grader. This task removes both, re-derives
only the affected numbers, and amends `23-reval.md`. Phase 1 restamp remains
W9-R.

---

## Fixes

1. **`attach_metric_cis`** (`src/ncaa_quant/evaluation/metrics.py`) — ATS
   interval sample is now `isfinite(p) & isfinite(y)`, matching
   `binary_accuracy`. A NaN probability is excluded, never scored as an away
   pick via `(nan >= 0.5) == False`. General, not a 2019 patch.
   `_regime_ats` asserts rate n == bootstrap n == naive n per regime.
2. **`_p_ats_gaussian`** (`scripts/_ats_regrade.py`) — missing σ stays NaN.
   The p∈{0.999, 0.001, 0.5} hard-edge fallback is gone. No flag.

Tests: `tests/unit/test_w9g_ats_grading.py`.

---

## Log-loss hypothesis (fundamental 2019)

Published W9-A ATS log-loss was 1.350 (n=657) vs snapshots 0.931.
Hypothesis: that gap is the invented hard-edge rows (a wrong pick at
p=0.001 costs 6.908).

```
n_2019_headline=763
n_entered_logloss (W9-A)=657
n_missing_sigma_finite_mu=110
n_invented_p_entering_logloss=104
n_invented_wrong=66
invented_p_uniques={0.001, 0.999}
invented_sum_contrib=455.94986742549713
invented_mean_contrib=4.38413334062978
published_mean_logloss=1.3499203258962642
remaining_n=553
remaining_mean_logloss=0.7792907535051509
```

104, not 110, entered the 657: 110 missing-σ rows, 109 received an invented
finite p (one lacked a finite spread), 104 of those had decided y. Expected
657−110=547 assumed every missing-σ row was in the rate; 657−104=553 is the
honest remainder.

66 wrong × −log(0.001) ≈ 455.91 plus 38 correct at p=0.999 ≈ 0.038 accounts
for the 455.95 sum. Removing them drops mean log-loss **1.350 → 0.779**.

**Hypothesis holds on the inflation, not on the 0.93 landing.** Invented
rows were the 1.35. Remaining 2019 is **0.779**, not ≈ snapshot 0.931, so
`ats_logloss_band` is restated as **0.78–0.93**, not collapsed to one number.

A2 2019: 109 invented entered the 662; 71 wrong; remaining n=553, LL
1.435 → 0.831.

---

## Affected metrics (before / after)

Old = W9-A first pass (`metrics_summary.json`). New = this regrade
(`docs/notes/_artifacts/webapp-w9g/acceptance.json`). Snapshots: zero
missing-σ rows; numbers byte-identical.

| id | old | new | old n | new n |
|---|---|---|---:|---:|
| `fund_ats_2019` | 47.8% [46.9%, 51.1%] | 49.9% [46.9%, 52.3%] | 657 | 553 |
| `fund_ats_snapshots` | 48.9% [47.5%, 50.5%] | 48.9% [47.5%, 50.5%] (identical) | 3496 | 3496 |
| A2 ATS 2019 | 43.8% [41.7%, 48.2%] | 45.6% [41.5%, 49.8%] | 662 | 553 |
| A2 ATS snapshots | 50.9% [49.8%, 52.2%] | 50.9% [49.8%, 52.2%] (identical) | 3496 | 3496 |
| `ats_logloss_band` (fund) | 0.93–1.35 vs 0.693 | 0.78–0.93 vs 0.693 | — | — |
| fund 2019 / snap LL | 1.350 / 0.931 | 0.779 / 0.931 | 657 / 3496 | 553 / 3496 |
| A2 2019 / snap LL | 1.435 / 0.935 | 0.831 / 0.935 | 662 / 3496 | 553 / 3496 |
| `scorecard_fund_ats` | MISSED (48.9% / 47.8%) | MISSED (48.9% / 49.9%) | — | — |
| `scorecard_logloss` | MISSED 0.93–1.35 | MISSED 0.78–0.93 | — | — |

2019 fund ATS +2.1 pp is the 104 invented rows leaving (66 wrong). Not a
third defect. Naive Wald now centers on the published rate (49.91% mid vs
49.91% rate).

---

## A2 plausibility guard (corrected sample)

```
n=553  rate=45.5696%
band z=3: [43.6214%, 56.3786%]
inside=true  tripped=false
```

43.81% on n=662 is gone with the invented rows. The guard does **not** fire.
Band not retuned.

---

## MAE / CRPS / OU — comparison, not an assertion

Compared to `23-reval.md` / W9-A `metrics_summary.json`. Equal on every
float and n:

```
fund MAE  14.533427013627348 n=4285 == 14.533427013627348 n=4285
fund CRPS 10.023904530821943 n=4175 == 10.023904530821943 n=4175
A2   MAE  15.506907425348972 n=4290 == 15.506907425348972 n=4290
A2   CRPS 10.754633859204280 n=4175 == 10.754633859204280 n=4175
fund OU 2019 0.5136116152450091 n=551 (CIs identical)
fund OU snap 0.5146683673469388 n=3136 (CIs identical)
A2   OU 2019 0.5045372050816697 n=551 (CIs identical)
A2   OU snap 0.5229591836734694 n=3136 (CIs identical)
ou_equal=true  mae_equal=true  crps_equal=true
```

---

## Denominator equality (every regime)

```
fund cfbd_2019           n_rate=553  n_ci_boot=553  n_ci_naive=553  equal=true
fund snapshots_2021_2024 n_rate=3496 n_ci_boot=3496 n_ci_naive=3496 equal=true
A2   cfbd_2019           n_rate=553  n_ci_boot=553  n_ci_naive=553  equal=true
A2   snapshots_2021_2024 n_rate=3496 n_ci_boot=3496 n_ci_naive=3496 equal=true
```

---

## Live path (deliverable 5) — does **not** share either defect

`src/ncaa_quant/webapp/grade.py` grades 2026+ `results_<season>.json` from
pre-kickoff published artifacts (`p_win_home`, margin/total intervals). It
does not import `_p_ats_gaussian`, does not call `attach_metric_cis`, and
does not compute ATS Φ((μ+S)/σ). Scope was **not** extended.

---

## Provenance

```
input parquet:
  data/backtests/task23_fundamental_reduced_v3/full/predictions.parquet
  mtime_utc=2026-08-17T20:41:46.173748+00:00
  size=1325254
  run_id=task23_fundamental_reduced_v3
  model_version=production-v0_reduced_v3
  n=4944
  N_2025=0

A2 input:
  data/backtests/task23_a2_reduced_v2/A2_frozen_after_week_1/predictions.parquet
  mtime_utc=2026-08-17T22:11:32.484988+00:00
  size=1320814
  run_id=task23_a2_reduced_v2
  model_version=production-v0_a2_reduced_v2
  n=4944
  N_2025=0
```

Same walk-forward as W9-A. Grade parquet rewritten in-place under each
`grade_v2/` after dropping invented p; Φ on finite-σ rows unchanged
(`max_abs_delta_kept_vs_gaussian=0.0`). n_cleared snapshots = 0.

---

## STOP AND REPORT (this run)

1. Live grading path shares either defect — **no**. Reported above; scope not
   extended.
2. Snapshot-regime number moves — **no**. Byte-identical to W9-A.
3. Corrected 2019 ATS rate moves by more than a few points — **+2.1 pp**
   (47.8% → 49.9%). Fully accounted for by 66/104 invented rows being wrong.
   Not a third issue.
4. MAE, CRPS, or OU values change — **no**. Byte-identical.
5. A2 guard still fires after correction — **no**. 45.57% inside
   [43.62%, 56.38%] on n=553.
6. CI-mask fix changes any metric outside ATS — **no**. Snapshots (zero NaN
   p) unchanged; OU CIs already used `isfinite(p) & isfinite(y)`.

Ambiguity: the brief’s expected remaining n=547 assumed all 110 missing-σ
rows were in the 657. Empirically 104 were. Recorded, not retuned.

No hyperparameters, ADR 0014 thresholds, or CQR changed. `23-readout.md`
untouched. No R2 write. No 2025. W9-R Phase 1 not started.
