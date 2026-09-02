# V4-B1 — forward kickoff-aligned `slot_close` capture

**Date:** 2026-09-02  
**Branch:** `social-s1-s2`  
**Prereg §5 void:** **NO** — does not edit `configs/betting.yaml`, production gate
ordering, or accept-loop stake/bypass semantics. `candidates_enabled` unchanged
(`false`).

---

## 1. Slot definition

### Derivation

A **kickoff slot** is the set of staged `games` rows sharing the same UTC
`start_date` within one CFBD `(season, week)` partition.

Implementation: `derive_kickoff_slots()` in
`src/ncaa_quant/ingestion/slot_close_capture.py` groups by
`to_utc(start_date)` and returns `KickoffSlot(season, week, kickoff, game_ids)`.

### Capture offset (DESIGN close instant)

DESIGN §2.7 closing line:

> **Closing line (definition):** the last captured snapshot strictly before kickoff
> from the designated reference book set …

TASKS.md §9.8 / `market_lines.py`:

- `SLOT_CLOSE_LEAD = timedelta(minutes=5)`
- `slot_close_instant(kickoff) = kickoff − 5 minutes` (UTC)

The forward job fires at `KickoffSlot.capture_at` (= `slot_close_instant(kickoff)`),
not on wall-clock cron.

### Tolerance window

`slot_capture_tolerance(kickoff)` delegates to `asof_tolerance_for(kickoff)`:
**5 minutes** post-Sept-2022 (DESIGN §3.4 snapshot granularity). The due window
for operator polling is `[capture_at, kickoff)` — a pull at or after kickoff is
refused and recorded **missed** (not backfilled).

### 2026 slot counts (staged `data/staged/games/season=2026`)

Derived 2026-09-02 via `credit_accounting_report(store, [2026], max_week=15)`:

| Week | Slots | Credits (×3) |
|------|------:|-------------:|
| 1 | 37 | 111 |
| 2 | 26 | 78 |
| 3 | 25 | 75 |
| 4 | 20 | 60 |
| 5 | 14 | 42 |
| 6 | 15 | 45 |
| 7 | 17 | 51 |
| 8 | 18 | 54 |
| 9 | 15 | 45 |
| 10 | 19 | 57 |
| 11 | 19 | 57 |
| 12 | 19 | 57 |
| 13 | 18 | 54 |
| 14 | — | — |
| 15 | 1 | 3 |
| **Total** | **263** | **789** |

Confirms V4 projection: **263 slots × 3 credits = 789**.

---

## 2. Capture job

### Modules

| Piece | Path |
|-------|------|
| Core capture | `src/ncaa_quant/ingestion/slot_close_capture.py` |
| Prefect flow | `src/ncaa_quant/pipelines/slot_close.py` |
| CLI | `ncaa-quant ingest odds-slot-close` in `src/ncaa_quant/cli.py` |

### Behavior

1. **Per-slot, not wall-clock:** `run_slot_close_capture(slot, captured_at=…)` fires
   one live Odds API pull per `KickoffSlot` when `capture_at ≤ as_of < kickoff`.
2. **One request per slot:** `run_odds_raw_capture()` → live endpoint; **3 credits**
   (3 markets × 1 `us` region). Payload filtered to events whose `commence_time`
   equals the slot kickoff (`filter_payload_to_slot`).
3. **Staging tags:** `decision_point=slot_close`, `snapshot_source=live`,
   `event_time=captured_at` (kickoff−5min instant), CFBD `season`/`week` from
   crosswalk.
4. **Per-book rows retained:** `normalize_odds_payload()` emits one row per
   `(book, market, side)` with `book=book_key` and unique `snapshot_id` hashing
   `game_key|book|market|side|line|price|captured_at`
   (`src/ncaa_quant/ingestion/odds_api.py`). Forward capture uses
   `_enrich_frame_via_crosswalk` + `write_odds_snapshots` unchanged — no
   cross-book aggregation at ingest.
5. **`source_row_id`:** `closing_quote_from_slot_close()` sets
   `ClosingQuote.source_row_id` from the staged row's `snapshot_id`.
   `settle()` raises without `bet_line_source_row_id` and close `source_row_id`.

---

## 3. Idempotency and failure

### Idempotency

- On-disk marker: `{raw_root}/_slot_close/{slot_id}.done` after successful capture.
- Re-run with marker or existing staged live `slot_close` rows → **no API call**, no
  double-stage (`test_slot_close_capture_idempotent_no_double_charge`).

