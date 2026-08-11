# TASK ATS-GRADE-FIX — Snapshot line ladder repair + v2 grades

**Date:** 2026-08-11  
**Status:** **COMPLETE** — ladder + guard + REGRADED_V2 + RERUN_V2 (A3/A6 published;
market-aware refused by guard).  
**Diagnosis:** `docs/notes/ats-grade-diag.md`  
**Git:** `7ea3cea` (fix) + follow-up memo/artifacts.

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
**Driver:** `scripts/_ats_v2_rerun.ps1`  
**Artifacts:** `docs/notes/_artifacts/ats_grade_fix/v2_wall_clocks.json`,
`rerun_v2_summary.json`.

| Run | Features | Grading ladder | Status |
|---|---|---|---|
| `task23_market_aware_reduced_v2` | snapshot `mkt_spread` (fixed) | fixed snapshot ladder | **FAILED guard** — no parquet |
| `task23_a3_reduced_v2` | market off | fixed snapshot ladder | **published** |
| `task23_a6_reduced_v2` | CFBD open/close | fixed snapshot ladder (same as all v2) | **published** |

A6: `market_feature_source=cfbd_open_close` affects **features only**; grading
always used the snapshot close ladder (now fixed). CONTAMINATED_v1 **36.5%** was
not “ATS vs CFBD close.”

### Wall clocks (actuals — estimator recalibration)

| config | wall_clock_sec | ~min |
|---|---:|---:|
| task23_market_aware_full_reduced_v2 | 2963.3 | 49.4 |
| task23_A3_market_features_off_reduced_v2 | 2347.6 | 39.1 |
| task23_A6_cfbd_open_close_reduced_v2 | 1592.9 | 26.5 |
| **total (sequential)** | **6903.8** | **115.1** |

v1 reduced runs were ~65–115 min each; these v2 wall clocks are **lower** on the
same machine/window (~26–49 min). Use these actuals for future reduced-scope
estimates, not the Task 23 plan’s ~90 s figure.

### market-aware full (RERUN_V2) — guard refusal

`AtsPlausibilityError` on **GOOD** side (two-sided guard working as designed):

```text
regime='snapshots_2021_plus': rate=52.71% n=3491
band=[47.46%, 52.54%] (z=3.0)
line_source_mix={'odds_api_snapshot_fallback': 2697, 'cfbd_close_eval': 495,
                 'odds_api_snapshot': 404, 'null': 17}
pct_|spread_close|<0.5=0.3%
```

Predictions **not published** (memo write blocked). Rate above is from the
exception only — do not treat as a graded table. Ladder diagnostics look healthy
(`pct_near0=0.3%`); this is a fair-coin-band trip, not the old ~0-close bug.

Driver note: first pass of `_ats_v2_rerun.ps1` did not check `$LASTEXITCODE`, so
A3/A6 continued after market-aware failed. Script now exits on non-zero.

### A3 market-off (RERUN_V2) — published

| Regime | ATS | n | log-loss (model / mkt) | MAE margin | 95% bootstrap CI |
|---|---:|---:|---|---:|---|
| CFBD 2019 | **50.7%** | 743 | 0.950 / 0.693 | 17.84 | [48.0%, 53.7%] |
| Snapshots 2021–24 | **52.2%** | 3491 | 0.820 / 0.693 | 14.21 | [50.3%, 54.2%] |

Just inside the plausibility band (upper 52.54%). Vs fundamental REGRADED_V2
snapshot ATS **50.7%**: A3 is **+1.5 pp** on the same fixed ladder.

**Revised A3 framing:** CONTAMINATED_v1’s “market features hurt” (A3 +6.9 pp ATS
vs market-aware) is **dead**. Market-aware’s unpublished 52.71% vs A3’s 52.2%
would reverse the sign if published; MAE comparison needs a published
market-aware table (blocked by guard).

### A6 CFBD open/close features (RERUN_V2) — published

| Source | ATS vs **fixed snapshot** close | n | 95% bootstrap CI | MAE margin |
|---|---:|---:|---|---:|
| A6 (RERUN_V2) | **51.9%** | 3369 | [50.9%, 53.0%] | 14.94 |
| CONTAMINATED_v1 (do not cite) | 36.5% | — | — | — |

Grading ladder identical to other RERUN_V2 runs; only features differ. v1
**36.5%** was grading contamination, not “CFBD features destroy ATS.”

---

## Corrected headline table (vintage-labeled)

| Stack | Vintage | Snapshot ATS 2021–24 | Notes |
|---|---|---:|---|
| fundamental | REGRADED_V2 | **50.7%** (n=3496) | predictions unchanged; closes fixed |
| A1 | REGRADED_V2 | (see regrade_summary.json) | regrade only |
| A2 | REGRADED_V2 | **50.4%** (n=3496) | Δ vs fund ≈ −0.3 pp (not −3.4) |
| A4 | REGRADED_V2 | **50.7%** (n=3496) | tied with fund (v1 +3.8 pp was noise) |
| A5 | REGRADED_V2 | (see regrade_summary.json) | regrade only |
| market-aware | RERUN_V2 | **unpublished** (52.71% tripped guard) | features re-run |
| A3 market off | RERUN_V2 | **52.2%** (n=3491) | features re-run |
| A6 | RERUN_V2 | **51.9%** (n=3369) | features re-run |

Never mix unlabeled v1 snapshot ATS with these rows.

---

## Standing caveats (carried forward verbatim)

1. **REDUCED** scope (ADR 0013) — not §5.2-complete.  
2. **Possessions mostly null** — totals head often lacks §4.5 key feature.  
3. **n_books gradient** — snapshot vs CFBD regimes; never pool.  
4. **Lockbox 2025** excluded.  
5. **Regime split** — 2019 CFBD vs 2021+ snapshots reported separately.

---

## `make lint typecheck test`

`make lint typecheck test` — **743 passed** at fix commit (`7ea3cea`).
