# Task 23-FIX — CODE audit (read-only)

**Date:** 2026-08-10  
**Scope:** Verify `docs/task-23-fix.md` items against source and tests only.
Do not trust `docs/notes/23.md` or amendments for closure status.
**Sanctioned edit:** this file only.

---

## Status table

| Item | Status | Evidence reference |
|---|---|---|
| P0-1 CALIBRATION PATH | **PARTIAL** | CQR from `models/conformal.py` is on the production fit→predict path; per-market isotonic from `models/calibrate.py` is **not** — replaced by distributional PIT in `models/pit_calibration.py`. |
| P0-2 CLV GUARD | **PARTIAL** | Same-source-row guard exists on `compute_clv` / `assert_distinct_line_sources` with a unit test, but `settle()` never threads source-row IDs, so the production settlement path cannot trip the guard. |
| P0-3 PROVENANCE | **CLOSED** | `scripts/_task23_backtest.py` is gone; CLI/`backtest_runner` stamps `run_id`/`ablation_id`, writes four-hash manifests, and determinism is covered by tests. |
| P1-4 ABLATION PRECONDITIONS | **CLOSED** | Runtime A1/A5 assertions exist, are wired from `build_production_stack`, and have failing-path unit tests. |
| P1-5 ROSTER SCHEMA | **PARTIAL** | Schemas accept legitimate negatives; builder supports null-with-indicator; feature-store rematerialization is incomplete (no `data/features/` season partitions). |
| P2-6 CI CONSTRUCTION | **CLOSED** | `attach_metric_cis` states confidence level + week-block construction and attaches naive Wald CIs beside bootstrap for headline proportions. |
| P2-7 A2 BASIS SPLIT | **OPEN** | No evaluation code structures A2 MAE/CRPS (all seasons) vs ATS (line-backed seasons) with separate season lists / n. |

---

## P0-1 — Calibration / conformal on predict path

### Call chain (what is actually invoked)

`ProductionEnsemblePredictor.fit` (`production_stack.py`):

1. OOF μ → σ / quantile / ρ → **`_fit_cqr_layer`** (L895) → **`_fit_calibration_from_oof`** (L896).
2. `_fit_cqr_layer` (L1266–1296) calls `fit_cqr` from `ncaa_quant.models.conformal`.
3. `_fit_calibration_from_oof` (L1298–1353) fits **`PitRecalibrator`** via `fit_pit_recalibrator` / `gate_pit_recalibrator` from `ncaa_quant.models.pit_calibration` — **not** `models/calibrate.py`.

`ProductionEnsemblePredictor.predict` (`production_stack.py`):

1. Point / σ / MC market probs (L899–1027).
2. CQR intervals: `conformalize_intervals(out, self._cqr, …)` (L984–990) when `_cqr` is set.
3. Calibrated probs: `self._apply_calibrator(...)` (L1028–1030) → margin/total PIT maps (L1381–1398).

Imports at top of `production_stack.py`:

- L64: `from ncaa_quant.models.conformal import CQRResult, conformalize_intervals, fit_cqr`
- L79+: `from ncaa_quant.models.pit_calibration import ...`
- **No** import of `ncaa_quant.models.calibrate`.

### `models/calibrate.py`

Module docstring (L1–16) states it is **diagnostics only**; production uses distributional PIT. Class docstring on `ProductionEnsemblePredictor` (L790–794) still says “isotonic per market,” which is stale relative to the PIT implementation.

### Verdict

Calibration **is** on the predict path (PIT), and CQR **is** on the path. Literal ask “`calibrate.py` isotonic per derived market **and** `conformal.py` CQR” → **PARTIAL** (CQR yes; `calibrate.py` no).

---

## P0-2 — CLV same-source-row guard

### Guard

`src/ncaa_quant/betting/clv.py`:

- `compute_clv` (L265–300): raises `ClvError` when both `bet_line_source_row_id` and `close_line_source_row_id` are non-`None` and equal.
- `assert_distinct_line_sources` (L307–319): same check.

### Test

`tests/unit/test_betting.py::test_clv_raises_on_same_source_row` (L312–328) covers both APIs.

### Gap (why PARTIAL)

