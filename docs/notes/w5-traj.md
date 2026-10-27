# W5-TRAJ — Rating trajectory gaps (read-only)

**Date:** 2026-10-02  
**Export:** False. No publish / R2 writes / code changes.

## Summary

| Class | Count |
|-------|------:|
| `team_ratings_2026.teams` | **0** (107-byte stub) |
| Present (game + rating point) | **0** |
| BYE (no completed game w1–4) | **18** |
| MISSING (completed game, no rating point) | **534** |
| EXPORT-ONLY | **534** |
| FILTER-MISSED | **0** |

Root cause: **W-RATINGS-WIRE** — live publish never passes `filter_history` into
`export_publish_artifacts`, so `team_ratings_2026.json` ships `teams: {}`.
Kalman **did** consume the games (`n_obs=6330` at 12:18Z publish; all 534
MISSING game_ids appear in 2026 plays-built obs). Predictions are unaffected.

What Game Detail actually charts: hardcoded `loadArtifact("team_ratings_2024")`
(`webapp/site/src/app/game/[gameId]/page.tsx` L71). Visible “gaps” are **2024**
bye/missing weeks (e.g. Ohio State 2024 week 3), not 2026 filter misses.

Full MISSING list: `docs/notes/_artifacts/w5-traj/missing_gaps.json`.

## Eligibility (filter)

`build_game_observations_from_plays`: both sides need EPA snaps (inner join);
no min-plays gate; FCS pooled inside `run_filter`, not dropped from obs.

## Display bugs (proposed diffs only)

1. **Wire ratings:** pass live `run_filter` history into export; load
   `team_ratings_{season}` on Game Detail (not hardcoded 2024).
2. **Y-axis:** `niceTicks` emits values outside `[yMin,yMax]`; CSS places them
   with `%` of axis height and no `overflow: hidden` → `-0.50` over caption;
   `formatEpa` always shows sign (`+1.00`). Clamp ticks to domain and/or clip.
