/** Honest absence when total_interval_* are null in the v1 export (§1.8). */
export const TOTAL_INTERVAL_ABSENT_REASON =
  "Conformal/quantile bounds were not emitted for totals in this export.";

/**
 * Fallback when margin_interval_* are null but `null_reason` is absent
 * (pre-fix live artifacts). Prefer `NULL_REASON_LABELS.incoherent_margin_interval`
 * via `nullReasonFootnote` when the code is present.
 */
export const MARGIN_INTERVAL_ABSENT_REASON =
  "Quantile bounds were not coherent with the point forecast.";