- `ClosingQuote.source_row_id` exists (L185), but `RecommendationRecord` has **no** bet-time source-row field.
- `settle()` (L445–494) never calls `assert_distinct_line_sources` / never compares source rows.
- Guard only fires when a caller explicitly passes matching IDs into `compute_clv`.

Also: `metrics.attach_metric_cis` (L1161–1167) raises if CLV values are identically zero — a metrics-layer backstop, not the CLV source-row guard.

---

## P0-3 — Run provenance

| Check | Result |
|---|---|
| `scripts/_task23_backtest.py` exists? | **No** (glob = 0). Related leftover: `scripts/_task23_fix_diag.py` (diag only, not the banned driver). |
| Loop ownership | Production path is `ncaa-quant backtest run` → `evaluation/backtest_runner.run_backtest` → `WalkForwardHarness` (`cli.py` L546–623; `backtest_runner.py`). |
| Prediction `run_id` / `ablation_id` | Stamped in `walkforward.py` row dict L1268–1269. |
| Four-hash manifest | `require_complete_manifest` (`backtest_runner.py` L452–466) requires `git_sha`, `dvc_hash`, `config_hash`, `environment_lockfile_hash`, non-empty `seed_manifest`, and ablation settings; `run_backtest` builds/writes via `build_manifest` / `write_manifest` (L636–645). Test: `tests/unit/test_task22b.py` around L496–502. |
| Determinism | `tests/unit/test_walkforward.py::test_determinism_byte_identical_prediction_tables` (L247–260): same config/seed → `predictions_bytes` equal. Also `test_task22b.py` production infoset determinism (~L580–614). |

**CLOSED** for code/infrastructure. (Whether historical Task 23 memo numbers were reproduced through this path is out of scope for this code audit.)

---

## P1-4 — A1 / A5 runtime preconditions

### Assertions

`production_stack.py`:

- `assert_a5_garbage_time_precondition` (L740–751): errors if no plays, or `n_on >= n_off` (inert GT filter).
- `assert_a1_priors_precondition` (L754–786): errors if priors missing/empty, no numeric columns, or all teams share the same prior values.

Wired in `build_production_stack` when `enforce_ablation_preconditions=True` (default) (L1648–1674): A1 when `preseason_priors == "league_mean"`; A5 when filter off and `play_counts` provided.

`backtest_runner.run_backtest` passes `enforce` from config (L511–523). CLI supplies `play_counts` from `build_observations_from_staged` (`cli.py` L602–616).

### Tests

- `tests/unit/test_task23_fix_distribution.py`: `test_a5_precondition_errors_when_inert` (L266+), `test_a1_precondition_errors_when_priors_missing` (L271+).
- `tests/unit/test_task22b.py`: A1/A5 precondition usage (~L247–250, L403–419).

**CLOSED.**

### Operational note (not a P1-4 code miss)

CLI `backtest_run` never loads or passes `priors_frame` (`cli.py` L610–623). An A1 config will therefore hit `assert_a1_priors_precondition(None)` and fail loud — correct for P1-4, but **blocks A1 in the eight-run CLI suite** until priors are supplied.

---

## P1-5 — Roster schema + rematerialization

### Schema (negatives)

`src/ncaa_quant/data/schemas.py`:

- `ReturningProductionSchema` (L330–343): `offense_pct` / `defense_pct` / `overall_pct` nullable floats — **no** `ge=0`; docstring explicitly allows negatives (Task 12 / 23-FIX).
- `RecruitingSchema.points` (L346–359): signed; test `tests/unit/test_recruiting_schema_negatives.py`.
- `PortalSchema.rating` (L365–375): nullable float, no non-negativity constraint.

### Builder null-with-indicator

`features/builders/roster.py` module docstring (L9): missing → null + `is_missing`. `RosterFeatureBuilder.compute` (L685–686, L709–710) emits `is_missing` when `null_policy == "indicator"`. Tests in `tests/unit/test_roster.py` assert portal net is NaN not 0 when absent.

### On-disk feature store

`data/features/`:

- Only `.gitkeep` and **`roster_task23_fix.parquet`** (not the `(feature, version, season, week)` layout from `features/materialize.py`).
- That parquet: **4858 rows**, seasons **2019–2025 only** (694 rows/season); **no** `is_missing` columns.
- `portal_net_rating` includes negatives (124 rows &lt; 0) — schema/data path accepts them.
- Many columns heavily null (e.g. `returning_defense_pct` 4858/4858 null) without companion indicators in this wide frame.

