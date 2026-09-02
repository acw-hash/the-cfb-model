# V3-A1-FIELD — baseline eligibility at recommendation time

**Date:** 2026-09-02  
**Branch:** `social-s1-s2`  
**Amendment 1 rule:** **(a) BASELINE-MATCHED** (`<SELECTED: a>`)  
**Prereg §5 void:** **NO** — code change only. Does not edit `configs/betting.yaml`,
production gate ordering, or accept-loop stake/bypass semantics.

---

## 1. `RecommendationRecord` and persistence path

### Record (excerpt — new fields)

`src/ncaa_quant/betting/clv.py`:

```python
@dataclass(frozen=True, slots=True)
class RecommendationRecord:
    ...
    baseline_convention_eligible: bool
    baseline_convention_exclusion_axes: tuple[str, ...]
    ...
```

### Construction (canonical)

`build_recommendation_record()` in `src/ncaa_quant/betting/clv.py` is the
**canonical constructor**. It calls
`compute_baseline_convention_eligibility()` from
`src/ncaa_quant/betting/baseline_convention.py` and stamps both fields before
returning the frozen record.

`src/ncaa_quant/evaluation/backtest_runner.py::build_recommendation_record`
delegates to the canonical builder (adds `edge` parameter).

Historical S4 CLV rebuild (`scripts/_s4_p0_clv_settle.py`) now uses the canonical
builder with `edge=float(b.edge)`.

### Written (persisted)

**No forward recommendation store exists yet.** `candidates_enabled: false`;
no `RecommendationRecord` has been written to disk. Settlement
(`src/ncaa_quant/pipelines/settle.py`) accepts in-memory
`Sequence[RecommendationRecord]` and does not persist recommendations. When a
forward paper-trade store is wired, it must call `build_recommendation_record`
(or equivalent) at accept time.

---

## 2. Eligibility computation

`src/ncaa_quant/betting/baseline_convention.py`:

```python
# Frozen §1 public edge bar (S3/S5 THRESHOLD). Intentionally **not**
# configs/betting.yaml min_edge_sides (0.025).
BASELINE_CONVENTION_MIN_EDGE_SIDES: Final = 0.05
```

Rule: CFBD `week >= 2` AND `edge >= 0.05` AND market is side/spread.

---

## 3. Settlement immutability guard

`RecommendationRecord` is `frozen=True` — fields cannot be mutated after
construction.

`settle()` docstring (excerpt):

> ``baseline_convention_eligible`` and ``baseline_convention_exclusion_axes``
> are recommendation-time snapshots on the frozen :class:`RecommendationRecord`;
> this function passes them through unchanged and never recomputes them.

`SettledBet.recommendation` holds the same object reference (`test_settle_passes_baseline_fields_through_unchanged`).

`RecommendationRecord.__post_init__` rejects inconsistent eligible/axes pairs at
construction time.

---

## 4. Tests

`tests/unit/test_baseline_convention.py`:

| Case | week | edge | market | eligible | axes |
|------|-----:|-----:|--------|:--------:|------|
| Eligible | 5 | 0.10 | spread | true | `()` |
| Week 1 | 1 | 0.1472 | spread | false | `week_lt_2` |
| Sub-0.05 | 5 | 0.04 | spread | false | `edge_lt_0.05` |
| Two+ axes | 1 | 0.04 | total | false | `week_lt_2`, `edge_lt_0.05`, `market_not_side` |

---

## 5. W1 step-5 dry-run (verbatim)

```
Auburn: eligible=False exclusion_axes=['week_lt_2']
Duke: eligible=False exclusion_axes=['week_lt_2']
```

Source: `compute_baseline_convention_eligibility(week=1, market='spread')` with
edges 0.1472 (Auburn) and 0.1048 (Duke).

---

## 6. Bypass paths

**Yes — a ticket can still be constructed without correct baseline stamping** via
the raw `RecommendationRecord(...)` dataclass constructor if a caller supplies
`baseline_convention_*` manually (or inconsistently — `__post_init__` catches
eligible/axes mismatch only).

**Canonical path that always computes correctly:**
`build_recommendation_record(..., edge=...)`.

No disk persistence path exists yet, so no ticket has been written with or without
the fields. Forward wiring must use `build_recommendation_record` exclusively.

---

## Verification

`make test` — see commit.
