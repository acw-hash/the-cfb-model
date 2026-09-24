import { formatAbsoluteUtc } from "@/lib/formatting/time";
import type { OddsPageContext } from "@/lib/odds/types";

import styles from "./OddsAsOf.module.css";

interface OddsAsOfProps {
  odds: OddsPageContext;
  timeZone?: string;
}

/**
 * Page-level odds freshness line near the published_at bar.
 * Delayed (>30h) uses --semantic-stale.
 */
export function OddsAsOf({ odds, timeZone }: OddsAsOfProps): React.ReactElement {
  const when = formatOddsAsOfLabel(odds.snapshot_at, timeZone);
  const text = odds.delayed
    ? `Odds snapshot delayed — as of ${when}`
    : `Odds as of ${when} · consensus of sportsbooks · ${odds.provider}`;

  return (
    <p
      className={odds.delayed ? styles.delayed : styles.line}
      data-testid="odds-as-of"
      data-delayed={odds.delayed ? "true" : "false"}
    >
      {text}
    </p>
  );
}

export function formatOddsAsOfLabel(snapshotAt: string, timeZone?: string): string {
  try {
    const d = new Date(snapshotAt);
    if (!Number.isFinite(d.getTime())) return snapshotAt;
    return new Intl.DateTimeFormat("en-US", {
      timeZone: timeZone ?? "America/New_York",
      weekday: "short",
      hour: "numeric",
      minute: "2-digit",
      timeZoneName: "short",
    }).format(d);
  } catch {
    return formatAbsoluteUtc(snapshotAt);
  }
}
