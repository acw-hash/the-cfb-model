/** Honest absence when total_interval_* are null in the v1 export (§1.8). */
export const TOTAL_INTERVAL_ABSENT_REASON =
  "Conformal/quantile bounds were not emitted for totals in this export.";

/** Honest absence when margin_interval_* are null (W9-D Amendment 2 gate). */
export const MARGIN_INTERVAL_ABSENT_REASON =
  "Quantile bounds were not coherent with the point forecast.";
