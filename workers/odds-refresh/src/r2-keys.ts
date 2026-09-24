/**
 * Worker R2 write allowlist — odds/, latest/odds_snapshot.json, sandbox/ only.
 * Enforced before every put; unit-tested.
 */

const ALLOWED = [
  /^odds\//,
  /^latest\/odds_snapshot\.json$/,
  /^sandbox\//,
] as const;

export function isAllowedWriteKey(key: string): boolean {
  if (!key || key.includes("..") || key.startsWith("/")) {
    return false;
  }
  return ALLOWED.some((re) => re.test(key));
}

export function assertAllowedWriteKey(key: string): void {
  if (!isAllowedWriteKey(key)) {
    throw new Error(`R2 write key not allowlisted: ${key}`);
  }
}

/**
 * History key: `odds/{season}/w{week}/{YYYY-MM-DD}T{HH}:{MM}.json` (or under
 * sandbox/). Minute resolution so a manual `/run` never overwrites the same
 * hour's cron output.
 */
export function datedOddsKey(
  season: number,
  week: number,
  snapshotAt: Date,
  opts: { sandbox: boolean },
): string {
  const ymdhm = snapshotAt.toISOString().slice(0, 16); // YYYY-MM-DDTHH:MM
  const relative = `odds/${season}/w${week}/${ymdhm}.json`;
  return opts.sandbox ? `sandbox/${relative}` : relative;
}

export function latestOddsKey(opts: { sandbox: boolean }): string {
  return opts.sandbox ? "sandbox/latest/odds_snapshot.json" : "latest/odds_snapshot.json";
}
