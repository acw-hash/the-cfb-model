# V4 — will the paper trade actually capture settleable CLV?

**Date:** 2026-09-02  
**Branch:** `social-s1-s2`  
**Mode:** read-only validation — no Odds API, no config changes, no store built  
**Prereg anchor:** commit `0026fb4`, Amendment 1 rule **(a) BASELINE-MATCHED**  
**V3-A1-FIELD cross-check:** confirmed (`docs/notes/_artifacts/v3a1-field/field.md`)

---

## Executive summary

**No.** A Week 1 ticket recommended today cannot become a settled, headline-eligible CLV row in December under the current wiring. Settlement math works (`settle()` dry-run passes), but three infrastructure pieces are missing:

1. **Forward recommendation store** — nothing persists `RecommendationRecord` at accept time.
2. **Same-book close capture** — no forward job pulls per-kickoff `slot_close` snapshots tagged by book; live `ingest_odds` is untagged and not kickoff-aligned.
3. **Settlement loader** — `settle_clv_flow` is a stub (empty recommendations, empty closes).

Week 1 is **knowingly incomplete** unless (2) is fixed before kickoff and (1)+(3) land before season-end settlement.

---

## 1. Recommendation persistence

### Confirmed (V3-A1-FIELD refuted on “maybe exists”)

| Claim | Status |
|-------|--------|
| `settle()` / `settle_week()` take in-memory `Sequence[RecommendationRecord]` | **CONFIRMED** |
| Settlement persists nothing | **CONFIRMED** — returns `SettledBet` + `WeeklyClvReport` only |
| No forward recommendation store | **CONFIRMED** — `candidates_enabled: false`; W1 survivors exist in probe artifacts only |

Evidence:

- `src/ncaa_quant/pipelines/settle.py::settle_clv_task` calls `run_settle_clv(..., recommendations=(), closes={})` — hard-coded empty inputs.
- `src/ncaa_quant/pipelines/predict.py::execute_predict_publish` runs `apply_bet_filters`, notifies accepted candidates, optionally exports social sidecar — **never** calls `build_recommendation_record` or writes Parquet.
- Prereg A1.3: “**No forward per-ticket write path exists.**”

### What must be built (scoped, not implemented)

| Component | Specification |
|-----------|---------------|
| **Schema** | Pandera-validated Parquet mirroring `RecommendationRecord` plus instrument fields: `stake_fraction`, `edge` (for tail-fill stratum), `accepted_at` (= publish `recommended_at`). All `RecommendationRecord` fields including `bet_line_source_row_id`, `baseline_convention_*`, `close_definition`. |
| **Partition key** | `recommendations/season={season}/week={week}/part.parquet` (matches existing staged layout). |
| **Write trigger** | Immediately after `apply_bet_filters` returns an accepted `BetCandidate` in `execute_predict_publish` (production accept path). |
| **Accept-path hook** | New function e.g. `persist_accepted_recommendations(accepted, provider_details, *, season, week, as_of, betting_config)` called from `execute_predict_publish` ~line 750, **after** filters, **before** social export. Must call `build_recommendation_record(..., edge=cand.edge)` exclusively. |
| **Provenance bridge** | `provider.py` `details` dict must expose `snapshot_id` (→ `bet_line_source_row_id`), both-side American prices, consensus two-way prices, and shopped `book` — currently missing (see §2). |

**Estimated change surface (not built):**

| File / function | Change |
|-----------------|--------|
| `src/ncaa_quant/data/schemas.py` | New `RecommendationRecordSchema` |
| New `src/ncaa_quant/betting/recommendations.py` (~120 LOC) | `record_from_accepted(...)`, `write_recommendations(...)`, `read_recommendations(...)` |
| `src/ncaa_quant/betting/provider.py` | Extend `details[key]` with `snapshot_id`, `bet_other_american`, consensus prices |
| `src/ncaa_quant/pipelines/predict.py` | ~15 LOC hook after `apply_bet_filters` |
| `src/ncaa_quant/pipelines/settle.py` | Load store + join closes; replace stub |
| New close join module or extend `scripts/_s4_p0_clv_settle.py` pattern | Resolve `ClosingQuote` from staged `odds_snapshots` |
| `tests/unit/test_recommendations.py`, extend `test_pipelines_gates.py` | Round-trip + guard coverage |

**§5 void rule:** Store can be built **without** editing `configs/betting.yaml`, production gate ordering in `apply_bet_filters`, or accept-loop stake/bypass semantics — provided the hook is a pure post-filter write. Provider provenance enrichment is **not** a gate-order change. **Flag only if** someone tries to fold persistence into filter logic or alter thresholds to capture fields.

---

## 2. Required-field completeness

### Fields `settle()` reads (directly or via helpers)

