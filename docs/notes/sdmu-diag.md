# TASK SDMU-DIAG — SD(mu)=0 on blocked Tuesday market-aware re-run

**Date:** 2026-08-12  
**Scope:** Diagnose only; no fix, no gate change, no full-config re-run.  
**Config replayed:** `task23_market_aware_full_reduced_v2_tue` (gate off; seasons
through 2023 w5; non-target weeks use point-μ only).  

Artifacts: `docs/notes/_artifacts/sdmu_diag/`.

---

## STEP 1 — Cheap replay (nine blocks)

Wall clock: **~84 min** (fresh harness; resume artifacts under `_artifacts/sdmu_diag/`).

Quality gate on replay (same D2 rule as production): **FAIL** — exact match to
the blocked MKT-2019-FIX error:

```
SD(mu)=0 in 7 (season, week) block(s):
[(2019, 2), (2019, 3), (2019, 4), (2023, 1), (2023, 2), (2023, 3), (2023, 4)]
```

### Per-block SD(mu) on replayed `pred_margin`

| season | week | status | n_games | SD(mu) | n_train_games | notes |
|---:|---:|---|---:|---:|---:|---|
| 2019 | 1 | CONTROL | **0** | — | — | cold-start: bank+reveal only; **no prediction rows** |
| 2019 | 2 | FAIL | 74 | 0.000000 | 125 | μ ≡ 11.816 |
| 2019 | 3 | FAIL | 68 | 0.000000 | 199 | μ ≡ 11.816 |
| 2019 | 4 | FAIL | 58 | 0.000000 | 267 | μ ≡ 11.816 |
| 2023 | 1 | FAIL | 136 | 0.000000 | 3239 | μ ≡ 2.5 |
| 2023 | 2 | FAIL | 85 | 0.000000 | 3375 | μ ≡ 2.5 |
| 2023 | 3 | FAIL | 75 | 0.000000 | 3460 | μ ≡ 2.5 |
| 2023 | 4 | FAIL | 67 | 0.000000 | 3535 | μ ≡ 2.5 |
| 2023 | 5 | CONTROL | 59 | 1.574973 | 3602 | passes after week-5 retrain |

Archived pre-fix Tuesday run also has **zero** 2019-w1 prediction rows
(first predicted week is w2). The gate “passes” (2019, w1) because the block
is absent, not because SD(mu)>0.

---

## STEP 2 — Stage-by-stage SD decomposition

| season | week | status | n | SD final | SD LGBM | SD ENet | SD stack | SD epistemic | NNLS weights (LGBM / ENet) |
|---:|---:|---|---:|---:|---:|---:|---:|---:|---|
| 2019 | 1 | CONTROL | 0 | — | — | — | — | — | — |
| 2019 | 2 | FAIL | 74 | **0** | ≈0 (≤1e-15) | **0** (fallback 2.5) | ≈0 | **0** | **1.0 / 0.0** |
| 2019 | 3 | FAIL | 68 | **0** | ≈0 | **0** (fallback 2.5) | ≈0 | **0** | **1.0 / 0.0** |
| 2019 | 4 | FAIL | 58 | **0** | **0** | **0** (fallback 2.5) | **0** | **0** | **1.0 / 0.0** |
| 2023 | 1 | FAIL | 136 | **0** | **16.06** | **0** (unfitted→2.5) | **0** | **0** | **0.0 / 1.0** |
| 2023 | 2 | FAIL | 85 | **0** | **17.15** | **0** (unfitted→2.5) | **0** | **0** | **0.0 / 1.0** |
| 2023 | 3 | FAIL | 75 | **0** | **16.39** | **0** (unfitted→2.5) | **0** | **0** | **0.0 / 1.0** |
| 2023 | 4 | FAIL | 67 | **0** | **13.09** | **0** (unfitted→2.5) | **0** | **0** | **0.0 / 1.0** |
| 2023 | 5 | CONTROL | 59 | **1.57** | **12.52** | **0** (still unfitted→2.5) | **1.60** | **1.57** | **0.128 / 0.872** |

**Two different mechanisms — do not collapse them.**

### Group A — (2019, w2–w4): constant LGBM leaf

1. Cold-start catchup fit after week-1 reveal (`n_train=125`); NNLS puts
   **100% weight on LGBM**.
2. LGBM emits a **constant** μ ≈ 11.816 on every game in w2–w4 (training-mean
   stump under a thin early-season feature set).
