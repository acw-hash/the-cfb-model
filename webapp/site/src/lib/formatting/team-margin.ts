import { formatProbability, sigmaDecimalPlaces } from "@/lib/formatting/numbers";

/**
 * Team-named margin presentation (clarity pass).
 * Home-margin sign convention: positive → home favored, negative → away favored.
 * Zero uses DESIGN §2.1 tie-break on p_win_home when provided.
 */

export type FavoredSide = "home" | "away";

function clampDecimals(value: number, decimals: number): string {
  return value.toFixed(decimals);
}

/**
 * Favored side from home-margin μ.
 * When μ is exactly 0, §2.1: home if p_win_home ≥ 0.5, else away.
 * If p_win_home is null at a zero margin, treat as home (same as μ ≥ 0 branch).
 */
export function favoredSideFromMargin(
  mu: number | null | undefined,
  pWinHome?: number | null,
): FavoredSide | null {
  if (mu == null || !Number.isFinite(mu)) {
    return null;
  }
  if (mu > 0) {
    return "home";
  }
  if (mu < 0) {
    return "away";
  }
  if (pWinHome == null || !Number.isFinite(pWinHome)) {
    return "home";
  }
  return pWinHome >= 0.5 ? "home" : "away";
}

/** "{Team} by {n}" for a signed home-margin point estimate. Null inputs → null. */
export function formatTeamNamedMargin(
  mu: number | null | undefined,
  homeTeam: string,
  awayTeam: string,
  sigma?: number | null,
  pWinHome?: number | null,
): string | null {
  if (mu == null || !Number.isFinite(mu)) {
    return null;
  }
  const side = favoredSideFromMargin(mu, pWinHome);
  if (side == null) {
    return null;
  }
  const decimals = sigmaDecimalPlaces(sigma ?? null);
  const magnitude = clampDecimals(Math.abs(mu), decimals);
  const team = side === "home" ? homeTeam : awayTeam;
  return `${team} by ${magnitude}`;
}

/**
 * Unsigned winning margin for scoreboard placement (beside the favored team).
 * Exact 0 → "PK". Null / non-finite → null (caller shows "—" on the home line).
 */
export function formatUnsignedMargin(
  mu: number | null | undefined,
  sigma?: number | null,
): string | null {
  if (mu == null || !Number.isFinite(mu)) {
    return null;
  }
  if (mu === 0) {
    return "PK";
  }
  const decimals = sigmaDecimalPlaces(sigma ?? null);
  return clampDecimals(Math.abs(mu), decimals);
}

/** One interval end as "{Team} by {n}". Zero → "even". */
export function formatTeamNamedBound(
  value: number,
  homeTeam: string,
  awayTeam: string,
  decimals: number,
): string {
  if (value === 0) {
    const zero = decimals > 0 ? `0.${"0".repeat(decimals)}` : "0";
    return `even (${zero})`;
  }
  const team = value > 0 ? homeTeam : awayTeam;
  return `${team} by ${clampDecimals(Math.abs(value), decimals)}`;
}

export type IntervalJoiner = "to" | "and";

/**
 * Interval as "{lo-named} to {hi-named}" (default) or "… and …" for "between" clauses.
 * Crossing zero reads like "Rutgers by 8.1 to Michigan by 16.5".
 */
export function formatTeamNamedInterval(
  lo: number | null | undefined,
  hi: number | null | undefined,
  homeTeam: string,
  awayTeam: string,
  sigma?: number | null,
  joiner: IntervalJoiner = "to",
): string | null {
  if (lo == null || hi == null || !Number.isFinite(lo) || !Number.isFinite(hi)) {
    return null;
  }
  const decimals = sigmaDecimalPlaces(sigma ?? null);
  const loText = formatTeamNamedBound(lo, homeTeam, awayTeam, decimals);
  const hiText = formatTeamNamedBound(hi, homeTeam, awayTeam, decimals);
  return `${loText} ${joiner} ${hiText}`;
}

