# V4-B1a — automate slot-close triggering under Prefect wall-clock model

**Date:** 2026-09-02  
**Branch:** `social-s1-s2`  
**Prereg §5 void:** **NO** — does not edit `configs/betting.yaml`, production gate
ordering, or accept-loop semantics. `candidates_enabled` unchanged (`false`).
`slot_close_capture.py` unchanged (scheduling layer only).

---

## 1. Options evaluated

### (a) Fine-grained cron poll (implemented)

`capture_slot_close_flow` runs on `*/2 * * * *` (UTC). Each tick calls
`execute_slot_close_poll()` → `run_due_slot_close_captures()`. When no slot is
in `[capture_at, kickoff)`, the tick is a **no-op** (zero API credits).

| Failure mode | Behavior |
|--------------|----------|
| Worker restart | Stateless ticks resume; any slot still inside its due window is captured on the next poll (≤2 min later). |
| Deploy mid-window | In-flight poll may be interrupted; uncaptured slots remain due until kickoff if the worker returns within the 5-minute window. |
| Clock drift | Poll uses host UTC; NTP drift within seconds is negligible vs 5-minute capture window. |
| Missed-window recovery | `record_missed_slots()` marks `.missed` at kickoff; no backfill. Recovery requires capture **before** kickoff on a subsequent poll. |
| No-op cost | One staged-games scan + `plan_forward_slot_captures`; no HTTP, no credits. |

### (b) Self-scheduling sleep flow (not implemented)

A long-running flow would `sleep` until each slot's `capture_at`.

| Failure mode | Behavior |
|--------------|----------|
| Worker restart | **Loses sleep state** — must re-derive and reschedule; easy to miss a slot if restart spans `capture_at`. |
| Deploy mid-window | Kills the sleeping run; same gap risk as restart. |
| Clock drift | `sleep` duration vs wall clock can drift over a 12-hour slate. |
| Missed-window recovery | Poor — no automatic catch-up unless a separate watchdog exists. |
| No-op cost | N/A — flow is either sleeping or capturing; idle Saturdays still hold a worker. |

### Recommendation: **(a) fine-grained cron**

Survives Prefect worker restarts, needs no long-running deployment, and reuses
the existing due-slot derivation. The 5-minute `[capture_at, kickoff)` window
absorbs a single missed 2-minute poll; acceptance simulation confirms zero
missed slots with one skipped poll and one transient API error.

---

## 2. Implementation

| Piece | Path |
|-------|------|
| Poll scheduler | `src/ncaa_quant/pipelines/slot_close_schedule.py` |
| Prefect flow | `src/ncaa_quant/pipelines/slot_close.py` |
| Cron registration | `src/ncaa_quant/pipelines/serve.py` |
| Config | `configs/pipeline.yaml` → `slot_close_poll_cron: "*/2 * * * *"` |

**Integration point (capture unchanged):**

```python
# slot_close_schedule.py
batch = run_due_slot_close_captures(
    seasons=seasons,
    config=cfg,
    as_of=now,
    ...
)
```

Called from `execute_slot_close_poll()` and from `capture_slot_close_flow` via
the same helper. CLI `ingest odds-slot-close --once` also uses
`execute_slot_close_poll`.

---

## 3. Due-window semantics

From `market_lines.py`:

- `SLOT_CLOSE_LEAD = timedelta(minutes=5)`
- `capture_at = slot_close_instant(kickoff) = kickoff − 5 minutes` (UTC)

A slot is **due** when (`plan_forward_slot_captures` / `slot_status`):

1. `as_of >= capture_at`, and
2. `as_of < kickoff`, and
3. not already captured (`.done` marker or staged live `slot_close` rows), and
4. not already `.missed`.

| Invocation time | Result |
|-----------------|--------|
| `as_of < capture_at` | No-op (not due) |
| `capture_at <= as_of < kickoff` | Capture once (3 credits) |
| `as_of >= kickoff`, never captured | `.missed` marker, no API pull |

Late invocation **inside** the window still captures (test:
`test_late_poll_inside_window_still_captures`).

---

## 4. Idempotency under concurrency

**Poll-level guard** (`slot_close_schedule.py`):

- `{raw_root}/_slot_close/.poll.lock` — exclusive create (`O_EXCL`); concurrent
  ticks return `outcome=skipped_concurrent` with zero credits.

