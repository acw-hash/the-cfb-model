"use client";

import { useMemo } from "react";

import { Figure } from "@/components/Figure/Figure";
import { formatKickoffLocal } from "@/lib/formatting/time";

type KickoffFigureVariant = "b2" | "c2";

interface KickoffTimeProps {
  kickoffUtc: string | null | undefined;
  variant?: KickoffFigureVariant;
  className?: string;
  /**
   * Viewer IANA timezone. Production omits this and resolves in the browser.
   * Tests pass an explicit zone so SSR markup matches visitor-local output.
   */
  timeZone?: string;
  "data-testid"?: string;
}

/**
 * Kickoff display in the visitor's timezone — shared by This Week, Game Detail,
 * and Results. Must be a client component: the shared formatter uses the
 * runtime default zone when `timeZone` is omitted, which is UTC on Vercel SSR.
 */
export function KickoffTime({
  kickoffUtc,
  variant = "c2",
  className,
  timeZone: timeZoneProp,
  "data-testid": dataTestId,
}: KickoffTimeProps): React.ReactElement {
  const timeZone = useMemo(
    () => timeZoneProp ?? Intl.DateTimeFormat().resolvedOptions().timeZone,
    [timeZoneProp],
  );
  const kickoff = formatKickoffLocal(kickoffUtc, timeZone);
  const title = kickoffUtc == null ? undefined : kickoff.utc;

  return (
    <Figure variant={variant} className={className} title={title} data-testid={dataTestId}>
      {kickoff.local}
    </Figure>
  );
}
