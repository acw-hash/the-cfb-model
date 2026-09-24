/** Pure consensus math — de-vig, median, sign conversion. */

export function americanToDecimal(american: number): number {
  if (american === 0) {
    throw new Error("american odds cannot be 0");
  }
  if (american > 0) {
    return 1 + american / 100;
  }
  return 1 + 100 / Math.abs(american);
}

/**
 * Two-way de-vig: p_home = (1/d_h) / (1/d_h + 1/d_a) on decimal odds.
 */
export function devigHomeWinProb(homeAmerican: number, awayAmerican: number): number {
  const dH = americanToDecimal(homeAmerican);
  const dA = americanToDecimal(awayAmerican);
  const iH = 1 / dH;
  const iA = 1 / dA;
  return iH / (iH + iA);
}

/** Median of a non-empty numeric array. Even count → mean of middle two. */
export function median(values: number[]): number {
  if (values.length === 0) {
    throw new Error("median of empty array");
  }
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  if (sorted.length % 2 === 1) {
    return sorted[mid]!;
  }
  return (sorted[mid - 1]! + sorted[mid]!) / 2;
}

/**
 * Book home spread points → Ridge home-minus-away margin.
 * market_home_margin = −median_home_spread (same scale as mu_margin).
 * Pick'em (0) stays 0.
 */
export function homeSpreadToMarketMargin(homeSpreadPoints: number): number {
  // Avoid -0 from IEEE sign of zero.
  if (homeSpreadPoints === 0) return 0;
  return -homeSpreadPoints;
}

export type SpreadConsensus = {
  spread_home_points: number;
  market_home_margin: number;
  spread_book_count: number;
  market_thin: boolean;
};

export type TotalConsensus = {
  total_points: number;
  total_book_count: number;
  market_thin: boolean;
};

export type H2hConsensus = {
  p_win_home_market: number;
  h2h_book_count: number;
  market_thin: boolean;
};

export function consensusSpread(homeSpreads: number[], minBooks: number): SpreadConsensus | null {
  if (homeSpreads.length < minBooks) {
    return null;
  }
  const med = median(homeSpreads);
  return {
    spread_home_points: med,
    market_home_margin: homeSpreadToMarketMargin(med),
    spread_book_count: homeSpreads.length,
    market_thin: false,
  };
}

export function consensusTotal(totals: number[], minBooks: number): TotalConsensus | null {
  if (totals.length < minBooks) {
    return null;
  }
  return {
    total_points: median(totals),
    total_book_count: totals.length,
    market_thin: false,
  };
}

export function consensusH2h(
  pairs: Array<{ home: number; away: number }>,
  minBooks: number,
): H2hConsensus | null {
  if (pairs.length < minBooks) {
    return null;
  }
  const probs = pairs.map((p) => devigHomeWinProb(p.home, p.away));
  return {
    p_win_home_market: median(probs),
    h2h_book_count: pairs.length,
    market_thin: false,
  };
}

/** Book notation label helper for pick'em. */
export function formatSpreadBookNotation(homeSpreadPoints: number | null): string | null {
  if (homeSpreadPoints === null) return null;
  if (homeSpreadPoints === 0) return "PK";
  return homeSpreadPoints > 0 ? `+${homeSpreadPoints}` : String(homeSpreadPoints);
}
