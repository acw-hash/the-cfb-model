/**
 * ILLUSTRATIVE example game for /visual. None of these values come from a
 * published artifact; the page labels them as examples everywhere they appear.
 * Numbers are chosen to sit in realistic ranges for FBS games.
 */
import type { Observation } from "./math";

export const HOME = "Home";
export const AWAY = "Away";

/** Preseason view of the home team's offense (EPA per snap, league-centered). */
export const PRIOR_MEAN = 0.08;
export const DEFAULT_RETURNING = 0.62;

/** One season of the home offense, week by week. `epa: null` is a bye. */
export const SEASON: Observation[] = [
  { week: 1, epa: 0.21, snaps: 68 },
  { week: 2, epa: 0.04, snaps: 74 },
  { week: 3, epa: 0.19, snaps: 61 },
  { week: 4, epa: 0.26, snaps: 70 },
  { week: 5, epa: 0.68, snaps: 58 },
  { week: 6, epa: 0.08, snaps: 72 },
  { week: 7, epa: null, snaps: 0 },
  { week: 8, epa: 0.17, snaps: 66 },
  { week: 9, epa: 0.12, snaps: 75 },
  { week: 10, epa: 0.22, snaps: 63 },
  { week: 11, epa: 0.1, snaps: 71 },
  { week: 12, epa: 0.18, snaps: 69 },
];

/** Stage 2 member estimates for the margin (home minus away, points). */
export const MEMBERS = [
  { id: "lgbm", name: "LightGBM", kind: "Gradient-boosted trees", mu: 4.9, weight: 0.62 },
  { id: "enet", name: "Elastic Net", kind: "Regularized linear model", mu: 3.1, weight: 0.38 },
] as const;

export const MU_TOTAL = 55.3;

/** σ decomposition, in squared points. */
export const SIGMA_HEAD = 13.2;
export const RATING_VAR = 15.4;

/**
 * Illustrative key-number weights by absolute final margin. The production
 * kernel is fit from out-of-fold residuals, not hand-set like this.
 */
export const KEY_WEIGHTS: Record<number, number> = {
  1: 0.8,
  2: 0.85,
  3: 1.95,
  4: 1.15,
  5: 0.9,
  6: 1.1,
  7: 1.75,
  8: 1.1,
  10: 1.3,
  11: 0.95,
  13: 0.95,
  14: 1.35,
  17: 1.2,
  21: 1.2,
  24: 1.1,
  28: 1.15,
};

/** Range step: raw quantile heads and the conformal widening. */
export const Q10 = -11.6;
export const Q90 = 19.7;
export const CONFORMAL_WIDEN = 1.4;
export const INCOHERENT = { q10: 5.0, q90: 19.0 };

export const KICKOFF_LABEL = "Sat 3:30 PM";
