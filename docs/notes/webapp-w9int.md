# W9-INT — empirical coverage of published margin intervals

**Date:** 2026-08-19  
**Status:** Complete. Measurement only. No construction change.  
**Authority:** `docs/notes/webapp-w9d.md` Amendment 1; `docs/notes/23-reval.md` §5
(calibration slope UNMEASURABLE); DESIGN §2.6; ADR 0014; product decision 2
(uncertainty is first-class, nulls are honest absence).

This task measures. It does not change the model, calibrator, CQR constant,
quantile heads, σ, export construction, schema, copy, fixtures, site, or
registry. No R2 write, publish, revalidation POST, fit, or 2025 grading.
The Tuesday interval-publish decision is the operator's.

Artifacts: `docs/notes/_artifacts/webapp-w9int/` (`measure.py`, `coverage.json`).

---

## Headline

On the v3 backtest analysis set (**n=4,743** rows with finite μ, q10, q90, and
realized margin; 2019–2024; **N_2025=0**), the **published construction**
`[min(q10,q90) − 6.837, max(q10,q90) + 6.837]` covers **0.874** of realized
margins (4,147 / 4,743) against a **nominal 0.80**. It **overcovers** in every
`|μ|` bucket with n≥50, every season, week 1 and the rest, and every σ
quartile.

The W9-D week-1 defect is **not** in this history. Coherence `q10 < μ < q90`
is **4,743 / 4,743**. Position `(μ−lo)/(hi−lo)` is outside [0.25, 0.75] on
**11 / 4,743**. Historical `|μ| ≥ 28` is **6.5%** of rows; 2026 week 1 was
**53 / 91 (58%)**.

---

## STOP AND REPORT

| # | Condition | Result |
|---|-----------|--------|
| 1 | Published construction cannot be reproduced from parquet columns | **Did not trip.** q10, q90, μ, σ, realized margin, and stored `cqr_lo`/`cqr_hi` are on the frame. Primary bands are reconstructed as W9-D / live predict: sort q10/q90, add champion **6.837**. `export.py` does not recompute; it copies `cqr_lo`/`cqr_hi` (fallback `pred_margin_q05`/`q95`). On this parquet those stored columns use **walk-forward thresholds** (17 values, 6.458–9.365), not a single 6.837. Median \|reconstructed − stored\| = 0.557 points; max 2.528. q10>q90 on the analysis set: **0** (head already sorted). |
| 2 | Coverage cannot be computed for a large share of rows | **Did not trip as silent filter.** Ineligible **201 / 4,944 = 4.07%**, reported in §0. Analysis n=4,743. |
| 3 | Any input row carries season 2025 | **Did not trip.** `N_2025 = 0`. |

---

## 0. Provenance and denominators

### Input parquet

| Field | Value |
|---|---|
| path | `data/backtests/task23_fundamental_reduced_v3/full/predictions.parquet` |
| mtime | **2026-08-17T20:41:46.173748+00:00** |
| size | 1,325,254 bytes |
| n | **4,944** |
| `run_id` | `task23_fundamental_reduced_v3` |
| `model_version` | `production-v0_reduced_v3` |
| `N_2025` | **0** |
| seasons | 2019: 763; 2020: 568; 2021: 887; 2022: 896; 2023: 910; 2024: 920 |
| manifest `created_at` | 2026-08-17T20:41:46Z |
| manifest `git_sha` | `157bc7d34e64462fb00a646d449bccdbf5bd15fe` |
| CQR constant used here | **6.837** (W9-D champion `_cqr.score_thresholds[0.8]`) |

Season counts include rows that later drop out of the interval sample.

### Construction measured

Live predict: LightGBM quantile head sorts the quantile vector, then
`conformalize_intervals` sets `cqr_lo = q10 − thr`, `cqr_hi = q90 + thr` at
nominal 0.8. `build_game_prediction` publishes those as `margin_interval_*`.

Primary tables use **thr = 6.837** on sorted parquet q10/q90 — the Tuesday
champion add, not the per-week walk-forward add already stored as `cqr_*`.
Stored-walk-forward coverage is in §5 only (reported, not adopted).

Gaussian counterfactual uses **μ ± 1.28σ** as specified (Φ⁻¹(0.9) ≈ 1.28155
is not used).

### Denominators (not a silent filter)

