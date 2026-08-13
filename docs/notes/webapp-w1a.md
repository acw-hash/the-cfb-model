# W1A — Conviction tier boundary amendment

**Date:** 2026-08-13  
**Status:** Complete  
**Authority:** `docs/webapp/DESIGN.md` §2 (amended); `docs/notes/webapp-w1.md` tier distribution finding

---

## Operator decision (verbatim)

```
DECISION: Option C boundaries, with the tier ladder relabeled.

strong_lean — p_favored ≥ 0.85 — "Strong lean {Team}"
clear_lean — 0.70 ≤ p_favored < 0.85 — "Clear lean {Team}"
lean — 0.575 ≤ p_favored < 0.70 — "Lean {Team}"
toss_up — p_favored < 0.575 — "Toss-up"

Hysteresis stays ±0.03 at every boundary, unchanged.
```

---

## W1A-1 — MEASURE

### Source and method

- **Artifact:** `data/backtests/task23_fundamental_reduced_v2/full/predictions.parquet`
- **Seasons:** 2019–2024 (all rows present in the production walkforward run)
- **`p_favored` derivation:** `compute_p_favored(pred_margin, p_ml_home)` from `export.py` (same as §2.1)
- **ADR 0014 exclusion:** `tier_suppressed(row)` — σ-gated refusal, missing μ/p, stale >6h
- **FBS scope:** all 4,944 walkforward `game_id`s match staged schedule partitions

### Inclusion / exclusion

| Metric | Count |
|--------|------:|
| Included (credible) | 4,944 |
| Excluded (ADR 0014 / suppression) | **0** |

### N per season

| Season | n |
|--------|--:|
| 2019 | 763 |
| 2020 | 568 |
| 2021 | 887 |
| 2022 | 896 |
| 2023 | 910 |
| 2024 | 920 |

### Deciles of `p_favored`

| Decile | Pooled | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 |
|--------|-------:|-----:|-----:|-----:|-----:|-----:|-----:|
| D01 | 0.539 | 0.530 | 0.514 | 0.545 | 0.553 | 0.549 | 0.540 |
| D02 | 0.579 | 0.572 | 0.558 | 0.596 | 0.612 | 0.592 | 0.569 |
| D03 | 0.618 | 0.611 | 0.591 | 0.645 | 0.654 | 0.635 | 0.592 |
| D04 | 0.664 | 0.645 | 0.611 | 0.688 | 0.694 | 0.681 | 0.628 |
| D05 | 0.701 | 0.690 | 0.645 | 0.734 | 0.735 | 0.715 | 0.678 |
| D06 | 0.742 | 0.742 | 0.688 | 0.784 | 0.780 | 0.762 | 0.704 |
| D07 | 0.790 | 0.787 | 0.745 | 0.819 | 0.819 | 0.815 | 0.729 |
| D08 | 0.837 | 0.838 | 0.804 | 0.857 | 0.856 | 0.866 | 0.760 |
| D09 | 0.897 | 0.895 | 0.889 | 0.905 | 0.922 | 0.925 | 0.819 |

### Per-boundary flap exposure (pooled, n=4,944, ±0.03 band)

| Boundary | Flap share |
|----------|----------:|
| 0.575 (lean / toss-up) | 15.2% |
| 0.70 (clear / lean) | 15.9% |
| 0.85 (strong / clear) | 11.1% |
| **Any boundary (union)** | **42.1%** |

~~**Acceptance rationale:** 42.1% pooled flap exposure is the union of three bands; Tue→Sat
**realized tier changes on the fixture week are 0/56** when Tuesday-primary and Saturday
daily-refresh publishes use the same walkforward snapshot (`p_favored` unchanged). Hysteresis
therefore absorbs boundary proximity within a week; flap exposure measures proximity, not
realized flicker.~~

