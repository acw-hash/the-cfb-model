import { OddsTimestamp } from "@/components/OddsTimestamp/OddsTimestamp";
import type { OddsPageContext } from "@/lib/odds/types";

import styles from "./OddsAsOf.module.css";

interface OddsAsOfProps {
  odds: OddsPageContext;
  timeZone?: string;
}

/**
 * Page-level odds freshness line near the published_at bar.
 * Delayed (>30h) uses --semantic-stale.
 * Timestamp uses the same visitor-local formatter as kickoff (UTC in tooltip).
 */
export function OddsAsOf({ odds, timeZone }: OddsAsOfProps): React.ReactElement {
  const when = <OddsTimestamp iso={odds.snapshot_at} timeZone={timeZone} />;
  return (
    <p
      className={odds.delayed ? styles.delayed : styles.line}
      data-testid="odds-as-of"
      data-delayed={odds.delayed ? "true" : "false"}
    >
      {odds.delayed ? (
        <>Odds snapshot delayed — as of {when}</>
      ) : (
        <>
          Odds as of {when} · consensus of sportsbooks · {odds.provider}
        </>
      )}
    </p>
  );
}
