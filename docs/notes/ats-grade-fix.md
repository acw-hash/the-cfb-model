# TASK ATS-GRADE-FIX — Snapshot line ladder repair + v2 grades

**Date:** 2026-08-11  
**Status:** FIX LANDED — ladder + guard + regrade **COMPLETE**; v2 reruns **IN FLIGHT**.  
**Diagnosis:** `docs/notes/ats-grade-diag.md`  
**Git:** (this commit)

---

## Fix summary

**Root cause:** `_resolve_from_snapshots` took `median(line)` over **all** spread
sides. Odds `outcome.point` is side-relative (±S), collapsing to ~0.

**Repair:** Filter `side == CFBD home school` (5b-patch2 name-based semantics),
then median across **books** only. Provenance fields: `book`, `side`,
`source_row_id` (Odds `snapshot_id` when present). Shared helper:
`src/ncaa_quant/features/market_lines.py`.

**Guard:** `assert_prediction_ats_plausible` — two-sided fair-coin band
`0.5 ± 3·√(0.25/n)` per regime; wired into `backtest_runner.run_backtest`
finalization (`AtsPlausibilityError` = PIPELINE ERROR).

**Games loader:** `load_staged_games` attaches `home_team` / `away_team` from
staged `teams` for CFBD-home name matching.

---

## STEP 1–3 — Code + tests

| Item | Location |
|---|---|
| Home-side ladder | `walkforward._resolve_from_snapshots`, `resolve_lines_for_games` |
| Market orientation | `features/market_lines.py` (used by ladder + tests) |
| Guard | `metrics.assert_ats_vs_close_plausible`, `assert_prediction_ats_plausible` |
| Guard wiring | `backtest_runner.run_backtest` |
| Tests | `tests/unit/test_ats_grade_fix.py` (24-fixture, synthetic ±S, guard two-sided) |

---

## STEP 4 — REGRADED_V2 (fundamental, A1, A2, A4, A5)

**Method:** `scripts/_ats_regrade.py` — refresh `spread_close` / `spread_asof` via
fixed ladder; recompute `p_ats_home` = Φ((μ+S)/σ) at corrected close.  
**Output:** `data/backtests/<run>/<subdir>/grade_v2/predictions.parquet` +
`grade_manifest.json` (`vintage=REGRADED_V2`). **v1 `predictions.parquet` retained.**

Machine summary: `docs/notes/_artifacts/ats_grade_fix/regrade_summary.json`.

### Fundamental full — snapshot regime (REGRADED_V2)

| Metric | CONTAMINATED_v1 | REGRADED_V2 | 95% CI (bootstrap / naive) |
|---|---:|---:|---|
| ATS vs close | **39.7%** | **50.7%** | [48.7%, 52.7%] / [49.0%, 52.3%] (n=3496) |
| ATS log-loss (model / mkt@0.5) | 0.998 / 0.693 | **0.924 / 0.693** | model still worse |
| `spread_close` abs median | 0.0 | **12.6** | pct \|S\|<0.5: 0.3% |

2019 CFBD ATS unchanged in spirit: **51.3%** REGRADED_V2 (n=743) vs 50.7% v1.

### A2 by basis (REGRADED_V2)

| Component | Basis | REGRADED_V2 | v1 (CONTAMINATED) |
|---|---|---:|---:|
| MAE margin | all_seasons | 16.45 | 16.45 (unchanged) |
| ATS | line_backed | **49.8%** (n=4239) | 38.1% |
| Snapshot ATS only | snapshots 2021–24 | **50.4%** (n=3496) | 36.3% vs fund 39.7% |

**Revised A2 headline:** continual updates still hurt **MAE** (+1.60 all-season);
snapshot **ATS delta** is ~**−0.3 pp** (50.4% vs 50.7%), not −3.4 pp.

### A4 ATS framing (REGRADED_V2)

| Side | REGRADED_V2 snapshot ATS | Fundamental REGRADED_V2 |
|---|---:|---:|
| Single LGBM (A4) | **50.7%** (n=3496) | **50.7%** |
| Increment (A4 − fund) | **0.0 pp** | — |

v1’s “A4 beats ensemble on snapshot ATS (+3.8 pp)” was a **grading artifact**.

---

## STEP 5 — RERUN_V2 (market-aware, A3, A6)

**Configs:** `configs/ablations/task23_*_reduced_v2.yaml`  
**Driver:** `scripts/_ats_v2_rerun.ps1` (sequential; wall clocks →
`docs/notes/_artifacts/ats_grade_fix/v2_wall_clocks.json`).

| Run | Features | Grading ladder | Status |
|---|---|---|---|
| `task23_market_aware_reduced_v2` | snapshot `mkt_spread` (fixed) | fixed snapshot ladder | **RUNNING** |
| `task23_a3_reduced_v2` | market off | fixed snapshot ladder | pending |
| `task23_a6_reduced_v2` | CFBD open/close | fixed snapshot ladder (same as all v2) | pending |

A6: `market_feature_source=cfbd_open_close` affects **features only**; grading
always used the snapshot close ladder (now fixed). v1 **36.5%** was not
“ATS vs CFBD close.”

*(Wall-clock table appended when reruns complete.)*

---

## Standing caveats (carried forward verbatim)

1. **REDUCED** scope (ADR 0013) — not §5.2-complete.  
2. **Possessions mostly null** — totals head often lacks §4.5 key feature.  
3. **n_books gradient** — snapshot vs CFBD regimes; never pool.  
4. **Lockbox 2025** excluded.  
5. **Regime split** — 2019 CFBD vs 2021+ snapshots reported separately.

---

## `make lint typecheck test`

Run at commit time (see session footer).
