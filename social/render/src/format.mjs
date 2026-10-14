/**
 * Read-only re-export of webapp number formatting (DESIGN §4.2).
 * Implemented here in plain JS to avoid a TS build; keep in sync with
 * webapp/site/src/lib/formatting/numbers.ts (source of truth).
 */
const MINUS = "\u2212";

export function sigmaDecimalPlaces(sigma) {
  if (sigma == null || !Number.isFinite(sigma)) return 1;
  const rounded = Number(sigma.toFixed(1));
  const text = rounded.toString();
  const dot = text.indexOf(".");
  return dot === -1 ? 0 : text.length - dot - 1;
}

function clampDecimals(value, decimals) {
  return value.toFixed(decimals);
}

function formatSigned(value, decimals) {
  const magnitude = clampDecimals(Math.abs(value), decimals);
  if (value > 0) return `+${magnitude}`;
  if (value < 0) return `${MINUS}${magnitude}`;
  const zero = decimals > 0 ? `0.${"0".repeat(decimals)}` : "0";
  return `+${zero}`;
}

export function formatMargin(value, sigma) {
  if (value == null) return null;
  return formatSigned(value, sigmaDecimalPlaces(sigma ?? null));
}

export function formatEpa(value) {
  if (value == null) return null;
  return formatSigned(value, 2);
}

/** Signed whole number (no trailing .0), mirrors Python format_signed_whole. */
export function formatSignedWhole(value) {
  if (value == null) return null;
  const rounded = Math.round(Number(value));
  return formatSigned(rounded, 0);
}

function roundHalfUpPercent(value) {
  const scaled = Number((value * 100).toFixed(8));
  return Math.floor(scaled + 0.5);
}

export function formatProbability(value) {
  if (value == null) return null;
  const pct = value * 100;
  if (pct >= 10) return `${roundHalfUpPercent(value)}%`;
  return `${Number(pct.toFixed(8)).toFixed(1)}%`;
}

/** Path note for run reports / audits. */
export const FORMAT_SOURCE = "webapp/site/src/lib/formatting/numbers.ts";
