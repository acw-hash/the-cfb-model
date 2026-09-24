/** Odds snapshot artifact — independent schema 1.0.0 (ADR-ODDS-SNAPSHOT). */

export const ODDS_SNAPSHOT_SCHEMA_MAJOR = 1;

export interface OddsSnapshotSource {
  provider: string;
  regions: string;
  markets: string[];
  requests_remaining: number | null;
  requests_used?: number | null;
  requests_last?: number | null;
}

export interface OddsSnapshotGame {
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
}

export interface OddsUnmatched {
  side: "odds_event" | "ridge_game";
  id: string;
  home: string;
  away: string;
}

export interface OddsSnapshot {
  schema_version: string;
  fixture?: boolean;
  season: number;
  week: number;
  snapshot_at: string;
  source: OddsSnapshotSource;
  consensus_method: string;
  games: OddsSnapshotGame[];
  unmatched: OddsUnmatched[];
}

/** Client-safe per-game market view for This Week / Game Detail. */
export interface OddsGameView {
  game_id: string;
  market_home_margin: number | null;
  spread_home_points: number | null;
  spread_book_count: number;
  total_points: number | null;
  total_book_count: number;
  p_win_home_market: number | null;
  h2h_book_count: number;
  market_thin: boolean;
  carried_forward: boolean;
  captured_at: string;
}

export interface OddsPageContext {
  snapshot_at: string;
  provider: string;
  consensus_method: string;
  /** True when snapshot_at is older than 30h (still shown until 72h). */
  delayed: boolean;
  byGameId: Record<string, OddsGameView>;
}