**Capture-level guards** (`slot_close_capture.py`, unchanged):

- `{raw_root}/_slot_close/{slot_id}.done` after successful capture
- `_staged_has_live_slot_close()` before API call
- Re-run returns `skipped_reason=capture_marker` or `staged_rows_present`, **0 credits**

**Flow-level guard** (`pipelines/common.py`):

- `run_idempotent(PartitionKey(source="capture_slot_close", partition=YYYYMMDDHHMM))`
  — duplicate ticks in the same UTC minute return cached result.

**Run exceeds cron interval:** A slow poll (>2 min) may overlap the next tick.
The poll lock causes the overlapping tick to skip (no double-pull). The in-flight
poll continues; capture markers prevent a second API charge once the first completes.

---

## 5. Observability

Per-poll JSONL ledger (append-only):

```
{raw_root}/_slot_close/outcomes/YYYY-MM-DD.jsonl
```

Each line records `run_id`, `as_of`, `outcome` (`no_op` | `captured` |
`skipped_concurrent` | `error`), `due_count`, per-slot `status`
(`captured` / `missed` / `error` / `skipped`), and `credits_spent`.

Structured logs: `slot_close_poll_complete`, `slot_close_poll_skipped_concurrent`,
`slot_close_poll_failed`.

**Post-slate check:** `grep '"status": "missed"' data/raw/odds_api/_slot_close/outcomes/2026-09-*.jsonl`
or inspect `{slot_id}.missed` under `_slot_close/`.

---

## 6. Missed-rate acceptance simulation

Harness: `simulate_saturday_polling()` replays 2-minute polls across a full
Saturday slate (fixtures only).

**Profile:** 2026-09-19 (busiest staged Saturday, **19 slots**, week 3) — kickoff
times remapped to `2024-09-07` anchor so `event_time <= ingested_at` under
point-in-time schema (capture module unchanged).

**Injected faults:**

- 1 skipped poll tick at mid-slot `capture_at` (worker restart)
- 1 API 500 on first attempt for slot 0 (retries on next due poll)

**Result:** `missed_slots=0`, `captured_slots=19`, `credits_spent=57` (19×3).
Acceptance **met**: zero missed absent unrecoverable API failure.

---

## 7. Credits

| Metric | Value |
|--------|------:|
| No-op poll cost | **0 credits** (no HTTP) |
| Season forward capture spend | **789 credits** (unchanged — 263 slots × 3) |
| Poll invocations per UTC day | **720** (`*/2` cron) |
| Poll invocations on busiest Saturday (19 slots) | **~717** (min `capture_at` − 2min through max `kickoff` + 2min) |

Cron polls 24/7; only due slots spend credits. Saturday wall-clock watch is
eliminated — operator checks the outcome ledger after the slate.

---

## 8. Operator runbook

### Deploy

```bash
# All §10 flows including capture_slot_close on */2 cron
python -m ncaa_quant.pipelines.serve

# Or slot-close only
python -c "from ncaa_quant.pipelines.slot_close import serve_capture_slot_close; serve_capture_slot_close()"
```

Confirm Prefect UI shows `capture_slot_close` deployment with cron `*/2 * * * *`.

### Before a slate

1. `ncaa-quant ingest odds-slot-close --estimate` — expect 263 slots / 789 credits.
2. Verify staged `games` for the week are current (kickoff times).
3. Confirm a recent ledger line exists:
   `tail -1 data/raw/odds_api/_slot_close/outcomes/$(date -u +%F).jsonl`

### After a slate

1. Read outcome ledger for the UTC date; zero lines with `"status": "missed"`.
2. Optional: `ls data/raw/odds_api/_slot_close/*.missed` — should be empty for the week.
3. Credits: sum `credits_spent` in ledger for the day ≈ `3 × slots_that_kicked_off`.

### Manual fallback (mid-Saturday)

```bash
ncaa-quant ingest odds-slot-close --once
```

Safe to repeat — capture markers prevent double-charge. Run every 1–2 minutes
until ledger shows all kickoffs captured if the Prefect worker is down.

---

## CI

`make lint typecheck test` — all pass (2026-09-02).

**Files added/changed:** `slot_close_schedule.py` (new), `slot_close.py`,
`serve.py`, `cli.py`, `config.py`, `pipeline.yaml`, `test_slot_close_schedule.py`.
