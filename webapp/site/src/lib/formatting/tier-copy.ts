import type { ConvictionTier } from "@/lib/artifacts/types";

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

/**
 * Short tier word for This Week scoreboard rows — derived from
 * `conviction_tier` (artifact field), not by parsing `conviction_label`.
 * Hidden when tier is null (σ-suppressed).
 */
export function shortTierWord(tier: ConvictionTier | null | undefined): string | null {
  if (tier == null) {
    return null;
  }
  switch (tier) {
    case "strong_lean":
      return "Strong";
    case "clear_lean":
      return "Clear";
    case "lean":
      return "Lean";
    case "toss_up":
      return "Toss-up";
    default:
      return null;
  }
}

/** Aria / spoken form of the short tier ("strong lean", "toss-up"). */
export function shortTierAria(tier: ConvictionTier | null | undefined): string | null {
  if (tier == null) {
    return null;
  }
  switch (tier) {
    case "strong_lean":
      return "strong lean";
    case "clear_lean":
      return "clear lean";
    case "lean":
      return "lean";
    case "toss_up":
      return "toss-up";
    default:
      return null;
  }
}

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
