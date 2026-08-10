# Phase 2 — Data quality

Date: 2026-08-07

Gate: per-season quality reports green **or explicitly quarantined**; zero
unexplained inert components; full staged `pit_audit`; off-machine odds backup.

Phase 1 leftovers B-3 / B-5 / C-5 were not touched (do not block quality work).

## What was built

### 1. Fail-loud inert components

- `evaluation/inert.py` — `InertComponentError` plus guards for:
  - registered-but-unmaterialized features (`expected_possessions`)
  - missing prior-family staged partitions (rosters / returning / recruiting / talent)
  - σ checklist claiming required columns that are absent
- `build_production_stack(..., enforce_ablation_preconditions=True)` is now the
  default (was False). A1/A5 silent no-ops raise.
- A5 precondition runs when the A5 ablation turns the filter off **and**
  `play_counts` are supplied — inert GT flags then fail loud instead of a
  zero delta. (Staged CFBD plays currently carry no usable GT flags —
  `n_on == n_off` on 2023 — so asserting on every non-A5 run would brick
  `backtest run`; that source gap is recorded, not papered over.)
- `backtest run` CLI passes `(n_on, n_off)` into `run_backtest` / the stack
  (previously discarded) and calls `assert_prior_family_staged` before loading.
- `scripts/backfill_23_cfbd.py` raises `SchemaValidationFinding` on prior-family
  schema failures instead of logging `SCHEMA_FAIL` and continuing.

Tests: `tests/unit/test_inert_guards.py`.

**Still open (does not block Phase 2 gate):**
- Loading a real fitted `priors_frame` on the CLI path (A1 `league_mean`
  already fails loud when priors are missing; fitted cold-start without
  Task-15 weights is Phase 4 wiring).
- Populating usable `garbage_time` flags on staged plays (wp nearly all-null
  at source) so the filter-on path is observably non-inert.

### 2. Recruiting schema + re-pull (2014/15/16/18)

- `RecruitingSchema.points` no longer requires `ge=0`. CFBD emits `-0.04` for
  those seasons; rejecting them left partitions empty while Task 23 still
  published numbers.
- Re-pulled recruiting: 2014→230, 2015→230, 2016→237, 2018→229 rows.

Test: `tests/unit/test_recruiting_schema_negatives.py`.

### 3. Venue overrides + re-enrichment

Added to `configs/venues_overrides.yaml`:

| venue_id | name | fix |
|---|---|---|
| 4737 | Croke Park | lat/lon + Europe/Dublin |
| 5455 | Ford Center At The Star | lat/lon + America/Chicago |
| 4779 | Thomas A. Robinson (Nassau) | lat/lon + America/Nassau |

Re-applied overrides to all 2014–2025 venues partitions (no CFBD spend):
timezone_filled 839/844 every season. Lat/lon filled for the two previously
blocking FBS hosts.

Weather for seasons that were blocked is unblocked; **2014 historical weather
completed** (`rows_written=868`, `gaps=0`, ~23 min). Remaining blocked/partial
seasons (2015–2018, 2025, and gaps in 2019/21/22/24) can use the same
`run_weather_historical(..., enrich_venues=False)` path. Quality suites do not
include weather.

### 4. `event_time_estimated` backfill on games

AUDIT-6 added `games.event_time_estimated` to the GE column set. 169 week
partitions (all seasons except those already carrying the column) were missing
it and quarantined on `expect_table_columns_to_match_set`. Backfilled
`event_time_estimated=True` (CFBD v1 has no completion timestamp — always
estimated at kickoff+5h/7h).

### 5. Quality reports 2014–2025

Authoritative combined run after the above fixes:

```
quality run_id=20260807T171336Z_a661584a seasons=2014-2025
  partitions checked=763 passed=653 quarantined=100 flagged=10
  report: docs/quality/reports/quality_20260807T171336Z_a661584a.md
```

2020 alone remains fully green (65/65): `quality_20260807T171833Z_5de4b629`.

Per-season (passed / quarantined / hard):