| Predicate | n | share of 4,944 |
|---|---:|---:|
| parquet rows | 4,944 | 1.000 |
| finite μ (`pred_margin`) | 4,854 | 0.982 |
| finite q10 and q90 | 4,744 | 0.960 |
| finite realized margin | 4,943 | 1.000 |
| finite `cqr_lo` and `cqr_hi` | 4,744 | 0.960 |
| `cqr_is_missing` | 200 | 0.040 |
| `null_reason=no_credible_members` | 90 | 0.018 |
| **eligible (finite μ, q10, q90, realized margin)** | **4,743** | **0.959** |
| ineligible | 201 | **0.041** |

The 200 missing quantile/CQR rows are **2019 weeks 2–4 only** (74+68+58): 90
ADR 0014 `no_credible_members` plus 110 rows with μ but no quantile head (the
W9-G / 23-reval missing-σ family). 2019 week 1 is absent from the parquet
(`absent_blocks=[[2019,1]]`). One additional 2024 week 5 row
(`game_id=401640992`) has finite μ/q/CQR and a missing realized margin; it is
out of the coverage sample.

`is_week1` matches `week==1` on the analysis set.

---

## 1. Empirical coverage of the published construction

Nominal **0.80**. Cell with n ≲ 50 is not evidence (`thin`).

### Overall

| n | hits | coverage | below lo | above hi |
|---:|---:|---:|---:|---:|
| 4,743 | 4,147 | **0.874** | 307 | 289 |

### By `|μ|`

| `|μ|` | n | hits | coverage | thin | below lo | above hi |
|---|---:|---:|---:|:---:|---:|---:|
| [0, 7) | 1,857 | 1,620 | 0.872 | no | 132 | 105 |
| [7, 14) | 1,397 | 1,235 | 0.884 | no | 95 | 67 |
| [14, 21) | 784 | 679 | 0.866 | no | 56 | 49 |
| [21, 28) | 397 | 338 | 0.851 | no | 16 | 43 |
| [28, 35) | 197 | 177 | 0.898 | no | 7 | 13 |
| [35, ∞) | 111 | 98 | 0.883 | no | 1 | 12 |

No bucket with n≥50 is under 0.80. The lowest is **0.851** at [21, 28).

### By season

| season | n | hits | coverage | thin | below lo | above hi |
|---:|---:|---:|---:|:---:|---:|---:|
| 2019 | 563 | 483 | 0.858 | no | 64 | 16 |
| 2020 | 568 | 496 | 0.873 | no | 40 | 32 |
| 2021 | 887 | 752 | 0.848 | no | 69 | 66 |
| 2022 | 896 | 790 | 0.882 | no | 46 | 60 |
| 2023 | 910 | 808 | 0.888 | no | 45 | 57 |
| 2024 | 919 | 818 | 0.890 | no | 43 | 58 |

2019 n=563 is the post–week-2–4 drop from 763 parquet rows. Lowest season is
2021 at **0.848**.

### By week (1 versus rest)

| slice | n | hits | coverage | thin | below lo | above hi |
|---|---:|---:|---:|:---:|---:|---:|
| week = 1 | 580 | 500 | 0.862 | no | 43 | 37 |
| week ≥ 2 | 4,163 | 3,647 | 0.876 | no | 264 | 252 |

Week 1 is 2020–2024 only.

### By σ quartile (analysis rows; all 4,743 have σ>0)

| σ quartile | n | hits | coverage | thin | below lo | above hi |
|---|---:|---:|---:|:---:|---:|---:|
| Q1 (lowest σ) | 1,186 | 1,016 | 0.857 | no | 88 | 82 |
| Q2 | 1,186 | 1,060 | 0.894 | no | 65 | 61 |
| Q3 | 1,185 | 1,033 | 0.872 | no | 78 | 74 |
| Q4 (highest σ) | 1,186 | 1,038 | 0.875 | no | 76 | 72 |

---

## 2. Coherence rate

Same 4,743 rows. “Before CQR” is raw parquet q10/q90 (already sorted by the
head). “Published” is lo/hi after ±6.837.

