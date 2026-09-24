/** Env bindings for the odds-refresh Worker. */
export interface Env {
  ARTIFACTS: R2Bucket;
  SUPPORTED_SCHEMA_MAJOR: string;
  ODDS_REQUESTS_REMAINING_FLOOR: string;
  FORCE_SANDBOX: string;
  ODDS_API_KEY: string;
  WEBAPP_REVALIDATE_SECRET: string;
  VERCEL_AUTOMATION_BYPASS_SECRET: string;
  MANUAL_RUN_SECRET: string;
  /** Production site revalidate URL — optional until L7 live flip. Never defaulted. */
  WEBAPP_REVALIDATE_URL?: string;
  /** Preview-only revalidate URL used when sandbox / FORCE_SANDBOX. Never falls back to WEBAPP_REVALIDATE_URL. */
  PREVIEW_REVALIDATE_URL?: string;
  NTFY_TOPIC?: string;
  NTFY_SERVER?: string;
  NTFY_AUTH_TOKEN?: string;
  TELEGRAM_BOT_TOKEN?: string;
  TELEGRAM_CHAT_ID?: string;
}

export const ODDS_SNAPSHOT_SCHEMA = "1.0.0";
export const SPORT_KEY = "americanfootball_ncaaf";
export const ODDS_MARKETS = ["h2h", "spreads", "totals"] as const;
export const ODDS_REGIONS = "us";
export const KICKOFF_MATCH_TOLERANCE_MS = 36 * 60 * 60 * 1000;
export const MIN_BOOKS = 3;
export const CONSENSUS_METHOD = "median_across_books_devigged_h2h";
