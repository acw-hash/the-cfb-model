# TASK MKT-2019-FIX — Snapshot features never use CFBD; provenance recorded

**Date:** 2026-08-11  
**Scope:** Snapshot feature ladder (`walkforward.resolve_lines_for_games` feature
path), `market_lines.provenance_from_line_source`, provider provenance stamp,
ablation config comment, tests, re-run + ledger.  
**Forbidden (honored):** no tuning; no lockbox; no guard-band widening.

Artifacts: `docs/notes/_artifacts/mkt_2019_fix/`.

---

## STEP 1 — Ladder

With `market_feature_source=snapshots`, `resolve_lines_for_games(...,
for_features=True)` uses Odds snapshots only in **every** season. A season with
no snapshots (2019) yields null + `is_missing`. No CFBD open, no CFBD close, no
exceptions.

`for_features=True` + `closing=True` is a hard `WalkForwardError`: close is
never a feature at any decision point in any regime (Tuesday-knowability,
DESIGN §7.2 item 8 / §2.7). Evaluation closes (`for_features=False`,
`closing=True`) are unchanged, including CFBD `cfbd_close_eval` fill.

Evaluation as-of lines for 2019 (`for_features=False`) still record CFBD
open/else close — that is the bet-time measurement instrument, not a feature.

## STEP 2 — Provenance

`market_provenance` is stamped from the resolving `line_source` via
`provenance_from_line_source` at resolution. Never inferred from non-nullness
or from `market_feature_source` config.

| resolving `line_source` | `market_provenance` |
|---|---|
| `null` / missing | `null` |
| `cfbd_*` | `cfbd` |
| Odds snapshot sources | `snapshots` |
| anything else | `null` (do not guess snapshots) |

A row resolved from nothing carries null provenance. A CFBD-sourced row can
never read `snapshots`.

## STEP 3 — Tests

`tests/unit/test_mkt_2019_fix.py`:

- 2019 fixture under snapshots config → all `mkt_*` null + `is_missing`
- A6 `cfbd_open_close` still works 2021–2024 and still hard-errors outside that window
- Feature resolution with `closing=True` is a hard error
- Provenance: unresolved → null; CFBD → never `snapshots`
- 757-row violation reproduced against the old relabel (failing old behavior),
  passing under the fix (staged 2019 + archived Tuesday prediction ids)

## STEP 4 — Re-run

Config: `task23_market_aware_full_reduced_v2_tue`  
Label: `mkt-2019-fix;FEATURE_TIME=TUESDAY_DECISION;ensemble_scope=REDUCED_PER_ADR_0013`

**Attempt (2026-08-11, ~48 min wall clock): FAILED before publish.**

```
PredictionQualityGateError: SD(mu)=0 in 7 (season, week) block(s):
[(2019, 2), (2019, 3), (2019, 4), (2023, 1), (2023, 2), (2023, 3), (2023, 4)]
```

No new `predictions.parquet` was written; on-disk bytes are still the pre-fix
Tuesday run (archived under `docs/notes/_artifacts/mkt_2019_fix/`). Likely
mechanism: null 2019 `mkt_*` removes cross-game spread in early 2019 weeks;
2023 early weeks may share a retrain/cold-start artifact. Prior week-align-fix
run passed this gate because 2019 still carried CFBD-close features.

**Blocked:** guard disposition, equivalence check, corrected 2021–2024 table.

## STEP 5 — Blast radius ledger

Reason code: **CONTAMINATED_2019_FEATURE_SOURCE**

Struck (retained, not deleted):

| Number | Where | Reason |
|---|---|---|
| Tuesday market-aware 2019 ATS **45.63%** [42.9%, 48.6%] (LL 0.835, MAE 14.59, n=743) | `week-align-fix.md` | CONTAMINATED_2019_FEATURE_SOURCE |
| kick−5min market-aware 2019 ATS **47.51%** [44.5%, 50.8%] (LL 0.961, MAE 15.11, n=743) | `mkt-asof-fix.md` | CONTAMINATED_2019_FEATURE_SOURCE |
| v1 market-aware 2019 ancestors | `23-rerun-r1.md` | CONTAMINATED_2019_FEATURE_SOURCE (+ CONTAMINATED_v1 grading) |

Superseded-by-retrain (not deleted; not in 2019 feature-source blast radius):

| Number | Where | Disposition |
|---|---|---|
| Tuesday snapshot 2021–2024 ATS **51.42%** [49.3%, 53.6%] (LL 0.812, MAE 14.32, n=3491) | `week-align-fix.md` | superseded-by-retrain |

Artifact: `docs/notes/_artifacts/mkt_2019_fix/blast_radius_ledger.json`.

## Built / decisions

- `for_features` flag on `resolve_lines_for_games` keeps evaluation as-of/close
  distinct from the feature information set. Default `False` preserves existing
  2019 CFBD as-of lines and snapshot-season evaluation closes.
- A6 still uses `_resolve_cfbd_only_line` (CFBD as features, 2021–2025 only).
  Close-as-feature ban on the snapshots ladder is structural
  (`for_features` + `closing=True` hard error).
- `line_source` is returned from `_resolve_market_lines` for audit/tests but is
  **not** added to `MARKET_FEATURE_COLS` (no extra model column; training change
  is 2019 null `mkt_*` only).
- Ambiguity: A6's dedicated CFBD feature helper still prefers close then open
  (historical A6 behavior; A6 not re-run here). Snapshot-config features never
  take that path.

## Acceptance

- [x] Step 1: snapshots feature path → 2019 null + is_missing; closing=True hard error
- [x] Step 2: provenance from resolving source
- [x] Step 3: failing-then-passing 757 test; provenance tests; A6 window
- [ ] Step 4: re-run table + guard disposition + equivalence check (**blocked:
  quality gate SD(mu)=0 on 7 week-blocks**)
- [x] Step 5: strike ledger (numbers retained)
- [ ] `make lint typecheck test`
