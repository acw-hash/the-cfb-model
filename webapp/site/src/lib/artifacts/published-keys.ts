/**
 * Published GamePrediction key allowlist (schema 1.2.0) and ADR 0015 withdrawal.
 * Exact keys — not a denylist. Unknown keys fail.
 */
export const WITHDRAWN_FIELDS = [
  "p_cover_home",
  "p_cover_home_credible",
  "p_over",
  "p_over_credible",
] as const;

export type WithdrawnField = (typeof WITHDRAWN_FIELDS)[number];

/** Exact keys of a published GamePrediction object after 1.2.0 withdrawal. */
export const PUBLISHED_GAME_PREDICTION_KEYS = [
  "away_team",
  "away_team_id",
  "conference_game",
  "conviction_basis",
  "conviction_label",
  "conviction_team",
  "conviction_tier",
  "ensemble_scope_label",
  "feature_time_label",
  "game_id",
  "home_team",
  "home_team_id",
  "is_stale",
  "kickoff_utc",
  "margin_interval_hi",
  "margin_interval_lo",
  "margin_interval_nominal",
  "mu_margin",
  "mu_total",
  "neutral_site",
  "null_reason",
  "p_win_home",
  "p_win_home_credible",
  "published_at",
  "refresh_kind",
  "season",
  "sigma_margin",
  "sigma_margin_credible",
  "sigma_total",
  "sigma_total_credible",
  "stale_sources",
  "stale_stamp",
  "tier_primary",
  "tier_revised_since_primary",
  "total_interval_hi",
  "total_interval_lo",
  "total_interval_nominal",
  "vintage_label",
  "week",
] as const;

export type PublishedGamePredictionKey = (typeof PUBLISHED_GAME_PREDICTION_KEYS)[number];

/**
 * Named site consumer for each published game key.
 * Unread retained fields are listed as DESIGN §1.2 (not rendered) — they stay
 * because this withdrawal only covers the four market-referenced probabilities.
 */
export const GAME_FIELD_CONSUMERS: Record<PublishedGamePredictionKey, string> = {
  game_id: "ThisWeekSlate row key / href; GamePage lookup",
  season: "DESIGN §1.2 row identity (week.season drives This Week header)",
  week: "GameDetail RatingTrajectoryChart throughWeek; seriesForTeam cap",
  home_team: "GameRow; MatchupHeader",
  away_team: "GameRow; MatchupHeader",
  home_team_id: "GamePage lookupTeam for rating series",
  away_team_id: "GamePage lookupTeam for rating series",
  kickoff_utc: "GameRow; sort.ts; MatchupHeader",
  neutral_site: "GameRow; MatchupHeader",
  conference_game: "DESIGN §1.2 schedule identity (not rendered)",
  mu_margin: "IntervalBand; ForecastBlock Margin",
  sigma_margin: "IntervalBand rounding; ForecastBlock",
  sigma_margin_credible: "probabilityIsCredible σ-gate",
  margin_interval_lo: "IntervalBand; ForecastBlock",
  margin_interval_hi: "IntervalBand; ForecastBlock",
  margin_interval_nominal: "ForecastBlock coverage line",
  mu_total: "ForecastBlock Total",
  sigma_total: "ForecastBlock Total",
  sigma_total_credible: "GameDetail total credibility / absence",
  total_interval_lo: "ForecastBlock Total",
  total_interval_hi: "ForecastBlock Total",
  total_interval_nominal: "ForecastBlock Total coverage",
  p_win_home: "ProbabilityList Home win",
  p_win_home_credible: "probabilityIsCredible",
  conviction_tier: "TierChip; sort.ts",
  conviction_team: "DESIGN §1.2 (label is rendered; team field not separately shown)",
  conviction_label: "TierChip; RevisionBlock",
  conviction_basis: "projectThisWeekGame → p_favored for conviction sort",
  tier_primary: "RevisedMarker",
  tier_revised_since_primary: "RevisedMarker",
  is_stale: "DESIGN §1.2 sibling of stale_stamp (StaleBadge reads stale_stamp)",
  stale_stamp: "StaleBadge",
  stale_sources: "StaleBadge tooltip",
  null_reason: "IntervalBand; ForecastBlock; ProbabilityList footnote",
  vintage_label: "ProvenanceStrip",
  ensemble_scope_label: "ProvenanceStrip",
  feature_time_label: "ProvenanceStrip",
  published_at: "ProvenanceStrip PublishedAtStamp",
  refresh_kind: "ProvenanceStrip formatRefreshKind",
};

const PUBLISHED_SET = new Set<string>(PUBLISHED_GAME_PREDICTION_KEYS);

/** Exact 1.2.0 published game key set. Extra or missing keys throw. */
export function assertPublishedGameKeys(game: object): void {
  const keys = Object.keys(game);
  const extra = keys.filter((k) => !PUBLISHED_SET.has(k));
  if (extra.length > 0) {
    throw new Error(`unpublished or withdrawn keys in GamePrediction: ${extra.sort().join(", ")}`);
  }
  const missing = [...PUBLISHED_GAME_PREDICTION_KEYS].filter((k) => !keys.includes(k));
  if (missing.length > 0) {
    throw new Error(`published GamePrediction missing required keys: ${missing.sort().join(", ")}`);
  }
}

/** 1.1.0-or-1.2.0 game: every key is a named consumer or withdrawn. */
export function assertConsumedOrWithdrawn(game: object): void {
  const allowed = new Set<string>([...PUBLISHED_GAME_PREDICTION_KEYS, ...WITHDRAWN_FIELDS]);
  const neither = Object.keys(game).filter((k) => !allowed.has(k));
  if (neither.length > 0) {
    throw new Error(`game key is neither consumed nor withdrawn: ${neither.sort().join(", ")}`);
  }
}
