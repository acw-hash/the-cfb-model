# TASK V2-BASELINE - Single-vintage v2 baseline + market-aware guard diagnosis

**Date:** 2026-08-11  
**Vintage in force:** **RERUN_V2** (fundamental under v2 code; A3 compared within-vintage).  
**ensemble_scope:** `REDUCED_PER_ADR_0013`.  
**FORBIDDEN in this memo:** cross-vintage comparisons; citing REGRADED_V2 as the
baseline; publishing market-aware without CLEAN audit; widening the guard band.

Artifacts: `docs/notes/_artifacts/v2_baseline/`.

---

## STEP 1 - Determinism + V2 BASELINE

| Run | SHA-256 of canonical prediction bytes |
|---|---|
| det1 | `fe02b517387a7c1c90bc5fa9fb6ab4935ab167ad51de5082099289e8e4eeda05` |
| det2 | `fe02b517387a7c1c90bc5fa9fb6ab4935ab167ad51de5082099289e8e4eeda05` |
| byte-identical | **YES** |

### V2 BASELINE - `task23_fundamental_reduced_v2` (snapshot + 2019)

All future comparisons are against this table, **not** REGRADED_V2.

| Regime | ATS | n | LL (model) | MAE margin | CRPS margin | 95% bootstrap CI |
|---|---:|---:|---:|---:|---:|---|
| cfbd_2019 | 50.7% | 743 | 0.950 | 17.84 | 13.04 | [48.1%, 54.0%] |
| snapshots_2021_2024 | 52.2% | 3491 | 0.820 | 14.21 | 10.18 | [50.3%, 54.2%] |

---

## STEP 2 - A3 equivalence (within vintage)

**Verdict:** **EQUIVALENT** - A3 ~ fundamental_v2; readout +1.5pp was cross-vintage
drift (REGRADED_V2 fund vs RERUN_V2 A3).

- n_fund=4944 n_a3=4944 both=4944 cell_disagreements=0

### Config deltas (market flag expected)

- `EXPECTED stack: fund='fundamental' a3='market_aware'`
- (walkforward identical aside from `run_id` / `ablation_id` / `model_version`;
  both have `market_features_available: false`)

### 6d restated within-vintage

- fundamental_v2 snapshot ATS: **52.2%**
- A3 RERUN_V2 snapshot ATS: **52.2%**
- delta (A3 - fund): **+0.00 pp**
- Note: within-vintage A3 and fundamental_v2 coincide; the readout's +1.5pp mixed
  REGRADED_V2 fundamental (50.7%) with RERUN_V2 A3 (52.2%).

---

## STEP 3 - Market-aware leak audit

**Verdict:** **LEAK**  
week-points=20 feature-rows=4736 leaks=256 same-source-row-as-grade=3016
not-before-decision=0

Per-feature resolution timestamps:
`docs/notes/_artifacts/v2_baseline/market_feature_audit.json`
(compact `mkt_spread` table in `market_feature_audit_table.json`).

Feature ladder = `closing=False` @ decision `as_of` (not capped at kickoff).  
Grading ladder = `closing=True` @ kickoff.  
Leak reason (all 256): `feature_event_time_at_or_after_kickoff` on snapshot
`mkt_*` when week `as_of` falls after kickoff.

Sample leak rows (`mkt_spread`):

| season | week | game_id | feature_et | grade_et | feat_row | grade_row | before? | distinct? | leak? |
|---:|---:|---:|---|---|---|---|---|---|---|
| 2021 | 2 | 401282809 | 2021-09-11T19:55:00+00:00 | 2021-09-11T19:25:00+00:00 | `d4f41786` | `9b41a47a` | True | True | True |
| 2021 | 2 | 401282066 | 2021-09-11T19:55:00+00:00 | 2021-09-11T19:25:00+00:00 | `6a4e27ef` | `76f7cd18` | True | True | True |
| 2021 | 2 | 401282189 | 2021-09-12T02:05:00+00:00 | 2021-09-12T01:55:00+00:00 | `1cfc3c1b` | `eb332899` | True | True | True |
| 2021 | 3 | 401282627 | 2021-09-19T00:55:00+00:00 | 2021-09-18T22:55:00+00:00 | `7a420927` | `8f439668` | True | True | True |
| 2021 | 3 | 401282072 | 2021-09-19T02:35:00+00:00 | 2021-09-19T02:25:00+00:00 | `444c1664` | `f2792b90` | True | True | True |

Enabling condition: `week_decision_as_of` is systematically the Tuesday **after**
CFBD-labeled week's games (`kickoff < as_of`), so feature `bound=as_of` admits
post-kickoff snaps into `mkt_spread` / `mkt_total` / `mkt_n_books` /
`mkt_is_missing`.

---

## STEP 4 - Disposition

**LEAK -> STOP.** See `docs/notes/_artifacts/v2_baseline/STOP.md`.

- Named features: all snapshot `mkt_*` via
  `resolve_lines_for_games(..., closing=False)` without a kickoff cap.
- Blast radius: market-aware stacks with `market_feature_source=snapshots`
  (including unpublished RERUN_V2 market-aware 52.71% exception rate - not a
  graded table). **Out of blast radius:** A3 (market off), A6 (CFBD features),
  fundamental.
- Fix scope (separate session): cap feature bound at `min(as_of, kickoff)`;
  separately investigate week as_of vs CFBD week labeling. Do **not** fix here.
- Market-aware **not** published. Guard band **not** widened. No ADR 0014
  (force-publish path requires CLEAN audit).

---

## Acceptance checklist

- [x] byte-identical hash pair
- [x] V2 baseline table (this memo)
- [x] equivalence verdict (`EQUIVALENT`) + config diff
- [x] audit table with per-feature resolution timestamps
- [x] STOP report (`docs/notes/_artifacts/v2_baseline/STOP.md`)
- [x] `make lint typecheck test` (session gate)
