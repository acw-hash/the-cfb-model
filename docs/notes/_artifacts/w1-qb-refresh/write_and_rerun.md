# W1-QB-WRITE — operator write attempt and gate re-run

**Task:** W1-QB-WRITE  
**Branch:** `social-s1-s2`  
**`as_of`:** `2026-09-01T22:30:00+00:00`  
**Odds API / publish / R2 / merge:** OFF  
**2025 lockbox / threshold writes / Best Bets:** untouched  
**ORPHAN rows:** not modified  

---

## Part 1 — Write path (reported before any write)

### Canonical mechanism

Documented in `docs/notes/12.md` (Task 12):

> `ncaa-quant roster set-qb --game … --team … --status {starter,backup,unknown}`
> writing versioned `qb_status` rows with `event_time`.

**CLI entry point:** `ncaa-quant roster set-qb`  
(`src/ncaa_quant/cli.py`, `@roster_app.command("set-qb")`)

**Module function:** `ncaa_quant.features.builders.roster.set_qb_status(store, game_id=…, team_id=…, status=…, event_time=…, ingested_at=…, source_version=…)`

**Storage:** direct parquet append via `ParquetStore.write_partition("qb_status", row, {"season": season}, mode="append")` inside `set_qb_status` — not a separate writer script.

### Required fields and types

From `QbStatusSchema` (`src/ncaa_quant/data/schemas.py`) plus `_TimedModel`:

| Field | Type | Constraint |
|-------|------|------------|
| `game_id` | `Int64` | `≥ 0` |
| `team_id` | `Int64` | `≥ 0`; must be home or away participant |
| `season` | `Int32` | `1900–2100`; derived from staged `games` |
| `status` | `str` | `isin=["starter", "backup", "unknown"]` (case-insensitive on input; stored lowercased) |
| `source_version` | `str` | nullable; defaults to `"manual_v1"` in `set_qb_status` |
| `event_time` | UTC `DateTime` | instant status became known; defaults to `datetime.now(UTC)` in CLI path |
| `ingested_at` | UTC `DateTime` | defaults to `datetime.now(UTC)`; must satisfy `event_time ≤ ingested_at` |

As-of reads take the latest row per `(game_id, team_id)` with `event_time ≤ as_of`. Multiple rows per key are retained (versioned append).

### Intended commands (not executed — all statuses `<TODO>`)

Had the operator supplied concrete statuses, the intended invocations would have been:

```bash
uv run ncaa-quant roster set-qb --game 401869129 --team 2348 --status <STATUS>
uv run ncaa-quant roster set-qb --game 401869129 --team 2466 --status <STATUS>
uv run ncaa-quant roster set-qb --game 401860879 --team 21 --status <STATUS>
uv run ncaa-quant roster set-qb --game 401860879 --team 2502 --status <STATUS>
```

**Future-stamp check:** CLI `set-qb` does not expose `--event-time`; it stamps `event_time = now(UTC)`. At wall time `2026-09-02T13:41Z`, any CLI write would produce `event_time > as_of=2026-09-01T22:30:00Z` and would be rejected under this task's constraint. A backdated write would require calling `set_qb_status(..., event_time=…)` programmatically with an explicit timestamp `≤ as_of`.

---

## Part 2 — Operator-supplied rows

| game_id | team | team_id | operator status | action |
|--------:|------|--------:|-----------------|--------|
| 401869129 | Louisiana Tech | 2348 | `<TODO>` | **skipped — unwritten** |
| 401869129 | Northwestern State | 2466 | `<TODO>` | **skipped — unwritten** |
| 401860879 | San Diego State | 21 | `<TODO>` | **skipped — unwritten** |
| 401860879 | Portland State | 2502 | `<TODO>` | **skipped — unwritten** |

No `qb_status` rows were appended. Staged parquet unchanged.

---

## Part 3 — Gate re-run (steps 1–5 at `as_of=2026-09-01T22:30:00Z`)

**Command:**

```bash
uv run python -c "from scripts._w1_qb_refresh import build_report; build_report()"
```

(Equivalent to `scripts/_w1_qb_refresh.py` analysis path; read-only, no parquet writes.)

### Step counts

```json
{"step1_snapshot_stale_kickoff_quarantine": 90, "step2_edge_ev_sigma": 84, "step3_model_market_disagree": 28, "step4_exposure_caps": 8, "step5_qb_status_unknown": 2, "start": 91}
```

### Step-5 survivor set

**Unchanged** from prior W1-QB report: `(Baylor @ Auburn, Tulane @ Duke)`.

| step-3 rank | game_id | matchup | edge | passes step 5 |
|------------:|--------:|---------|-----:|:-------------:|
| 2 | 401856636 | Baylor @ Auburn | 0.1472 | yes |
| 4 | 401858209 | Tulane @ Duke | 0.1048 | yes |

#### Per-game QB detail

**Baylor @ Auburn** (`401856636`, edge 0.1472, step-3 rank 2)

| team | team_id | status | source | event_time |
|------|--------:|--------|--------|------------|
| Auburn | 2 | starter | manual_v1 | 2026-09-01T22:15:27.497134+00:00 |
| Baylor | 239 | starter | manual_v1 | 2026-09-01T22:15:22.454398+00:00 |

**Tulane @ Duke** (`401858209`, edge 0.1048, step-3 rank 4)

| team | team_id | status | source | event_time |
|------|--------:|--------|--------|------------|
| Duke | 150 | starter | manual_v1 | 2026-09-01T22:15:36.968358+00:00 |
| Tulane | 2655 | starter | manual_v1 | 2026-09-01T22:15:32.064135+00:00 |

### Step-4 survivors still blocked on step 5 (four unlooked-up teams unchanged)

| step-3 rank | game_id | matchup | edge | QB gap |
|------------:|--------:|---------|-----:|--------|
| 1 | 401869129 | Northwestern State @ Louisiana Tech | 0.1483 | both teams: no row |
| 3 | 401860879 | Portland State @ San Diego State | 0.1154 | both teams: no row |

---

## STOP AND REPORT

| Check | Result |
|-------|--------|
| Write path identified | yes — `ncaa-quant roster set-qb` → `set_qb_status` → parquet append |
| Future-stamped rows | none in store (`event_time ≤ as_of` for all existing rows) |
| Rows written this task | 0 (all operator statuses `<TODO>`) |
| Step-5 set vs prior | **unchanged** — Baylor @ Auburn (#2), Tulane @ Duke (#4) |
