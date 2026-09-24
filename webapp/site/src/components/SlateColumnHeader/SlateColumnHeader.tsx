import grid from "@/components/ScoreboardGrid/ScoreboardGrid.module.css";

import styles from "./SlateColumnHeader.module.css";

interface SlateColumnHeaderProps {
  showMarket: boolean;
}

/**
 * Two-level sticky column labels sharing GameRow's scoreboard grid.
 * Group labels span their columns; sub-labels sit in the same cells as values.
 */
export function SlateColumnHeader({ showMarket }: SlateColumnHeaderProps): React.ReactElement {
  const gridClass = showMarket ? grid.withMarket : grid.modelOnly;

  return (
    <div className={`${gridClass} ${styles.header}`} role="row" data-testid="slate-column-header">
      <span className={styles.kickPad} aria-hidden="true" />
      <span className={styles.teamsPad} aria-hidden="true" />

      <span role="columnheader" className={styles.groupModel}>
        Model
      </span>
      {showMarket ? <span className={styles.rule} aria-hidden="true" /> : null}
      {showMarket ? (
        <span role="columnheader" className={styles.groupMarket}>
          <span className={styles.desktopOnly}>Market</span>
          <span className={styles.mobileOnly}>Mkt</span>
        </span>
      ) : null}

      <span className={`${styles.sub} ${styles.subModelMargin}`} aria-hidden="true">
        Wins by
      </span>
      <span className={`${styles.sub} ${styles.subModelWin}`} aria-hidden="true">
        Win %
      </span>
      <span className={`${styles.sub} ${styles.subTier}`} aria-hidden="true">
        Tier
      </span>
      {showMarket ? (
        <span className={`${styles.sub} ${styles.subMarketMargin}`} aria-hidden="true">
          Wins by
        </span>
      ) : null}
      {showMarket ? (
        <span className={`${styles.sub} ${styles.subMarketWin}`} aria-hidden="true">
          Win %
        </span>
      ) : null}

      {showMarket ? (
        <span className={styles.mobileWinsBy} aria-hidden="true">
          Wins by
        </span>
      ) : (
        <span className={styles.mobileWinsBySolo} aria-hidden="true">
          Wins by
        </span>
      )}
    </div>
  );
}
