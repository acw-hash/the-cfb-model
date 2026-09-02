import type { ThisWeekClientGame } from "@/lib/this-week/sort";

const APOSTROPHE_LIKE =
  /['\u2018\u2019\u201A\u201B\u2032\u02B9\u02BB\u02BC\u02BD\u02C8\u0060\u00B4]/g;

/** Normalize a team name for case-insensitive substring search. Null-safe. */
export function normalizeTeamSearchText(value: string | null | undefined): string {
  if (value == null || value === "") {
    return "";
  }
  return value
    .normalize("NFD")
    .replace(/\p{M}/gu, "")
    .replace(APOSTROPHE_LIKE, "")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

/** Tokenize a trimmed query on whitespace; each token is normalized. */
export function tokenizeSearchQuery(query: string): string[] {
  const trimmed = query.trim();
  if (!trimmed) {
    return [];
  }
  return trimmed
    .split(/\s+/)
    .map((token) => normalizeTeamSearchText(token))
    .filter((token) => token.length > 0);
}

function gameMatchesSearchTokens(game: ThisWeekClientGame, tokens: string[]): boolean {
  const away = normalizeTeamSearchText(game.away_team);
  const home = normalizeTeamSearchText(game.home_team);
  return tokens.every((token) => away.includes(token) || home.includes(token));
}

/**
 * Filter the slate by team name. Searches away_team and home_team only.
 * Empty or whitespace-only query returns the input array unchanged.
 */
export function filterGamesByQuery<T extends ThisWeekClientGame>(games: T[], query: string): T[] {
  const tokens = tokenizeSearchQuery(query);
  if (tokens.length === 0) {
    return games;
  }
  return games.filter((game) => gameMatchesSearchTokens(game, tokens));
}

/** Read ?q= from the URL. Missing param is an empty query. */
export function parseSearchQuery(value: string | null | undefined): string {
  return value ?? "";
}
