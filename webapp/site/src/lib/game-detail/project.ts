import type { GamePrediction } from "@/lib/artifacts/types";
import {
  PUBLISHED_GAME_PREDICTION_KEYS,
  type PublishedGamePredictionKey,
} from "@/lib/artifacts/published-keys";

/**
 * Project a GamePrediction (including a 1.1.0 object that still carries
 * withdrawn keys) onto the 1.2.0 published key set. Extra keys are dropped
 * so Game Detail never serializes Cover/Over fields.
 */
export function projectGameDetailGame(game: GamePrediction): GamePrediction {
  const out = {} as GamePrediction;
  for (const key of PUBLISHED_GAME_PREDICTION_KEYS) {
    const typedKey = key as PublishedGamePredictionKey;
    (out as unknown as Record<string, unknown>)[typedKey] = (
      game as unknown as Record<string, unknown>
    )[typedKey];
  }
  return out;
}

export function projectGameDetailGames(games: GamePrediction[]): GamePrediction[] {
  return games.map(projectGameDetailGame);
}