3. ENet path: selected features are ratings-only (no `mkt_*`); predict falls
   the **block-wide** credible check → replaced with constant 2.5. Irrelevant
   to final μ because NNLS weight on ENet is 0.
4. Epistemic mix / final μ inherit the LGBM constant.

### Group B — (2023, w1–w4): NNLS all-in on dead ENet

1. LGBM is **healthy** (SD ≈ 13–17). Constancy is **not** at the tree member.
2. ENet `fit` fails (silenced by `contextlib.suppress` in
   `ProductionEnsemblePredictor.fit`); predict raises *“ElasticNet instance is
   not fitted yet”* → `_predict_point` replaces the **entire** member vector
   with 2.5.
3. Offseason / pre-week-5 NNLS weights are **`enet=1.0, lgbm=0.0`** → stack =
   final μ = 2.5 for every game.
4. Root cause of ENet fit failure: expanding-window training matrix includes
   **2019 all-null `mkt_spread` / `mkt_total`** (snapshots feature path).
   sklearn `ElasticNet` **rejects NaN** (`ValueError: Input X contains NaN`);
   no imputation, no row drop — fit aborts. Verified independently against
   sklearn.

### Feature-matrix inventory

#### (2019, w2–w4) — representative w2 (74 × 20)

| column | null_share | constant |
|---|---:|---|
| `mkt_spread` | **1.0** | (all null) |
| `mkt_total` | **1.0** | (all null) |
| `mkt_n_books` | 0.0 | **True** (all 0) |
| `mkt_is_missing` | 0.0 | **True** (all 1) |
| `expected_possessions` | **1.0** | — |

Also constant: `home_st_value`, `away_st_value`, `st_value_diff`.  
ENet selected (12): ratings/pace only — markets never enter.

#### (2023, w1–w4) — markets present, ENet still dead

| week | `mkt_spread` null_share | LGBM SD | ENet | NNLS |
|---:|---:|---:|---|---|
| 1 | 0.62 | 16.1 | unfitted → 2.5 | 100% ENet |
| 2 | 0.48 | 17.2 | unfitted → 2.5 | 100% ENet |
| 3 | 0.28 | 16.4 | unfitted → 2.5 | 100% ENet |
| 4 | 0.06 | 13.1 | unfitted → 2.5 | 100% ENet |
| 5 | 0.05 | 12.5 | unfitted → 2.5 | **12.8% LGBM** |

ENet `selected_features` still *lists* `mkt_spread` (stale / partial state from
a prior successful selection attempt), but `_model` is not fitted.

### ENet NaN path (verified)

| step | behavior |
|---|---|
| `select_top_k_features` | skips columns with `nanstd < 1e-12`; pairwise corr uses finite mask — **does not impute** |
| `StandardScaler.fit_transform` | accepts all-NaN columns with warnings; does **not** fill |
| `ElasticNet.fit` | **hard-fails** on any NaN in X |
| `ProductionEnsemblePredictor.fit` | `suppress(Exception)` → ENet left unfitted |
| `_predict_point` | any non-credible / exception → **block-wide** fill 2.5 |

No silent zero-fill; no row drop. Failure is loud at sklearn, then swallowed
into a constant fallback member.

---

## STEP 3 — Boundary explanations

### Why (2019, w1) “passes” while w2–w4 fail

**Vacuous pass.** Under the cold-start path the predictor is unfitted at
week 1: harness banks features, reveals labels, then
`cold_start_catchup` fits on those 125 games — **no `pred_margin` rows are
emitted for (2019, w1)**. The D2 gate only evaluates blocks with ≥2 scored
games, so (2019, w1) never appears in `zero_sd_blocks`.

w2–w4 share the **same** post-w1 cold-start model (no retrain until week 5).
That model’s LGBM member is a constant leaf → SD(mu)=0. The null-2019-market
hypothesis covers this group: without CFBD-fill market spreads, early-season
cross-game differentiation collapses. Pre-fix (contaminated) Tuesday
artifacts show SD(mu)≈7–8 on the same weeks when CFBD closes were still
features.

### Why 2023 failures stop exactly at week 5

Retrain schedule is `[5, 10]`. Events from the replay:

| event | n_train |
|---|---:|
| 2023 offseason (week 0) | 3239 |
| 2023 week 5 retrain | 3602 |

