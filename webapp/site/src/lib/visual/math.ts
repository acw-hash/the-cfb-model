/**
 * Math for the /visual walkthrough.
 *
 * Every formula here mirrors the production model; constants are copied from
 * the backend so the figures behave like the real thing:
 *   - prior variance ............ ratings/priors.py        PriorConfig
 *   - Kalman update + winsorize . ratings/state_space.py   StateSpaceConfig
 *   - key-number margin PMF ..... distribution/key_numbers.py
 *   - conviction ladder ......... docs/webapp/DESIGN.md §2.2–§2.3
 *
 * The inputs fed through them (observations, member estimates, kernel
 * weights) are ILLUSTRATIVE and live in ./example.ts.
 */

export const PRIOR = {
  baseVar: 0.02,
  turnoverScale: 2.5,
  missingVarPenalty: 0.015,
  confRegression: 0.3,
} as const;

export const KALMAN = {
  q: 0.0025, // weekly process noise, off_epa
  rEpaBase: 0.12,
  refSnaps: 70,
  winsorSigma: 2.5,
} as const;

export const TIERS = {
  lean: 0.575,
  clear: 0.7,
  strong: 0.85,
  holdBand: 0.03,
} as const;

export function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}

/** Standard normal CDF (Abramowitz–Stegun 7.1.26 via erf; |err| < 1.5e-7). */
export function normCdf(z: number): number {
  const x = Math.abs(z) / Math.SQRT2;
  const t = 1 / (1 + 0.3275911 * x);
  const y =
    1 -
    ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) *
      t *
      Math.exp(-x * x);
  return z >= 0 ? 0.5 * (1 + y) : 0.5 * (1 - y);
}

export function normPdf(x: number, mu: number, sigma: number): number {
  const z = (x - mu) / sigma;
  return Math.exp(-0.5 * z * z) / (sigma * Math.sqrt(2 * Math.PI));
}

/** Prior variance: base × turnover inflation + penalty per missing input. */
export function priorVariance(returning: number, nMissing: number): number {
  const turnover = 1 - clamp(returning, 0, 1);
  return (
    PRIOR.baseVar * (1 + PRIOR.turnoverScale * turnover) +
    PRIOR.missingVarPenalty * Math.max(0, nMissing)
  );
}

export type Observation = { week: number; epa: number | null; snaps: number };

export type KalmanRow = {
  week: number;
  predMean: number;
  predVar: number;
  obs: number | null;
  obsVar: number | null;
  innovation: number | null;
  capped: number | null;
  wasCapped: boolean;
  gain: number | null;
  mean: number;
  variance: number;
};

/**
 * One-dimensional Kalman walk for a single team rating.
 * Row 0 is the preseason prior; rows 1..n are weekly posteriors.
 */
export function runKalman(m0: number, p0: number, obs: Observation[]): KalmanRow[] {
  const rows: KalmanRow[] = [
    {
      week: 0,
      predMean: m0,
      predVar: p0,
      obs: null,
      obsVar: null,
      innovation: null,
      capped: null,
      wasCapped: false,
      gain: null,
      mean: m0,
      variance: p0,
    },
  ];
  let m = m0;
  let p = p0;
  for (const o of obs) {
    const predVar = p + KALMAN.q;
    const predMean = m;
    if (o.epa === null) {
      rows.push({
        week: o.week,
        predMean,
        predVar,
        obs: null,
        obsVar: null,
        innovation: null,
        capped: null,
        wasCapped: false,
        gain: null,
        mean: predMean,
        variance: predVar,
      });
      m = predMean;
      p = predVar;
      continue;
    }
    const r = KALMAN.rEpaBase ** 2 * (KALMAN.refSnaps / Math.max(o.snaps, 1));
    const s = predVar + r;
    const innovation = o.epa - predMean;
    const limit = KALMAN.winsorSigma * Math.sqrt(s);
    const capped = clamp(innovation, -limit, limit);
    const gain = predVar / s;
    m = predMean + gain * capped;
    p = (1 - gain) * predVar;
    rows.push({
      week: o.week,
      predMean,
      predVar,
      obs: o.epa,
      obsVar: r,
      innovation,
      capped,
      wasCapped: Math.abs(innovation) > limit,
      gain,
      mean: m,
      variance: p,
    });
  }
  return rows;
}

export type MarginPmf = { ks: number[]; ps: number[] };

/**
 * Whole-number margin distribution. Normal mass at each integer, optionally
 * reweighted by a key-number kernel and renormalized (key_numbers.py).
 */
export function marginPmf(
  mu: number,
  sigma: number,
  kernel: Record<number, number> | null,
  range: [number, number] = [-70, 70],
): MarginPmf {
  const ks: number[] = [];
  const raw: number[] = [];
  let total = 0;
  for (let k = range[0]; k <= range[1]; k += 1) {
    let mass = normCdf((k + 0.5 - mu) / sigma) - normCdf((k - 0.5 - mu) / sigma);
    if (kernel) mass *= kernel[Math.abs(k)] ?? 1;
    ks.push(k);
    raw.push(mass);
    total += mass;
  }
  return { ks, ps: raw.map((v) => v / total) };
}

/** Win share with exact-tie draws set aside: P(M>0) / (P(M>0) + P(M<0)). */
export function winShare(pmf: MarginPmf): number {
  let win = 0;
  let loss = 0;
  pmf.ks.forEach((k, i) => {
    if (k > 0) win += pmf.ps[i];
    else if (k < 0) loss += pmf.ps[i];
  });
  return win / (win + loss);
}

/** Seeded PRNG (mulberry32) so simulations are reproducible, like the backend. */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function samplerFor(pmf: MarginPmf): (u: number) => number {
  const cdf: number[] = [];
  let acc = 0;
  for (const p of pmf.ps) {
    acc += p;
    cdf.push(acc);
  }
  return (u: number) => {
    let lo = 0;
    let hi = cdf.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (cdf[mid] < u) lo = mid + 1;
      else hi = mid;
    }
    return pmf.ks[lo];
  };
}

export type TierId = "strong_lean" | "clear_lean" | "lean" | "toss_up";

export function rawTier(pFavored: number): TierId {
  if (pFavored >= TIERS.strong) return "strong_lean";
  if (pFavored >= TIERS.clear) return "clear_lean";
  if (pFavored >= TIERS.lean) return "lean";
  return "toss_up";
}

export const TIER_NAMES: Record<TierId, string> = {
  strong_lean: "Strong lean",
  clear_lean: "Clear lean",
  lean: "Lean",
  toss_up: "Toss-up",
};

export function tierLabel(tier: TierId, team: string): string {
  return tier === "toss_up" ? "Toss-up" : `${TIER_NAMES[tier]} ${team}`;
}

/* ---------- formatting (DESIGN §4.2) ---------- */

const MINUS = "\u2212";

export function fmtSigned(v: number, digits = 1): string {
  const s = Math.abs(v).toFixed(digits);
  if (Number(s) === 0) return (0).toFixed(digits);
  return v > 0 ? `+${s}` : `${MINUS}${s}`;
}

export function fmtNum(v: number, digits = 1): string {
  const s = v.toFixed(digits);
  return v < 0 ? `${MINUS}${s.slice(1)}` : s;
}

export function fmtPct(p: number): string {
  const pct = p * 100;
  return pct >= 10 ? `${Math.round(pct)}%` : `${pct.toFixed(1)}%`;
}

/** ASCII fixed-point for SVG path data (not display copy; keeps `-` not U+2212). */
export function svgCoord(v: number, digits = 1): string {
  return v.toFixed(digits);
}
