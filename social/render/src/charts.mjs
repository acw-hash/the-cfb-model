/**
 * Spec → HTML for SOC-2 charts. Numbers come only from the Python spec JSON.
 * Layout sizes are CSS px at 1×; cli.mjs screenshots at deviceScaleFactor 2.
 */
import {
  formatEpa,
  formatMargin,
  formatProbability,
  formatSignedWhole,
} from "./format.mjs";
import {
  makeScalesForView,
  niceTicks,
  pathsForSeries,
  xTickWeeks,
  yDomain,
} from "./geometry.mjs";

const TYPE = {
  title: "font-size:34px;line-height:40px;font-weight:600;",
  dek: "font-size:17px;line-height:22px;font-weight:400;",
  label: "font-size:15px;line-height:20px;",
  figure: "font-size:16px;line-height:20px;font-variant-numeric:tabular-nums;",
  footer: "font-size:12px;line-height:16px;",
};

const NOWRAP = "white-space:nowrap;";
const TRAJ_RIGHT_GUTTER = 72;

function esc(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function fileUrl(p) {
  if (!p) return "";
  const norm = String(p).replace(/\\/g, "/");
  return norm.startsWith("file:") ? norm : `file://${norm}`;
}

function contentWidth(f) {
  return f.width - 80;
}

function calloutSvg(callout, t, toPx) {
  if (!callout) return "";
  const [ax, ay] = toPx(callout.anchor_x, callout.anchor_y);
  const [lx, ly] = toPx(callout.label_x, callout.label_y);
  return `<g data-role="callout-group">
    <line x1="${ax}" y1="${ay}" x2="${lx}" y2="${ly}" stroke="${t.accent}" stroke-width="1.25" />
    <circle cx="${ax}" cy="${ay}" r="3" fill="${t.accent}" />
    <text x="${lx}" y="${ly}" text-anchor="middle" dominant-baseline="middle"
      font-size="15" font-weight="600" fill="${t.text_primary}" style="${NOWRAP}">${esc(callout.label)}</text>
  </g>`;
}

function tickLabelFor(axes, v, idx) {
  const labels = axes.x_tick_labels;
  if (Array.isArray(labels) && labels[idx] != null && labels[idx] !== "") {
    return String(labels[idx]);
  }
  if (v === 0) return axes.zero_label || "0";
  return formatSignedWhole(v) ?? formatMargin(v, null) ?? String(v);
}

function xTicksOutside(axes, plot, t, xOf, tickBelow = 5, labelBelow = 18) {
  const ticks = axes.x_ticks && axes.x_ticks.length ? axes.x_ticks : niceTicks(axes.x_min, axes.x_max, 4);
  let out = "";
  ticks.forEach((v, idx) => {
    const x = xOf(v);
    const label = tickLabelFor(axes, v, idx);
    out += `<line data-role="axis-tick" data-axis="x" x1="${x}" y1="${plot.y + plot.h}" x2="${x}" y2="${plot.y + plot.h + tickBelow}" stroke="${t.border}" />`;
    out += `<text data-role="axis-tick" data-axis="x" x="${x}" y="${plot.y + plot.h + labelBelow}" text-anchor="middle" font-size="15" fill="${t.text_tertiary}" style="${NOWRAP}">${esc(label)}</text>`;
  });
  return out;
}

function frameChrome(spec, inner) {
  const f = spec.frame;
  const t = f.tokens;
  return `
<div id="root" class="chart-root" data-theme="${esc(f.theme)}" style="
  width:${f.width}px;height:${f.height}px;
  background:${t.bg_primary};color:${t.text_primary};
  font-family: var(--font-stack);
  position:relative;overflow:hidden;
  font-variant-numeric: tabular-nums lining-nums;
  display:flex;flex-direction:column;
  padding:24px 40px 20px;
  box-sizing:border-box;
">
  <header class="hdr" data-role="header" style="display:flex;align-items:baseline;gap:12px;flex:0 0 auto;margin-bottom:4px;${NOWRAP}">
    <div style="font-weight:600;letter-spacing:0.14em;font-size:15px;line-height:20px;">${esc(f.wordmark || "RIDGE")}</div>
    <div style="margin-left:auto;color:${t.text_secondary};${TYPE.label}">${esc(f.week_stamp)}</div>
  </header>
  <div data-role="titles" style="flex:0 0 auto;padding:4px 0 6px;">
    <h1 style="margin:0;${TYPE.title}${NOWRAP}">${esc(f.title)}</h1>
    <p data-role="dek" data-allow-wrap="${spec.chart === "readmap" || spec.chart === "movers" ? "1" : "0"}" style="margin:4px 0 0;color:${t.text_secondary};${TYPE.dek}${spec.chart === "readmap" || spec.chart === "movers" ? "white-space:normal;" : NOWRAP}">${esc(f.subtitle)}</p>
  </div>
  <main data-role="body" style="flex:1 1 auto;min-height:0;display:flex;flex-direction:column;justify-content:stretch;margin:0;overflow:hidden;">
    ${inner}
  </main>
  <footer data-role="footer" style="flex:0 0 auto;padding-top:6px;${TYPE.footer}color:${t.text_tertiary};${NOWRAP}">
    ${esc(f.footer)}
  </footer>
</div>`;
}

function dataToPx(plot, axes, x, y) {
  const xSpan = axes.x_max - axes.x_min;
  const ySpan = axes.y_max - axes.y_min;
  const px = plot.x + ((x - axes.x_min) / xSpan) * plot.w;
  const py = axes.y_inverted
    ? plot.y + ((y - axes.y_min) / ySpan) * plot.h
    : plot.y + ((axes.y_max - y) / ySpan) * plot.h;
  return [px, py];
}

function logoBox(p, fallback) {
  const w = p.logo_display_w ?? p.logo_w_px ?? fallback;
  const h = p.logo_display_h ?? p.logo_h_px ?? fallback;
  return { w, h };
}

function logoLabelPos(lx, ly, sizeW, plot) {
  const right = lx >= plot.x + plot.w * 0.5;
  return {
    x: right ? lx + sizeW / 2 + 8 : lx - sizeW / 2 - 8,
    anchor: right ? "start" : "end",
    y: ly,
  };
}

function deltaLabel(raw) {
  if (raw == null) return "";
  return String(raw).replace(/\s*pts\s*$/i, "");
}

export function renderReadmap(spec) {
  const f = spec.frame;
  const t = f.tokens;
  const plotRaw = spec.plot;
  const axes = spec.axes;
  const cw = contentWidth(f);
  const plot = {
    ...plotRaw,
    w: Math.min(Number(plotRaw.w), Math.max(40, cw - Number(plotRaw.x) - 4)),
    h: Number(plotRaw.h),
    x: Number(plotRaw.x),
    y: Number(plotRaw.y),
  };
  const midX = axes.x_zero != null ? axes.x_zero : 0;
  const midY = axes.y_mid != null ? axes.y_mid : 50;
  const [zx] = dataToPx(plot, axes, midX, axes.y_min);
  const xGutter = Number(plotRaw.bottom_axis_gutter || 56);
  const yGutter = Number(plotRaw.y_gutter || 118);
  const svgH = plot.y + plot.h + xGutter;

  let marks = "";
  for (const p of spec.points || []) {
    const [tx, ty] = dataToPx(plot, axes, p.true_x, p.true_y);
    const [lx, ly] = dataToPx(plot, axes, p.logo_x, p.logo_y);
    const { w: sizeW, h: sizeH } = logoBox(p, 28);

    if (p.draw_leader) {
      marks += `<line data-role="leader" x1="${tx}" y1="${ty}" x2="${lx}" y2="${ly}" stroke="${t.border}" stroke-width="1" />`;
    }

    const logoImg = p.logo_path
      ? `<image class="logo" data-team="${p.team_id}" href="${fileUrl(p.logo_path)}" x="${lx - sizeW / 2}" y="${ly - sizeH / 2}" width="${sizeW}" height="${sizeH}" />`
      : "";
    if (logoImg) {
      marks += `<g data-role="logo-label-group" data-draw-leader="${p.draw_leader ? "1" : "0"}" data-team="${p.team_id}">${logoImg}</g>`;
    }
  }

  const [, yTop] = dataToPx(plot, axes, axes.x_min, axes.y_max);
  const [, yBot] = dataToPx(plot, axes, axes.x_min, axes.y_min);
  const topLines = axes.y_top_lines || [];
  const botLines = axes.y_bottom_lines || [];
  const endX = plot.x - 10;
  const yTicks = Array.isArray(axes.y_ticks) ? axes.y_ticks : [];
  const yTickLabels = Array.isArray(axes.y_tick_labels) ? axes.y_tick_labels : [];
  let yTickSvg = "";
  let yGridSvg = "";
  // Certainty-percentile ticks with ±pts SD labels + faint gridlines.
  yTicks.forEach((yv, idx) => {
    const [, py] = dataToPx(plot, axes, axes.x_min, yv);
    const isMid = Math.abs(Number(yv) - Number(midY)) < 1e-6;
    const opacity = isMid ? 1 : 0.28;
    yGridSvg += `<line data-role="gridline" data-axis="y" data-mid="${isMid ? "1" : "0"}" x1="${plot.x}" y1="${py}" x2="${plot.x + plot.w}" y2="${py}" stroke="${t.border}" stroke-opacity="${opacity}" />`;
    yTickSvg += `<line data-role="axis-tick" data-axis="y" x1="${plot.x - 5}" y1="${py}" x2="${plot.x}" y2="${py}" stroke="${t.border}" />`;
    const lab = yTickLabels[idx] != null ? String(yTickLabels[idx]) : "";
    if (lab) {
      yTickSvg += `<text data-role="axis-tick" data-axis="y" x="${endX}" y="${py}" text-anchor="end" dominant-baseline="middle" font-size="13" fill="${t.text_tertiary}" style="${NOWRAP}">${esc(lab)}</text>`;
    }
  });
  // "Most sure" / "Least sure" sit just outside the end tick labels (muted).
  if (topLines[0]) {
    yTickSvg += `<text data-role="axis-end-label" data-axis="y" data-end="top" x="${endX}" y="${yTop - 12}" text-anchor="end" dominant-baseline="auto" font-size="12" fill="${t.text_tertiary}" style="${NOWRAP}">${esc(topLines[0])}</text>`;
  }
  if (botLines[0]) {
    yTickSvg += `<text data-role="axis-end-label" data-axis="y" data-end="bottom" x="${endX}" y="${yBot + 14}" text-anchor="end" dominant-baseline="hanging" font-size="12" fill="${t.text_tertiary}" style="${NOWRAP}">${esc(botLines[0])}</text>`;
  }

  // Outside-plot labels (no text inside the plot frame).
  // Top row: Known struggles | average | Proven good (aligned to frame edges / x=0).
  // Bottom row (below x-tick labels, never sharing that row): Hard to read | x-title | Good, still proving it.
  const byId = Object.fromEntries((spec.quadrants || []).map((q) => [q.id, q]));
  const midLabel = axes.zero_midline_label || "average";
  const topRowY = Math.max(12, plot.y - 8);
  const tickBelow = 5;
  const labelBelow = 20;
  const outsideBottomY = plot.y + plot.h + Math.min(44, xGutter - 8);
  const xTickBlock = xTicksOutside(
    axes,
    plot,
    t,
    (v) => dataToPx(plot, axes, v, axes.y_min)[0],
    tickBelow,
    labelBelow,
  );
  const yTitle = axes.y_label || "How sure Ridge is about each rating";
  const yTitleX = Math.max(16, plot.x - yGutter + 22);
  const yTitleY = plot.y + plot.h / 2;
  const qKnown = byId.known_struggles?.label || "Known struggles";
  const qProven = byId.proven_good?.label || "Proven good";
  const qHard = byId.hard_to_read?.label || "Hard to read";
  const qGood = byId.good_proving?.label || "Good, still proving it";
  const outsideLabels = `
  <g data-role="outside-label-row" data-side="top">
    <text data-role="quadrant-label" data-quad="known_struggles" data-placement="outside" x="${plot.x}" y="${topRowY}" text-anchor="start" dominant-baseline="auto" font-size="13" fill="${t.text_tertiary}" style="${NOWRAP}">${esc(qKnown)}</text>
    <text data-role="zero-midline-label" data-placement="outside" x="${zx}" y="${topRowY}" text-anchor="middle" dominant-baseline="auto" font-size="13" fill="${t.text_tertiary}" style="${NOWRAP}">${esc(midLabel)}</text>
    <text data-role="quadrant-label" data-quad="proven_good" data-placement="outside" x="${plot.x + plot.w}" y="${topRowY}" text-anchor="end" dominant-baseline="auto" font-size="13" fill="${t.text_tertiary}" style="${NOWRAP}">${esc(qProven)}</text>
  </g>
  <g data-role="outside-label-row" data-side="bottom">
    <text data-role="quadrant-label" data-quad="hard_to_read" data-placement="outside" x="${plot.x}" y="${outsideBottomY}" text-anchor="start" dominant-baseline="hanging" font-size="13" fill="${t.text_tertiary}" style="${NOWRAP}">${esc(qHard)}</text>
    <text data-role="axis-title-x" x="${plot.x + plot.w / 2}" y="${outsideBottomY}" text-anchor="middle" dominant-baseline="hanging" font-size="14" fill="${t.text_secondary}" style="${NOWRAP}">${esc(axes.x_label)}</text>
    <text data-role="quadrant-label" data-quad="good_proving" data-placement="outside" x="${plot.x + plot.w}" y="${outsideBottomY}" text-anchor="end" dominant-baseline="hanging" font-size="13" fill="${t.text_tertiary}" style="${NOWRAP}">${esc(qGood)}</text>
  </g>`;

  // Fill dek→footer: no vertical centering (empty bands must stay ≤6% of canvas).
  const inner = `
<div data-role="chart-group" style="flex:1 1 auto;min-height:0;display:flex;flex-direction:column;justify-content:flex-start;align-items:stretch;">
<svg class="plot" data-chart="readmap" width="${cw}" height="${svgH}" viewBox="0 0 ${cw} ${svgH}"
  style="flex:0 0 auto;display:block;width:${cw}px;height:${svgH}px;max-width:${cw}px;max-height:${svgH}px;">
  <line x1="${zx}" y1="${plot.y}" x2="${zx}" y2="${plot.y + plot.h}" stroke="${t.border}" />
  ${yGridSvg}
  <rect x="${plot.x}" y="${plot.y}" width="${plot.w}" height="${plot.h}" fill="none" stroke="${t.border}" />
  ${outsideLabels}
  <g data-role="axis-block">${yTickSvg}${xTickBlock}
  <text data-role="axis-title-y" data-rotated="1" transform="translate(${yTitleX}, ${yTitleY}) rotate(-90)" text-anchor="middle" font-size="14" fill="${t.text_secondary}" style="${NOWRAP}">${esc(yTitle)}</text>
  </g>
  ${marks}
</svg>
</div>`;
  return frameChrome(spec, inner);
}

export function renderMovers(spec) {
  const f = spec.frame;
  const t = f.tokens;
  const axes = spec.axes;
  const cw = contentWidth(f);
  const isWide = f.size === "16x9";
  const maxRows = isWide ? 6 : 99;
  let groups = spec.groups || [{ title: null, rows: spec.rows || [] }];
  if (isWide) {
    const flat = [];
    for (const g of groups) {
      for (const r of g.rows || []) flat.push(r);
    }
    groups = [{ title: null, rows: flat.slice(0, maxRows) }];
  }
  const nRows = groups.reduce((n, g) => n + (g.rows || []).length, 0) || (spec.rows || []).length;
  const nGroupTitles = groups.filter((g) => g.title).length;
  const groupTitleH = 22;
  // Two-line header allowed; value rows never wrap.
  const colHeaderH = 36;
  const axisH = isWide ? 52 : 68;
  const chromeH = isWide ? 155 : 195;
  const slots = nRows + nGroupTitles;
  const availForRows = Math.max(140, f.height - chromeH - axisH - colHeaderH);
  // Fill dek → axis: row height = available / (rows + group headers).
  let rowH = Math.max(
    isWide ? 26 : 34,
    Math.floor(availForRows / Math.max(slots, 1)),
  );
  const usedCore = nRows * rowH + nGroupTitles * groupTitleH;
  const leftover = Math.max(0, availForRows - usedCore);
  const gapSlots = Math.max(nRows - 1 + nGroupTitles, 0);
  const rowGap = gapSlots > 0 ? leftover / gapSlots : 0;

  const teamCol = Math.round(cw * 0.18);
  const plotCol = Math.round(cw * 0.32);
  const deltaCol = Math.round(cw * 0.14);
  const resultCol = Math.round(cw * 0.20);
  const expectedCol = Math.max(64, cw - teamCol - plotCol - deltaCol - resultCol);
  const plotX = teamCol;
  const plotW = plotCol;
  const xSpan = axes.x_max - axes.x_min || 1;
  const xOf = (v) => plotX + ((v - axes.x_min) / xSpan) * plotW;
  const zeroX = xOf(0);
  const deltaX = plotX + plotW;
  const resultX = deltaX + deltaCol;
  const expectedX = resultX + resultCol;
  const expectedW = expectedCol;

  const cols = spec.columns || [];
  const plotHeader =
    (cols.find((c) => c.id === "plot") || {}).header ||
    "Ridge rating: before ○ → after ●";
  const deltaHeader = (cols.find((c) => c.id === "delta") || {}).header || "Rating change";
  const resultHeader = (cols.find((c) => c.id === "result") || {}).header || "Result";
  const expectedHeader =
    (cols.find((c) => c.id === "expected") || {}).header || "Ridge expected";
  const hdrStyle = `${TYPE.dek}color:${t.text_tertiary};line-height:16px;white-space:normal;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;`;

  let colHeaders = `
<div data-role="col-headers" style="position:relative;width:${cw}px;height:${colHeaderH}px;flex:0 0 auto;">
  <div data-allow-wrap="1" data-role="col-header" style="position:absolute;left:${plotX}px;top:0;width:${plotW}px;${hdrStyle}">${esc(plotHeader)}</div>
  <div data-allow-wrap="1" data-role="col-header" style="position:absolute;left:${deltaX}px;top:0;width:${deltaCol}px;font-weight:600;${hdrStyle}">${esc(deltaHeader)}</div>
  <div data-allow-wrap="1" data-role="col-header" style="position:absolute;left:${resultX}px;top:0;width:${resultCol}px;${hdrStyle}">${esc(resultHeader)}</div>
  <div data-allow-wrap="1" data-role="col-header" style="position:absolute;left:${expectedX}px;top:0;width:${expectedW}px;${hdrStyle}">${esc(expectedHeader)}</div>
</div>`;

  const avgLabel = axes.zero_midline_label || "avg";
  let body = "";
  for (const g of groups) {
    if (g.title) {
      body += `<div style="height:${groupTitleH}px;display:flex;align-items:flex-end;${TYPE.label}font-weight:600;color:${t.text_secondary};${NOWRAP}">${esc(g.title)}</div>`;
    }
    for (const r of g.rows || []) {
      const mid = rowH / 2;
      const { w: lw0, h: lh0 } = logoBox(r, 22);
      const maxLogo = Math.max(14, rowH - 8);
      const logoScale = Math.min(1, maxLogo / Math.max(lw0, lh0, 1));
      const lw = Math.round(lw0 * logoScale * 10) / 10;
      const lh = Math.round(lh0 * logoScale * 10) / 10;
      const logo = r.logo_path
        ? `<img class="logo" src="${fileUrl(r.logo_path)}" width="${lw}" height="${lh}" style="width:${lw}px;height:${lh}px;object-fit:contain;" />`
        : "";
      const x0 = xOf(r.net_prev);
      const x1 = xOf(r.net_cur);
      const arrowDx = x1 - x0;
      const tip = 6;
      const ux = arrowDx === 0 ? 0 : arrowDx / Math.abs(arrowDx);
      const ax = x1 - ux * 5;
      const ay = mid;
      const arrowHead =
        Math.abs(arrowDx) > 10
          ? `<polygon points="${x1},${ay} ${ax - ux * tip},${ay - 3.5} ${ax - ux * tip},${ay + 3.5}" fill="${t.text_secondary}" />`
          : "";
      body += `
<div class="movers-row learned-row" data-role="logo-label-group" data-team="${r.team_id}" style="position:relative;height:${rowH}px;width:${cw}px;margin-bottom:${rowGap}px;flex:0 0 auto;">
  <div style="position:absolute;left:0;top:50%;transform:translateY(-50%);display:flex;align-items:center;gap:6px;width:${teamCol - 4}px;${NOWRAP}">
    ${logo}<span style="font-weight:600;${TYPE.label}">${esc(r.short_name || r.abbreviation)}</span>
  </div>
  <svg data-marks="1" width="${cw}" height="${rowH}" viewBox="0 0 ${cw} ${rowH}" style="position:absolute;left:0;top:0;display:block;">
    <line x1="${zeroX}" y1="2" x2="${zeroX}" y2="${rowH - 2}" stroke="${t.border}" stroke-width="1" />
    <line x1="${x0}" y1="${mid}" x2="${x1}" y2="${mid}" stroke="${t.text_secondary}" stroke-width="2" />
    ${arrowHead}
    <circle class="mu-dot" data-role="before-dot" cx="${x0}" cy="${mid}" r="9" fill="${t.bg_primary}" stroke="${t.text_primary}" stroke-width="1.75" />
    <circle class="mu-dot" data-role="after-dot" cx="${x1}" cy="${mid}" r="9" fill="${t.accent}" />
  </svg>
  <div style="position:absolute;left:${deltaX}px;top:50%;transform:translateY(-50%);width:${deltaCol}px;overflow:hidden;${NOWRAP}">
    <span style="font-weight:700;${TYPE.figure}">${esc(deltaLabel(r.delta_net_label))}</span>
  </div>
  <div style="position:absolute;left:${resultX}px;top:50%;transform:translateY(-50%);width:${resultCol}px;overflow:hidden;${TYPE.label}color:${t.text_secondary};${NOWRAP}">${esc(r.result_col || r.result_label || "")}</div>
  <div style="position:absolute;left:${expectedX}px;top:50%;transform:translateY(-50%);width:${expectedW}px;overflow:hidden;${TYPE.label}color:${t.text_secondary};${NOWRAP}">${esc(r.expected_col || r.pregame_label || "")}</div>
</div>`;
    }
  }

  const ticks = axes.x_ticks && axes.x_ticks.length ? axes.x_ticks : niceTicks(axes.x_min, axes.x_max, 4);
  let tickRow = `<div data-role="axis-block" style="position:relative;height:22px;margin-top:2px;width:${cw}px;flex:0 0 auto;">`;
  ticks.forEach((v, idx) => {
    const x = xOf(v);
    const label = tickLabelFor({ ...axes, zero_label: "0" }, v, idx);
    tickRow += `<span data-role="axis-tick" style="position:absolute;left:${x}px;transform:translateX(-50%);${TYPE.label}color:${t.text_tertiary};${NOWRAP}">${esc(label)}</span>`;
  });
  // "avg" sits on the 0 rule once (not as a tick duplicate).
  tickRow += `<span data-role="zero-midline-label" style="position:absolute;left:${zeroX}px;top:-14px;transform:translateX(-50%);font-size:12px;line-height:14px;color:${t.text_tertiary};${NOWRAP}">${esc(avgLabel)}</span>`;
  tickRow += `</div>`;
  tickRow += `<div style="margin-top:2px;flex:0 0 auto;${TYPE.label}color:${t.text_secondary};${NOWRAP}">${esc(axes.x_label)}</div>`;

  const inner = `
<div style="flex:1 1 auto;min-height:0;display:flex;flex-direction:column;justify-content:flex-start;">
  ${colHeaders}
  <div style="flex:1 1 auto;min-height:0;display:flex;flex-direction:column;justify-content:flex-start;">${body}</div>
  <div style="flex:0 0 auto;padding-top:4px;">${tickRow}</div>
</div>`;
  return frameChrome(spec, inner);
}

/** @deprecated alias — chart type renamed to movers in SOC-2 round 5 */
export function renderLearned(spec) {
  return renderMovers(spec);
}

export function renderSlateRanges(spec) {
  const f = spec.frame;
  const t = f.tokens;
  const axes = spec.axes;
  const layout = spec.layout || {};
  const cw = contentWidth(f);
  const isWide = f.size === "16x9";
  const allRows = spec.rows || [];
  const rowList = isWide ? allRows.slice(0, Math.min(8, allRows.length)) : allRows;
  const nRows = rowList.length;
  const axisH = isWide ? 40 : 48;
  const chromeH = isWide ? 155 : 190;
  const availBody = Math.max(160, f.height - chromeH - axisH);
  const rowH = Math.max(
    isWide ? 26 : 36,
    Math.min(isWide ? 36 : 48, Math.floor(availBody / Math.max(nRows, 1))),
  );
  const colMatch = Math.round(cw * (layout.matchup_frac || 0.28));
  const colBars = Math.round(cw * (layout.bars_frac || 0.3));
  const colRidge = Math.round(cw * (layout.ridge_frac || 0.15));
  const colMarket = Math.round(cw * (layout.market_frac || 0.15));
  const colGap = cw - colMatch - colBars - colRidge - colMarket;
  const plotX = colMatch;
  const plotW = colBars;
  const xSpan = axes.x_max - axes.x_min || 1;
  const xOf = (v) => plotX + ((v - axes.x_min) / xSpan) * plotW;
  const zeroX = xOf(0);
  const axisCaption =
    axes.caption || axes.x_label || "Projected margin for market favorite (pts)";
  const barHalf = Math.max(3, Math.min(6, rowH * 0.22));
  const labelFont = rowH < 34 ? "font-size:13px;line-height:16px;" : TYPE.label;

  let header = `
<div style="display:flex;width:${cw}px;height:18px;flex:0 0 auto;${TYPE.label}color:${t.text_tertiary};${NOWRAP}">
  <div style="width:${colMatch}px;"></div>
  <div style="width:${colBars}px;"></div>
  <div style="width:${colRidge}px;text-align:center;">Ridge</div>
  <div style="width:${colMarket}px;text-align:center;">Market</div>
  <div style="width:${colGap}px;text-align:center;">Gap</div>
</div>`;

  let rows = "";
  rowList.forEach((r) => {
    const mid = rowH / 2;
    const barX = xOf(r.bar_lo);
    const barW = Math.max(2, xOf(r.bar_hi) - barX);
    const ridgeX = xOf(r.x_ridge ?? r.mu);
    const mktX = xOf(r.x_mkt ?? r.x_ridge);
    const fill = r.bar_fill || t.text_tertiary;
    const { w: lw0, h: lh0 } = logoBox(
      { logo_display_w: r.fav_logo_w, logo_display_h: r.fav_logo_h },
      18,
    );
    const maxLogo = Math.max(12, rowH - 6);
    const logoScale = Math.min(1, maxLogo / Math.max(lw0, lh0, 1));
    const scaleLogo = (w, h) => ({
      w: Math.round((w ?? lw0) * logoScale * 10) / 10,
      h: Math.round((h ?? lh0) * logoScale * 10) / 10,
    });
    const favSz = scaleLogo(r.fav_logo_w, r.fav_logo_h);
    const dogSz = scaleLogo(r.dog_logo_w, r.dog_logo_h);
    const fav = r.fav_logo
      ? `<img class="logo" src="${fileUrl(r.fav_logo)}" width="${favSz.w}" height="${favSz.h}" style="width:${favSz.w}px;height:${favSz.h}px;object-fit:contain;" />`
      : "";
    const dog = r.dog_logo
      ? `<img class="logo" src="${fileUrl(r.dog_logo)}" width="${dogSz.w}" height="${dogSz.h}" style="width:${dogSz.w}px;height:${dogSz.h}px;object-fit:contain;" />`
      : "";
    const connStroke = r.gap_strong ? t.text_primary : t.text_tertiary;
    const connW = r.gap_strong ? 1.75 : 1;
    rows += `
<div class="slate-row" data-game="${esc(r.game_id)}" style="position:relative;height:${rowH}px;width:${cw}px;flex:0 0 auto;">
  <div style="position:absolute;left:0;top:50%;transform:translateY(-50%);display:flex;align-items:center;gap:4px;width:${colMatch}px;${labelFont}${NOWRAP}">
    ${fav}${dog}<span>${esc(r.label)}</span>
  </div>
  <svg data-marks="1" width="${cw}" height="${rowH}" viewBox="0 0 ${cw} ${rowH}" style="position:absolute;left:0;top:0;display:block;">
    <line x1="${zeroX}" y1="2" x2="${zeroX}" y2="${rowH - 2}" stroke="${t.border}" />
    <rect class="bar" x="${barX}" y="${mid - barHalf}" width="${barW}" height="${barHalf * 2}" fill="${fill}" fill-opacity="0.28" />
    <line x1="${mktX}" y1="${mid}" x2="${ridgeX}" y2="${mid}" stroke="${connStroke}" stroke-width="${connW}" />
    <polygon data-role="market-diamond" points="${mktX},${mid - 5} ${mktX + 5},${mid} ${mktX},${mid + 5} ${mktX - 5},${mid}" fill="${t.bg_primary}" stroke="${t.text_primary}" stroke-width="1.5" />
    <circle class="mu-dot" data-role="ridge-dot" cx="${ridgeX}" cy="${mid}" r="4.5" fill="${t.text_primary}" />
  </svg>
  <div style="position:absolute;left:${colMatch + colBars}px;top:50%;transform:translateY(-50%);width:${colRidge}px;text-align:center;${labelFont}font-variant-numeric:tabular-nums;${NOWRAP}">${esc(r.ridge_col || "")}</div>
  <div style="position:absolute;left:${colMatch + colBars + colRidge}px;top:50%;transform:translateY(-50%);width:${colMarket}px;text-align:center;${labelFont}font-variant-numeric:tabular-nums;${NOWRAP}">${esc(r.market_col || "")}</div>
  <div style="position:absolute;left:${colMatch + colBars + colRidge + colMarket}px;top:50%;transform:translateY(-50%);width:${colGap}px;text-align:center;${labelFont}font-variant-numeric:tabular-nums;${NOWRAP}">${esc(r.gap_col || "")}</div>
</div>`;
  });

  const ticks = axes.x_ticks && axes.x_ticks.length ? axes.x_ticks : niceTicks(axes.x_min, axes.x_max, 4);
  let tickRow = `<div data-role="axis-block" style="position:relative;height:20px;margin-top:4px;width:${cw}px;flex:0 0 auto;">`;
  ticks.forEach((v, idx) => {
    const x = plotX + ((v - axes.x_min) / xSpan) * plotW;
    const label = tickLabelFor(axes, v, idx);
    tickRow += `<span data-role="axis-tick" style="position:absolute;left:${x}px;transform:translateX(-50%);${TYPE.label}color:${t.text_tertiary};${NOWRAP}">${esc(label)}</span>`;
  });
  tickRow += `</div>`;
  tickRow += `<div style="margin-top:2px;flex:0 0 auto;${TYPE.label}color:${t.text_secondary};${NOWRAP}">${esc(axisCaption)}</div>`;

  const inner = `
<div style="flex:1 1 auto;min-height:0;display:flex;flex-direction:column;justify-content:flex-start;">
  ${header}
  <div style="flex:0 0 auto;display:flex;flex-direction:column;">${rows}</div>
  <div style="flex:1 1 auto;min-height:0;"></div>
  ${tickRow}
</div>`;
  return frameChrome(spec, inner);
}

function resolveStatBlock(numbers, key, fallbacks) {
  const block = numbers[key];
  if (block && typeof block === "object") {
    const label = block.label ?? block.title ?? "";
    const value = block.value ?? block.text ?? "";
    if (label || value) return { label, value };
  }
  for (const fb of fallbacks) {
    if (numbers[fb]) return { label: "", value: numbers[fb] };
  }
  return null;
}

function trajectoryPanel({
  title,
  dimension,
  home,
  away,
  scales,
  throughWeek,
  t,
  endLabels,
  panelCallout,
  panelW,
  panelH,
}) {
  const homePaths = pathsForSeries(home, dimension, scales);
  const awayPaths = pathsForSeries(away, dimension, scales);
  let yTicks = niceTicks(scales.yMin, scales.yMax, 3);
  if (yTicks.length > 3) yTicks = [yTicks[0], yTicks[Math.floor(yTicks.length / 2)], yTicks[yTicks.length - 1]];
  while (yTicks.length < 3 && yTicks.length > 0) {
    yTicks.push(yTicks[yTicks.length - 1]);
  }
  const zeroInRange = scales.yMin < 0 && scales.yMax > 0;
  const weeks = xTickWeeks(scales.xMin, scales.xMax);
  const innerBottom = panelH - (scales.pad?.bottom ?? 28);

  const mark = (points, cls) =>
    points
      .map((p) => {
        const mean = dimension === "off" ? p.off_epa : p.def_epa;
        const current = p.week === throughWeek;
        const fill = current ? t.bg_primary : cls === "home" ? t.text_primary : t.text_secondary;
        const stroke = cls === "home" ? t.text_primary : t.text_secondary;
        return `<circle class="traj-pt" data-week="${p.week}" data-side="${cls}" cx="${scales.x(p.week)}" cy="${scales.y(mean)}" r="3.5" fill="${fill}" stroke="${stroke}" stroke-width="1.5" />`;
      })
      .join("");

  const bands = [
    ...awayPaths.band.map((d) => `<path d="${d}" fill="${t.text_secondary}" fill-opacity="0.1" />`),
    ...homePaths.band.map((d) => `<path d="${d}" fill="${t.text_primary}" fill-opacity="0.1" />`),
  ].join("");
  const lines = [
    ...awayPaths.line.map(
      (d) =>
        `<path d="${d}" fill="none" stroke="${t.text_secondary}" stroke-width="1.5" stroke-dasharray="5 4" />`,
    ),
    ...homePaths.line.map(
      (d) => `<path d="${d}" fill="none" stroke="${t.text_primary}" stroke-width="1.5" />`,
    ),
  ].join("");

  const yLabels = yTicks
    .map((tick) => {
      const y = scales.y(tick);
      return `<text data-role="axis-tick" x="4" y="${y}" dominant-baseline="middle" font-size="15" fill="${t.text_tertiary}" style="${NOWRAP}">${esc(formatEpa(tick))}</text>`;
    })
    .join("");

  const xLabels = weeks
    .map((w) => {
      const x = scales.x(w);
      return `<text class="week-tick" data-role="axis-tick" data-week="${w}" x="${x}" y="${innerBottom - 2}" text-anchor="middle" font-size="15" fill="${t.text_tertiary}">${w}</text>`;
    })
    .join("");

  let ends = "";
  for (const el of endLabels || []) {
    if (el.dimension !== dimension) continue;
    const series = el.side === "home" ? home : away;
    const last = series.find((p) => p.week === throughWeek) || series[series.length - 1];
    if (!last) continue;
    const mean = dimension === "off" ? last.off_epa : last.def_epa;
    const x = scales.x(last.week);
    const y = scales.y(mean);
    const logoW = el.logo_w || 16;
    const logoH = el.logo_h || 16;
    const logo = el.logo_path
      ? `<image class="logo" href="${fileUrl(el.logo_path)}" x="${panelW - TRAJ_RIGHT_GUTTER + 4}" y="${y - logoH / 2}" width="${logoW}" height="${logoH}" />`
      : "";
    ends += `<g data-role="logo-label-group">${logo}<text x="${panelW - TRAJ_RIGHT_GUTTER + 4 + logoW + 4}" y="${y}" dominant-baseline="middle" font-size="15" font-weight="600" fill="${t.text_primary}" style="${NOWRAP}">${esc(el.rank_label)}</text></g>`;
  }

  let calloutMark = "";
  if (panelCallout && panelCallout.dimension === dimension && panelCallout.label) {
    const cx = scales.x(throughWeek);
    const cy = scales.y(
      dimension === "off"
        ? (home.find((p) => p.week === throughWeek) || home[home.length - 1])?.off_epa ?? 0
        : (home.find((p) => p.week === throughWeek) || home[home.length - 1])?.def_epa ?? 0,
    );
    const label = String(panelCallout.label || "");
    const shortCallout = label.length > 18 ? `${label.slice(0, 17)}…` : label;
    // Sit just left of the last point so the right gutter stays clear.
    const tx = Math.max(scales.x(scales.xMin) + 4, cx - 8);
    calloutMark = `<text x="${tx}" y="${cy - 10}" text-anchor="end" font-size="13" font-weight="500" fill="${t.accent}" style="${NOWRAP}">${esc(shortCallout)}</text>`;
  }

  return `
<div class="traj-panel" data-dimension="${dimension}" style="flex:1 1 0;min-height:0;display:flex;flex-direction:column;">
  <h3 style="margin:0 0 4px;${TYPE.label}color:${t.text_secondary};font-weight:500;${NOWRAP}">${esc(title)}</h3>
  <svg width="${panelW}" height="${panelH}" viewBox="0 0 ${panelW} ${panelH}" style="width:${panelW}px;height:${panelH}px;max-width:${panelW}px;max-height:${panelH}px;display:block;flex:0 0 auto;">
    ${zeroInRange ? `<line x1="${scales.x(scales.xMin)}" x2="${scales.x(scales.xMax)}" y1="${scales.y(0)}" y2="${scales.y(0)}" stroke="${t.border}" />` : ""}
    ${bands}${lines}
    ${mark(away, "away")}
    ${mark(home, "home")}
    ${yLabels}
    ${xLabels}
    ${ends}
    ${calloutMark}
  </svg>
</div>`;
}

export function renderGamecard(spec) {
  const f = spec.frame;
  const t = f.tokens;
  const h = spec.header;
  const bar = spec.margin_bar || {};
  const db = spec.dumbbells || {};
  const cw = contentWidth(f);
  const isWide = f.size === "16x9";
  const hairline = `border-top:1px solid ${t.border};`;
  const logoSlot = isWide ? 64 : 120;
  const winFs = isWide ? 32 : 64;
  const winLh = isWide ? 36 : 64;
  const nameFs = isWide ? 14 : 17;
  const nameLh = isWide ? 18 : 20;
  const plotH = 14;
  // Bar SVG: room above for μ label, track, market label, end labels under ends.
  const plotY = isWide ? 24 : 30;
  const barH = isWide ? 78 : 96;
  const dbRowH = isWide ? 36 : 44;
  const dbMid = Math.round(dbRowH / 2);
  const rankFs = isWide ? 14 : 17;

  const capHdr = (w, hgt, max = logoSlot) => {
    const ww = w || logoSlot;
    const hh = hgt || logoSlot;
    const s = Math.min(1, max / Math.max(ww, hh));
    return { w: Math.round(ww * s * 10) / 10, h: Math.round(hh * s * 10) / 10 };
  };
  const homeSz = capHdr(h.home_logo_w, h.home_logo_h);
  const awaySz = capHdr(h.away_logo_w, h.away_logo_h);
  // Equal-height logo slots, bottom-aligned.
  const homeLogo = h.home_logo
    ? `<div data-role="logo-slot" style="width:${logoSlot}px;height:${logoSlot}px;display:flex;align-items:flex-end;justify-content:center;"><img class="logo" src="${fileUrl(h.home_logo)}" height="${homeSz.h}" width="${homeSz.w}" style="height:${homeSz.h}px;width:${homeSz.w}px;object-fit:contain;" /></div>`
    : `<div data-role="logo-slot" style="width:${logoSlot}px;height:${logoSlot}px;"></div>`;
  const awayLogo = h.away_logo
    ? `<div data-role="logo-slot" style="width:${logoSlot}px;height:${logoSlot}px;display:flex;align-items:flex-end;justify-content:center;"><img class="logo" src="${fileUrl(h.away_logo)}" height="${awaySz.h}" width="${awaySz.w}" style="height:${awaySz.h}px;width:${awaySz.w}px;object-fit:contain;" /></div>`
    : `<div data-role="logo-slot" style="width:${logoSlot}px;height:${logoSlot}px;"></div>`;

  // Grid: logos | names | win% — shared baselines; @ centered on logo slots.
  const teamsRow = `
<div data-role="teams-row" style="display:grid;grid-template-columns:1fr auto 1fr;column-gap:12px;row-gap:4px;padding:${isWide ? 4 : 6}px 0;align-items:center;flex:0 0 auto;${NOWRAP}">
  <div style="display:flex;justify-content:center;">${awayLogo}</div>
  <div data-role="at-mark" data-allow-wrap="1" style="display:flex;align-items:center;justify-content:center;height:${logoSlot}px;font-size:22px;line-height:24px;font-weight:500;color:${t.text_secondary};">@</div>
  <div style="display:flex;justify-content:center;">${homeLogo}</div>
  <div data-role="team-name" style="text-align:center;font-size:${nameFs}px;line-height:${nameLh}px;font-weight:600;">${esc(h.away_display || h.away_team)}</div>
  <div></div>
  <div data-role="team-name" style="text-align:center;font-size:${nameFs}px;line-height:${nameLh}px;font-weight:600;">${esc(h.home_display || h.home_team)}</div>
  <div data-role="win-pct" style="text-align:center;font-size:${winFs}px;line-height:${winLh}px;font-weight:600;">${esc(h.p_away_label || "—")}</div>
  <div data-role="win-chance-label" style="text-align:center;font-size:13px;line-height:${winLh}px;color:${t.text_tertiary};align-self:center;">win chance</div>
  <div data-role="win-pct" style="text-align:center;font-size:${winFs}px;line-height:${winLh}px;font-weight:600;">${esc(h.p_home_label || "—")}</div>
</div>`;

  const xMin = Number(bar.x_min ?? -20);
  const xMax = Number(bar.x_max ?? 20);
  const xSpan = xMax - xMin || 1;
  const xOf = (v) => ((v - xMin) / xSpan) * cw;
  const zeroX = xOf(0);
  const lo = bar.lo;
  const hi = bar.hi;
  const mu = bar.mu;
  const mkt = bar.market;
  let rangeRect = "";
  if (lo != null && hi != null) {
    const rx = xOf(Math.min(lo, hi));
    const rw = Math.max(2, Math.abs(xOf(hi) - xOf(lo)));
    rangeRect = `<rect data-role="range-bar" x="${rx}" y="${plotY}" width="${rw}" height="${plotH}" fill="${t.text_tertiary}" fill-opacity="0.28" />`;
  }
  let muMark = "";
  if (mu != null) {
    const mx = xOf(mu);
    muMark = `
      <circle data-role="ridge-dot" cx="${mx}" cy="${plotY + plotH / 2}" r="5.5" fill="${t.text_primary}" />
      <text data-role="mu-label" x="${mx}" y="${plotY - 8}" text-anchor="middle" font-size="15" font-weight="600" fill="${t.text_primary}" style="${NOWRAP}">${esc(bar.mu_label || "")}</text>`;
  }
  let mktMark = "";
  if (mkt != null) {
    const kx = xOf(mkt);
    // Market below the bar; μ above — separated vertically so they never overlap.
    mktMark = `
      <polygon data-role="market-diamond" points="${kx},${plotY + plotH / 2 - 5.5} ${kx + 5.5},${plotY + plotH / 2} ${kx},${plotY + plotH / 2 + 5.5} ${kx - 5.5},${plotY + plotH / 2}" fill="${t.bg_primary}" stroke="${t.text_primary}" stroke-width="1.5" />
      <text data-role="market-label" x="${kx}" y="${plotY + plotH + 20}" text-anchor="middle" font-size="14" fill="${t.text_secondary}" style="${NOWRAP}">${esc(bar.market_label || "")}</text>`;
  }
  // Range end labels sit directly under the bar ends.
  const endY = plotY + plotH + 36;
  let endLabs = "";
  if (bar.lo_label && lo != null) {
    endLabs += `<text data-role="lo-label" x="${xOf(lo)}" y="${endY}" text-anchor="middle" font-size="13" fill="${t.text_tertiary}" style="${NOWRAP}">${esc(bar.lo_label)}</text>`;
  }
  if (bar.hi_label && hi != null) {
    endLabs += `<text data-role="hi-label" x="${xOf(hi)}" y="${endY}" text-anchor="middle" font-size="13" fill="${t.text_tertiary}" style="${NOWRAP}">${esc(bar.hi_label)}</text>`;
  }

  const marginBlock = `
<div data-role="margin-bar" style="padding:${isWide ? 4 : 6}px 0 2px;${hairline};flex:0 0 auto;">
  <div style="${TYPE.label}color:${t.text_secondary};margin-bottom:4px;${NOWRAP}">${esc(bar.label || "Projected margin · 80% range")}</div>
  <svg width="${cw}" height="${barH}" viewBox="0 0 ${cw} ${barH}" style="display:block;width:${cw}px;height:${barH}px;">
    ${rangeRect}
    <line data-role="zero-rule" x1="${zeroX}" y1="${plotY - 2}" x2="${zeroX}" y2="${plotY + plotH + 2}" stroke="${t.border}" stroke-width="1" />
    ${muMark}
    ${mktMark}
    ${endLabs}
  </svg>
</div>`;

  const labelW = isWide ? 160 : 200;
  const trackX = labelW + 10;
  const trackW = cw - trackX - 8;
  const dbRows = (db.rows || [])
    .map((row) => {
      const leftPct = Number(row.left_pct ?? 50);
      const rightPct = Number(row.right_pct ?? 50);
      const lx = trackX + (leftPct / 100) * trackW;
      const rx = trackX + (rightPct / 100) * trackW;
      const better = row.better || "tie";
      const leftFill = better === "left" ? t.accent : t.text_tertiary;
      const rightFill = better === "right" ? t.accent : t.text_tertiary;
      const segStroke = better === "tie" ? t.border : t.accent;
      return `
<div data-role="dumbbell-row" style="position:relative;height:${dbRowH}px;width:${cw}px;flex:1 1 0;min-height:${dbRowH}px;margin-bottom:4px;">
  <div style="position:absolute;left:0;top:50%;transform:translateY(-50%);width:${labelW}px;font-size:${rankFs}px;line-height:20px;${NOWRAP}">
    <div style="font-weight:600;">${esc(row.top)}</div>
    <div style="color:${t.text_secondary};">${esc(row.bottom)}</div>
  </div>
  <svg width="${cw}" height="${dbRowH}" viewBox="0 0 ${cw} ${dbRowH}" style="position:absolute;left:0;top:0;display:block;">
    <line x1="${trackX}" y1="${dbMid}" x2="${trackX + trackW}" y2="${dbMid}" stroke="${t.border}" stroke-width="1" />
    <line x1="${lx}" y1="${dbMid}" x2="${rx}" y2="${dbMid}" stroke="${segStroke}" stroke-width="3" />
    <circle cx="${lx}" cy="${dbMid}" r="5" fill="${leftFill}" />
    <circle cx="${rx}" cy="${dbMid}" r="5" fill="${rightFill}" />
  </svg>
</div>`;
    })
    .join("");

  const dumbbellBlock = `
<div data-role="dumbbells" style="padding:${isWide ? 6 : 10}px 0 0;${hairline};flex:1 1 auto;min-height:0;display:flex;flex-direction:column;">
  <div style="font-size:${rankFs}px;line-height:20px;color:${t.text_secondary};margin-bottom:8px;${NOWRAP}">${esc(db.title || "Where it's decided · FBS rank, better →")}</div>
  <div style="flex:1 1 auto;min-height:0;display:flex;flex-direction:column;justify-content:space-evenly;">${dbRows}</div>
</div>`;

  const inner = `
<div style="flex:1 1 auto;min-height:0;display:flex;flex-direction:column;justify-content:space-between;">
  ${teamsRow}
  ${marginBlock}
  ${dumbbellBlock}
</div>`;
  return frameChrome(spec, inner);
}

export function renderChartHtml(spec) {
  switch (spec.chart) {
    case "readmap":
      return renderReadmap(spec);
    case "movers":
    case "learned":
      return renderMovers(spec);
    case "slate_ranges":
      return renderSlateRanges(spec);
    case "gamecard":
      return renderGamecard(spec);
    default:
      throw new Error(`Unknown chart type: ${spec.chart}`);
  }
}

/** Expose formatters for HTML number scan tests. */
export { formatProbability, formatMargin };