(2023, w1–w4) use the offseason fit: NNLS weight **100% ENet** (dead → 2.5).  
At week 5 the mapping refits: NNLS shifts to **≈12.8% LGBM / 87.2% ENet**.
ENet is **still** unfitted (same NaN training pathology), but the non-zero
LGBM weight restores cross-game spread (stack SD≈1.60 → final SD≈1.57).

This is **not** the same mechanism as 2019. Null 2019 markets are an
*upstream cause* of ENet fit failure in the expanding window, but the
block-level constancy is an **NNLS + fallback** artifact, not a constant
LGBM.

---

## STEP 4 — Published-run latency (SD < 0.01)

Threshold: population SD(mu) < 0.01 within any (season, week) with ≥2 scored
games.

| run | path | n_scored | low-SD blocks |
|---|---|---:|---:|
| fundamental_v2 | `data/backtests/task23_fundamental_reduced_v2/full/predictions.parquet` | 4376 | **0** |
| A3_v2 | `data/backtests/task23_a3_reduced_v2/A3_market_off/predictions.parquet` | 4376 | **0** |
| A6_v2 | `data/backtests/task23_a6_reduced_v2/A6_cfbd_open_close/predictions.parquet` | 3486 | **0** |
| SLOT_CLOSE | `data/backtests/task23_market_aware_reduced_v2_slot_close/full/predictions.parquet` | 4376 | **0** |

**Finding:** no published fundamental / A3 / A6 / SLOT_CLOSE table consumed a
zero or near-zero SD(mu) block. Cited metrics from those runs
(v2-baseline, week-align equivalence baselines, A6 RERUN_V2 ATS, kick−5min
SLOT_CLOSE tables) are **not** latently poisoned by this gate failure.

The failure is specific to the **post–MKT-2019-FIX** Tuesday market-aware
re-run (null 2019 snapshot features), which never published.

---

## STEP 5 — Fix scope (not implemented)

### Mechanism A — (2019, w2–w4)

| item | detail |
|---|---|
| Mechanism | Cold-start LGBM emits constant μ; NNLS weight 100% LGBM |
| Fix lands | Primarily mapping cold-start / feature handling when markets are structurally null: `ProductionEnsemblePredictor.fit` / `LightGBMMuHead`; possibly refuse or widen features when `mkt_is_missing` is block-constant |
| Training inputs? | **Yes** if the fix changes what LGBM sees or when the first fit occurs → forces full reduced-v2 Tuesday re-run |
| Supersedes | Blocked MKT-2019-FIX Step 4 table only (nothing published) |

### Mechanism B — (2023, w1–w4)

| item | detail |
|---|---|
| Mechanism | ENet fit dies on NaN `mkt_*` in the expanding window; `_predict_point` block-fills 2.5; NNLS puts weight 1.0 on that dead member until week-5 retrain |
| Fix lands | `src/ncaa_quant/models/heads/elasticnet.py` (impute / drop-null columns before `StandardScaler`+`ElasticNet`) **and/or** `ProductionEnsemblePredictor._predict_point` / `_set_weights` (do not assign positive NNLS weight to an unfitted / fallback-constant member; per-row not block-wide fallback) |
| Training inputs? | ENet imputation changes **training** → re-run. Weighting/fallback-only changes are **prediction-time** and still require a market-aware Tuesday re-run to publish, but may not invalidate unrelated published runs |
| Supersedes | Same blocked Tuesday re-run; does **not** supersede published fundamental/A3/A6/SLOT_CLOSE numbers (latency check clean) |

### What not to do

- Do not treat “null 2019 features” as a single story for both groups.
- Do not widen the quality gate.
- Do not re-run the full config until both mechanisms have an explicit fix plan.

---

## Built / decisions

- Diag script: `scripts/_sdmu_diag.py` (harness proxy: full predict only on the
  nine target blocks; point-μ elsewhere).
- Artifacts: `replay_predictions.parquet`, `block_captures.json`,
  `replay_summary.json`, `latency_table.json`.
- Ambiguity recorded: ENet capture still lists `selected_features` including
  `mkt_spread` while `_model` is unfitted — selection state vs estimator state
  can diverge after a suppressed fit failure; fix should clear both.

## Acceptance

- [x] Stage-by-stage SD table for all nine blocks
- [x] Feature-matrix inventory for constant members
- [x] Both boundary explanations (vacuous 2019-w1; week-5 NNLS reweight)
- [x] Published-run latency table (all clean)
- [x] Per-mechanism fix scope (not implemented)
