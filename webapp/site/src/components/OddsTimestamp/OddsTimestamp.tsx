"use client";

import { useMemo } from "react";

import { formatKickoffLocal } from "@/lib/formatting/time";

interface OddsTimestampProps {
  iso: string;
  timeZone?: string;
  className?: string;
}

/**
 * Same visitor-local formatter as kickoff times; UTC string in the title tooltip.
 */
export function OddsTimestamp({
  iso,
  timeZone: timeZoneProp,
  className,
}: OddsTimestampProps): React.ReactElement {
  const timeZone = useMemo(
    () => timeZoneProp ?? Intl.DateTimeFormat().resolvedOptions().timeZone,
    [timeZoneProp],
  );
  const formatted = formatKickoffLocal(iso, timeZone);
  return (
    <span className={className} title={formatted.utc}>
      {formatted.local}
    </span>
  );
}
