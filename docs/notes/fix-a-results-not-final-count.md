# Fix A — `/results` game_not_final wall (minimal collapsed count)

**Date:** 2026-09-08  
**Status:** Implemented (operator chose option b, minimal).

## What was wrong

Live `results_2026.json` has 888 games: 99 `graded` + 789 `game_not_final`
(season-schedule placeholders, weeks 2–15). `GradedGamesSection` mapped the
full array. Empty state only fires when `games.length === 0`, so launch
verification never saw the mixed state. Production HTML confirmed 888 rows.

`ResultsPage` / `ResultsTabs` do not filter, paginate, or cap.

## Decision (operator)

Minimal (b): graded (and other non-`game_not_final`) rows render as before;
below the list, a single count line for `game_not_final` derived from the
same `results.games` array. No expansion UI.

**Other ungraded statuses:** `no_pre_kickoff_publish` and `postgame_missing`
are **not** folded into the not-final count. They remain rendered as
`GradedGameRow`s — distinct grading outcomes, not season-schedule
placeholders. Only `game_not_final` is collapsed.

## Changes

- `GradedGamesSection.tsx` — filter out `game_not_final` from the list; emit
  `data-testid="game-not-final-count"` with `data-count`.
- `copy.ts` — `gameNotFinalCountCopy(n)`.
- `results.test.tsx` — mixed fixture (`fixture: true`) asserting 2 graded
  rows, 0 not-final rows, count line = 3, and the other two statuses still
  present as rows.

## Ambiguity

Season-level `published_at` is still not shown on `/results` (unchanged);
per-row `graded_from.published_at` remains on graded rows.
