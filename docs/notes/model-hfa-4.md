# MODEL-HFA-4 — reduced_v4 candidate: explicit home-site term

**Date:** 2026-10-08  
**Scope:** Build + walkforward + readout. **No promotion**, no registry/champion change, no publish/export changes.  
**Artifacts:** `docs/notes/_artifacts/model-hfa-4/{summary.json,run_hfa4_eval.py,walkforward.out}`  
**Config:** `configs/ablations/task23_fundamental_full_reduced_v4.yaml`  
**Predictions:** `data/backtests/task23_fundamental_reduced_v4/full/predictions.parquet`

---

## What changed

| piece | v3 | v4 |
|---|---|---|
| Stage-2 feature | ratings / pace / possessions only | + `is_home_site` = 0 if `neutral_site` else 1 |
| ElasticNet | sklearn default `fit_intercept=True` | `fit_intercept=False`; `is_home_site` force-included and **unpenalized** (Enet on other cols, then OLS of residual on `[1 \| is_home_site]`) |
| Other members | unchanged feature set | same `is_home_site` column |
| Fit windows | 2019 + 2021–24 (2020 continuity) | same; **2026 continuity + `freeze_mapping_from_season=2026`** so 2026 labels never enter a retrain |

Penalty note: sklearn ElasticNet has no per-feature `penalty.factor`. Exclusion is by sequential residual OLS, so the reported `is_home_site` coef is **not shrunk** by α/L1.

### Fitted home edge (final mapping fit before 2026)

From `home_edge.json` (predict with `is_home_site` 1 vs 0, all else equal):

| member | implied home edge (pts) |
|---|---:|
| LGBM μ | **+4.62** |
| ElasticNet μ | **+5.46** (= unpenalized `is_home_site` coef) |
| Enet residual intercept (neutral baseline after strength) | +1.81 |
| Enet sklearn `intercept_` | 0.00 (as configured) |
| NNLS weights (LGBM / Enet) | 0.612 / 0.388 |
| **Stacked** | **+4.95** |

Compare HFA-3 production v3: Enet intercept ≈ +7.4 × weight ≈ **+4.6** pts applied on **home and neutral alike**. v4 moves that mass onto a gated site term.

---

## Walkforward 2019–24 (headline folds) — v4 vs v3

Bootstrap 95% CIs, seed `20261008`, `n_boot=2000`.  
Slices: FBS–FBS home, FCS-opp (non-neutral), neutral regular, neutral postseason.

### 1. Residual intercept `a` (`actual = a + b·μ`)

| slice | v3 a [95% CI] | v4 a [95% CI] |
|---|---|---|
| overall | +1.17 [+0.49, +1.82] | +0.55 [−0.09, +1.19] |
| FBS–FBS home | +0.04 [−0.66, +0.74] | **−0.38 [−1.06, +0.30]** |
| FCS games | +21.5 [+18.4, +24.3] | +17.7 [+14.8, +20.6] |
| neutral regular | −2.55 [−6.22, +0.66] | **+1.03 [−2.23, +4.32]** |
| neutral postseason | −1.22 [−4.35, +1.73] | −0.67 [−3.33, +1.94] |

### 2. MAE / CRPS (margin)

| metric / slice | v3 | v4 |
|---|---|---|
| MAE overall | 14.53 [14.19, 14.89] | **14.24 [13.90, 14.58]** |
| MAE FBS–FBS home | 13.90 [13.52, 14.27] | 13.85 [13.52, 14.22] |
| MAE neutral regular | 14.88 [12.92, 16.91] | 14.42 [12.45, 16.53] |
| CRPS overall | 10.02 [9.82, 10.23] | **9.96 [9.75, 10.18]** |

### 3. Interval coverage vs nominal

CQR-style intervals on the walkforward rows (nominal ≈ 0.80):

| | empirical | Δ vs nominal |
|---|---:|---:|
| v3 | 88.1% | **+8.1 pp** |
| v4 | 88.6% | **+8.6 pp** |

Both over-cover by more than ±3 pp (pre-existing; not introduced uniquely by v4).

### 4. `p_win_home` log-loss / Brier

| source | v3 LL / Brier | v4 LL / Brier |
|---|---|---|
| calibrated | 0.555 / 0.188 | **0.549 / 0.186** |
| Φ(μ/σ) | 0.548 / 0.186 | **0.544 / 0.184** |
| raw MC | 0.551 / 0.187 | **0.548 / 0.186** |

