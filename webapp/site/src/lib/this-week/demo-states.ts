import type { GamePrediction, WeekPredictions } from "@/lib/artifacts/types";

/**
 * Doctored clones for W3-3 state demos. Fixtures on disk are never written.
 * Every clone is a shallow copy; numeric fields stay as in the source row
 * unless the state requires honest absence (null).
 */

/** W3-3.2 — fixture subset with no strong_lean and no clear_lean. */
export function cloneEmptyTopTiers(games: GamePrediction[]): GamePrediction[] {
  return games.filter(
    (game) => game.conviction_tier !== "strong_lean" && game.conviction_tier !== "clear_lean",
  );
}

/** W3-3.3 — σ failed credibility; tiers suppressed; margin may remain. */
export function cloneSuppressed(game: GamePrediction): GamePrediction {
  return {
    ...game,
    sigma_margin: null,
    sigma_margin_credible: false,
    p_win_home: null,
    p_win_home_credible: false,
    conviction_tier: null,
    conviction_team: null,
    conviction_label: null,
    conviction_basis: null,
    null_reason: "cold_start_insufficient",
  };
}

/** W3-3.4 — per-game STALE stamp; age 4.0h so §2.4 does not suppress the tier. */
export function cloneStale(game: GamePrediction): GamePrediction {
  return {
    ...game,
    is_stale: true,
    stale_stamp: "STALE(odds, 4.0h)",
    stale_sources: [
      {
        source: "odds",
        age_hours: 4.0,
        last_good_at: "2024-09-24T02:00:00Z",
      },
    ],
  };
}

/**
 * W3-3.5 — two-band jump (strong_lean → current tier).
 * Caller should pass a current lean/toss_up row so displayed numbers stay
 * fixture-verbatim; only revision flags change.
 */
export function cloneTwoBandRevision(game: GamePrediction): GamePrediction {
  return {
    ...game,
    tier_primary: "strong_lean",
    tier_revised_since_primary: true,
  };
}

/** W3-3.6 — meta still points at a week, games array is empty. */
export function cloneOffseason(week: WeekPredictions): WeekPredictions {
  return { ...week, games: [] };
}
