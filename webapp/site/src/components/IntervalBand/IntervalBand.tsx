import { Figure } from "@/components/Figure/Figure";
import { formatTeamNamedInterval, formatTeamNamedMargin } from "@/lib/formatting/team-margin";
import { renderAbsent, renderForecastUnavailable } from "@/lib/formatting/numbers";
import type { GamePrediction } from "@/lib/artifacts/types";

import styles from "./IntervalBand.module.css";

interface IntervalBandProps {
  game: Pick<
    GamePrediction,
    | "mu_margin"
    | "sigma_margin"
    | "margin_interval_lo"
    | "margin_interval_hi"
    | "null_reason"
    | "home_team"
    | "away_team"
  > & {
    p_win_home?: number | null;
  };
  /**
   * When false, omit the range line (This Week: range lives on Game Detail at
   * phone width — team-named intervals do not fit cleanly at 390px).
   */
  showRange?: boolean;
}

/**
 * N1 team-named margin with quiet N2 range on its own line (§4.3, clarity).
 */
export function IntervalBand({ game, showRange = true }: IntervalBandProps): React.ReactElement {
  const {
    mu_margin,
    sigma_margin,
    margin_interval_lo,
    margin_interval_hi,
    null_reason,
    home_team,
    away_team,
    p_win_home,
  } = game;

  if (mu_margin == null) {
    const unavailable = renderForecastUnavailable(null_reason);
    return (
      <Figure variant="n2" className={styles.unavailable} title={unavailable.title}>
        {unavailable.text}
      </Figure>
    );
  }

  const muText =
    formatTeamNamedMargin(mu_margin, home_team, away_team, sigma_margin, p_win_home) ??
    renderAbsent();
  const rangeText = formatTeamNamedInterval(
    margin_interval_lo,
    margin_interval_hi,
    home_team,
    away_team,
    sigma_margin,
  );
  const hasBand = rangeText != null;

  return (
    <span className={styles.wrap} data-testid="interval-band">
      <Figure variant="n1" className={styles.mu}>
        {muText}
      </Figure>
      {showRange ? (
        <span className={styles.intervalLine} data-testid="interval-line">
          {hasBand ? (
            <Figure variant="n2" className={styles.bound}>
              {rangeText}
            </Figure>
          ) : (
            <Figure variant="n1" className={styles.absent} data-testid="interval-absent">
              {renderAbsent()}
            </Figure>
          )}
        </span>
      ) : null}
    </span>
  );
}
