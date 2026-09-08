# S8 — Wire `results_<season>.json` into the publish artifact set (Option A)

**Status:** STOP on test-count gate (see §5). Implementation + verification
items 1–4 complete; no R2 / push / export-gate / history-store writes.

## What was built

`export_publish_artifacts` now calls `grade_export(season=…, published_at=clock)`
after `team_ratings` is built, for seasons `>= LIVE_PUBLISH_MIN_SEASON` (2026),
and adds `results_{season}.json` to the artifacts dict with the same publish
clock as `week_predictions` / `meta` / `track_record` / `team_ratings`.

Return payload also includes `"results"`.

## Pointer key report

`meta.artifact_pointers` does **not** have a `"results"` key. Existing keys
(from `build_meta`):

- `week_predictions`
- `track_record`
- `results_current_season` → already `latest/results_{season}.json`
- `team_ratings`

Per task: do **not** invent `"results"`. Conditional update is present but
inert. `results_current_season` already points at the season results file.

## Fixture path

`generate_fixture_week_artifacts` untouched. Still calls
`build_results_season(..., fixture=True, allow_historical_fixture=True)` and
writes `results_{season}.json` with `"fixture": true`. Live export never sets
`fixture`.

## Guard

`assert_live_season` / `LIVE_PUBLISH_MIN_SEASON` still refuse grading seasons
`< 2026`. Live export skips `grade_export` for those seasons (keeps 2024
denylist unit tests working). Fixture path continues to use
`allow_historical_fixture=True`.

## Unit-test collateral

Isolated-staged export tests that only wrote `teams/` parquet now also write a
minimal `games/season=2026/week=1` partition so `grade_export` finds staged
games under the tmp staged root (otherwise `GradeExportError: no staged games`).

Files: `tests/unit/test_webapp_w1.py`, `test_webapp_w9d.py`, `test_webapp_w9pub1.py`.

## Verification

### 2. `grade_export` wall clock (season 2026, current publish history)

| Metric | Value |
|--------|-------|
| Wall clock | **0.223 s** |
| Games | 888 |
| `graded` | **99** |
| `postgame_missing` | **0** |
| `no_pre_kickoff_publish` | **0** |
| `game_not_final` | 789 |

### 3. Local artifact set (history **copy** only; real store untouched)

| File | Bytes |
|------|------:|
| `meta.json` | 904 |
| `week_predictions.json` | 151761 |
| `track_record.json` | 6303 |
| `team_ratings_2026.json` | 107 |
| `results_2026.json` | 804333 |

`results_2026.json` status counts match grade_export: **99 graded**,
**0 postgame_missing**, **0 no_pre_kickoff_publish**. Shared
`published_at=2026-09-08T14:30:28Z` across week_predictions / meta / results.

Real history sizes unchanged after verification:
`2026_w1.jsonl` 397617 B, `2026_w2.jsonl` 234928 B.

Artifacts under `docs/notes/_artifacts/social-s8/`.

### 4. Allowlist

`assert_push_artifact_allowlists` passed on the full local set. No graded-row
keys outside `_GRADED_GAME_KEYS`.

### 5. `make test` — **STOP**

| Expected (task) | Actual |
|-----------------|--------|
| 952 passed, 1 deselected | **1052 passed, 1 deselected** |

Coverage 80.60%. Deselected count matches (1 live). Passed count is **+100**
vs the task expectation — likely a stale baseline on this branch (social /
slot-close suites). Per task gate: **STOP**; do not treat S8 as closed until
the operator confirms the expected count.

## Ambiguities / decisions

1. **Season gate in export:** `grade_export` only for `season >= 2026`. Calling
   it unconditionally would raise on the existing season-2024 denylist export
   tests. Aligns with lockbox guard; not a grading-rule change.
2. **Pointer `"results"`:** absent; left alone. `results_current_season` already
   correct.
3. **History append order:** grade runs **before** `append_publish_history`, so
   the in-flight publish is not yet a grading candidate (completed games with
   past kickoffs would not select a post-kickoff stamp anyway).
4. **No R2 / push / export gate / history-store edits** on the real path.

## Forbidden checks

- Export gate left `False`.
- No `push_artifacts_to_r2` / R2 calls.
- Grading rule / `REFRESH_KIND_PRECEDENCE` / §1.3 unchanged.
- Real `data/webapp/publish_history/` not written by verification (copy only).
