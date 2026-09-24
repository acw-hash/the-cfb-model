import { KICKOFF_MATCH_TOLERANCE_MS } from "./env";
import { normalizeTeamName } from "./team-map";

export type RidgeGame = {
  game_id: string;
  home_team: string;
  away_team: string;
  kickoff_utc: string;
  neutral_site?: boolean;
};

export type OddsEvent = {
  id: string;
  home_team: string;
  away_team: string;
  commence_time: string;
  bookmakers: OddsBookmaker[];
};

export type OddsBookmaker = {
  key: string;
  markets?: OddsMarket[];
};

export type OddsMarket = {
  key: string;
  outcomes?: OddsOutcome[];
};

export type OddsOutcome = {
  name: string;
  price: number;
  point?: number;
};

export type MatchResult = {
  game_id: string;
  odds_event_id: string;
  swapped: boolean;
  event: OddsEvent;
};

export type UnmatchedEntry = {
  side: "odds_event" | "ridge_game";
  id: string;
  home: string;
  away: string;
};

function parseKickoff(iso: string): number {
  return Date.parse(iso);
}

function withinTolerance(aMs: number, bMs: number, tolMs: number): boolean {
  return Math.abs(aMs - bMs) <= tolMs;
}

/**
 * Exact mapped (home, away) + kickoff ±36h. If ordered miss, try reversed pair
 * (neutral-site home/away swap) and set swapped=true so callers flip signs.
 * Never fuzzy-matches. Ambiguous (multiple candidates) → unmatched both sides.
 */
export function matchEventsToGames(
  events: OddsEvent[],
  games: RidgeGame[],
  opts: { nowMs: number; toleranceMs?: number },
): { matches: MatchResult[]; unmatched: UnmatchedEntry[] } {
  const toleranceMs = opts.toleranceMs ?? KICKOFF_MATCH_TOLERANCE_MS;
  const nowMs = opts.nowMs;
  const unmatched: UnmatchedEntry[] = [];
  const matches: MatchResult[] = [];
  const matchedGameIds = new Set<string>();
  const matchedEventIds = new Set<string>();

  const normalizedGames = games.map((g) => ({
    ...g,
    home: normalizeTeamName(g.home_team),
    away: normalizeTeamName(g.away_team),
    kickMs: parseKickoff(g.kickoff_utc),
  }));

  for (const ev of events) {
    const home = normalizeTeamName(ev.home_team);
    const away = normalizeTeamName(ev.away_team);
    const kickMs = parseKickoff(ev.commence_time);
    if (Number.isNaN(kickMs)) {
      unmatched.push({ side: "odds_event", id: ev.id, home, away });
      continue;
    }

    const ordered = normalizedGames.filter(
      (g) =>
        g.home === home &&
        g.away === away &&
        withinTolerance(g.kickMs, kickMs, toleranceMs),
    );
    let swapped = false;
    let cands = ordered;
    if (cands.length === 0) {
      cands = normalizedGames.filter(
        (g) =>
          g.home === away &&
          g.away === home &&
          withinTolerance(g.kickMs, kickMs, toleranceMs),
      );
      swapped = cands.length > 0;
    }

    if (cands.length === 1) {
      const g = cands[0]!;
      if (matchedGameIds.has(g.game_id)) {
        unmatched.push({ side: "odds_event", id: ev.id, home, away });
        continue;
      }
      matchedGameIds.add(g.game_id);
      matchedEventIds.add(ev.id);
      matches.push({
        game_id: String(g.game_id),
        odds_event_id: ev.id,
        swapped,
        event: ev,
      });
    } else {
      unmatched.push({ side: "odds_event", id: ev.id, home, away });
    }
  }

  for (const g of normalizedGames) {
    if (!matchedGameIds.has(g.game_id)) {
      unmatched.push({
        side: "ridge_game",
        id: String(g.game_id),
        home: g.home,
        away: g.away,
      });
    }
  }

  // Silence unused nowMs for API symmetry with in-play filter caller.
  void nowMs;
  return { matches, unmatched };
}

export function isInPlay(commenceTimeIso: string, nowMs: number): boolean {
  const kick = parseKickoff(commenceTimeIso);
  return !Number.isNaN(kick) && kick <= nowMs;
}