| Field | Source in `settle()` | Required at recommendation? |
|-------|----------------------|------------------------------|
| `recommendation_id` | `settle_week` join key | Yes |
| `game_id`, `season`, `week`, `side` | Identity / filtering | Yes |
| `book` | Same-book match vs `ClosingQuote.book` | Yes |
| `market`, `bet_line`, `total_side` | `translate_close_to_bet_line` | Yes (spread: `bet_line`) |
| `bet_side_american`, `bet_other_american` | CLV probability computation | Yes |
| `bet_line_source_row_id` | `_require_distinct_line_sources` guard | **Required — raises if missing** |
| `consensus_side_american`, `consensus_other_american` | `compute_line_shopping_capture` (NaN if missing) | Optional for CLV; required for shopping metric |
| `close_definition` | `summarize_settlements` only | Yes (stored on record) |
| `recommended_at` | Not read by `settle()` | Yes for audit / prereg |
| `baseline_convention_*` | Pass-through only | Yes (Amendment 1) |
| `n_books_available` | Not read by `settle()` | No for CLV |

Close side (`ClosingQuote`): `side_american`, `other_american`, `book`, `line`, `source_row_id` (required), optional `alt_*` for line translation.

### Availability at recommendation time today

| Field | Available if persisted today? | Gap |
|-------|--------------------------------|-----|
| `bet_line_source_row_id` | **NULL / missing** | Provider `details` has no `snapshot_id`; `BetCandidate` has no provenance |
| `book` | In `provider_details` only | Not on `BetCandidate`; not written anywhere |
| `bet_side_american` | `BetCandidate.american_odds` | Available |
| `bet_other_american` | **NULL** | Not captured in provider `details` |
| `bet_line` | In `provider_details.market_line` | Available in sidecar path only |
| `recommended_at` | Publish `as_of` | Available at accept instant |
| `close_definition` | **NULL unless set** | No default write path; would use `"odds_api_consensus"` |
| `consensus_*` | **NULL** | Provider has `p_market` (prob) not consensus two-way American |
| `baseline_convention_*` | **NULL** | Only via `build_recommendation_record(edge=...)` — not called on accept path |

**Conclusion:** A naive persist of today's accept output would produce records that **`settle()` refuses** (`bet_line_source_row_id` missing) or settle with incomplete shopping metadata.

---

## 3. Close capture

### What exists

| Job | Schedule | `decision_point` | Purpose |
|-----|----------|------------------|---------|
| **`ingest_odds`** (`src/ncaa_quant/pipelines/odds.py`) | Cron `0 0,4,8,12,16,20 * * *` (`configs/pipeline.yaml`) — **6×/day** | **`None`** (live wall-clock) | Bet-time freshness / general board capture |
| **`run_historical_backfill`** | Operator-triggered, resumable | `slot_close` among others | **Backfill only** — 30 credits/call; not forward season |
| **`settle_clv_flow`** | Sunday (DESIGN §9.8) | N/A | **Stub** — does not load closes |

Live ingest explicitly sets `decision_point=None`, `event_time=captured_at` (`run_odds_ingest` ~1880–1882). It does **not** tag kickoff−5min closes or distinguish closing lines from Tuesday bet-time snapshots.

S4 historical CLV rebuild (`scripts/_s4_p0_clv_settle.py`) resolves closes from staged rows with `decision_point == "slot_close"` and matching `book` — **that path works for backfill data, not for 2026 forward unless forward `slot_close` rows exist.**

### Same-book requirement

`SettledBet.is_headline` requires `clv_settlement == "same_book"` AND probability-valued `clv_method`. Auburn (FanDuel) and Duke (DraftKings) need **each book's own** kickoff-close snapshot, not consensus or a different book's line.

**Defect (prereg known-defects candidate):** Same-book close capture for the forward instrument **must be run every week by hand** (operator-triggered live pulls at each distinct kickoff slot, staged with `decision_point=slot_close`) until an automated forward close job exists. The 6×/day cron is **not sufficient** — it is not kickoff-aligned, not `slot_close`-tagged, and may miss the kickoff−5min window entirely.

---

## 4. Guards (verbatim)

**Missing bet or close `source_row_id`** (`src/ncaa_quant/betting/clv.py`, `_require_distinct_line_sources`):

```python
if bet_id is None or str(bet_id).strip() == "":
    raise ClvError(
        f"recommendation {recommendation.recommendation_id!r} is missing "
        "bet_line_source_row_id; refusing to settle CLV without the source-row guard"
    )
if close_id is None or str(close_id).strip() == "":
    raise ClvError(
        f"close for recommendation {recommendation.recommendation_id!r} is missing "
        "source_row_id; refusing to settle CLV without the source-row guard"
    )
```

**Same row degeneracy** (`assert_distinct_line_sources` / `compute_clv`):

