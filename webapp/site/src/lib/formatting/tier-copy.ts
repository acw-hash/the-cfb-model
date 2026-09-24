/**
 * DESIGN §2.2 enter thresholds — single site source for UI explainers.
 * Source of truth: docs/webapp/DESIGN.md §2.2 (Strong ≥ 0.85, Clear ≥ 0.70,
 * Lean ≥ 0.575, Toss-up below). Export applies hysteresis (§2.3); the site
 * never recomputes tiers from these numbers. A chip can hold at exit bands
 * (0.82 / 0.67 / 0.545) while the row shows a lower win chance.
 */
export const TIER_ENTER = {
  strong_lean: 0.85,
  clear_lean: 0.7,
  lean: 0.575,
} as const;

/** Documented §2.2 enter values — tests fail if TIER_ENTER drifts. */
export const DESIGN_TIER_ENTER = {
  strong_lean: 0.85,
  clear_lean: 0.7,
  lean: 0.575,
} as const;

export type TierEnterKey = keyof typeof TIER_ENTER;

/**
 * One plain sentence for the published conviction tier.
 * Avoids absolute "at least X%" that contradicts sticky hysteresis labels.
 */
export function tierExplainerSentence(
  tier: "strong_lean" | "clear_lean" | "lean" | "toss_up" | null,
): string | null {
  if (tier == null) {
    return null;
  }
  switch (tier) {
    case "strong_lean":
      return "Strong lean is the top conviction band. Tiers are sticky: a game near a line keeps its label until the chance moves clearly.";
    case "clear_lean":
      return "Clear lean is the second conviction band. Tiers are sticky: a game near a line keeps its label until the chance moves clearly.";
    case "lean":
      return "Lean is a mild conviction band. Tiers are sticky: a game near a line keeps its label until the chance moves clearly.";
    case "toss_up":
      return "Toss-up means the forecast does not lean hard either way. Tiers are sticky: a game near a line keeps its label until the chance moves clearly.";
    default:
      return null;
  }
}
