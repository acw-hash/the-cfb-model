import type { RatingDimension, RatingPoint } from "./ratings";
import { contiguousSegments } from "./ratings";

export const CHART_VIEW_WIDTH = 320;
export const CHART_VIEW_HEIGHT = 200;

const PAD_LEFT = 8;
const PAD_RIGHT = 8;
const PAD_TOP = 10;
const PAD_BOTTOM = 10;

export interface ChartScales {
  x: (week: number) => number;
  y: (epa: number) => number;
  yMin: number;
  yMax: number;
  xMin: number;
  xMax: number;
}

export interface DimPoint {
  week: number;
  mean: number;
  sd: number;
}

export function dimPoints(points: RatingPoint[], dimension: RatingDimension): DimPoint[] {
  return points.map((p) =>
    dimension === "off"
      ? { week: p.week, mean: p.off_epa, sd: p.off_sd }
      : { week: p.week, mean: p.def_epa, sd: p.def_sd },
  );
}

/** Shared Y domain across both teams and both EPA dimensions (mean ± 1 SD). */
export function yDomain(series: RatingPoint[][]): { min: number; max: number } {
  let min = Infinity;
  let max = -Infinity;
  for (const points of series) {
    for (const p of points) {
      min = Math.min(min, p.off_epa - p.off_sd, p.def_epa - p.def_sd);
      max = Math.max(max, p.off_epa + p.off_sd, p.def_epa + p.def_sd);
    }
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) {
    return { min: -0.2, max: 0.2 };
  }
  if (min === max) {
    return { min: min - 0.05, max: max + 0.05 };
  }
  const pad = (max - min) * 0.12;
  return { min: min - pad, max: max + pad };
}

export function makeScales(xMin: number, xMax: number, yMin: number, yMax: number): ChartScales {
  const xSpan = Math.max(1, xMax - xMin);
  const ySpan = Math.max(1e-6, yMax - yMin);
  const innerW = CHART_VIEW_WIDTH - PAD_LEFT - PAD_RIGHT;
  const innerH = CHART_VIEW_HEIGHT - PAD_TOP - PAD_BOTTOM;
  return {
    xMin,
    xMax,
    yMin,
    yMax,
    x: (week: number) => PAD_LEFT + ((week - xMin) / xSpan) * innerW,
    y: (epa: number) => PAD_TOP + ((yMax - epa) / ySpan) * innerH,
  };
}

function niceStep(raw: number): number {
  const exp = Math.floor(Math.log10(raw));
  const base = raw / 10 ** exp;
  let nice: number;
  if (base <= 1) {
    nice = 1;
  } else if (base <= 2) {
    nice = 2;
  } else if (base <= 5) {
    nice = 5;
  } else {
    nice = 10;
  }
  return nice * 10 ** exp;
}

/** Quiet Y ticks — labels only, no gridline clutter (§4.3). */
export function niceTicks(min: number, max: number, target = 4): number[] {
  if (min === max) {
    return [min];
  }
  const step = niceStep((max - min) / Math.max(1, target - 1));
  const start = Math.floor(min / step) * step;
  const end = Math.ceil(max / step) * step;
  const ticks: number[] = [];
  const n = Math.round((end - start) / step);
  for (let i = 0; i <= n; i += 1) {
    const value = start + i * step;
    ticks.push(Number(value.toPrecision(6)));
  }
  return ticks;
}

function linePathFor(points: DimPoint[], scales: ChartScales): string {
  if (points.length === 0) {
    return "";
  }
  return points
    .map((p, i) => {
      const cmd = i === 0 ? "M" : "L";
      return `${cmd}${fmt(scales.x(p.week))},${fmt(scales.y(p.mean))}`;
    })
    .join(" ");
}

function bandPathFor(points: DimPoint[], scales: ChartScales): string {
  if (points.length === 0) {
    return "";
  }
  if (points.length === 1) {
    const p = points[0];
    const x = fmt(scales.x(p.week));
    const yHi = fmt(scales.y(p.mean + p.sd));
    const yLo = fmt(scales.y(p.mean - p.sd));
    return `M${x},${yHi} L${x},${yLo}`;
  }
  const top = points.map((p) => `${fmt(scales.x(p.week))},${fmt(scales.y(p.mean + p.sd))}`);
  const bot = [...points]
    .reverse()
    .map((p) => `${fmt(scales.x(p.week))},${fmt(scales.y(p.mean - p.sd))}`);
  return `M${top[0]} L${top.slice(1).join(" L")} L${bot.join(" L")} Z`;
}

function fmt(n: number): string {
  return n.toFixed(2);
}

export interface SeriesPaths {
  line: string[];
  band: string[];
}

/**
 * Build SVG path strings per contiguous week run. A missing mid-season week
 * yields a new `M` command — the line is never drawn across the gap.
 */
export function pathsForSeries(
  points: RatingPoint[],
  dimension: RatingDimension,
  scales: ChartScales,
): SeriesPaths {
  const dim = dimPoints(points, dimension);
  const byWeek = new Map(dim.map((p) => [p.week, p]));
  const segments = contiguousSegments(points);
  const line: string[] = [];
  const band: string[] = [];
  for (const segment of segments) {
    const pts = segment.map((p) => byWeek.get(p.week)).filter((p): p is DimPoint => p != null);
    const lp = linePathFor(pts, scales);
    const bp = bandPathFor(pts, scales);
    if (lp) {
      line.push(lp);
    }
    if (bp) {
      band.push(bp);
    }
  }
  return { line, band };
}

export function xTickWeeks(xMin: number, xMax: number): number[] {
  const weeks: number[] = [];
  for (let w = xMin; w <= xMax; w += 1) {
    weeks.push(w);
  }
  return weeks;
}