**Flap exposure status (W1A-FIX):** The 42.1% pooled figure remains **UNRESOLVED** as a
proxy for realized intra-week tier flicker. Realized Tue→Sat tier-change counts are **NOT
MEASURED** — both Tuesday-primary and Saturday daily-refresh fixture publishes consumed the
same walkforward snapshot, so `p_favored` was identical by construction and the test could
not have produced a nonzero count. The historical walkforward emits one row per game at the
Tuesday decision point; intra-week tier movement is not measurable from any existing artifact.

**Provisional acceptance:** The amended ladder is accepted on the hypothesis that ratings
move little between Tuesday and Saturday for teams that have not played — not on measurement.

**W7 deferral:** Instrument realized tier-change counts per publish (`game_id`, prior tier,
new tier, `hysteresis_applied`) as a **W7 deploy deliverable**, reported after the first four
live publish weeks of 2026.

### Amended tier shares (chosen ladder)

| Tier | Pooled % | Worst season |
|------|---------:|--------------|
| strong_lean (≥0.85) | 17.3 | 21.4% (2022) |
| clear_lean (0.70–0.85) | 32.9 | 37.2% (2022) |
| lean (0.575–0.70) | 30.8 | 26.8% (2022) |
| toss_up (<0.575) | 18.9 | 14.6% (2022) |
| clear+strong combined | 50.3 | **58.6%** (2022) |

Superseded §2 Strong-only at ≥0.65: **63.0%** pooled, **71.2%** worst season (2022).

---

## W1A-2 — AMEND

### Changes

| File | Change |
|------|--------|
| `docs/webapp/DESIGN.md` §2 | Four-tier boundaries + hysteresis; superseded W1 values preserved |
| `src/ncaa_quant/webapp/export.py` | Threshold constants, `clear_lean` tier, labels, `schema_version` → **1.1.0** |
| `src/ncaa_quant/webapp/schemas/week_predictions.schema.json` | `clear_lean` enum value |
| `tests/unit/test_webapp_w1.py` | Hysteresis + `test_w1a_old_boundaries_no_longer_apply` |
| `webapp/fixtures/` | Regenerated at schema 1.1.0 |

### Schema version

Minor bump **1.0.0 → 1.1.0** (additive `clear_lean`; `strong_lean` threshold moved). No
pre-amendment Ridge artifacts were published (`webapp.export_enabled` OFF through W1).

### Old-boundary regression test

`test_w1a_old_boundaries_no_longer_apply` asserts `p_favored=0.70` → `clear_lean`, not
`strong_lean`; `p_favored=0.65` → `lean`.

### Fixture week 2024 w5 tier distribution (regenerated)

| Tier | Count | % |
|------|------:|--:|
| Strong lean | 1 | 1.8 |
| Clear lean | 22 | 39.3 |
| Lean | 23 | 41.1 |
| Toss-up | 10 | 17.9 |
| Suppressed | 0 | 0.0 |

**Degeneracy:** PASS — strong_lean 1.8% (was 67.9% under superseded §2).

### Tue→Sat fixture publish simulation

~~Tuesday-primary then Saturday `daily_refresh` on the same week-5 walkforward rows:
**0/56** games with `tier_revised_since_primary=true`.~~

**NOT MEASURED.** Both publishes consumed the same walkforward snapshot, so `p_favored` was
identical by construction and the simulation could not have produced a nonzero count. See
W7 deferral above.

---

## W1A-FIX — Record correction (2026-08-13)

Struck the Tue→Sat **0/56** claim and the flap acceptance rationale that rested on it.
Replaced with NOT MEASURED + reason; named W7 successor for realized tier-change
instrumentation; added multi-boundary hysteresis test (`test_hysteresis_multi_boundary_exit_then_reassign`).

---

## Acceptance

```
$ make lint
uv run ruff check src tests
All checks passed!
uv run ruff format --check src tests
199 files already formatted

$ make typecheck
uv run mypy
Success: no issues found in 120 source files

$ make test
uv run pytest -m "not live"
827 passed, 1 deselected, 29 warnings in 250.51s (0:04:10)
Required test coverage of 80% reached. Total coverage: 80.51%
```

---

*End of W1A task notes.*
