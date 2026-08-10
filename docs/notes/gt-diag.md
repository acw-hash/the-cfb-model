# TASK GT-DIAG — Garbage-time zero-flag diagnosis

**Date:** 2026-08-10  
**Scope:** Read-only. Sanctioned edits: this note + `scripts/_gt_diag.py`. No fixes.  
**Artifact:** `docs/notes/_artifacts/gt_diag_report.json` (script stdout).

---

## Verdict: WORLD A — FLAGS NEVER MATERIALIZED

Staged plays never carry usable GT inputs or a persisted `garbage_time` column.
When A5 / `build_observations_from_staged` calls `apply_garbage_time` at runtime,
every play is classified non-garbage (`n_garbage_true == 0`), so `n_on == n_off`
for every season 2014–2025. This is not a threshold-tuning failure (WORLD B) and
not a measurement-stage mismatch (WORLD C).

---

## Evidence

### 1. Staged column inventory (all seasons identical shape)

Every season under `data/staged/plays/` has exactly these columns:

`defense_id`, `distance`, `down`, `drive_id`, `epa`, `event_time`, `game_id`,
`ingested_at`, `offense_id`, `period`, `play_id`, `play_type`, `scoring`,
`season`, `source_version`, `success`, `week`, `wp`, `yards_gained`,
`yards_to_goal`.

| GT-relevant field | On staged? | Status |
|---|---|---|
| `garbage_time` | **No** | Flag never persisted |
| `wp` | Yes | **100% null** all seasons 2014–2025 |
| `wp_before` / `wp_after` | **No** | `apply_garbage_time` defaults to `wp_before` |
| `score_margin` | **No** | Connelly input absent |
| `offense_score` / `defense_score` | **No** | Dropped at ingest (`PlaysSchema` / `normalize_plays_payload`) |
| `clock` | **No** | Dropped at ingest |
| `period` | Yes | Present; alone insufficient |

Matches `docs/notes/08.md` prior-task gap and `docs/notes/23.md` D-4.

### 2. A5 counts (replicates 23-fix-close Item 5)

| season | n_on | n_off | n_garbage after apply | n_on < n_off |
|---:|---:|---:|---:|:---:|
| 2014 | 158315 | 158315 | 0 | no |
| 2015 | 160180 | 160180 | 0 | no |
| 2016 | 158518 | 158518 | 0 | no |
| 2017 | 158574 | 158574 | 0 | no |
| 2018 | 160512 | 160512 | 0 | no |
| 2019 | 159915 | 159915 | 0 | no |
| 2020 | 102809 | 102809 | 0 | no |
| 2021 | 158634 | 158634 | 0 | no |
| 2022 | 160327 | 160327 | 0 | no |
| 2023 | 159011 | 159011 | 0 | no |
| 2024 | 162726 | 162726 | 0 | no |
| 2025 | 166057 | 166057 | 0 | no |

Aliasing staged `wp` → `wp_before` still yields **0** true flags (WP is all-null).
`filter_garbage_time(staged)` returns the full play set every season.

### 3. Why both rules are inert on staged

1. **Primary WP rule (DESIGN §4.2):** `apply_garbage_time(..., wp_col="wp_before")`.
   Staged has `wp`, not `wp_before`, and `wp` is 100% null at source/staging.
   Even with rename, WP never fires.
2. **Connelly fallback:** requires non-null `score_margin` (or scores to derive it).
   Staged has neither. Fallback path is selected (`gt_fallback_used=True` /
   `gt_rule=connelly_fallback`) but `connelly_ready` is False → `garbage_time=False`.

### 4. Blowout Q4 hand-picks (2023, final margin > 28)

Ten largest blowouts; last Q4 play each. All should obviously be garbage under
Connelly Q4 (`|margin| > 16`):

| game_id | final_margin | wp | score_margin col? | filter decision |
|---:|---:|---|---|---|
| 401523992 | 74 | null | no | **False** |
| 401525822 | 73 | null | no | **False** |
| 401531438 | 69 | null | no | **False** |
| 401532398 | 68 | null | no | **False** |
| 401520168 | 66 | null | no | **False** |
| 401525466 | 65 | null | no | **False** |
| 401520322 | 63 | null | no | **False** |
| 401520199 | 62 | null | no | **False** |
| 401520339 | 62 | null | no | **False** |
| 401551773 | 60 | null | no | **False** |

Rule reported: `connelly_fallback` with no score → cannot classify → False.

### 5. Thresholds vs DESIGN (not the failure mode)

| Source | WP low | WP high | Connelly Q1–Q4 |
|---|---:|---:|---|
| DESIGN §4.2 | 0.02 | 0.98 | “Connelly-style” |
| `features/epa.py` constants | 0.02 | 0.98 | 28 / 24 / 21 / 16 |
| `DataConfig.garbage_wp_*` | 0.02 | 0.98 | — |

Config WP thresholds match DESIGN and code constants, but
`apply_garbage_time` **hardcodes** module constants — `DataConfig.garbage_wp_*`
is unused. Irrelevant to zero flags (inputs absent).

### 6. Was the Task 8/9/13 builder ever run on 2014–2025?

- **GT owner is Task 8** (`features/epa.py`), not Task 9 (registry) or Task 13
  (Elo). Prompt’s “9/13” references are off; 23-fix-close Item 5’s “Task 9/13
  finding” is the same mislabel.
- Task 8 acceptance ran `load_season_plays_from_cfbd_raw` on **2023 raw only**
  (~17% GT via Connelly; WP null; see `docs/notes/08.md`).
