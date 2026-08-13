/** Supported artifact schema major version (§1.7). */
export const SUPPORTED_SCHEMA_MAJOR = 1;

export type RefreshKind = "tuesday_primary" | "daily_refresh" | "t_minus_6h" | "t_minus_1h";

export type ConvictionTier = "strong_lean" | "clear_lean" | "lean" | "toss_up";

export interface StaleSource {
  source: string;
  age_hours: number;
  last_good_at: string;
}

export interface ConvictionBasis {
  p_favored: number;
  p_win_home: number;
  mu_margin: number;
  sigma_margin: number;
  mu_sigma_ratio: number;
  favored_side: "home" | "away";
  hysteresis_applied: boolean;
  previous_tier: ConvictionTier | null;
  raw_tier: ConvictionTier;
}

export interface GamePrediction {
  game_id: string;
  season: number;
  week: number;
  home_team: string;
  away_team: string;
  home_team_id: number;
  away_team_id: number;
  kickoff_utc: string;
  neutral_site: boolean;
  conference_game: boolean;
  mu_margin: number | null;
  sigma_margin: number | null;
  sigma_margin_credible: boolean;
  margin_interval_lo: number | null;
  margin_interval_hi: number | null;
  margin_interval_nominal: number | null;
  mu_total: number | null;
  sigma_total: number | null;
  sigma_total_credible: boolean;
  total_interval_lo: number | null;
  total_interval_hi: number | null;
  total_interval_nominal: number | null;
  p_win_home: number | null;
  p_win_home_credible: boolean;
  p_cover_home: number | null;
  p_cover_home_credible: boolean;
  p_over: number | null;
  p_over_credible: boolean;
  conviction_tier: ConvictionTier | null;
  conviction_team: string | null;
  conviction_label: string | null;
  conviction_basis: ConvictionBasis | null;
  tier_primary: ConvictionTier | null;
  tier_revised_since_primary: boolean;
  is_stale: boolean;
  stale_stamp: string | null;
  stale_sources: StaleSource[];
  null_reason: string | null;
  vintage_label: string;
  ensemble_scope_label: string;
  feature_time_label: string;
  published_at: string;
  refresh_kind: RefreshKind;
}

export interface ModelIdentity {
  registry_name: string;
  champion_version: number;
  model_version: string;
  run_id: string;
}

export interface PublishStale {
  is_stale: boolean;
  combined_stamp: string | null;
  sources: StaleSource[];
}

export interface WeekPredictions {
  schema_version: string;
  season: number;
  week: number;
  refresh_kind: RefreshKind;
  published_at: string;
  feature_time_label: string;
  ensemble_scope_label: string;
  vintage_label: string;
  fixture?: boolean;
  model_identity: ModelIdentity;
  publish_stale: PublishStale;
  games: GamePrediction[];
}

export interface ChampionModel {
  registry_name: string;
  champion_version: number;
  model_version: string;
  registered_at: string;
}

export interface PublishSchedule {
  primary: string;
  refresh: string;
  postgame_ratings: string;
}

export interface ArtifactPointers {
  week_predictions: string;
  track_record: string;
  results_current_season: string;
  team_ratings: string;
}

export interface MetaArtifact {
  schema_version: string;
  published_at: string;
  season: number;
  week: number;
  refresh_kind: RefreshKind;
  next_expected_publish_utc: string;
  champion_model: ChampionModel;
  publish_schedule: PublishSchedule;
  artifact_pointers: ArtifactPointers;
  feature_time_label: string;
  ensemble_scope_label: string;
  vintage_label: string;
  fixture?: boolean;
}

export interface TrackRecordMetric {
  id: string;
  label: string;
  value: number | string;
  unit: "percent" | "points" | "ratio" | "none";
  ci_lower: number | null;
  ci_upper: number | null;
  ci_kind: "bootstrap_95" | "naive_95" | "none";
  n: number | null;
  regime: string | null;
  vintage: string;
  run: string | null;
  notes: string | null;
}

export interface TrackRecord {
  schema_version: string;
  published_at: string;
  source_memo: string;
  fixture?: boolean;
  ensemble_scope_label?: string;
  vintage_labels?: string[];
  verdict: {
    label: string;
    plain_language: string;
  };
  metrics: TrackRecordMetric[];
}

export type GradeStatus =
  "graded" | "no_pre_kickoff_publish" | "game_not_final" | "postgame_missing";

export interface GradedFrom {
  refresh_kind: RefreshKind;
  published_at: string;
}

export interface GradedGame {
  game_id: string;
  week: number;
  kickoff_utc: string;
  home_team: string;
  away_team: string;
  home_points: number | null;
  away_points: number | null;
  actual_margin: number | null;
  actual_total: number | null;
  graded_from: GradedFrom | null;
  mu_margin: number | null;
  sigma_margin: number | null;
  margin_interval_lo: number | null;
  margin_interval_hi: number | null;
  margin_interval_nominal: number | null;
  mu_total: number | null;
  total_interval_lo: number | null;
  total_interval_hi: number | null;
  total_interval_nominal: number | null;
  p_win_home: number | null;
  conviction_tier: ConvictionTier | null;
  conviction_team: string | null;
  conviction_label: string | null;
  margin_interval_hit: boolean | null;
  total_interval_hit: boolean | null;
  home_win: boolean;
  p_win_home_realized: number | null;
  grade_status: GradeStatus;
}

export interface ResultsSeason {
  schema_version: string;
  season: number;
  published_at: string;
  grading_rule: "last_pre_kickoff_publish";
  fixture?: boolean;
  games: GradedGame[];
}

export interface TeamRatingWeek {
  week: number;
  as_of_utc: string;
  off_epa: number;
  def_epa: number;
  pace: number;
  off_sd: number;
  def_sd: number;
}

export interface TeamRatingEntry {
  school: string;
  weeks: TeamRatingWeek[];
}

export interface TeamRatings {
  schema_version: string;
  season: number;
  published_at: string;
  fixture?: boolean;
  teams: Record<string, TeamRatingEntry>;
}

export type ArtifactName =
  "meta" | "week_predictions" | "track_record" | "results_2024" | "team_ratings_2024";

export const ARTIFACT_FILES: Record<ArtifactName, string> = {
  meta: "meta.json",
  week_predictions: "week_predictions.json",
  track_record: "track_record.json",
  results_2024: "results_2024.json",
  team_ratings_2024: "team_ratings_2024.json",
};
