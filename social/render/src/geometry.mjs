/**
 * Copied geometry helpers from webapp/site/src/lib/game-detail/geometry.ts
 * Source commit: 98124ba54cc09d6d26c54f29c3501ab45d8f1db7
 * Keep visual output identical (bands, gaps, scales).
 */

export const CHART_VIEW_WIDTH = 320;
export const CHART_VIEW_HEIGHT = 200;

const DEFAULT_PAD = { left: 8, right: 8, top: 10, bottom: 10 };

export function dimPoints(points, dimension) {
  return points.map((p) =>
    dimension === "off"
      ? { week: p.week, mean: p.off_epa, sd: p.off_sd }
      : { week: p.week, mean: p.def_epa, sd: p.def_sd },
  );
}

export function contiguousSegments(points) {
  const segments = [];
  let current = [];
  for (const point of points) {
    const prev = current[current.length - 1];
    if (prev && point.week !== prev.week + 1) {
      segments.push(current);
      current = [];
    }
    current.push(point);
  }
  if (current.length) segments.push(current);
  return segments;
}

/**
 * @param {Array<Array<{off_epa:number,def_epa:number,off_sd:number,def_sd:number}>>} series
 * @param {{ pad?: number, forceZero?: boolean }} [options]
 */
export function yDomain(series, options = {}) {
  const padFrac = options.pad ?? 0.12;
  const forceZero = options.forceZero ?? false;
  let min = Infinity;
  let max = -Infinity;
  for (const points of series) {
    for (const p of points) {
      min = Math.min(min, p.off_epa - p.off_sd, p.def_epa - p.def_sd);
      max = Math.max(max, p.off_epa + p.off_sd, p.def_epa + p.def_sd);
    }
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) return { min: -0.2, max: 0.2 };
  if (forceZero) {
    min = Math.min(min, 0);
    max = Math.max(max, 0);
  }
  if (min === max) return { min: min - 0.05, max: max + 0.05 };
  const pad = (max - min) * padFrac;
  return { min: min - pad, max: max + pad };
}

/**
 * @param {number} xMin
 * @param {number} xMax
 * @param {number} yMin
 * @param {number} yMax
 * @param {number} viewW
 * @param {number} viewH
 * @param {{ left?: number, right?: number, top?: number, bottom?: number }} [pad]
 */
export function makeScalesForView(xMin, xMax, yMin, yMax, viewW, viewH, pad = DEFAULT_PAD) {
  const pl = pad.left ?? DEFAULT_PAD.left;
  const pr = pad.right ?? DEFAULT_PAD.right;
  const pt = pad.top ?? DEFAULT_PAD.top;
  const pb = pad.bottom ?? DEFAULT_PAD.bottom;
  const xSpan = Math.max(1, xMax - xMin);
  const ySpan = Math.max(1e-6, yMax - yMin);
  const innerW = viewW - pl - pr;
  const innerH = viewH - pt - pb;
  return {
    xMin,
    xMax,
    yMin,
    yMax,
    viewW,
    viewH,
    pad: { left: pl, right: pr, top: pt, bottom: pb },
    x: (week) => pl + ((week - xMin) / xSpan) * innerW,
    y: (epa) => pt + ((yMax - epa) / ySpan) * innerH,
  };
}

export function makeScales(xMin, xMax, yMin, yMax) {
  return makeScalesForView(xMin, xMax, yMin, yMax, CHART_VIEW_WIDTH, CHART_VIEW_HEIGHT);
}

function niceStep(raw) {
  const exp = Math.floor(Math.log10(raw));
  const base = raw / 10 ** exp;
  let nice;
  if (base <= 1) nice = 1;
  else if (base <= 2) nice = 2;
  else if (base <= 5) nice = 5;
  else nice = 10;
  return nice * 10 ** exp;
}

export function niceTicks(min, max, target = 4) {
  if (min === max) return [min];
  const step = niceStep((max - min) / Math.max(1, target - 1));
  const start = Math.floor(min / step) * step;
  const end = Math.ceil(max / step) * step;
  const ticks = [];
  const n = Math.round((end - start) / step);
  for (let i = 0; i <= n; i += 1) {
    const value = Number((start + i * step).toFixed(6));
    if (value >= min - 1e-12 && value <= max + 1e-12) ticks.push(value);
  }
  return ticks.length ? ticks : [min, max];
}

function fmt(n) {
  return n.toFixed(2);
}

function linePathFor(points, scales) {
  if (!points.length) return "";
  return points
    .map((p, i) => `${i === 0 ? "M" : "L"}${fmt(scales.x(p.week))},${fmt(scales.y(p.mean))}`)
    .join(" ");
}

function bandPathFor(points, scales) {
  if (!points.length) return "";
  if (points.length === 1) {
    const p = points[0];
    const x = fmt(scales.x(p.week));
    return `M${x},${fmt(scales.y(p.mean + p.sd))} L${x},${fmt(scales.y(p.mean - p.sd))}`;
  }
  const top = points.map((p) => `${fmt(scales.x(p.week))},${fmt(scales.y(p.mean + p.sd))}`);
  const bot = [...points]
    .reverse()
    .map((p) => `${fmt(scales.x(p.week))},${fmt(scales.y(p.mean - p.sd))}`);
  return `M${top[0]} L${top.slice(1).join(" L")} L${bot.join(" L")} Z`;
}

export function pathsForSeries(points, dimension, scales) {
  const dim = dimPoints(points, dimension);
  const byWeek = new Map(dim.map((p) => [p.week, p]));
  const segments = contiguousSegments(points);
  const line = [];
  const band = [];
  for (const segment of segments) {
    const pts = segment.map((p) => byWeek.get(p.week)).filter(Boolean);
    const lp = linePathFor(pts, scales);
    const bp = bandPathFor(pts, scales);
    if (lp) line.push(lp);
    if (bp) band.push(bp);
  }
  return { line, band };
}

export function xTickWeeks(xMin, xMax) {
  const weeks = [];
  for (let w = xMin; w <= xMax; w += 1) weeks.push(w);
  return weeks;
}
