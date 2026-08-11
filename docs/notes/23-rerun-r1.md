# TASK 23-RERUN-R1 — Eight-run set on REDUCED ensemble

> **CONTAMINATION NOTICE (2026-08-11, ATS-GRADE-FIX):** All **v1 snapshot-regime
> ATS** and **ATS log-loss** numbers in this memo are **INVALID** — grading used
> `median(line)` over both Odds spread sides (~0 closes). Do **not** cite without
> the `CONTAMINATED_v1` label. Superseded snapshot ATS / log-loss / A2 ATS deltas /
> market-aware snapshot ATS (**32.7%**) / A3 “market features hurt margin” finding /
> A6 **36.5%** → see **`docs/notes/ats-grade-fix.md`** (**REGRADED_V2** /
> **RERUN_V2**). MAE, CRPS, weekly MAE curve, A2 MAE (+1.60), OU@close, and SU
> numbers in this file are **untouched**.

**Date:** 2026-08-11  
**Status:** **COMPLETE** — all eight runs executed.  
**ensemble_scope:** `REDUCED_PER_ADR_0013`  
**Composition (not §5.2-complete):** margin: LGBM μ + ElasticNet μ → NNLS
stack; total: single-LGBM stub (no diversity stack); quantile: margin only; MC
(`sample_joint`, 100k draws) + epistemic (`_epistemic_mix`) active.  
**Driver:** `ncaa-quant backtest run --label ensemble_scope=REDUCED_PER_ADR_0013`
against `configs/ablations/task23_*_reduced_v1.yaml`.  
**Git at run time:** `a6f05c7`. **Lockbox 2025:** excluded (asserted at load).

Every number below carries the **REDUCED** label. This is not a full §5.2 system
(ADR 0013).

---

## Executive verdict (REDUCED)

1. **A2 (ratings frozen after Week 1) is materially worse** on the REDUCED stack:
   Δ MAE margin +1.60 (all-season basis), snapshot-regime ATS 36.3% vs 39.7%
   (fundamental). Continual Stage-1 updates remain directionally valuable.
2. **Fundamental beats market-aware on margin MAE** (14.85 vs 15.12 all-season)
   but **market-aware total MAE is lower on snapshots** (10.34 vs 13.14) —
   interpret cautiously given structural `expected_possessions` nulls (below).
3. **A4:** single LGBM **beats** the reduced NNLS margin stack on MAE (−0.70)
   and snapshot ATS (43.5% vs 39.7%). Total-side A4 is **identical** (stub vs
   stub) — measures nothing about §5.2 ensembling.
4. **§1.6 criteria mostly missed** on this REDUCED measurement (miss list below).
   No tuning was performed.

Artifacts: `data/backtests/task23_*_reduced_v1/*/predictions.parquet`,
`manifest.json` (stamped `ensemble_scope` + `ensemble_composition`).
Post-hoc summary: `docs/notes/_artifacts/task23_reduced_v1/metrics_summary.json`.

---

## Step 0 — abbreviated re-verify (2026-08-11)

| Check | Result |
|---|---|
| Snapshots 2021–2024 load, lockbox 2025 raises | **PASS** — n=1,242,542 staged rows; `(2024,2025)` request raises |
| Filter history = promoted GT-active artifact | **PASS** — `data/artifacts/state_space/filter_history.parquet` |
| A1 priors populated | **PASS** — fitted priors frame shape (548, 15) |
| A5 GT at construction: n_on < n_off | **PASS** — n_on=753,288, n_off=903,422 (prep aggregate) |
| Possessions PIT engages at first retrain | **PARTIAL** — drives staged **2023 only** (793 training rows); fit at (2019,5) returns None; first finite values only after 2023 week≥5 retrain |

---

## Headline — fundamental full (REDUCED)

n_predictions=4944, n_headline=4376 (2020 continuity excluded).  
Regimes **never pooled**.

### CFBD 2019

| Metric (REDUCED) | Value | 95% CI (bootstrap / naive) |
|---|---|---|
| ATS vs close | **50.7%** | [47.9%, 54.0%] / [47.1%, 54.3%] (n=743) |
| OU vs close | **50.9%** | [46.6%, 55.4%] / [47.3%, 54.5%] (n=747) |
| MAE margin | **17.84** | — |
| CRPS margin | **13.04** | — |
| MAE total | **14.22** | — |
| Log-loss ATS (model / market@0.5) | **0.950 / 0.693** | model worse |

### Snapshots 2021–2024

| Metric (REDUCED) | Value | 95% CI (bootstrap / naive) |
|---|---|---|
| ATS vs close | **39.7%** | [35.6%, 44.4%] / [38.1%, 41.3%] (n=3577) |
| OU vs close | **52.3%** | [49.7%, 54.8%] / [50.6%, 54.1%] (n=3136) |
| MAE margin | **14.21** | — |
| MAE total | **13.14** | — |
| Log-loss ATS (model / market@0.5) | **0.998 / 0.693** | model worse |

**All-season basis (A2 components):** MAE margin 14.85 (n=4375); CRPS margin
10.68; ATS 41.6% line-backed (n=4320).

---

## Headline — market-aware full (REDUCED)

Same seasons/headline n. Snapshot ATS **32.7%** [30.2%, 35.2%] — worse than
fundamental. Total MAE on snapshots **10.34** vs fundamental **13.14** (REDUCED;
possessions mostly null — see below).

---

## Ablation deltas (REDUCED, vs fundamental unless noted)

