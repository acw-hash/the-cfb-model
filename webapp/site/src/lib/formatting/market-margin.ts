import { formatProbability, sigmaDecimalPlaces } from "@/lib/formatting/numbers";
import {
  favoredSideFromMargin,
  formatTeamNamedMargin,
  formatUnsignedMargin,
} from "@/lib/formatting/team-margin";

/**
 * Market margin on the same home-minus-away scale as mu_margin.
 * Null → "—" (never zero).
 * Exact 0 → "Even".
 */
export function formatMarketTeamNamedMargin(
  marketHomeMargin: number | null | undefined,
  homeTeam: string,
  awayTeam: string,
  pWinHomeMarket?: number | null,
): string {
  if (marketHomeMargin == null || !Number.isFinite(marketHomeMargin)) {
    return "—";
  }
  if (marketHomeMargin === 0) {
    return "Even";
  }
  return formatTeamNamedMargin(marketHomeMargin, homeTeam, awayTeam, null, pWinHomeMarket) ?? "—";
}

/**
 * Unsigned market margin for scoreboard / Model-and-market cells.
 * Null → null (caller places "—" on the home line). Exact 0 → "PK".
 */
export function formatUnsignedMarketMargin(
  marketHomeMargin: number | null | undefined,
): string | null {
  return formatUnsignedMargin(marketHomeMargin, null);
}

/**
 * Book-notation consensus spread for Game Detail (home team perspective).
 * 0 → "PK". Null → null (caller shows —).
 * Example: home −3.5 → "Texas A&M −3.5"
 */
export function formatConsensusSpreadBook(
  spreadHomePoints: number | null | undefined,
  homeTeam: string,
): string | null {
  if (spreadHomePoints == null || !Number.isFinite(spreadHomePoints)) {
    return null;
  }
  if (spreadHomePoints === 0) {
    return `${homeTeam} PK`;
  }
  const sign = spreadHomePoints > 0 ? "+" : "\u2212";
  const mag = Math.abs(spreadHomePoints);
  const decimals = Number.isInteger(mag) ? 0 : 1;
  return `${homeTeam} ${sign}${mag.toFixed(decimals)}`;
}

export function formatMarketWinChance(
  marketHomeMargin: number | null | undefined,
  pWinHomeMarket: number | null | undefined,
): string {
  if (pWinHomeMarket == null || !Number.isFinite(pWinHomeMarket)) {
    return "—";
  }
  if (marketHomeMargin == null || !Number.isFinite(marketHomeMargin)) {
    return formatProbability(pWinHomeMarket) ?? "—";
  }
  const side = favoredSideFromMargin(marketHomeMargin, pWinHomeMarket);
  if (side == null) {
    return "—";
  }
  const pFav = side === "home" ? pWinHomeMarket : 1 - pWinHomeMarket;
  return formatProbability(pFav) ?? "—";
}

export function formatMarketTotal(totalPoints: number | null | undefined): string {
  if (totalPoints == null || !Number.isFinite(totalPoints)) {
    return "—";
  }
  const decimals = sigmaDecimalPlaces(null);
  return totalPoints.toFixed(decimals);
}

/** Unit-test surface covering home fav / away fav / even / null. */
export function formatMarketMarginCases(
  marketHomeMargin: number | null,
  homeTeam: string,
  awayTeam: string,
): string {
  return formatMarketTeamNamedMargin(marketHomeMargin, homeTeam, awayTeam);
}

/** Aria-friendly "{Team} by n.n" / "even" without toFixed in components. */
export function formatSignedHomeMarginAria(
  value: number,
  homeTeam: string,
  awayTeam: string,
): string {
  if (value === 0) {
    return "even";
  }
  const team = value > 0 ? homeTeam : awayTeam;
  const mag = Math.abs(value);
  const label = Number.isInteger(mag) ? String(mag) : mag.toFixed(1);
  return `${team} by ${label}`;
}
