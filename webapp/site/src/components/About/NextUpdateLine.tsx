"use client";

import { useMemo } from "react";

import { formatKickoffLocal } from "@/lib/formatting/time";

import styles from "./AboutPage.module.css";

interface NextUpdateLineProps {
  /** ISO UTC from meta.next_expected_publish_utc. */
  nextExpectedPublishUtc: string;
  /** Test injection — production resolves visitor TZ in the browser. */
  timeZone?: string;
}

/**
 * "Next update: {local}" with UTC in the tooltip — same local formatter as kickoff.
 * Omits itself when the timestamp is nullish (caller) or already in the past.
 */
export function NextUpdateLine({
  nextExpectedPublishUtc,
  timeZone: timeZoneProp,
}: NextUpdateLineProps): React.ReactElement | null {
  const timeZone = useMemo(
    () => timeZoneProp ?? Intl.DateTimeFormat().resolvedOptions().timeZone,
    [timeZoneProp],
  );

  const ms = Date.parse(nextExpectedPublishUtc);
  if (!Number.isFinite(ms) || ms <= Date.now()) {
    return null;
  }

  const formatted = formatKickoffLocal(nextExpectedPublishUtc, timeZone);
  return (
    <p className={styles.body} data-testid="next-update" title={formatted.utc}>
      Next update: {formatted.local}
    </p>
  );
}