- Output was **never written to staged**. Intended landing: in-memory columns from
  `normalize_epa_plays` / `apply_garbage_time` at feature/observation build time.
  `PlaysSchema` deliberately omits scores/clock/GT flag
  (`ingestion/cfbd.py::normalize_plays_payload`).
- Task 10 efficiency acceptance used **raw** plays for 2023 (notes/10.md).
- `data/features/` has no live GT-dependent partitions (only `_quarantine/`).

### 7. WORLD B / C ruled out

- **Not B:** No populated WP distribution that fails to cross 0.02/0.98. WP is
  absent/null; Connelly inputs absent. Flag rate after apply = **0%** everywhere
  because inputs never arrive — not because thresholds are too strict.
- **Not C:** A5 measure and filter act on the **same** staged plays frame:

  - Measure: `cli backtest run` → `build_observations_from_staged` →
    `apply_garbage_time(plays)` → `(n_on, n_off)`.
  - Filter: same function → `build_game_observations_from_plays(..., drop_garbage)`
    → `filter_garbage_time(plays)` → same `apply_garbage_time` when column absent.

  `n_on == n_off` is a true inert filter, not a check sitting too early.

### 8. Raw path proves Connelly works when scores exist

`load_season_plays_from_cfbd_raw(2023)` → `garbage_frac ≈ 0.166`,
`wp_before_nonnull = 0`, `fallback_frac = 1.0`. Raw JSON has
`offenseScore` / `defenseScore` / `clock` (confirmed on a 2023 week-1 archive);
ingest drops them. Raw play archives exist for **all** seasons 2014–2025
(16–35 files each under `data/raw/cfbd/`).

---

## Downstream features spec’d as GT-excluded (Tasks 9 / 11 / 13)

### Task 9 — Feature registry & as-of engine
**None.** Registry/materializer only; no play-level builders.

### Task 11 — Tempo / possession (GT-excluded by spec)
From Task 11 + registry hypotheses / `tempo.py` exclusions:

- `adj_plays_per_game_{std,ewma,l3d}` — garbage-filtered snaps
- `adj_sec_per_play_{std,ewma,l3d}` — explicitly ex-garbage (+ end-of-half,
  kneel/spike, hurry-up)
- `adj_pass_rate_oe_{std,ewma,l3d}` / `adj_rush_rate_oe_{std,ewma,l3d}` — built on
  the same filtered/neutralized play set
- `expected_possessions` — consumes pace / pass-rate inputs that inherit GT
  filtering upstream

Situational features (rest, travel, rivalry, rule-era, …) are **not**
GT-filtered.

### Task 13 — Elo baseline
**Not GT-excluded.** Elo updates on game margins / results, not play EPA.
Unaffected by the inert play filter (except insofar as A5 ablation is meant to
isolate GT’s effect on the production stack, which Elo is not part of).

### Also currently affected (outside 9/11/13 but on the A5 / Stage-1 path)

- Task 8 aggregators + Task 10 efficiency family (`adj_{off,def}_*_epa/success/
  explosiveness/havoc/finishing/field_position_*`) — DESIGN §4.5 “all
  garbage-filtered”.
- Task 14 measurement model: `build_game_observations_from_plays` →
  garbage-filtered EPA observations into the Kalman filter (this is what A5
  pretends to ablate).

With GT inert, all of the above currently train/aggregate on the **full** play
set (labels remain correctly unfiltered per §2.7 / §4.2).

---

## Replay / API spend

**Not pure replay-from-staged.** Staged partitions lack the score/clock fields
Connelly needs; WP is null at source. That is itself a finding relative to the
prompt’s expectation.

**Zero API is still available** via existing raw CFBD play archives
(`data/raw/cfbd/**/plays_s{season}_*.json` for 2014–2025): either

1. widen `PlaysSchema` + re-normalize raw → staged (preferred for A5 CLI path), or
2. have observation/feature builders call `load_season_plays_from_cfbd_raw` /
   `normalize_epa_plays` at runtime (Task 8 already supports this).

No CFBD/Odds API spend required if raw archives remain on disk. Live re-fetch
only if an archive is missing for a week.

---

## Fix-scope estimate (do not implement here)

**Files (likely):** `src/ncaa_quant/data/schemas.py` (`PlaysSchema`),
`src/ncaa_quant/ingestion/cfbd.py` (`normalize_plays_payload` — keep
`offenseScore`/`defenseScore`/`clock`), possibly `features/epa.py` call sites so
staged `wp` aliases to `wp_before` when going through
`build_observations_from_staged` / `filter_garbage_time` without
`normalize_epa_plays`. Optional ADR if DESIGN’s WP-primary rule stays primary
while CFBD WP remains absent (see notes/23.md D-4 proposal — not endorsed here).

**Rematerialization:** Restage `plays` from raw (all seasons). Recompute Stage-1
observations / any efficiency & tempo feature partitions that were built without
effective GT (today `data/features/` is empty aside from quarantine — low
immediate rematerialization debt; Kalman observation caches / backtest archives
that assumed inert GT should be regenerated before A5 is declared RUN). No
filter-history table exists separately.

**Tests:** Hand-labeled `tests/unit/test_epa.py` fixtures stay valid (they supply
scores/WP). Synthetic A5 tests in `test_task22b.py` /
`test_task23_fix_distribution.py` stay valid. Unlikely to invalidate committed
tests; may **add** an integration assert that staged (or raw-normalized) seasons
have `n_on < n_off`. Do not retune WP/Connelly thresholds against A5 deltas.

---

## Reproduction

```text
uv run python scripts/_gt_diag.py
# → docs/notes/_artifacts/gt_diag_report.json
```
