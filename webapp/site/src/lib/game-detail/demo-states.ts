import type { GamePrediction, TeamRatingEntry, TeamRatings } from "@/lib/artifacts/types";
import { cloneStale, cloneSuppressed, cloneTwoBandRevision } from "@/lib/this-week/demo-states";

import { omitWeek } from "./ratings";

/**
 * W4-5 doctored clones. Fixtures on disk are never written.
 * Numeric fields stay fixture-verbatim unless the state requires honest nulls.
 */

export function cloneSuppressedSigma(game: GamePrediction): GamePrediction {
  const base = cloneSuppressed(game);
  return {
    ...base,
    p_win_home: null,
    p_win_home_credible: false,
    sigma_total: null,
    sigma_total_credible: false,
  };
}

export { cloneStale, cloneTwoBandRevision };

/** Total μ remains; interval fields stay null (v1 export) with an explicit reason shown in UI. */
export function cloneNullTotalInterval(game: GamePrediction): GamePrediction {
  return {
    ...game,
    total_interval_lo: null,
    total_interval_hi: null,
    total_interval_nominal: null,
  };
}

/**
 * Drop a mid-season week from both teams' in-memory rating series.
 * Used to prove the chart gaps instead of interpolating.
 */
export function cloneRatingsMissingWeek(
  ratings: TeamRatings,
  homeTeamId: number,
  awayTeamId: number,
  week: number,
): TeamRatings {
  const teams: Record<string, TeamRatingEntry> = { ...ratings.teams };
  const homeKey = String(homeTeamId);
  const awayKey = String(awayTeamId);
  const home = teams[homeKey];
  const away = teams[awayKey];
  if (home) {
    teams[homeKey] = omitWeek(home, week);
  }
  if (away) {
    teams[awayKey] = omitWeek(away, week);
  }
  return { ...ratings, teams };
}