### 5. Tier shares vs W1A pool (17.3 / 32.9 / 30.8 / 18.9)

| tier | W1A pool | v3 WF | v4 WF | v4 − v3 (pp) |
|---|---:|---:|---:|---:|
| strong_lean | 17.3 | 23.7 | 24.8 | **+1.07** |
| clear_lean | 32.9 | 35.9 | 37.0 | **+1.02** |
| lean | 30.8 | 27.2 | 23.4 | **−3.88** |
| toss_up | 18.9 | 13.1 | 14.9 | **+1.78** |
| clear+strong | 50.3 | 59.7 | 61.7 | +2.10 |

Worst-season top-two share: v3 **66.8%** (2024) → v4 **67.1%** (2023).

---

## Held-out 2026 weeks 1–6 (Tuesday as-of)

v4: continuity season with mapping frozen (offseason retrain before 2026 only; no week-5/10 retrain on 2026 labels).  
Published v3: `data/social/artifacts_cache/latest/results_2026.json` (graded). **Site graded results stay under v3.**

| | published v3 | v4 held-out |
|---|---|---|
| n graded / scored | 387 | 390 (MAE on finite); 448 rows incl. ungraded |
| overall a | **−4.67 [−7.48, −1.79]** | **+2.56 [+0.13, +4.99]** |
| FBS–FBS home a | **−4.29 [−7.48, −1.27]** | +0.65 [−1.78, +3.20] |
| MAE overall | 15.14 [13.95, 16.30] | 15.12 [13.98, 16.30] |
| CRPS overall | 10.78 [10.05, 11.54] | 10.86 [10.08, 11.66] |
| p_win cal LL / Brier | 0.460 / 0.146 | **0.413 / 0.131** |
| Φ LL / Brier | 0.440 / 0.141 | 0.424 / 0.136 |

Reading: published v3 still shows the HFA-2 home-overstatement on 2026; frozen-mapping v4 flips the overall intercept positive (and FBS–FBS CI includes 0). MAE/CRPS are within CI overlap. This is a held-out read only — **not** a tune signal.

---

## Proposed promotion gates (report only — do not act)

| gate | result | detail |
|---|---|---|
| Neutral regular intercept CI includes 0 | **PASS** | a=+1.03 [−2.23, +4.32] |
| FBS–FBS home intercept CI includes 0 | **PASS** | a=−0.38 [−1.06, +0.30] |
| MAE no worse than v3 beyond CI overlap | **PASS** | 14.24 vs 14.53; CIs overlap, point better |
| CRPS no worse than v3 beyond CI overlap | **PASS** | 9.96 vs 10.02; CIs overlap, point better |
| Interval coverage within ±3 pp of nominal | **FAIL** | +8.6 pp (v3 also +8.1 pp) |
| Tier-share change vs v3 | **stated** | lean −3.9 pp; strong/clear/toss-up each ≈ +1–2 pp |

**Hard-gate bundle: FAIL** (coverage). Do not promote.

### Run hygiene

Walkforward compute completed; CLI exited 1 on `DistributionDegeneracyError: sigma_t` zero variance in 2019 week 3 (totals σ). Predictions were persisted before that gate. Same trip on a prior attempt. Not a promotion blocker by itself, but the run is not “clean green” under the distribution gate.

---

## If mid-season promotion happened (hypothetical — not done)

| surface | what would change |
|---|---|
| `vintage_label` | New champion vintage (not `W9A_REVAL`); site copy would show the new label |
| `track_record` | Rebuild from v4 walkforward metrics; published 2026 graded history would **not** silently re-grade under v4 |
| tier shares | Lean share down ~4 pp vs v3 WF; clear+strong up ~2 pp; worst-season top-two still ~67% |
| graded 2026 on site | **Stay as published under v3** per task |

---

## Ambiguities / decisions

1. **Unpenalized path:** sequential Enet-then-OLS with intercept on the residual block (so `is_home_site` is the home−neutral contrast, not the grand mean under `fit_intercept=False`).
2. **2026 in config:** continuity + mapping freeze (not test/headline), so ratings update and Tuesday predicts exist without fitting on 2026 labels.
3. **Coverage gate:** fails for v4 and would fail for v3 under the same ±3 pp rule — treat as a separate conformal/width issue, not a reason to promote v4.
4. **No registry write, no champion bump, no export/publish change.**
