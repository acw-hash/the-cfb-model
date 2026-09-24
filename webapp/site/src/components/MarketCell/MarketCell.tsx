import { Figure } from "@/components/Figure/Figure";
import { OddsTimestamp } from "@/components/OddsTimestamp/OddsTimestamp";
import type { OddsGameView } from "@/lib/odds/types";
import {
  formatMarketTeamNamedMargin,
  formatMarketTotal,
  formatMarketWinChance,
} from "@/lib/formatting/market-margin";

import styles from "./MarketCell.module.css";

interface MarketCellProps {
  odds: OddsGameView | null | undefined;
  homeTeam: string;
  awayTeam: string;
  /** Desktop: stacked column. Mobile: single compact line. */
  variant?: "desktop" | "mobile";
  timeZone?: string;
}

function CarriedForwardLabel({
  capturedAt,
  timeZone,
}: {
  capturedAt: string | undefined;
  timeZone?: string;
}): React.ReactElement {
  if (!capturedAt) {
    return (
      <span className={styles.carried} data-testid="carried-forward-label">
        Last pre-kickoff snapshot.
      </span>
    );
  }
  return (
    <span className={styles.carried} data-testid="carried-forward-label">
      Last pre-kickoff snapshot · <OddsTimestamp iso={capturedAt} timeZone={timeZone} />.
    </span>
  );
}

/**
 * Market figures beside the model column — N2, tabular-nums, --text-secondary.
 */
export function MarketCell({
  odds,
  homeTeam,
  awayTeam,
  variant = "desktop",
  timeZone,
}: MarketCellProps): React.ReactElement | null {
  if (!odds) {
    return null;
  }

  const margin = formatMarketTeamNamedMargin(
    odds.market_home_margin,
    homeTeam,
    awayTeam,
    odds.p_win_home_market,
  );
  const win = formatMarketWinChance(odds.market_home_margin, odds.p_win_home_market);
  const total = formatMarketTotal(odds.total_points);
  const ou = total === "—" ? "—" : `O/U ${total}`;
  const carried = odds.carried_forward ? (
    <CarriedForwardLabel capturedAt={odds.captured_at} timeZone={timeZone} />
  ) : null;

  if (variant === "mobile") {
    return (
      <div
        className={styles.mobile}
        data-testid="market-cell"
        data-carried-forward={odds.carried_forward ? "true" : "false"}
      >
        <span className={styles.label}>Market</span>
        <Figure variant="n2" className={styles.value}>
          {`${margin} · ${win} · ${ou}`}
        </Figure>
        {carried}
      </div>
    );
  }

  return (
    <div
      className={styles.desktop}
      data-testid="market-cell"
      data-carried-forward={odds.carried_forward ? "true" : "false"}
    >
      <span className={styles.label}>Market</span>
      <Figure variant="n2" className={styles.value}>
        {margin}
      </Figure>
      <Figure variant="n2" className={styles.value}>
        {win}
      </Figure>
      <Figure variant="n2" className={styles.value}>
        {ou}
      </Figure>
      {carried}
    </div>
  );
}
