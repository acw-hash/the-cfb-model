import type { OddsGameView, OddsPageContext, OddsSnapshot } from "./types";
import { ODDS_SNAPSHOT_SCHEMA_MAJOR } from "./types";

export const ODDS_DELAYED_MS = 30 * 60 * 60 * 1000;
export const ODDS_HIDE_MS = 72 * 60 * 60 * 1000;

export function isOddsSnapshotEnabled(): boolean {
  return process.env.ODDS_SNAPSHOT_ENABLED?.trim().toLowerCase() === "true";
}

export function parseOddsSchemaMajor(schemaVersion: string): number | null {
  const major = Number.parseInt(schemaVersion.split(".")[0] ?? "", 10);
  return Number.isFinite(major) ? major : null;
}

export function isOddsSchemaSupported(schemaVersion: string | null | undefined): boolean {
  if (schemaVersion == null || schemaVersion.trim() === "") {
    return false;
  }
  return parseOddsSchemaMajor(schemaVersion) === ODDS_SNAPSHOT_SCHEMA_MAJOR;
}

export function oddsAgeMs(snapshotAt: string, nowMs: number): number | null {
  const t = Date.parse(snapshotAt);
  if (!Number.isFinite(t)) return null;
  return nowMs - t;
}

/**
 * >72h → hide entirely (null).
 * >30h → delayed label.
 * Missing/invalid → null.
 */
export function evaluateOddsFreshness(
  snapshotAt: string,
  nowMs: number = Date.now(),
): "fresh" | "delayed" | "hide" | "invalid" {
  const age = oddsAgeMs(snapshotAt, nowMs);
  if (age == null || age < 0) return "invalid";
  if (age > ODDS_HIDE_MS) return "hide";
  if (age > ODDS_DELAYED_MS) return "delayed";
  return "fresh";
}

export function projectOddsGame(game: OddsSnapshot["games"][number]): OddsGameView {
  return {
    game_id: String(game.game_id),
    market_home_margin: game.market_home_margin,
    spread_home_points: game.spread_home_points,
    spread_book_count: game.spread_book_count,
    total_points: game.total_points,
    total_book_count: game.total_book_count,
    p_win_home_market: game.p_win_home_market,
    h2h_book_count: game.h2h_book_count,
    market_thin: game.market_thin,
    carried_forward: game.carried_forward,
    captured_at: game.captured_at,
  };
}

export function buildOddsPageContext(
  snapshot: OddsSnapshot,
  nowMs: number = Date.now(),
): OddsPageContext | null {
  if (!isOddsSchemaSupported(snapshot.schema_version)) {
    return null;
  }
  const freshness = evaluateOddsFreshness(snapshot.snapshot_at, nowMs);
  if (freshness === "hide" || freshness === "invalid") {
    return null;
  }
  const byGameId: Record<string, OddsGameView> = {};
  for (const g of snapshot.games ?? []) {
    byGameId[String(g.game_id)] = projectOddsGame(g);
  }
  return {
    snapshot_at: snapshot.snapshot_at,
    provider: snapshot.source?.provider ?? "The Odds API",
    consensus_method: snapshot.consensus_method,
    delayed: freshness === "delayed",
    byGameId,
  };
}