| test | n | count | fraction |
|---|---:|---:|---:|
| `q10 < μ < q90` | 4,743 | 4,743 | **1.000** |
| `q90 < μ` | 4,743 | **0** | 0.000 |
| `μ < q10` | 4,743 | 0 | 0.000 |
| published `lo < μ < hi` | 4,743 | 4,743 | **1.000** |
| μ ≥ published hi | 4,743 | 0 | 0.000 |
| μ ≤ published lo | 4,743 | 0 | 0.000 |

By `|μ|`: every bucket is 1.000 on both tests (n as in §1).

W9-D reconstructed **19 / 91** with `q90 < μ` on 2026 week 1, and **1 / 91**
with μ above published hi. The historical rate of that incoherence is **0**.

---

## 3. Position distribution `(μ − lo) / (hi − lo)`

Historical analogue of W9-D's **46 / 91** outside [0.25, 0.75].

### Overall

| n | min | p10 | median | p90 | max | n outside [0.25, 0.75] |
|---:|---:|---:|---:|---:|---:|---:|
| 4,743 | 0.222 | 0.441 | **0.509** | 0.577 | 0.850 | **11 (0.23%)** |

### By `|μ|`

| `|μ|` | n | min | p10 | median | p90 | max | n outside [0.25, 0.75] |
|---|---:|---:|---:|---:|---:|---:|---:|
| [0, 7) | 1,857 | 0.224 | 0.438 | 0.499 | 0.551 | 0.655 | 2 |
| [7, 14) | 1,397 | 0.243 | 0.432 | 0.505 | 0.558 | 0.680 | 2 |
| [14, 21) | 784 | 0.298 | 0.446 | 0.516 | 0.580 | 0.751 | 1 |
| [21, 28) | 397 | 0.326 | 0.452 | 0.535 | 0.617 | 0.715 | 0 |
| [28, 35) | 197 | 0.274 | 0.492 | 0.570 | 0.647 | 0.725 | 0 |
| [35, ∞) | 111 | 0.222 | 0.549 | 0.642 | 0.720 | 0.850 | **6** |

The high-μ tail is mildly high-sided (median 0.642 at `|μ|≥35`) and is where
6 of the 11 outliers sit. It does not reach W9-D's week-1 median **0.881** on
`|μ|≥28` or max **1.023**. Backtest max `|μ|` is 48.14 (week-1 max 46.87),
inside the range W9-D quoted for v3 `pred_margin`.

---

## 4. Miss asymmetry

Of realized margins **outside** the published band.

| slice | n (cell) | n miss | below lo | above hi |
|---|---:|---:|---:|---:|
| overall | 4,743 | 596 | **307** | **289** |
| `|μ|` [0, 7) | 1,857 | 237 | 132 | 105 |
| [7, 14) | 1,397 | 162 | 95 | 67 |
| [14, 21) | 784 | 105 | 56 | 49 |
| [21, 28) | 397 | 59 | 16 | **43** |
| [28, 35) | 197 | 20 | 7 | **13** |
| [35, ∞) | 111 | 13 | 1 | **12** |

Overall misses are nearly balanced (307 vs 289). From `|μ|≥21` the misses
flip to **above hi**: the band is shifted down relative to realized margins
on large favorites, even though μ itself stayed inside the band in this
sample.

---

## 5. Counterfactual comparison (reported, not adopted)

Same 4,743 rows. No code path was switched to these constructions.

| construction | n | hits | coverage | below lo | above hi |
|---|---:|---:|---:|---:|---:|
| **published** sorted q10/q90 ± 6.837 | 4,743 | 4,147 | **0.874** | 307 | 289 |
| (a) raw sorted q10/q90, no CQR add | 4,743 | 3,566 | **0.752** | 601 | 576 |
| (b) μ ± 1.28σ (Gaussian) | 4,743 | 3,867 | **0.815** | 475 | 401 |
| stored walk-forward `cqr_lo`/`cqr_hi` (not Tuesday) | 4,743 | 4,186 | 0.883 | 290 | 267 |

The CQR add **raises** coverage from 0.752 to 0.874. That is help relative to
the raw quantile band and **overshoot** relative to nominal 0.80. The Gaussian
80% band is the closest of the three to nominal. Walk-forward stored CQR
overcovers slightly more than the constant 6.837 (thresholds were typically
larger than 6.837; median implied thr 7.394).

### Same `|μ|` breakout

