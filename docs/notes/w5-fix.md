# W5-FIX — Trajectory season bug + display fixes + ratings export wiring

**Date:** 2026-10-02  
**Branch:** `w5-display-fixes`  
**Export:** False. No publish / R2 writes. Did not touch `_s6_w5_card.py`,
social artifacts, `qb_status`, `publish_history`, or `tier_state`.

## A — Webapp

1. **Season ratings load.** `page.tsx` calls `loadTeamRatingsSeason(game.season)`.
   Loader returns `null` for missing file / empty `teams` / no teams object.
   Never falls back to another season. Empty chart slot:
   `Rating history for <season> isn't published yet.`
2. **Y-axis.** `niceTicks` clamps to `[min, max]`. `.yAxis { overflow: hidden }`.
   Cause of `+1.00` below zero: unclamped nice endpoints (`floor`/`ceil` of the
   domain) painted outside the plot box.
3. **Market columns.** `ThisWeekSlate` passes `showMarket={Boolean(odds)}` so
   unmatched rows keep both market cells and render `—` via `nullDash`.
4. **Checks.** `npm run test` 250 passed; `npm run lint` clean; `npm run build` ok.

### Hardcoded `2024` artifact-load hits (src only)

| Location | Notes |
|----------|--------|
| `src/lib/artifacts/types.ts` | Union key `team_ratings_2024` + `ARTIFACT_FILES` map entry (legacy named load). Game Detail no longer uses it; season loads go through `teamRatingsArtifactFile(season)`. |
| `src/app/gallery/game-detail-states/page.tsx` | Gallery fixture still loads `loadArtifact("team_ratings_2024")` for design states — not live Game Detail. |

No other `src/` call site hardcodes `team_ratings_2024` for production pages.
Tests/fixtures still use 2024 sample data (expected).

## B — W-RATINGS-WIRE

Pass live `run_filter` history into `export_publish_artifacts` /
`build_team_ratings`:

- `StateSpaceRatingEngine.last_filter_history` set from `run_filter` result.
- `live_predict_rows` captures history + appends current-week `weekly` points.
- `execute_predict_publish` → `result["_filter_history"]` → all three export
  call sites (`export_enabled`, helper sandbox, `run_isolated_week_export`).
- `build_team_ratings` collapses to one point per `(team, week)`, preferring
  `postgame` over `weekly`.

### Diff quote (predict wiring)

```diff
+ filter_history=result.get("_filter_history"),
```

at each `export_publish_artifacts(...)` call; plus capture via
`take_live_filter_history()` after `predict(...)`.

## B7 — Isolated dry-run proof

Script: `docs/notes/_artifacts/w5-fix/dry_run_proof.py`  
One `execute_predict_publish` (isolated temp paths, export False), then export
twice (`push=False`, separate temp `tier_state` / `tier_changes` /
`publish_history`): once `filter_history=None`, once with live history.

| Check | Result |
|-------|--------|
| `week_predictions` identical except `published_at` | **True** (fully identical too — same clock) |
| `no_history` `teams` | **0** |
| `with_history` team count | **138** |
| Ohio State (194) weeks | **[1, 2, 3, 4, 5]** (w3 present — Kent State game) |
| Iowa (2294) weeks | **[1, 2, 3, 4, 5]** |
| `n_obs` | 6330 |
| `filter_history_rows` | 23213 |

## make test

`==== 3 failed, 1054 passed, 1 deselected, 32 warnings in 387.63s ====`

Known pre-existing failures (unchanged):

1. `test_betting_language_guard.py::test_ratchet_matches_exact_pin`
2. `test_slot_close_capture.py::test_2026_credit_accounting_matches_v4_projection`
3. `test_webapp_w9r.py::test_copy_cites_amended_numbers_and_reval_memo`

Mid-fix a first full run also failed two W9L mocks that lacked
`last_filter_history` — fixed with `getattr(engine, "last_filter_history", None)`.
Those two + `test_w5_ratings_wire` re-confirmed green before the final `make test`.

## Commits / preview

- `a99de1ba1675f3207ec584116be02786f7dd87ab` — A+B fix
- `25a60a2946ae6924e593b3f872794b71c129cd49` — ruff format follow-up
- Preview: https://the-cfb-model-j13mmokz7-alecs-projects-2eeacfd8.vercel.app
- Not merged. Export stayed False; no R2 writes / publish.

## Ambiguities

- Gallery page keeps a hardcoded 2024 ratings fixture load (design states only).
- Current-week points are `kind=weekly` with `game_id=None` at `as_of`; postgame
  for the same week (after the game) wins on next export via collapse rule.
