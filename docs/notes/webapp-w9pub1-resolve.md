# W9-PUB1-RESOLVE — merge w9-pub1 into post-w9-d main

**Branch:** `w9-pub1-resolve` (off post-w9-d `main`; do not merge to main until acceptance)  
**Date:** 2026-08-21  
**Base:** `5e60a5f` (main after W9-MERGE Phase 3)  
**Merged:** `814a8b0` (W9-PUB1)

Precondition: `NCAA_QUANT_WEBAPP__EXPORT_ENABLED=false`;
`load_config().webapp.export_enabled is False`.

## Conflicts (only two hunks; both additive)

### `predict.py` — live row stamp

Kept **both**:
- w9-d: `champion_version`, `registered_at`
- w9-pub1: `as_of=resolved_as_of`, `as_of_source`

### `export.py` — `export_publish_artifacts`

Kept **both**:
- w9-pub1: `as_of` / `as_of_source` from `publish_result`
- w9-d: `identity` + `provenance` from `merged_rows` (vintage / champion stamps)

History write remains **after** `build_week_predictions` (gate inside `build_game_prediction`).

## Invariants

1. **History = post-gate.** `append_publish_history(week_preds)` uses the gated artifact. New test `test_history_line_carries_post_gate_null_bands` asserts history line == `week_predictions` and nulled bands on an incoherent row. **Pre-fix demo:** temporarily restored raw CQR into the history payload only → `assert records[0] == wp` failed. Restored post-gate write.
2. **Schema version `1.3.0`.** Covers w9-pub1 `as_of` / `as_of_source` (optional on push allowlist so 1.2.0 fixtures still validate). w9-d vintage / absence fields were already in the 1.2.0 game key set (nulls, not new keys). Major stays 1.
3. **Order:** slate filter → predict → coherence gate (in game build) → artifact → slate-regression (game ids) → history → push.
4. **Both stamps:** `as_of`, `as_of_source`, `vintage_label` / provenance coexist.
5. **Operator `as_of` does not change gate logic.**

## Fixture adaptation (not a dropped test)

`test_export_writes_history_with_export_disabled` used `run_id="test"`, which w9-d’s provenance registry refuses. Updated to `task23_fundamental_reduced_v3` so the same test still runs.

## Test count reconciliation

| | count |
|---|---|
| post-w9-d main | 942 passed, 1 deselected |
| w9-pub1 marker tests | +9 |
| resolve invariant test | +1 (`test_history_line_carries_post_gate_null_bands`) |
| **Expected** | **952 passed, 1 deselected** |

Task arithmetic `942+9=951` predates the required new history test; **952** is the reconciled target.

## Marker tests

- `test_containment_week2_as_of_none_slate_unchanged`
- `test_incoherent_band_assertion_bite`
- `test_history_line_carries_post_gate_null_bands`

## `make test`

```
make test
# → uv run pytest -m "not live"
# ========= 952 passed, 1 deselected, 32 warnings in 305.57s (0:05:05) ==========
# Required test coverage of 80% reached. Total coverage: 80.53%
```

## Dry run (export gated, `push=False`, temp history)

Operator `as_of=2026-08-25T10:00:00Z`:

- 99 games; all eight early ids present
- **15** suppressed bands (same ids as VERIFY-2)
- `as_of_source=operator`, `schema_version=1.3.0`, `vintage_label=W9A_REVAL`
- one history line; `history == week_predictions`; `push is None`; `export_enabled=False`

## `git diff --stat` vs post-w9-d main (`5e60a5f`)

| path | attribution |
|---|---|
| `docs/adr/0017-…md` | w9-pub1 |
| `docs/notes/webapp-w9pub1.md` | w9-pub1 |
| `docs/notes/webapp-w9pub1-resolve.md` | **resolution** |
| `src/ncaa_quant/config.py` | w9-pub1 |
| `src/ncaa_quant/pipelines/predict.py` | w9-pub1 + **resolution** (kept w9-d champion stamps) |
| `src/ncaa_quant/webapp/export.py` | w9-pub1 + **resolution** (kept w9-d provenance; history post-gate) |
| `src/ncaa_quant/webapp/grade.py` | w9-pub1 |
| `src/ncaa_quant/webapp/publish_history.py` | w9-pub1 |
| `src/ncaa_quant/webapp/push.py` | w9-pub1 |
| `tests/integration/test_pipelines_e2e.py` | w9-pub1 |
| `tests/unit/test_webapp_w1.py` | w9-pub1 |
| `tests/unit/test_webapp_w8c.py` | w9-pub1 |
| `tests/unit/test_webapp_w9p.py` | w9-pub1 |
| `tests/unit/test_webapp_w9pub1.py` | w9-pub1 + **resolution** (new history-null test; v3 `run_id`) |
| `tests/unit/test_webapp_w9r.py` | w9-pub1 |

w9-d code already on base (`export` gate, vintage); not re-listed.

**Ready for operator merge to `main` when accepted.** Not merged to main in this task.
