import type { GamePrediction } from "@/lib/artifacts/types";
import { nullReasonFootnote, renderForecastUnavailable } from "@/lib/formatting/numbers";
import {
  formatFavoredWinChance,
  formatIntervalLandSentence,
  formatTeamNamedInterval,
  formatTeamNamedMargin,
  formatWinChanceSentence,
  favoredSideFromMargin,
} from "@/lib/formatting/team-margin";
import { probabilityIsCredible } from "@/lib/game-detail/credibility";

/**
 * Plain-English Game Detail lead summary from named artifact fields.
 * Honest absence when μ is null or σ is suppressed for probabilities.
 */

export interface PlainForecastSummary {
  /** Primary paragraph(s) for the lead. */
  paragraphs: string[];
  /** True when μ is present and a forecast sentence was built. */
  hasForecast: boolean;
}

function favoredTeamName(game: GamePrediction): string | null {
  const side = favoredSideFromMargin(game.mu_margin, game.p_win_home);
  if (side == null) {
    return null;
  }
  return side === "home" ? game.home_team : game.away_team;
}

/**
 * Build the lead summary for Game Detail.
 * Fields: mu_margin, sigma_margin, margin_interval_*, margin_interval_nominal,
 * p_win_home (+ credibility), home_team, away_team, null_reason.
 */
export function buildPlainForecastSummary(game: GamePrediction): PlainForecastSummary {
  if (game.mu_margin == null) {
    const unavailable = renderForecastUnavailable(game.null_reason);
    const footnote = nullReasonFootnote(game.null_reason);
    const reason = footnote ?? unavailable.title;
    const paragraphs = [
      reason
        ? `No forecast for this game. ${unavailable.text}: ${reason}.`
        : `No forecast for this game. ${unavailable.text}.`,
    ];
    return { paragraphs, hasForecast: false };
  }

  const favoriteLine = formatTeamNamedMargin(
    game.mu_margin,
    game.home_team,
    game.away_team,
    game.sigma_margin,
    game.p_win_home,
  );
  if (favoriteLine == null) {
    return {
      paragraphs: ["No forecast for this game. Forecast unavailable."],
      hasForecast: false,
    };
  }

  const paragraphs: string[] = [favoriteLine + "."];
  const favored = favoredTeamName(game);

  if (probabilityIsCredible(game, "p_win_home") && favored != null) {
    const pct = formatFavoredWinChance(game.mu_margin, game.p_win_home);
    if (pct != null) {
      paragraphs.push(formatWinChanceSentence(favored, pct));
    }
  } else if (!game.sigma_margin_credible) {
    paragraphs.push(
      "Win chance is not shown for this game. The model withheld its usual uncertainty estimate, so a probability would not be honest.",
    );
  }

  const interval = formatTeamNamedInterval(
    game.margin_interval_lo,
    game.margin_interval_hi,
    game.home_team,
    game.away_team,
    game.sigma_margin,
    "and",
  );
  if (interval != null) {
    paragraphs.push(formatIntervalLandSentence(interval, game.margin_interval_nominal));
  }

  return { paragraphs, hasForecast: true };
}
