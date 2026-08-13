import type { GamePrediction } from "@/lib/artifacts/types";

export type ProbabilityField = "p_win_home" | "p_cover_home" | "p_over";

/**
 * Per-field credibility, with σ-gating authoritative (§1.8).
 * When sigma_margin_credible is false, every probability is treated as not
 * credible regardless of its own flag.
 */
export function probabilityIsCredible(game: GamePrediction, field: ProbabilityField): boolean {
  if (!game.sigma_margin_credible) {
    return false;
  }
  switch (field) {
    case "p_win_home":
      return game.p_win_home_credible && game.p_win_home != null;
    case "p_cover_home":
      return game.p_cover_home_credible && game.p_cover_home != null;
    case "p_over":
      return game.p_over_credible && game.p_over != null;
  }
}

export function probabilityValue(game: GamePrediction, field: ProbabilityField): number | null {
  if (!probabilityIsCredible(game, field)) {
    return null;
  }
  switch (field) {
    case "p_win_home":
      return game.p_win_home;
    case "p_cover_home":
      return game.p_cover_home;
    case "p_over":
      return game.p_over;
  }
}
