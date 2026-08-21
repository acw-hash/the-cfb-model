const STALE_THRESHOLD_HOURS = 36;

/** Format ISO UTC timestamp as absolute display (§4 C2 context). */
export function formatAbsoluteUtc(iso: string): string {
  const date = new Date(iso);
  const formatted = new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: "UTC",
  }).format(date);
  return `${formatted} UTC`;
}

const ABSENT_KICKOFF = "—";

/**
 * Format kickoff in visitor-local time with UTC available for tooltip.
 *
 * `timeZone` should be the viewer IANA zone (e.g. from
 * `Intl.DateTimeFormat().resolvedOptions().timeZone`). Omitting it falls back
 * to the runtime default — correct in the browser, wrong on a UTC SSR host.
 */
export function formatKickoffLocal(
  kickoffUtc: string | null | undefined,
  timeZone?: string,
): {
  local: string;
  utc: string;
} {
  if (kickoffUtc == null) {
    return { local: ABSENT_KICKOFF, utc: ABSENT_KICKOFF };
  }
  const date = new Date(kickoffUtc);
  const local = new Intl.DateTimeFormat("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    ...(timeZone != null ? { timeZone } : {}),
  }).format(date);
  const utc = new Intl.DateTimeFormat("en-US", {
    hour: "numeric",
    minute: "2-digit",
    timeZone: "UTC",
    timeZoneName: "short",
  }).format(date);
  return { local, utc };
}

/** Relative time from now to published_at (§3 freshness display). */
export function formatRelativeAge(iso: string, now: Date = new Date()): string {
  const then = new Date(iso);
  const diffMs = now.getTime() - then.getTime();
  const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
  if (diffHours < 1) {
    const diffMinutes = Math.max(1, Math.floor(diffMs / (1000 * 60)));
    return `${diffMinutes}m ago`;
  }
  if (diffHours < 48) {
    return `${diffHours}h ago`;
  }
  const diffDays = Math.floor(diffHours / 24);
  return `${diffDays}d ago`;
}

/** Site staleness per §3.2 — >36h past published_at AND past next expected slot. */
export function isSiteStale(
  publishedAt: string,
  nextExpectedPublishUtc: string,
  now: Date = new Date(),
): boolean {
  const published = new Date(publishedAt);
  const nextExpected = new Date(nextExpectedPublishUtc);
  const hoursSincePublish = (now.getTime() - published.getTime()) / (1000 * 60 * 60);
  return hoursSincePublish > STALE_THRESHOLD_HOURS && now > nextExpected;
}

export function stalenessBannerMessage(publishedAt: string): string {
  return `Data may be stale — last updated ${formatAbsoluteUtc(publishedAt)}`;
}

/** Format stale source age for badge tooltips. */
export function formatAgeHours(ageHours: number): string {
  return `${ageHours.toFixed(1)}h`;
}
