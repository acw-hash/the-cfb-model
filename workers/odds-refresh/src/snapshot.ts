import {
  CONSENSUS_METHOD,
  MIN_BOOKS,
  ODDS_MARKETS,
  ODDS_REGIONS,
  ODDS_SNAPSHOT_SCHEMA,
  SPORT_KEY,
} from "./env";
import {
  consensusH2h,
  consensusSpread,
  consensusTotal,
} from "./consensus";
import type { MatchResult, OddsBookmaker, OddsEvent, UnmatchedEntry } from "./match";
import { isInPlay } from "./match";
import { normalizeTeamName } from "./team-map";

export type OddsSnapshotGame = {
  game_id: string;
  odds_event_id: string;
  captured_at: string;
  carried_forward: boolean;
  spread_home_points: number | null;
  market_home_margin: number | null;
  spread_book_count: number;
  total_points: number | null;
  total_book_count: number;
  p_win_home_market: number | null;
  h2h_book_count: number;
  market_thin: boolean;
};

export type OddsSnapshot = {
  schema_version: string;
  fixture: boolean;
  season: number;
  week: number;
  snapshot_at: string;
  source: {
    provider: string;
    regions: string;
    markets: string[];
    requests_remaining: number | null;
    requests_used: number | null;
    requests_last: number | null;
  };
  consensus_method: string;
  games: OddsSnapshotGame[];
  unmatched: UnmatchedEntry[];
};

function extractHomeSpread(book: OddsBookmaker, homeCanon: string, awayCanon: string): number | null {
  const mkt = (book.markets ?? []).find((m) => m.key === "spreads");
  if (!mkt?.outcomes) return null;
  const homeOut = mkt.outcomes.find((o) => normalizeTeamName(o.name) === homeCanon);
  if (homeOut && typeof homeOut.point === "number") return homeOut.point;
  const awayOut = mkt.outcomes.find((o) => normalizeTeamName(o.name) === awayCanon);
  if (awayOut && typeof awayOut.point === "number") return -awayOut.point;
  return null;
}

function extractTotal(book: OddsBookmaker): number | null {
  const mkt = (book.markets ?? []).find((m) => m.key === "totals");
  if (!mkt?.outcomes) return null;
  const over = mkt.outcomes.find((o) => o.name.trim().toLowerCase() === "over");
  if (over && typeof over.point === "number") return over.point;
  const any = mkt.outcomes.find((o) => typeof o.point === "number");
  return any?.point ?? null;
}

function extractH2h(
  book: OddsBookmaker,
  homeCanon: string,
  awayCanon: string,
): { home: number; away: number } | null {
  const mkt = (book.markets ?? []).find((m) => m.key === "h2h");
  if (!mkt?.outcomes) return null;
  const homeOut = mkt.outcomes.find((o) => normalizeTeamName(o.name) === homeCanon);
  const awayOut = mkt.outcomes.find((o) => normalizeTeamName(o.name) === awayCanon);
  if (!homeOut || !awayOut) return null;
  if (typeof homeOut.price !== "number" || typeof awayOut.price !== "number") return null;
  return { home: homeOut.price, away: awayOut.price };
}

/**
 * Build consensus for one matched event.
 * When swapped=true, Odds home/away are reversed vs Ridge — flip spread sign
 * and swap h2h sides so values stay on Ridge home-minus-away scale.
 */
export function consensusForMatch(
  match: MatchResult,
  opts: { capturedAtIso: string; minBooks?: number },
): OddsSnapshotGame {
  const capturedAtIso = opts.capturedAtIso;
  const minBooks = opts.minBooks ?? MIN_BOOKS;
  const oddsHome = normalizeTeamName(match.event.home_team);
  const oddsAway = normalizeTeamName(match.event.away_team);
  // When swapped, Ridge home is Odds away — extract using Odds labels then flip.
  const homeSpreads: number[] = [];
  const totals: number[] = [];
  const h2hPairs: Array<{ home: number; away: number }> = [];

  for (const book of match.event.bookmakers ?? []) {
    const sp = extractHomeSpread(book, oddsHome, oddsAway);
    if (sp !== null) {
      homeSpreads.push(match.swapped ? -sp : sp);
    }
    const tot = extractTotal(book);
    if (tot !== null) totals.push(tot);
    const h2h = extractH2h(book, oddsHome, oddsAway);
    if (h2h) {
      h2hPairs.push(match.swapped ? { home: h2h.away, away: h2h.home } : h2h);
    }
  }

  const spread = consensusSpread(homeSpreads, minBooks);
  const total = consensusTotal(totals, minBooks);
  const h2h = consensusH2h(h2hPairs, minBooks);

  const anyThin =
    (homeSpreads.length > 0 && homeSpreads.length < minBooks) ||
    (totals.length > 0 && totals.length < minBooks) ||
    (h2hPairs.length > 0 && h2hPairs.length < minBooks) ||
    (spread === null && total === null && h2h === null);

  return {
    game_id: match.game_id,
    odds_event_id: match.odds_event_id,
    captured_at: capturedAtIso,
    carried_forward: false,
    spread_home_points: spread?.spread_home_points ?? null,
    market_home_margin: spread?.market_home_margin ?? null,
    spread_book_count: spread?.spread_book_count ?? homeSpreads.length,
    total_points: total?.total_points ?? null,
    total_book_count: total?.total_book_count ?? totals.length,
    p_win_home_market: h2h?.p_win_home_market ?? null,
    h2h_book_count: h2h?.h2h_book_count ?? h2hPairs.length,
    market_thin: anyThin || (spread === null && total === null && h2h === null),
  };
}

