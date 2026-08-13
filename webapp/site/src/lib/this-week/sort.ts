import type { ConvictionTier, GamePrediction } from "@/lib/artifacts/types";

/** Client-side slate orderings (§5.1). Default is kickoff — see notes. */
export type SlateOrder = "kickoff" | "conviction";

export const DEFAULT_SLATE_ORDER: SlateOrder = "kickoff";

/** Strong → clear → lean → toss-up; suppressed (null) last. */
export const TIER_RANK: Record<ConvictionTier, number> = {
  strong_lean: 0,
  clear_lean: 1,
  lean: 2,
  toss_up: 3,
};

const SUPPRESSED_RANK = 4;

export const TIER_GROUP_LABEL: Record<ConvictionTier, string> = {
  strong_lean: "Strong lean",
  clear_lean: "Clear lean",
  lean: "Lean",
  toss_up: "Toss-up",
};

export const NO_TIER_GROUP_ID = "none";
export const NO_TIER_GROUP_LABEL = "No tier";

export interface SlateGroup {
  id: string;
  label: string;
  games: GamePrediction[];
}

/** p_favored from the artifact only — never recomputed client-side. */
export function pFavoredOf(game: GamePrediction): number | null {
  return game.conviction_basis?.p_favored ?? null;
}

function tierRank(game: GamePrediction): number {
  if (game.conviction_tier == null) {
    return SUPPRESSED_RANK;
  }
  return TIER_RANK[game.conviction_tier];
}

/**
 * BY KICKOFF comparator.
 * Tie-break: kickoff_utc ascending, then game_id lexicographic.
 */
export function compareByKickoff(a: GamePrediction, b: GamePrediction): number {
  const kick = a.kickoff_utc.localeCompare(b.kickoff_utc);
  if (kick !== 0) {
    return kick;
  }
  return a.game_id.localeCompare(b.game_id);
}

/**
 * BY CONVICTION comparator.
 * Tie-break: tier rank ascending, then p_favored descending (null last),
 * then game_id lexicographic.
 */
export function compareByConviction(a: GamePrediction, b: GamePrediction): number {
  const rankDiff = tierRank(a) - tierRank(b);
  if (rankDiff !== 0) {
    return rankDiff;
  }
  const pA = pFavoredOf(a);
  const pB = pFavoredOf(b);
  if (pA == null && pB == null) {
    return a.game_id.localeCompare(b.game_id);
  }
  if (pA == null) {
    return 1;
  }
  if (pB == null) {
    return -1;
  }
  if (pA !== pB) {
    return pB - pA;
  }
  return a.game_id.localeCompare(b.game_id);
}

function localDateKey(iso: string, timeZone: string): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date(iso));
  const year = parts.find((p) => p.type === "year")?.value ?? "0000";
  const month = parts.find((p) => p.type === "month")?.value ?? "01";
  const day = parts.find((p) => p.type === "day")?.value ?? "01";
  return `${year}-${month}-${day}`;
}

/** Format a YYYY-MM-DD calendar key as a scores-app day header. */
export function formatDayLabel(dateKey: string): string {
  const [year, month, day] = dateKey.split("-").map((part) => Number.parseInt(part, 10));
  const utcNoon = new Date(Date.UTC(year, month - 1, day, 12, 0, 0));
  return new Intl.DateTimeFormat("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  }).format(utcNoon);
}

/** Group by visitor-local calendar day of kickoff_utc. Empty days omitted. */
export function groupByKickoffDay(games: GamePrediction[], timeZone: string): SlateGroup[] {
  const sorted = [...games].sort(compareByKickoff);
  const buckets = new Map<string, GamePrediction[]>();
  for (const game of sorted) {
    const key = localDateKey(game.kickoff_utc, timeZone);
    const list = buckets.get(key);
    if (list) {
      list.push(game);
    } else {
      buckets.set(key, [game]);
    }
  }
  return [...buckets.entries()].map(([id, groupGames]) => ({
    id,
    label: formatDayLabel(id),
    games: groupGames,
  }));
}

/** Group by conviction_tier descending; omit empty tiers; suppressed last. */
export function groupByConviction(games: GamePrediction[]): SlateGroup[] {
  const sorted = [...games].sort(compareByConviction);
  const buckets = new Map<string, GamePrediction[]>();
  for (const game of sorted) {
    const id = game.conviction_tier ?? NO_TIER_GROUP_ID;
    const list = buckets.get(id);
    if (list) {
      list.push(game);
    } else {
      buckets.set(id, [game]);
    }
  }
  const order: string[] = ["strong_lean", "clear_lean", "lean", "toss_up", NO_TIER_GROUP_ID];
  return order
    .filter((id) => buckets.has(id))
    .map((id) => ({
      id,
      label: id === NO_TIER_GROUP_ID ? NO_TIER_GROUP_LABEL : TIER_GROUP_LABEL[id as ConvictionTier],
      games: buckets.get(id) ?? [],
    }));
}

export function groupSlate(
  games: GamePrediction[],
  order: SlateOrder,
  timeZone: string,
): SlateGroup[] {
  if (order === "conviction") {
    return groupByConviction(games);
  }
  return groupByKickoffDay(games, timeZone);
}

export function parseSlateOrder(value: string | null | undefined): SlateOrder {
  return value === "conviction" ? "conviction" : DEFAULT_SLATE_ORDER;
}
