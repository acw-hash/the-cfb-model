import type { TrackRecordMetric } from "@/lib/artifacts/types";

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