### Missed slots

- Marker: `{slot_id}.missed` via `mark_slot_missed()` / `record_missed_slots()`.
- Post-kickoff capture attempt → missed, not staged from a later line
  (`test_missed_slot_not_backfilled_from_later_pull`).
- `record_missed_slots()` marks any past-kickoff slot never captured.

### API error mid-slot

- Exception propagates from `run_slot_close_capture`; **no `.done` marker** written
  → safe retry without double-charge once succeeded.
- `run_due_slot_close_captures()` **stops the batch** on API error (remaining slots
  stay `pending` for next poll).

---

## 4. Scheduler

**The current Prefect scheduler cannot fire kickoff-aligned triggers.**

`serve_all()` registers only wall-clock crons (`ingest_odds` at
`0 0,4,8,12,16,20 * * *`). Prefect `.serve(cron=…)` has no per-game schedule.

`capture_slot_close_flow` is **not** added to `serve_all()`.
`serve_capture_slot_close()` blocks with **no cron** (operator / external poller).

### Interim invocation

```bash
# Credit accounting only (no API)
ncaa-quant ingest odds-slot-close --estimate

# Capture all slots due now
ncaa-quant ingest odds-slot-close --once

# Or Prefect flow (no cron)
python -m ncaa_quant.pipelines.slot_close  # serve_capture_slot_close()
```

Operator runs `--once` on a short poll loop (e.g. every 1–2 minutes) during game
windows so each slot fires in `[kickoff−5min, kickoff)`.

---

## 5. Tests (fixtures only)

`tests/unit/test_slot_close_capture.py`:

| Test | Asserts |
|------|---------|
| `test_derive_kickoff_slots_groups_shared_kickoff` | Slot grouping by `start_date` |
| `test_filter_payload_to_slot_matches_kickoff_only` | Kickoff filter on API payload |
| `test_slot_close_capture_stages_per_book_and_source_row_id` | `fanduel` + `draftkings` rows; `snapshot_id` present |
| `test_slot_close_capture_idempotent_no_double_charge` | Second run: 0 API calls |
| `test_missed_slot_not_backfilled_from_later_pull` | `.missed` marker; no post-kickoff stage |
| `test_partial_book_coverage_still_stages` | FanDuel-only payload stages |
| `test_kickoff_moved_after_derivation_old_slot_missed_new_captured` | Reschedule handling |
| `test_wall_clock_ingest_does_not_tag_slot_close` | `run_odds_ingest` leaves `decision_point` null |
| `test_2026_credit_accounting_matches_v4_projection` | 263 / 789 vs staged 2026 games |
| `test_record_missed_slots_marks_past_kickoff` | `record_missed_slots` |
| `test_plan_forward_slot_captures_respects_due_window` | Due-window planning |

No live Odds API calls in this task.

---

## 6. Settlement integration test

`test_settlement_integration_auburn_fanduel_ticket` — V4 dry-run ticket shape:

- `game_id=401856636`, `book=fanduel`, `bet_line=-7.5`, `bet_side_american=-102`,
  `edge=0.1472`
- Fixture close staged by `run_slot_close_capture` at kickoff−5min
- `closing_quote_from_slot_close` → `settle(rec, same_book_close=close)`

Assertions:

- `clv_method == "same_line"` (probability-valued path)
- `clv_settlement == "same_book"`
- `is_headline is True`
- `clv == p_close_fair - p_bet_fair`

End-to-end proof that forward `slot_close` rows are settleable for same-book CLV.

---

## 7. Credit accounting

From staged 2026 games (2026-09-02):

| Metric | Value |
|--------|------:|
| Total slots (weeks 1–15) | 263 |
| Credits per slot | 3 |
| **Season forward spend** | **789** |
| Odds API balance (V4) | 99,997 |
| **Balance after season** | **99,208** |

Week 14 has no staged 2026 games in the current partition set (0 slots).

---

## Files added/changed (V4-B1 scope)

- `src/ncaa_quant/ingestion/slot_close_capture.py` (new)
- `src/ncaa_quant/pipelines/slot_close.py` (new)
- `src/ncaa_quant/cli.py` (`ingest odds-slot-close`)
- `tests/unit/test_slot_close_capture.py` (new)

**Not touched:** `configs/betting.yaml`, gate ordering, accept-loop, 2025 lockbox,
`candidates_enabled`.

**CI:** `make lint typecheck test` — 1039 passed (2026-09-02).
