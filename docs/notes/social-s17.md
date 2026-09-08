# S17 — Verify margin orientation in graded rows

**Date:** 2026-09-08  
**Mode:** READ-ONLY (no product / R2 / history writes)

**Sources:**
- staged: `data/staged/games/season=2026/week=1/part.parquet` (+ `teams/season=2026`)
- results: live R2 `latest/results_2026.json`
- published μ: `data/webapp/publish_history/2026_w1.jsonl` row
  `published_at=2026-08-27T11:33:22Z`, `refresh_kind=daily_refresh`
  (matches each row’s `graded_from`)
- `latest/week_predictions.json` is week **2** — all three game_ids **ABSENT** there

Helper: `docs/notes/_artifacts/social-s17/check_orientation.py`

## Game IDs

| Matchup | game_id |
|---------|---------|
| LSU / Clemson | 401856660 |
| Utah State / Idaho State | 401860880 |
| Rutgers / Massachusetts | 401858423 |

## 1. Staged games

| game_id | home_team | away_team | home_points | away_points | home−away |
|---------|-----------|-----------|------------:|------------:|----------:|
| 401856660 | LSU | Clemson | 51 | 10 | 41 |
| 401860880 | Utah State | Idaho State | 17 | 29 | −12 |
| 401858423 | Rutgers | Massachusetts | 21 | 37 | −16 |

(Staged table has `home_team_id` / `away_team_id` only; schools via `teams`.)

## 2. `results_2026.json` (graded)

| game_id | home | away | actual_margin | mu_margin |
|---------|------|------|--------------:|----------:|
| 401856660 | LSU | Clemson | 41 | −4.551763060713326 |
| 401860880 | Utah State | Idaho State | −12 | 40.29333107549373 |
| 401858423 | Rutgers | Massachusetts | −16 | 41.69856667964856 |

## 3. Publish-history snapshot (`graded_from`)

| game_id | home | away | mu_margin | p_win_home | conviction_team |
|---------|------|------|----------:|-----------:|-----------------|
| 401856660 | LSU | Clemson | −4.551763060713326 | 0.3866380519344982 | Clemson |
| 401860880 | Utah State | Idaho State | 40.29333107549373 | 0.9838104043633068 | Utah State |
| 401858423 | Rutgers | Massachusetts | 41.69856667964856 | 0.9880532654437665 | Rutgers |

`mu_margin` and home/away labels match results exactly (0 mismatches across all 99 graded).

## 4. Same orientation?

**Yes.** In every case:

- `actual_margin == home_points − away_points` (staged and results)
- `mu_margin` in results == `mu_margin` as published (history snapshot)
- home/away labels identical across staged (resolved), results, and publish history
- `conviction_team` / sign(`mu_margin`) / `p_win_home` agree on who is favored (home-minus-away μ)

Large abs errors are model misses (wrong favorite or wrong magnitude), not a sign flip between μ and actual.

## 5. Corrected recomputation

**No mismatch → no corrected orientation.** As-is metrics unchanged from S16:

| version | MAE | interval hits / n | hit rate |
|---------|-----|------------------:|----------|
| as-is (reported) | 16.285905928544235 | 65 / 84 | 0.7738095238095238 |

Forced flip of μ (and interval ends) for diagnostics only — **not** a correction:

| version | MAE | interval hits / n | hit rate |
|---------|-----|------------------:|----------|
| flip μ+interval | 56.37479540156891 | 31 / 84 | 0.36904761904761907 |

Flip worsens both metrics, consistent with orientation already being correct.
