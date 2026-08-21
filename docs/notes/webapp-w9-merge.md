# W9-MERGE — branch audit; merge w9-d and w9-pub1 to main

**Date:** 2026-08-21  
**Branch:** `main`  
**Authority:** W9-PUB1-VERIFY-2; DESIGN §1.5, §1.7

Precondition: `NCAA_QUANT_WEBAPP__EXPORT_ENABLED=false`;
`load_config().webapp.export_enabled is False`.

## Phase 1 — branch audit

| branch | head | merge-base with main | ahead/behind | src/ files touched | tests added | merged? |
|---|---|---|---|---|---|---|
| main | 545f5ce→(post-merge) | — | — | — | — | — |
| origin/main | c58d6b7 | c58d6b7 | local main was +4/−0 | 0 | 0 | remote behind local |
| prod-500 | 184026a | 184026a | +0/−1 | 0 | 0 | **yes** (via 545f5ce) |
| w10-ui | 724c2ae | 545f5ce | +5/−0 | **0** | 0 (site tests only) | **no** |
| w9-d | 95c85e1 | 92a245f | +3/−2 | 2 (`predict.py`, `export.py`) | 10 | **no** → Phase 3 |
| w9-pub1 | 545f5ce | 545f5ce | +0/−0 | **0 at tip** | 0 at tip | tip == main; **all work uncommitted** → Phase 4 |

### Unmerged branches — correctness questions

1. **w10-ui** — touches **no** `src/ncaa_quant/`. Site/docs/screenshots only. Not pipeline correctness.
2. **w9-d** — yes, `export.py` / `predict.py`. Adds `IncoherentMarginIntervalError`, `assert_no_incoherent_margin_interval`, `apply_margin_interval_coherence_gate`, `UnknownRunProvenanceError`. Notes on `w9-d` say Amendment 2 “Gate in export” / complete while main lacked the symbols — classic stranded-notes mismatch.
3. **w9-pub1** — tip identical to main; stranded work lived only in the working tree (`publish_history.py`, day-resolution idempotency, `as_of` override, export/grade/push touch). Notes `webapp-w9pub1.md` untracked.

### Ranked stranded correctness (beyond UI)

1. **w9-d @ 95c85e1** — Amendment 2 interval-coherence gate (export) + vintage provenance — **merge target**
2. **w9-pub1 uncommitted WIP** — publish history / day token / week-1 `as_of` — **merge target after commit**
3. *(none other)* — `w10-ui` is site-only; `prod-500` already on main

No other stranded `src/ncaa_quant/` correctness work. Proceed.

## Phase 2 — coherence gate semantics (BLOCKING)

**Suppress, do not abort.** Call site in `_build_game` (export):

1. `apply_margin_interval_coherence_gate(...)` → returns `(None, None, None)` when heads fail `q10 < μ < q90`
2. `assert_no_incoherent_margin_interval(...)` — **early-returns** when `lo is None and hi is None`; raises only if a non-null band would still be written while heads are incoherent

`test_coherence_gate_nulls_incoherent_margin_interval` asserts nulls, not an exception. A live `predict_publish` with ~15 incoherent rows continues; no operator override needed for suppress path. The raise path is a safety net against writing an incoherent band, not a slate abort.

**Artifact shape:** keys present with JSON `null` — `margin_interval_lo`, `margin_interval_hi`, `margin_interval_nominal` (not omitted, no separate flag).

**UI:** `GameDetail` passes `MARGIN_INTERVAL_ABSENT_REASON` when lo/hi null → ForecastBlock “Interval not computed”. `IntervalBand` / This Week `GameRow`: `hasBand` false → `renderAbsent()` / `data-testid="interval-absent"`. `formatIntervalParts` returns `loText`/`hiText` null without NaN.

## Phase 3 — merge w9-d → main

`git merge w9-d` on main. Ort auto-merged the three conflicted paths. Resolution record:

| file | winner | why |
|---|---|---|
| `docs/runbooks/pre_publish.md` | **both** | Kept main’s PROD-500 revalidation section; kept w9-d Amendment 1/2 paragraphs. Deduped a duplicated Amendment 1 line introduced by w9-d tip. |
| `GameDetail.tsx` | **both** | Kept main’s empty-series guard around `RatingTrajectoryChart`; kept w9-d `MARGIN_INTERVAL_ABSENT_REASON` wiring for suppressed bands. |
| `results.test.tsx` | **main** for CI contract | Retained main’s `rateHasCi` / honest-absence MetricRow tests; took w9-d verdict plain_language assertions where non-overlapping. |

Post-merge: `assert_no_incoherent_margin_interval` and `test_incoherent_band_assertion_bite` present on main.

## Phase 4 — merge w9-pub1 → main

*(filled after commit + merge)*