/** Visible σ replacement — same rounded value, plain wording. */
export function formatTypicalMiss(sigma: number | null | undefined): string | null {
  if (sigma == null || !Number.isFinite(sigma)) {
    return null;
  }
  return `Typical miss: \u00b1${sigma.toFixed(1)} pts.`;
}

/**
 * True when nominal × 10 is an integer (0.8 → 8, 0.9 → 9).
 * 0.85 → 8.5 is not a clean tenth; do not phrase as "N times in 10".
 */
export function isCleanTenthNominal(nominal: number): boolean {
  if (!Number.isFinite(nominal) || nominal <= 0 || nominal > 1) {
    return false;
  }
  const tenths = nominal * 10;
  return Math.abs(tenths - Math.round(tenths)) < 1e-9;
}

/**
 * Nominal coverage as an integer N for "N times in 10" phrasing.
 * Null when nominal is null, not finite, or not a clean tenth.
 */
export function nominalAsNIn10(nominal: number | null | undefined): number | null {
  if (nominal == null || !Number.isFinite(nominal) || !isCleanTenthNominal(nominal)) {
    return null;
  }
  return Math.round(nominal * 10);
}

export type NominalCoveragePhrase =
  | { kind: "n_in_10"; n: number; label: string }
  | { kind: "percent"; pctLabel: string; label: string }
  | { kind: "absent" };

/**
 * Human coverage phrase from margin_interval_nominal.
 * Clean tenths → "8 times in 10"; otherwise → "85% of the time"; null → absent.
 */
export function formatNominalCoveragePhrase(
  nominal: number | null | undefined,
): NominalCoveragePhrase {
  if (nominal == null || !Number.isFinite(nominal)) {
    return { kind: "absent" };
  }
  const nIn10 = nominalAsNIn10(nominal);
  if (nIn10 != null) {
    return { kind: "n_in_10", n: nIn10, label: `${nIn10} times in 10` };
  }
  const pctLabel = formatProbability(nominal) ?? `${Math.round(nominal * 100)}%`;
  return { kind: "percent", pctLabel, label: `${pctLabel} of the time` };
}

/**
 * Leading article for a probability percent string ("76%", "89%").
 * Vowel-sound starts (8, 11, 18, 80–89) take "an".
 */
export function articleForPercentLabel(pctLabel: string): "a" | "an" {
  const digits = pctLabel.match(/^(\d+)/);
  if (!digits) {
    return "a";
  }
  const n = Number(digits[1]);
  if (n === 8 || n === 11 || n === 18 || (n >= 80 && n <= 89)) {
    return "an";
  }
  // Also: 80–89 as two-digit, and any number whose spoken English starts with a vowel
  // via its leading digit being 8 (8, 80–89) already covered; 11 and 18 covered.
  return "a";
}

/** Favored team's win probability from p_win_home + side. */
export function favoredWinProbability(
  mu: number | null | undefined,
  pWinHome: number | null | undefined,
): number | null {
  if (mu == null || pWinHome == null || !Number.isFinite(pWinHome)) {
    return null;
  }
  const side = favoredSideFromMargin(mu, pWinHome);
  if (side == null) {
    return null;
  }
  return side === "home" ? pWinHome : 1 - pWinHome;
}

export function formatFavoredWinChance(
  mu: number | null | undefined,
  pWinHome: number | null | undefined,
): string | null {
  return formatProbability(favoredWinProbability(mu, pWinHome));
}

/**
 * "Ridge gives {team} a/an {p}% chance to win."
 */
export function formatWinChanceSentence(team: string, pctLabel: string): string {
  const article = articleForPercentLabel(pctLabel);
  return `Ridge gives ${team} ${article} ${pctLabel} chance to win.`;
}

/**
 * Interval sentence with correct "between X and Y" grammar and nominal phrase.
 */
export function formatIntervalLandSentence(
  intervalAndJoined: string,
  nominal: number | null | undefined,
): string {
  const coverage = formatNominalCoveragePhrase(nominal);
  if (coverage.kind === "absent") {
    return `Likely range: between ${intervalAndJoined}.`;
  }
  return `${coverage.label}, the final margin should land between ${intervalAndJoined}.`;
}
