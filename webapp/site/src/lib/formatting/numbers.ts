const MINUS = "\u2212";

export const ABSENT = "\u2014";
export const NOT_COMPUTED = "not computed";
export const FORECAST_UNAVAILABLE = "Forecast unavailable";

/** Decimal places implied by sigma rounding (§4.2 precision cap). */
export function sigmaDecimalPlaces(sigma: number | null | undefined): number {
  if (sigma == null || !Number.isFinite(sigma)) {
    return 1;
  }
  const rounded = Number(sigma.toFixed(1));
  const text = rounded.toString();
  const dot = text.indexOf(".");
  return dot === -1 ? 0 : text.length - dot - 1;
}

function clampDecimals(value: number, decimals: number): string {
  return value.toFixed(decimals);
}

function formatSigned(value: number, decimals: number): string {
  const magnitude = clampDecimals(Math.abs(value), decimals);
  if (value > 0) {
    return `+${magnitude}`;
  }
  if (value < 0) {
    return `${MINUS}${magnitude}`;
  }
  const zero = decimals > 0 ? `0.${"0".repeat(decimals)}` : "0";
  return `+${zero}`;
}

/** Margin μ — sign always shown; precision capped by σ (§4.2). */
export function formatMargin(
  value: number | null | undefined,
  sigma?: number | null,
): string | null {
  if (value == null) {
    return null;
  }
  const decimals = sigmaDecimalPlaces(sigma ?? null);
  return formatSigned(value, decimals);
}

/** Margin σ label fragment — prefix "σ" in labels (§4.2). */
export function formatSigma(value: number | null | undefined): string | null {
  if (value == null) {
    return null;
  }
  return `\u03c3 ${value.toFixed(1)}`;
}

/** Total μ — 1 decimal, no sign (§4.2). */
export function formatTotal(value: number | null | undefined): string | null {
  if (value == null) {
    return null;
  }
  return clampDecimals(value, 1);
}

/** Nominal interval coverage as a percent (§4.2 probability rounding). */
export function formatNominalCoverage(value: number | null | undefined): string | null {
  return formatProbability(value);
}

/**
 * Stage-1 EPA (relative, league-mean-centered) — 2 decimals, sign shown.
 * Posterior SD in the ratings artifact is ~0.04–0.17, so 2 decimals is the
 * precision the σ warrants.
 */
export function formatEpa(value: number | null | undefined): string | null {
  if (value == null) {
    return null;
  }
  return formatSigned(value, 2);
}

/** Probability percent — 0 decimals if ≥10%; 1 decimal if <10% (§4.2). */
export function formatProbability(value: number | null | undefined): string | null {
  if (value == null) {
    return null;
  }
  const pct = value * 100;
  if (pct >= 10) {
    return `${Math.round(pct)}%`;
  }
  return `${pct.toFixed(1)}%`;
}

export interface IntervalParts {
  mu: string;
  lo: string | null;
  hi: string | null;
}

/** Interval band parts for `μ [lo, hi]` inline rendering (§4.2). */
export function formatIntervalParts(
  mu: number | null | undefined,
  lo: number | null | undefined,
  hi: number | null | undefined,
  sigma?: number | null,
): IntervalParts | null {
  if (mu == null) {
    return null;
  }
  const decimals = sigmaDecimalPlaces(sigma ?? null);
  const muText = formatSigned(mu, decimals);
  const loText = lo == null ? null : formatSigned(lo, decimals);
  const hiText = hi == null ? null : formatSigned(hi, decimals);
  return { mu: muText, lo: loText, hi: hiText };
}

/** Render interval as single quiet string when both bounds present. */
export function formatIntervalInline(parts: IntervalParts): string {
  if (parts.lo == null || parts.hi == null) {
    return parts.mu;
  }
  return `${parts.mu} [${parts.lo}, ${parts.hi}]`;
}

/**
 * Total interval parts — unsigned μ and bounds (§4.2 total formatting).
 * Null when μ is absent.
 */
export function formatTotalIntervalParts(
  mu: number | null | undefined,
  lo: number | null | undefined,
  hi: number | null | undefined,
  sigma?: number | null,
): IntervalParts | null {
  if (mu == null) {
    return null;
  }
  const decimals = sigmaDecimalPlaces(sigma ?? null);
  return {
    mu: clampDecimals(mu, decimals),
    lo: lo == null ? null : clampDecimals(lo, decimals),
    hi: hi == null ? null : clampDecimals(hi, decimals),
  };
}

/** Actual final margin (integer points) — sign shown, no invented decimals. */
export function formatActualMargin(value: number | null | undefined): string | null {
  if (value == null) {
    return null;
  }
  if (value > 0) {
    return `+${value}`;
  }
  if (value < 0) {
    return `${MINUS}${Math.abs(value)}`;
  }
  return "0";
}

/** Actual final score pair — away–home. */
export function formatFinalScore(
  awayPoints: number | null | undefined,
  homePoints: number | null | undefined,
): string | null {
  if (awayPoints == null || homePoints == null) {
    return null;
  }
  return `${awayPoints}\u2013${homePoints}`;
}

/** §1.8 honest absence for generic nullable numbers. */
export function renderAbsent(): string {
  return ABSENT;
}

/** §1.8 σ-gated probability refusal. */
export function renderNotComputed(): string {
  return NOT_COMPUTED;
}

/** §1.8 μ_margin null with optional ADR reason in tooltip copy. */
export function renderForecastUnavailable(nullReason?: string | null): {
  text: string;
  title?: string;
} {
  if (nullReason) {
    return { text: FORECAST_UNAVAILABLE, title: nullReason };
  }
  return { text: FORECAST_UNAVAILABLE };
}

/** Map null_reason codes to display footnote when present (§1.8). */
export function nullReasonFootnote(nullReason: string | null | undefined): string | null {
  if (!nullReason) {
    return null;
  }
  return nullReason.replaceAll("_", " ");
}
