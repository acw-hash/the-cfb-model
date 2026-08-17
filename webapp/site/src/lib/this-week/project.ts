import type { GamePrediction, StaleSource, ThisWeekGame } from "@/lib/artifacts/types";

/** Keys that may appear on a projected This Week client row. */
export const THIS_WEEK_GAME_KEYS = [
  "game_id",
  "home_team",
  "away_team",
  "kickoff_utc",
  "neutral_site",
  "mu_margin",
  "sigma_margin",
  "margin_interval_lo",
  "margin_interval_hi",
  "null_reason",
  "conviction_tier",
  "conviction_label",
  "tier_primary",
  "tier_revised_since_primary",
  "stale_stamp",
  "stale_sources",
  "p_favored",
] as const satisfies ReadonlyArray<keyof ThisWeekGame>;

function projectStaleSources(sources: StaleSource[]): StaleSource[] {
  return sources.map((source) => ({
    source: source.source,
    age_hours: source.age_hours,
    last_good_at: source.last_good_at,
  }));
}

/**
 * Project a GamePrediction to the This Week client DTO.
 * Single named function so a future consumed field has one place to go through.
 */
export function projectThisWeekGame(game: GamePrediction): ThisWeekGame {
  return {
    game_id: game.game_id,
    home_team: game.home_team,
    away_team: game.away_team,
    kickoff_utc: game.kickoff_utc,
    neutral_site: game.neutral_site,
    mu_margin: game.mu_margin,
    sigma_margin: game.sigma_margin,
    margin_interval_lo: game.margin_interval_lo,
    margin_interval_hi: game.margin_interval_hi,
    null_reason: game.null_reason,
    conviction_tier: game.conviction_tier,
    conviction_label: game.conviction_label,
    tier_primary: game.tier_primary,
    tier_revised_since_primary: game.tier_revised_since_primary,
    stale_stamp: game.stale_stamp,
    stale_sources: projectStaleSources(game.stale_sources),
    p_favored: game.conviction_basis?.p_favored ?? null,
  };
}

export function projectThisWeekGames(games: GamePrediction[]): ThisWeekGame[] {
  return games.map(projectThisWeekGame);
}