### Staged prior-family (contrast)

Under `data/staged/`, `rosters`, `returning_production`, `recruiting`, `talent`, `portal`, `coaches` all have `season=2014` … `season=2025`.

**PARTIAL:** schema + builder OK; feature-store rematerialization / full-history roster feature partitions **not** done.

---

## P2-6 — CI construction in `metrics.py`

`attach_metric_cis` (`metrics.py` L1106–1177):

- Docstring (L1117–1120): confidence level = `1 - alpha` (default 95% via `DEFAULT_ALPHA = 0.05` in `significance.py` L19); blocks = whole week-label groups, variable length.
- Bootstrap via `block_bootstrap` / `rate_ci_block`; `ConfidenceInterval.method = "block_bootstrap"` (`significance.py` L42, L153+).
- Naive Wald: `naive_proportion_ci` (L1081–1103); attached as `ats_accuracy_naive`, `pct_positive_clv_naive` beside bootstrap counterparts (L1149–1175).
- `format_rate_with_ci` prints `(level% CI, n=…)` (`significance.py` L116–118).

**CLOSED** for the code deliverable.

---

## P2-7 — A2 basis split

Searched `src/ncaa_quant/evaluation/` for A2 basis-split / line-backed season reporting: **no** structured reporter that separates MAE/CRPS (all seasons) from ATS (line-backed only) with per-component season lists and n.

Any such table in notes is documentation only — not code.

**OPEN.**

---

## Materialized-seasons inventory

### `data/staged/`

Core and reference tables present for **2014–2025** (spot-checked):

| Table | Seasons |
|---|---|
| `games`, `plays`, `advanced_box`, `lines_historical` | 2014–2025 |
| `rosters`, `returning_production`, `recruiting`, `talent`, `portal`, `coaches` | 2014–2025 |

(Other staged families: `drives`, `teams`, `venues`, `weather`, odds tables — not re-enumerated week-by-week here.)

### `data/features/`

| Artifact | Seasons | Notes |
|---|---|---|
| *(no feature/version/season/week partitions)* | — | Feature store layout empty |
| `roster_task23_fix.parquet` | 2019–2025 | Ad-hoc; 694 rows/season; not null-with-indicator wide frame |

### Task 14 full-history filter (2014–2025)

**Executed** — verified on disk, not from notes alone:

| Artifact | Evidence |
|---|---|
| `data/tmp/state_space_acceptance_14/summary.json` | `n_obs=10316`, `filter_wall_clock_sec≈2.72`, health present |
| `…/history.parquet` | seasons **2014–2025**, 37870 rows |
| `…/games.parquet` | seasons **2014–2025**, 10372 games |
| `data/tmp/backfill_23_filter/summary.json` | `seasons_included: 2014…2025`, `n_obs=10316`, `elapsed_s=2.61` |

---

## Verdict — ready for the eight-run re-run?

**Not fully ready.** Code closed enough for fundamental / A2–A4 / market-aware CLI runs to *start* (staged 2014–2025 + Task 14 filter history present; calibration+CQR wired via PIT+conformal; provenance via `ncaa-quant backtest run`). Exact blockers / must-fix before trusting the suite:

1. **P1-5 incomplete** — no proper `data/features/` roster (or other) partitions; only a 2019–2025 ad-hoc parquet without `is_missing`. Confirm the production feature provider builds roster/prior inputs from **staged** tables for 2014–2025, or rematerialize before treating “roster family present” as true.
2. **A1 CLI blocked** — `backtest run` never passes `priors_frame`; A1 precondition will raise. Wire fitted priors into the CLI path or A1 stays NOT RUN.
3. **A5 may still fail** on real data if WP-derived GT flags do not change play counts (precondition is present and correct — verify `n_on < n_off` on the staged play set before scheduling A5).
4. **P2-7 open** — A2 results will again mix bases unless reporting is split by hand or coded before citation.
5. **P0-2 partial** — CLV guard is not on `settle()`; do not treat CLV as safe until settlement threads distinct source rows (or continues to refuse computation).

Non-blocking for execution but still open vs the literal P0-1 wording: production uses **PIT**, not `models/calibrate.py` per-market isotonic.
