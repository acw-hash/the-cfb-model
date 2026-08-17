import type { GamePrediction } from "@/lib/artifacts/types";

export type ProbabilityField = "p_win_home";

/**
 * Per-field credibility, with σ-gating authoritative (§1.8).
 * When sigma_margin_credible is false, every probability is treated as not
 * credible regardless of its own flag.
 */
export function probabilityIsCredible(game: GamePrediction, field: ProbabilityField): boolean {
  if (!game.sigma_margin_credible) {
    return false;
  }
  return field === "p_win_home" && game.p_win_home_credible && game.p_win_home != null;
}

export function probabilityValue(game: GamePrediction, field: ProbabilityField): number | null {
  if (!probabilityIsCredible(game, field)) {
    return null;
  }
  return game.p_win_home;
}
