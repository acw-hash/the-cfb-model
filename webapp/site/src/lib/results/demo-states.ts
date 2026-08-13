import type {
  GradedGame,
  ResultsSeason,
  TrackRecord,
  TrackRecordMetric,
} from "@/lib/artifacts/types";

/** Empty live 2026 results — real launch state before Week 1 grades exist. */
export function emptyLiveResults(publishedAt: string): ResultsSeason {
  return {
    schema_version: "1.1.0",
    season: 2026,
    published_at: publishedAt,
    grading_rule: "last_pre_kickoff_publish",
    fixture: false,
    games: [],
  };
}

function baseUngraded(
  overrides: Partial<GradedGame> & Pick<GradedGame, "grade_status">,
): GradedGame {
  const { grade_status, ...rest } = overrides;
  return {
    game_id: "demo-ungraded",
    week: 1,
    kickoff_utc: "2026-09-05T19:00:00Z",
    home_team: "Home U",
    away_team: "Away U",
    home_points: null,
    away_points: null,
    actual_margin: null,
    actual_total: null,
    graded_from: null,
    mu_margin: null,
    sigma_margin: null,
    margin_interval_lo: null,
    margin_interval_hi: null,
    margin_interval_nominal: null,
    mu_total: null,
    total_interval_lo: null,
    total_interval_hi: null,
    total_interval_nominal: null,
    p_win_home: null,
    conviction_tier: null,
    conviction_team: null,
    conviction_label: null,
    margin_interval_hit: null,
    total_interval_hit: null,
    home_win: false,
    p_win_home_realized: null,
    grade_status,
    ...rest,
  };
}

/** Doctored clones for each ungraded status — fixtures on disk untouched. */
export function cloneUngradedStatuses(): GradedGame[] {
  return [
    baseUngraded({
      game_id: "demo-not-final",
      grade_status: "game_not_final",
      home_team: "App State",
      away_team: "Liberty",
    }),
    baseUngraded({
      game_id: "demo-no-publish",
      grade_status: "no_pre_kickoff_publish",
      home_team: "Demo Home",
      away_team: "Demo Away",
      home_points: 24,
      away_points: 17,
      actual_margin: 7,
      actual_total: 41,
      home_win: true,
    }),
    baseUngraded({
      game_id: "demo-postgame-missing",
      grade_status: "postgame_missing",
      home_team: "Demo A",
      away_team: "Demo B",
    }),
  ];
}

/** Drop a metric id so the page must show honest absence, not a skipped row. */
export function cloneTrackRecordMissingMetric(track: TrackRecord, missingId: string): TrackRecord {
  return {
    ...track,
    metrics: track.metrics.filter((m) => m.id !== missingId),
  };
}

/** Expected metric ids for the recorded 23-readout table (display order). */
export const EXPECTED_METRIC_IDS: readonly string[] = [
  "fund_ats_snapshots",
  "fund_ats_2019",
  "fund_ou_snapshots",
  "fund_ou_2019",
  "mae_margin_fund",
  "mae_margin_a2",
  "crps_margin_fund",
  "crps_margin_a2",
  "ats_logloss_band",
  "scorecard_clv",
  "scorecard_fund_ats",
  "scorecard_fund_ou",
  "scorecard_logloss",
] as const;

export function metricById(
  metrics: TrackRecordMetric[],
  id: string,
): TrackRecordMetric | undefined {
  return metrics.find((m) => m.id === id);
}
