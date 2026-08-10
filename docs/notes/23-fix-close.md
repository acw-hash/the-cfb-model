# Task 23-FIX-CLOSE

**Date:** 2026-08-10  
**Scope:** Close open items from `docs/notes/23-fix-audit.md` in the order
prescribed by the task prompt. Eight-run set **not** executed.

---

## Item 1 — P0-1 resolution (CLOSED)

**Ruling:** PIT + CQR is the AUDIT-4-compliant calibration architecture.
`models/calibrate.py` remains diagnostics-only. Task 23-FIX P0-1's literal
"isotonic per market" wording predates AUDIT-4 and is satisfied by
`pit_calibration` + `conformal` on the predict path.

- ADR: `docs/adr/0011-p0-1-calibration-path.md`
- Docstring fixed on `ProductionEnsemblePredictor` (no longer says "isotonic
  per market")
- Acceptance artifacts (held-out 2023 smoke, PIT path):
  - `docs/notes/_artifacts/task23_fix/calibration_slope_intercept_pit_2023.json`
  - `docs/notes/_artifacts/task23_fix/reliability_2023.png`
  - `docs/notes/_artifacts/task23_fix/pit_2023.png`

ML Cox on smoke emit: slope_before≈0.634 → slope_after≈0.114 (gate may leave
map off on thin smoke; diagnostics still recorded). Margin PIT KS improves
in-sample under the isotonic map fit on a time-ordered prefix.

**Forbidden path not taken:** `calibrate.py` isotonic was not wired into
production.

---

## Item 2 — P0-2 CLV guard on settle() (CLOSED)

- `RecommendationRecord.bet_line_source_row_id` added; populated at
  recommendation time (via `backtest_runner.build_recommendation_record`).
- `settle()` requires both bet and close source-row IDs — missing either
  raises `ClvError`. Distinct IDs call `assert_distinct_line_sources`;
  `same_line` rows also thread IDs into `compute_clv`.
- `settle_week` rejects bare `(side, other)` tuples (cannot carry the guard).
- Tests: `test_settle_raises_on_same_source_row`,
  `test_settle_passes_with_distinct_source_rows` (end-to-end through
  `settle()`).

---

## Item 3 — P1-5 scope ruling (CLOSED — staged world)

### Step 3A read path

`ProductionFeatureProvider.compute_game_features`
(`production_stack.py` ~L339–383) builds features only from:

1. `rating_state` (Stage-1 snapshot from `StateSpaceRatingEngine`)
2. optional market columns from snapshots / CFBD lines

It never opens `data/features/`. Roster / prior inputs enter Stage-1 only as
`priors_frame` → `StateSpaceRatingEngine._priors_for_season` →
`build_preseason_states` (`production_stack.py` ~L249–275). The CLI already
asserts staged prior-family tables via `assert_prior_family_staged`.

**World: staged.** The original Task 23 "missing feature family" caveat is
about Stage-1 priors from staged prior-family tables (2014–2025 present), not
about rematerializing `data/features/` roster partitions. Mapping-layer roster
columns were never on this provider.

### Step 3B-staged action

- Quarantined `data/features/roster_task23_fix.parquet` →
  `data/features/_quarantine/` (+ README)
- Tests: `tests/unit/test_p1_5_roster_path.py` — provider does not read the
  feature store; `RosterFeatureBuilder` emits `is_missing`

`features/materialize.py` was **not** edited (Step 3A said staged).

---

## Item 4 — A1 priors into CLI (CLOSED)

`ncaa-quant backtest run` now loads and passes `priors_frame` via
`load_fitted_priors_frame_for_backtest` (Task 15 cache parquet, else rebuild
from staged + `summary.json` weights). A1 precondition unchanged — still
raises on `None`/degenerate.

Test: `tests/unit/test_a1_cli_priors.py` — CLI loader + A1
`build_production_stack` reaches precondition with a populated frame.

---

## Item 5 — A5 real-data verification (CLOSED — NOT RUN)

Garbage-time filter on staged plays, per season 2014–2025
(`build_observations_from_staged` → `apply_garbage_time`):

| season | n_on | n_off | n_on < n_off? |
|---:|---:|---:|:---:|
| 2014 | 158315 | 158315 | no |
| 2015 | 160180 | 160180 | no |
| 2016 | 158518 | 158518 | no |
| 2017 | 158574 | 158574 | no |
| 2018 | 160512 | 160512 | no |
| 2019 | 159915 | 159915 | no |
| 2020 | 102809 | 102809 | no |
| 2021 | 158634 | 158634 | no |
| 2022 | 160327 | 160327 | no |
| 2023 | 159011 | 159011 | no |
| 2024 | 162726 | 162726 | no |
| 2025 | 166057 | 166057 | no |

**Verdict:** A5 is **NOT RUN** for every season 2014–2025. The precondition
correctly blocks (`n_on >= n_off`). Cause: WP / garbage-time flags do not
change the play set (Task 9/13 finding — not patched here).

---

## Item 6 — P2-7 A2 basis-split reporter (CLOSED)

`report_a2_components_by_basis` in `evaluation/metrics.py` emits separate
`BasisMetricRecord`s for MAE/CRPS (`all_seasons`) and ATS (`line_backed`) with
season lists and n. No pooled render path exists.

Test: `tests/unit/test_a2_basis_split.py`.

---

## Acceptance checklist

| Paste | Status |
|---|---|
| ADR + corrected docstring + artifact paths | Item 1 |
| settle()-level guard test + `bet_line_source_row_id` | Item 2 |
| Step 3A staged verdict + 3B quarantine | Item 3 |
| A1 CLI fixture → populated precondition | Item 4 |
| Per-season n_on/n_off + NOT RUN | Item 5 |
| Basis-split test | Item 6 |
| `make lint typecheck test` | below |

**Explicitly not done:** eight-run set; any hyperparameter / threshold /
prior-weight / filter-cutoff tuning; relaxing any precondition.
