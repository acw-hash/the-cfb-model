import type { TrackRecordMetric } from "@/lib/artifacts/types";

/**
 * Format a recorded numeric value without meaning-changing rounding.
 * Uses the decimal places present in the artifact number (JSON → JS Number),
 * not §4.2 probability integer rounding (50.7 must not become "51%").
 */
export function formatRecordedNumber(value: number): string {
  if (!Number.isFinite(value)) {
    throw new Error(`Non-finite track-record number: ${value}`);
  }
  if (Object.is(value, -0) || value === 0) {
    return "0";
  }
  // Prefer shortest round-trip that preserves recorded decimals (e.g. 50.7, 14.85).
  return String(value);
}

/** Percent rate as recorded — value is already on a 0–100 scale in the artifact. */
export function formatRecordedPercent(value: number): string {
  return `${formatRecordedNumber(value)}%`;
}

/** CI bounds as recorded: `[lo%, hi%]` or `[lo, hi]` for non-percent. */
export function formatRecordedCi(
  lower: number,
  upper: number,
  unit: TrackRecordMetric["unit"],
): string {
  const lo = formatRecordedNumber(lower);
  const hi = formatRecordedNumber(upper);
  if (unit === "percent") {
    return `[${lo}%, ${hi}%]`;
  }
  return `[${lo}, ${hi}]`;
}

export class MissingConfidenceIntervalError extends Error {
  constructor(metricId: string) {
    super(`Track-record rate "${metricId}" cannot render without ci_lower and ci_upper`);
    this.name = "MissingConfidenceIntervalError";
  }
}

/**
 * A percent rate is impossible to render without its CI beside it (W5-1).
 * Non-rate metrics (points, ratio bands, MISSED labels) are exempt when ci_kind is none.
 */
export function assertRateHasCi(metric: TrackRecordMetric): void {
  if (metric.unit !== "percent" || typeof metric.value !== "number") {
    return;
  }
  if (
    metric.ci_kind === "none" ||
    metric.ci_lower == null ||
    metric.ci_upper == null ||
    !Number.isFinite(metric.ci_lower) ||
    !Number.isFinite(metric.ci_upper)
  ) {
    throw new MissingConfidenceIntervalError(metric.id);
  }
}

/** True when the recorded bootstrap/naive interval includes 50 (percent rates). */
export function ciIncludesFifty(metric: TrackRecordMetric): boolean {
  if (metric.unit !== "percent") {
    return false;
  }
  if (metric.ci_lower == null || metric.ci_upper == null) {
    return false;
  }
  return metric.ci_lower <= 50 && metric.ci_upper >= 50;
}