```python
if bet_source_row_id == close_source_row_id:
    msg = (
        "CLV degeneracy: bet-time and closing prices resolve to the same "
        f"source row ({bet_source_row_id}); refusing to report CLV≈0 "
        "by construction"
    )
    raise ClvError(msg)
```

---

## 5. Credit budget (Odds API live endpoint)

**Formula:** live cost = `markets × regions` = **3 × 1 = 3 credits/call** (`configs/data.yaml`: h2h + spreads + totals, `us` region).

**Last-read balance:** **99,997** credits (S6-W1-CARD-B pull, `docs/notes/social-s6-w1-card.md` — `100,000` before, `x-requests-last=3`).

**2026 `slot_close` plan** (measured from staged `games`, `plan_historical_units(..., decision_points=['slot_close'])`):

| Quantity | Value |
|----------|------:|
| Distinct kickoff slots weeks 1–15 | **263** |
| Credits per slot (live) | **3** |
| **Total through CFBD week 15** | **789** |
| Projected remaining after full season | **99,208** |

Per-week slot counts (requests): `{1: 37, 2: 26, 3: 25, 4: 20, 5: 14, 6: 15, 7: 17, 8: 18, 9: 15, 10: 19, 11: 19, 12: 19, 13: 18, 15: 1}` — week 14 absent in staged games partition.

Example week 1 only: 37 slots × 3 = **111 credits**.

**Multi-season (Amendment 1 §A1.5):** Instrument continues until **n = 300** headline tickets (~4 seasons at projected ≈80–84/season under rule (a)). Close-capture cost ≈ **789 credits/season × 4 ≈ 3,200 credits** — **affordable** at current rates against a 99,997 balance. Operational burden is scheduling/automation, not credit ceiling.

*(Historical backfill uses 30 credits/call — not applicable to forward live close capture.)*

---

## 6. Dry-run — Auburn ticket (synthetic same-book close)

Built via `build_recommendation_record` (FanDuel, −7.5, −102, week 1, edge 0.1472); settled against synthetic FanDuel close at same line (−7.5) with distinct `source_row_id`s.

**Verbatim outputs:**

```
clv_method: same_line
clv: 0.01303437164630139
is_headline: True
clv_settlement: same_book
baseline_convention_eligible: False
baseline_convention_exclusion_axes: ('week_lt_2',)
```

Method is **probability-valued** (`same_line`), not `line_units`. Under rule **(a)**, Auburn is settled but **excluded from primary n = 300** (`week_lt_2`).

---

## 7. Verdict

**Can a Week 1 ticket recommended today become a settled headline-eligible row in December?**

**No** — not end-to-end. Settlement **math** is ready; the **pipeline** is not.

### Missing pieces (build order)

1. **Forward same-book close capture** — before/alongside each week's kickoffs: live Odds API pull per distinct kickoff slot, staged as `decision_point=slot_close`, preserving per-book spread rows and `snapshot_id`. *Without this, Week 1 CLV is written off regardless of store.*

2. **Provider provenance enrichment** — `snapshot_id`, `bet_other_american`, consensus two-way prices into `provider_details` (no gate-order change).

3. **Recommendation store** — post-accept `build_recommendation_record` + Parquet write (partition `season`/`week`).

4. **Settlement loader** — `settle_clv_flow` reads store + joins `slot_close` `ClosingQuote`s by `(recommendation_id, book)`; wire `summarize_settlements` with tail-fill stratum filter.

5. **Operator enablement** — `candidates_enabled: true` is an operational act (frozen yaml values unchanged per prereg §1).

### STOP conditions triggered

| Condition | Result |
|-----------|--------|
| No same-book close capture exists | **YES — report before kickoff** |
| Dry-run returns `clv_method == "line_units"` | **NO** — returns `same_line` |
| Store requires gate/yaml changes (§5 void) | **NO** — if scoped as post-filter write + provider provenance only |

### December headline eligibility note

Even with full wiring, W1 Auburn/Duke rows are **`baseline_convention_eligible=False`** under rule **(a)**. They settle for audit but do **not** count toward primary n = 300. Headline-eligible CLV for the confirmatory read begins CFBD week ≥ 2.

---

## Epistemic ledger

| Claim | Status |
|-------|--------|
| No forward recommendation store | **MEASURED** (code trace) |
| `settle_clv` stub empty inputs | **MEASURED** |
| Live ingest ≠ `slot_close` close capture | **MEASURED** |
| Provider missing `bet_line_source_row_id` bridge | **MEASURED** |
| 2026 slot_close = 263 × 3 = 789 credits | **MEASURED** (staged games plan) |
| Balance 99,997 | **MEASURED** (S6-W1-CARD-B) |
| Dry-run probability-valued CLV | **MEASURED** |
| Week 1 → December E2E without new infra | **NO** |
