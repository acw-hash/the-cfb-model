# S14 — Fix `snapshot_event_time_unique` key

**Date:** 2026-09-08  
**Export gate:** left `False`. No push / R2.

## Change

`src/ncaa_quant/quality/validators.py` — `check_snapshot_monotonicity`:

- uniqueness key was `(game_key, book, market, event_time)`
- now `(game_key, book, market, side, event_time)`

Two-sided book quotes (home/away, over/under) share `event_time` by design;
omitting `side` false-failed every odds partition.

## Quality re-run

`uv run ncaa-quant quality run --seasons 2026` → run_id
`20260908T161718Z_68212148`.

| Check | Result |
|---|---|
| `odds_snapshots` s2026 w1 `snapshot_event_time_unique` | **n_failures = 0** (was 3650 at S12; partition now 5002 rows after S13 ingest — old key would still fail 5002) |
| All other 2026 `odds_snapshots` weeks (0,2–12,14) | **PASSED** — no `snapshot_event_time_unique` regressions |

Unrelated hard fails remain (not introduced here): `plays` w1
`play_sequence_monotone_within_drive`, `drives` w1 `pbp_drive_points_reconcile`.

## Tests

`make test`: **1052 passed, 1 deselected** (matches baseline). Coverage 80.60%.