| season | passed | quarantined | hard |
|---|---:|---:|---:|
| 2014 | 45 | 20 | 20 |
| 2015 | 53 | 8 | 9 |
| 2016 | 44 | 17 | 19 |
| 2017 | 53 | 8 | 8 |
| 2018 | 55 | 6 | 6 |
| 2019 | 57 | 4 | 5 |
| 2020 | 65 | 0 | 0 |
| 2021 | 55 | 4 | 4 |
| 2022 | 58 | 3 | 3 |
| 2023 | 61 | 14 | 14 |
| 2024 | 51 | 8 | 8 |
| 2025 | 56 | 8 | 9 |

**Explicit quarantines (checks not loosened — Task 7 standing rule):**

| expectation | n | note |
|---|---:|---|
| `completeness_advanced_box_vs_games` | 33 | CFBD advanced box missing a few game_ids / week |
| `completeness_plays_vs_games` | 30 | CFBD PBP missing a few game_ids / week |
| `play_sequence_monotone_within_drive` | 28 | opaque / duplicated `play_id` proxy |
| `pbp_drive_points_reconcile` | 14 | drive points vs finals > 8 (mostly 2023) |

Soft flags (10): `line_open_close_move` ≥ 20 pts — flag only, never quarantine.

### 6. Full staged `pit_audit`

```
ncaa-quant quality pit-audit --seasons 2014-2025
pit_audit seasons=[2014..2025]
  partitions checked=925 passed=925 failed=0
```

Temporal contract `event_time <= ingested_at` holds on every staged partition
under the amended event_time semantics. Feature-store recomputation
(`features.pit_audit`) still requires materialized feature partitions — the
`features` CLI remains unwired; that is recorded, not papered over (see inert
guard on `expected_possessions`).

### 7. Off-machine odds backup (E-1)

```
ncaa-quant ingest odds-backup --source data/raw/odds_api --restore-drill
odds backup n_files=25 bytes=4436287 dest=D:\ncaa-quant-backups\odds_api
restore drill ok n_files=25
```

C: is Samsung NVMe; D: is a separate 2 TB HDD — genuinely off-machine.
Layout: `current/` mirror + `snapshots/{utc}/` + `backup_manifest.json` with
SHA-256 per file; 24h freshness check; restore drill verifies digests against
manifest and live archive.

Module: `src/ncaa_quant/ops/odds_backup.py`. Override dest via
`ODDS_RAW_BACKUP_ROOT` or `--dest`. Tests: `tests/unit/test_odds_backup.py`.

## Decisions / ambiguities

1. **"Green" vs quarantined.** Gate is "green or explicitly quarantined." Real
   CFBD completeness / drive-reconcile / play-sequence issues stay quarantined
   and documented; checks were not loosened.
2. **`expected_possessions`.** Fail-loud when claimed; not materialized in this
   phase (builder exists; materialize path still unwired). Phase 4 / feature
   wiring owns materialization.
3. **Weather.** Venue blockers cleared; full multi-season Open-Meteo fill is
   hours of wall clock and was started for 2014. Not required for the quality
   suite (weather is outside `TABLE_SUITES`).
4. **Backup target.** Spec says "S3-class"; no cloud credentials on this host.
   Separate physical volume satisfies the immediate SPOF (NVMe death). Runbook:
   `docs/runbooks/odds_archive_backup.md`. Promote `ODDS_RAW_BACKUP_ROOT` to a
   versioned R2/B2/S3 remote when available — same-chassis D: is interim, not
   the final E-1 destination.

## Verification

- Targeted new tests: 10 passed (`test_inert_guards`, `test_odds_backup`,
  `test_recruiting_schema_negatives`)
- `make test`: **684 passed**, 1 deselected, coverage **80.80%** (gate 80%)
- pit_audit 925/925
- odds backup + restore drill on `D:\ncaa-quant-backups\odds_api`
- quality combined report: 653 passed / 100 quarantined (documented above);
  2020 alone 65/65 green
