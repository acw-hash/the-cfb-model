import type { TeamRatingEntry, TeamRatingWeek, TeamRatings } from "@/lib/artifacts/types";
import { formatEpa } from "@/lib/formatting/numbers";

/** One week of Stage-1 ratings after PIT filter and per-week collapse. */
export interface RatingPoint {
  week: number;
  as_of_utc: string;
  off_epa: number;
  def_epa: number;
  off_sd: number;
  def_sd: number;
}

export type RatingDimension = "off" | "def";

/**
 * Collapse fixture/history snapshots to one point per week, never reading
 * as_of_utc after `asOfUtc` (point-in-time) and never past `throughWeek`.
 *
 * Duplicate week rows in the v1 ratings dump are identical Kalman snapshots;
 * the latest as_of still ≤ asOfUtc wins. Weeks whose only snapshots are later
 * than asOfUtc are omitted — not interpolated, not zero-filled.
 */
export function seriesForTeam(
  entry: TeamRatingEntry | undefined,
  asOfUtc: string,
  throughWeek: number,
): RatingPoint[] {
  if (!entry?.weeks?.length) {
    return [];
  }
  const asOfMs = Date.parse(asOfUtc);
  const byWeek = new Map<number, RatingPoint>();
  for (const row of entry.weeks) {
    if (row.week > throughWeek) {
      continue;
    }
    const rowMs = Date.parse(row.as_of_utc);
    if (!Number.isFinite(rowMs) || rowMs > asOfMs) {
      continue;
    }
    const prev = byWeek.get(row.week);
    if (!prev || Date.parse(prev.as_of_utc) <= rowMs) {
      byWeek.set(row.week, toPoint(row));
    }
  }
  return [...byWeek.values()].sort((a, b) => a.week - b.week);
}

function toPoint(row: TeamRatingWeek): RatingPoint {
  return {
    week: row.week,
    as_of_utc: row.as_of_utc,
    off_epa: row.off_epa,
    def_epa: row.def_epa,
    off_sd: row.off_sd,
    def_sd: row.def_sd,
  };
}

export function lookupTeam(ratings: TeamRatings, teamId: number): TeamRatingEntry | undefined {
  const teams = ratings.teams;
  if (!teams) {
    return undefined;
  }
  return teams[String(teamId)];
}

/**
 * Split a weekly series into contiguous week runs. A missing week is a gap:
 * the next point starts a new segment. Never insert points across the hole.
 */
export function contiguousSegments(points: RatingPoint[]): RatingPoint[][] {
  const segments: RatingPoint[][] = [];
  let current: RatingPoint[] = [];
  for (const point of points) {
    const prev = current[current.length - 1];
    if (prev && point.week !== prev.week + 1) {
      segments.push(current);
      current = [];
    }
    current.push(point);
  }
  if (current.length > 0) {
    segments.push(current);
  }
  return segments;
}

export type TravelDirection = "up" | "down" | "steady";

/** Direction of travel from first to last point. Epsilon ~0.01 EPA. */
export function travelDirection(first: number, last: number, epsilon = 0.01): TravelDirection {
  const delta = last - first;
  if (delta > epsilon) {
    return "up";
  }
  if (delta < -epsilon) {
    return "down";
  }
  return "steady";
}

function travelPhrase(direction: TravelDirection, fromWeek: number): string {
  switch (direction) {
    case "up":
      return `up from week ${fromWeek}`;
    case "down":
      return `down from week ${fromWeek}`;
    case "steady":
      return `steady vs week ${fromWeek}`;
  }
}

function teamCaption(school: string, points: RatingPoint[]): string {
  if (points.length === 0) {
    return `${school} ratings are not in this publish.`;
  }
  const first = points[0];
  const last = points[points.length - 1];
  const off = formatEpa(last.off_epa) ?? "";
  const def = formatEpa(last.def_epa) ?? "";
  const offNote =
    first.week === last.week
      ? ""
      : ` (${travelPhrase(travelDirection(first.off_epa, last.off_epa), first.week)})`;
  const defNote =
    first.week === last.week
      ? ""
      : ` (${travelPhrase(travelDirection(first.def_epa, last.def_epa), first.week)})`;
  return `${school} offense ${off}${offNote}; defense ${def}${defNote}.`;
}

/**
 * Visible text alternative for the trajectory chart: current ratings and
 * direction of travel. Lives in the DOM so the page stays usable if SVG fails.
 */
export function trajectoryCaption(
  homeSchool: string,
  awaySchool: string,
  home: RatingPoint[],
  away: RatingPoint[],
  throughWeek: number,
): string {
  const latest = Math.max(0, ...home.map((p) => p.week), ...away.map((p) => p.week));
  const weekLabel = latest > 0 ? latest : throughWeek;
  const head = `Stage-1 ratings through week ${weekLabel}, league-mean-centered EPA. Pace is omitted (different quantity than EPA; §4.3 charts offense and defense).`;
  return `${head} ${teamCaption(awaySchool, away)} ${teamCaption(homeSchool, home)}`;
}

/** Drop a week from in-memory clones — fixtures on disk are never written. */
export function omitWeek(entry: TeamRatingEntry, week: number): TeamRatingEntry {
  return {
    ...entry,
    weeks: entry.weeks.filter((row) => row.week !== week),
  };
}