| `|μ|` | n | published | raw q | Gaussian |
|---|---:|---:|---:|---:|
| [0, 7) | 1,857 | 0.872 | 0.761 | 0.808 |
| [7, 14) | 1,397 | 0.884 | 0.752 | 0.826 |
| [14, 21) | 784 | 0.866 | 0.750 | 0.809 |
| [21, 28) | 397 | 0.851 | 0.723 | 0.816 |
| [28, 35) | 197 | 0.898 | 0.741 | 0.822 |
| [35, ∞) | 111 | 0.883 | 0.730 | 0.838 |

Hits for those rates: published as §1; raw q 1413 / 1051 / 588 / 287 / 146 /
81; Gaussian 1500 / 1154 / 634 / 324 / 162 / 93.

---

## 6. Week-1 specificity

2026 week 1 (published, W9-D): **53 / 91 (58%)** with `|μ| ≥ 28`.

| quantity | n | fraction of analysis (4,743) |
|---|---:|---:|
| backtest `|μ| ≥ 28` | 308 | **0.065** |
| backtest `|μ| ≥ 35` | 111 | 0.023 |
| backtest week 1 | 580 | 0.122 |
| week 1 and `|μ| ≥ 28` | 104 | **0.022** |
| week 1 rows that have `|μ| ≥ 28` | 104 / 580 | 0.179 |

2026 week 1's `|μ|≥28` share is about **9×** the backtest base rate (0.58 vs
0.065) and about **3×** historical week 1 (0.58 vs 0.18).

Week-1 `|μ|≥28` by season on the analysis set: 2020: 1; 2021: 15; 2022: 22;
2023: 31; 2024: 35. 2019 week 1 is not in the file.

### Coverage, week 1

| slice | n | hits | coverage | thin | below lo | above hi |
|---|---:|---:|---:|:---:|---:|---:|
| all week 1 | 580 | 500 | 0.862 | no | 43 | 37 |
| week 1 and `|μ| ≥ 28` | 104 | 95 | 0.913 | no | 1 | 8 |
| all `|μ| ≥ 28` | 308 | 275 | 0.893 | no | 8 | 25 |

### Week 1 by `|μ|`

| `|μ|` | n | hits | coverage | thin |
|---|---:|---:|---:|:---:|
| [0, 7) | 156 | 141 | 0.904 | no |
| [7, 14) | 141 | 119 | 0.844 | no |
| [14, 21) | 105 | 85 | 0.810 | no |
| [21, 28) | 74 | 60 | 0.811 | no |
| [28, 35) | 65 | 58 | 0.892 | no |
| [35, ∞) | 39 | 37 | 0.949 | **yes (n=39)** |

The 2026 week-1 regime (`|μ|≥28`, FCS-opener heavy) is **thin as a share of
history** (6.5% of rows; week-1 ∩ `|μ|≥35` is n=39, not evidence). Where that
regime *does* appear in 2020–2024, published coverage is **high** (0.89–0.91)
and coherence remains 100%. That is not a measurement of the 2026 quantile-head
saturation W9-D recorded (`q90 < μ` on 19 / 91). The backtest never produced
that pattern.

---

## Closing statement

The published construction **overcovers** relative to its 80% nominal on the
v3 backtest: **0.874** overall (4,147 / 4,743), and **at or above 0.848** in
every season, week-1 vs rest, σ quartile, and `|μ|` bucket with n≥50. It does
**not** fail historically by undercoverage.

What it does not test is the W9-D week-1 failure mode. Historical `q10 < μ <
q90` is **1.000**; historical position outliers are **11 / 4,743**, not 46 / 91.
Misses become **above-hi** once `|μ| ≥ 21`. Raw q10/q90 cover **0.752**; μ ±
1.28σ cover **0.815**; the 6.837 add is what pushes the published band to
0.874. The 2026 week-1 `|μ|≥28` mix is largely absent from this sample.

---

## Forbidden actions not taken

No CQR / quantile / calibrator / σ / model / export / schema / copy / fixture /
site change. No R2 write, publish, revalidation POST, fit, retrain, promotion,
registry change, 2025 grading, Prefect, CI, or guard change. Counterfactuals
are tables only.

```
========= 936 passed, 1 deselected, 32 warnings in 277.49s (0:04:37) ==========
Required test coverage of 80% reached. Total coverage: 80.51%
```

No src/tests change in this task. The suite is the current tree.