export function buildSnapshot(args: {
  fixture: boolean;
  season: number;
  week: number;
  snapshotAt: Date;
  requestsRemaining: number | null;
  requestsUsed: number | null;
  requestsLast?: number | null;
  games: OddsSnapshotGame[];
  unmatched: UnmatchedEntry[];
}): OddsSnapshot {
  return {
    schema_version: ODDS_SNAPSHOT_SCHEMA,
    fixture: args.fixture,
    season: args.season,
    week: args.week,
    snapshot_at: args.snapshotAt.toISOString().replace(/\.\d{3}Z$/, "Z"),
    source: {
      provider: "The Odds API",
      regions: ODDS_REGIONS,
      markets: [...ODDS_MARKETS],
      requests_remaining: args.requestsRemaining,
      requests_used: args.requestsUsed,
      requests_last: args.requestsLast ?? null,
    },
    consensus_method: CONSENSUS_METHOD,
    games: args.games,
    unmatched: args.unmatched,
  };
}

/**
 * For in-play matched events, carry forward prior pre-kickoff row unchanged.
 */
export function applyInPlayCarryForward(
  matches: MatchResult[],
  freshGames: OddsSnapshotGame[],
  previous: OddsSnapshot | null,
  opts: { nowMs: number; capturedAtIso: string },
): OddsSnapshotGame[] {
  const { nowMs, capturedAtIso } = opts;
  const byId = new Map(freshGames.map((g) => [g.game_id, g]));
  const prevById = new Map((previous?.games ?? []).map((g) => [g.game_id, g]));
  const out: OddsSnapshotGame[] = [];

  for (const m of matches) {
    if (isInPlay(m.event.commence_time, nowMs)) {
      const prev = prevById.get(m.game_id);
      if (prev) {
        out.push({ ...prev, carried_forward: true });
      }
      // else: no prior → omit (do not invent in-play)
      continue;
    }
    const fresh = byId.get(m.game_id);
    if (fresh) {
      out.push({ ...fresh, captured_at: capturedAtIso, carried_forward: false });
    }
  }
  return out;
}

export type OddsApiResult = {
  events: OddsEvent[];
  requestsRemaining: number | null;
  requestsUsed: number | null;
  requestsLast: number | null;
  status: number;
};

export class OddsApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly retryable: boolean,
  ) {
    super(message);
    this.name = "OddsApiError";
  }
}

function parseHeaderInt(headers: Headers, name: string): number | null {
  const raw = headers.get(name);
  if (raw == null) return null;
  const n = Number.parseInt(raw, 10);
  return Number.isFinite(n) ? n : null;
}

export function isRetryableStatus(status: number): boolean {
  return status === 429 || status >= 500;
}

export async function fetchOddsApi(
  apiKey: string,
  opts: {
    commenceTimeFrom: string;
    commenceTimeTo: string;
    fetchImpl?: typeof fetch;
    maxAttempts?: number;
  },
): Promise<OddsApiResult> {
  const { commenceTimeFrom, commenceTimeTo } = opts;
  const fetchImpl = opts.fetchImpl ?? fetch;
  const maxAttempts = opts.maxAttempts ?? 3;
  const url = new URL(`https://api.the-odds-api.com/v4/sports/${SPORT_KEY}/odds`);
  url.searchParams.set("apiKey", apiKey);
  url.searchParams.set("regions", ODDS_REGIONS);
  url.searchParams.set("markets", ODDS_MARKETS.join(","));
  url.searchParams.set("oddsFormat", "american");
  url.searchParams.set("commenceTimeFrom", commenceTimeFrom);
  url.searchParams.set("commenceTimeTo", commenceTimeTo);

  let lastError: unknown;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      const resp = await fetchImpl(url.toString(), { method: "GET" });
      const remaining = parseHeaderInt(resp.headers, "x-requests-remaining");
      const used = parseHeaderInt(resp.headers, "x-requests-used");
      const last = parseHeaderInt(resp.headers, "x-requests-last");
      if (!resp.ok) {
        const retryable = isRetryableStatus(resp.status);
        const err = new OddsApiError(
          `Odds API HTTP ${resp.status}`,
          resp.status,
          retryable,
        );
        if (!retryable || attempt === maxAttempts) {
          throw err;
        }
        lastError = err;
        await sleep(2 ** (attempt - 1) * 250);
        continue;
      }
      const data = (await resp.json()) as unknown;
      if (!Array.isArray(data)) {
        throw new OddsApiError("Odds API payload must be an array", 422, false);
      }
      return {
        events: data as OddsEvent[],
        requestsRemaining: remaining,
        requestsUsed: used,
        requestsLast: last,
        status: resp.status,
      };
    } catch (e) {
      if (e instanceof OddsApiError) {
        if (!e.retryable || attempt === maxAttempts) throw e;
        lastError = e;
        await sleep(2 ** (attempt - 1) * 250);
        continue;
      }
      // network
      lastError = e;
      if (attempt === maxAttempts) {
        throw new OddsApiError(
          `Odds API network error: ${e instanceof Error ? e.message : String(e)}`,
          0,
          true,
        );
      }
      await sleep(2 ** (attempt - 1) * 250);
    }
  }
  throw lastError instanceof Error ? lastError : new Error(String(lastError));
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

/** Kickoff window from week games ± small pad. */
export function commenceWindowFromGames(
  games: Array<{ kickoff_utc: string }>,
): { from: string; to: string } | null {
  const times = games
    .map((g) => Date.parse(g.kickoff_utc))
    .filter((t) => Number.isFinite(t))
    .sort((a, b) => a - b);
  if (times.length === 0) return null;
  const padMs = 12 * 60 * 60 * 1000;
  return {
    from: new Date(times[0]! - padMs).toISOString().replace(/\.\d{3}Z$/, "Z"),
    to: new Date(times[times.length - 1]! + padMs).toISOString().replace(/\.\d{3}Z$/, "Z"),
  };
}