| Run | Δ MAE margin (all-season) | Snapshot ATS Δ (pp) | Comment |
|---|---:|---:|---|
| A1 league-mean priors | −0.04 | ~−0.3 | Near no-op |
| **A2 frozen ratings** | **+1.60** | **−3.4** | Headline |
| A3 market off (vs mkt-aware) | −0.28 | +6.9 | Market features hurt margin |
| A4 single LGBM | −0.70 | +3.8 | NNLS stack loses to single LGBM here |
| A5 GT filter off | −0.46 | +0.6 | GT now active (not inert) |

### A4 framing (REDUCED)

| Side | Reduced ensemble | Single LGBM | Increment (reduced − single) |
|---|---:|---:|---:|
| **Margin MAE** | 14.85 | 14.15 | **+0.70** (ensemble worse) |
| **Total MAE (2019 CFBD)** | 14.22 | 14.22 | 0.00 |

Total-side A4 is **stub-vs-single** and measures nothing about §5.2 ensembling.

### A6 — CFBD open/close vs snapshots (REDUCED, 2021–2024 only)

| Source | ATS vs close | 95% bootstrap CI |
|---|---:|---|
| Market-aware snapshots (full) | 32.7% | [30.2%, 35.2%] |
| A6 cfbd_open_close | 36.5% | [35.2%, 37.8%] |

Different line ladder — compare rates separately, never pool with 2019 CFBD.

### A2 by basis (REDUCED)

| Component | Basis | Full continual | A2 frozen | Δ |
|---|---|---:|---:|---:|
| MAE margin | all_seasons | 14.85 | 16.45 | +1.60 |
| ATS | line_backed | 41.6% | 38.1% | −3.5 pp |

---

## Possessions null discipline (REDUCED)

Per prep ambiguity: **no `is_missing` indicator column** in provider output;
values are **NaN** (LightGBM-native), **never zero-filled**. Drives staged for
**2023 only** → structural 100% null for `expected_possessions` except partial
2023 week≥5 after first PIT retrain.

| Season | Weeks 1–4 null share | Weeks 5+ null share | OU caveat |
|---|---|---|---|
| 2019–2022, 2024 | **100%** structural | **100%** structural | OU measured without key totals feature |
| 2023 w1–4 | **100%** | — | early-season totals head lacks possessions |
| 2023 w5+ | — | **partial** (PIT may engage) | only season with staged drives |

Report OU metrics alongside this table; Weeks 1–4 OU accuracy is partly on a
totals feature that is absent for almost all rows.

---

## Weekly error curve — fundamental (REDUCED)

| Checkpoint | MAE margin |
|---|---:|
| Week 4 | 14.98 |
| Week 10 | 13.55 |
| Week 10 − Week 4 | **−1.43** ✓ |

---

## CLV (REDUCED)

**NOT COMPUTED** — `backtest run` output has no `bets.parquet` / settle path in
this runner. Reason recorded in manifests.

---

## Determinism (REDUCED)

Single-pass byte hash of fundamental predictions (no re-run verification):

```text
sha256(data/backtests/task23_fundamental_reduced_v1/full/predictions.parquet)
  = a3d0495d0e2b6cf0a6621388d1af80a9c71788877bfa973dc64f37a27293129a
```

---

## §1.6 success criteria — explicit miss list (REDUCED)

| Criterion | Result (REDUCED) |
|---|---|
| Mean CLV > 0, 95% CI excludes 0, n≥300 | **MISS** — CLV not computed |
| Fundamental ATS ≥ 51.5% | **MISS** — 39.7% snapshots [35.6%, 44.4%]; 50.7% 2019 only |
| Fundamental OU ≥ 51.5% | **MARGINAL** — 52.3% snapshots [49.7%, 54.8%] |
| Brier/log-loss ≤ market baseline | **MISS** — ATS log-loss ≈1.0 vs 0.693 market |
| Calibration slope ∈ [0.9, 1.1] | **Not re-scored this session** |
| Zero leakage / process | Prep wiring + 22B audits; not re-litigated here |
| Full §5.2 ensemble | **MISS by definition** — ADR 0013 REDUCED scope |

No hyperparameters tuned against these numbers.

---

## Standing caveats (REDUCED)

1. **Not §5.2-complete** — missing XGB/CatBoost/NGBoost, ENet total, LGBM
   quantile total (ADR 0013).
2. **Possessions mostly null** — totals head often lacks the §4.5 key feature.
3. **n_books gradient** — snapshot regime uses multi-book close; 2019 CFBD is
   single-source; never pool.
4. **CFBD vs snapshot line divergence** — A6 encodes this explicitly.
5. **Lockbox 2025** — excluded and asserted at snapshot load.
6. Prior Task 23 numbers remain superseded (see `docs/notes/23.md` AMENDMENT).

---

## Run wall-clock (informational)

| run_id | wall_clock_sec |
|---|---:|
| task23_fundamental_reduced_v1 | 5304 |
| task23_market_aware_reduced_v1 | 6891 |
| task23_a1_reduced_v1 | 6251 |
| task23_a2_reduced_v1 | 5642 |
| task23_a3_reduced_v1 | 5450 |
| task23_a4_reduced_v1 | 5430 |
| task23_a5_reduced_v1 | 5549 |
| task23_a6_reduced_v1 | 3909 |

Plan estimate (~90 s/run) understated MC+epistemic cost (~88 min/run).

---

## `make lint typecheck test`

```text
uv run ruff check src tests          → All checks passed!
uv run ruff format --check src tests → 168 files already formatted
uv run mypy                          → Success: no issues found in 104 source files
uv run pytest -m "not live"          → 732 passed, 1 deselected; coverage 80.33%
```
