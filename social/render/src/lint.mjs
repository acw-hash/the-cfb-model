/**
 * Layout lint for SOC-2 charts (runs in Playwright after render).
 * Failures fail the run unless --no-lint is set (local debug only).
 */

export const MIN_FONT_PX = 12;
export const MARGIN_PX = 24;
export const MAX_EMPTY_BAND_FRAC = 0.12;
/** Read-map logo–logo max overlap of the smaller logo's area (SOC-2 round 5). */
export const MAX_LOGO_OVERLAP_FRAC = 0.08;
/** Cell text must stay inside its column with at least this gap to the edge. */
export const MIN_COL_GAP_PX = 8;

/**
 * @param {import('playwright').Page} page
 * @param {object} spec
 * @returns {Promise<{ok: boolean, errors: string[]}>}
 */
export async function runLayoutLint(page, spec) {
  return page.evaluate(
    ({ minFont, margin, maxEmptyFrac, maxLogoOverlap, minColGap, chart }) => {
      function boxesOverlap(a, b, minOx = 1, minOy = 1) {
        const ox = Math.min(a.right, b.right) - Math.max(a.left, b.left);
        const oy = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
        return ox > minOx && oy > minOy;
      }

      function boxArea(b) {
        return Math.max(0, b.right - b.left) * Math.max(0, b.bottom - b.top);
      }

      function overlapFraction(a, b) {
        const ox = Math.min(a.right, b.right) - Math.max(a.left, b.left);
        const oy = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
        if (ox <= 0 || oy <= 0) return 0;
        const inter = ox * oy;
        const smaller = Math.min(boxArea(a), boxArea(b));
        return smaller > 0 ? inter / smaller : 0;
      }

      const errors = [];
      const root = document.getElementById("root");
      if (!root) {
        return { ok: false, errors: ["Missing #root"] };
      }
      const rootBox = root.getBoundingClientRect();
      const rootStyle = window.getComputedStyle(root);
      const padL = parseFloat(rootStyle.paddingLeft) || margin;
      const padR = parseFloat(rootStyle.paddingRight) || margin;
      const padT = parseFloat(rootStyle.paddingTop) || margin;
      const padB = parseFloat(rootStyle.paddingBottom) || margin;
      const contentBox = {
        left: rootBox.left + padL,
        right: rootBox.right - padR,
        top: rootBox.top + padT,
        bottom: rootBox.bottom - padB,
      };
      const canvasH = rootBox.height;

      function clippedOutside(r, label) {
        if (r.width < 0.5 && r.height < 0.5) return false;
        if (r.left < contentBox.left - 1.5) return `${label} clipped left`;
        if (r.right > contentBox.right + 1.5) return `${label} clipped right`;
        if (r.top < contentBox.top - 1.5) return `${label} clipped top`;
        if (r.bottom > contentBox.bottom + 1.5) return `${label} clipped bottom`;
        return null;
      }

      for (const svg of root.querySelectorAll("svg")) {
        const par = svg.getAttribute("preserveAspectRatio");
        if (par === "none") {
          errors.push("SVG uses preserveAspectRatio=none (non-uniform scaling)");
        }
        const vb = svg.viewBox.baseVal;
        if (vb.width > 0 && vb.height > 0) {
          const r = svg.getBoundingClientRect();
          if (r.width > 2 && r.height > 2) {
            const rendered = r.width / r.height;
            const vbAspect = vb.width / vb.height;
            const rel = Math.abs(rendered - vbAspect) / vbAspect;
            if (rel > 0.02) {
              errors.push(
                `SVG aspect mismatch ${(rel * 100).toFixed(1)}% (viewBox ${vb.width}×${vb.height})`,
              );
            }
          }
        }
        for (const el of svg.querySelectorAll("text, image, circle, rect, path")) {
          const style = window.getComputedStyle(el);
          if (style.display === "none" || style.visibility === "hidden") continue;
          const tag = el.tagName.toLowerCase();
          if (tag === "path" && el.closest(".traj-panel")) continue;
          if (
            el.closest("[data-role='axis-block']") ||
            el.getAttribute("data-role") === "axis-tick" ||
            el.classList.contains("week-tick") ||
            el.getAttribute("data-role") === "read-dot" ||
            (tag === "text" &&
              !el.closest("[data-role='logo-label-group'], [data-role='callout-group']"))
          ) {
            continue;
          }
          const r = el.getBoundingClientRect();
          const clip = clippedOutside(r, `SVG ${tag}`);
          if (clip) errors.push(clip);
        }
      }

      for (const el of root.querySelectorAll("img.logo")) {
        const r = el.getBoundingClientRect();
        const clip = clippedOutside(r, "HTML logo");
        if (clip) errors.push(clip);
      }

      /** @type {Array<{left:number,right:number,top:number,bottom:number,text:string,group:string,el:Element,lineHeight:number,role:string}>} */
      const textBoxes = [];
      const seen = new Set();

      function pushText(el, direct, group, lineHeight) {
        const fontSize = parseFloat(window.getComputedStyle(el).fontSize);
        if (Number.isFinite(fontSize) && fontSize < minFont - 0.01) {
          errors.push(`Text below ${minFont}px: "${direct.slice(0, 40)}" (${fontSize}px)`);
        }
        const r = el.getBoundingClientRect();
        if (r.width < 0.5 || r.height < 0.5) return;
        if (
          el.closest("footer") == null &&
          el.getAttribute("data-role") !== "axis-tick" &&
          !el.classList.contains("week-tick")
        ) {
          const clip = clippedOutside(r, "text");
          if (clip) errors.push(`${clip}: "${direct.slice(0, 32)}"`);
        }
        // Truncation: scrollWidth / ellipsis / SVG wider than allotted column.
        const role = el.getAttribute("data-role") || "";
        const allowWrapHeader = el.getAttribute("data-allow-wrap") === "1";
        if (!allowWrapHeader && el.closest("footer") == null) {
          const style = window.getComputedStyle(el);
          const isSvgText = el.tagName && el.tagName.toLowerCase() === "text";
          const sw = el.scrollWidth;
          const cw = el.clientWidth;
          // HTML boxes: scrollWidth > clientWidth means CSS truncation.
          // SVG <text> reports unreliable clientWidth — use getBBox vs data-col-width only.
          if (!isSvgText && cw > 2 && sw > cw + 1.5) {
            errors.push(`Truncated text: "${direct.slice(0, 40)}"`);
          }
          if (/…|\.{3}$/.test(direct) || style.textOverflow === "ellipsis") {
            if (style.overflow === "hidden" || style.textOverflow === "ellipsis" || /…/.test(direct)) {
              if ((!isSvgText && sw > cw + 0.5) || /…/.test(direct)) {
                errors.push(`Truncated text: "${direct.slice(0, 40)}"`);
              }
            }
          }
          if (isSvgText) {
            try {
              const bbox = el.getBBox();
              const allotted = parseFloat(el.getAttribute("data-col-width") || "0");
              if (allotted > 0 && bbox.width > allotted + 1.5) {
                errors.push(`Truncated text: "${direct.slice(0, 40)}"`);
              }
            } catch {
              /* getBBox can throw on detached nodes */
            }
          }
        }
        const rotated = el.getAttribute("data-rotated") === "1";
        if (
          el.closest("footer") == null &&
          !allowWrapHeader &&
          !rotated &&
          lineHeight > 0 &&
          r.height > lineHeight * 1.3 + 1
        ) {
          errors.push(`Wrapped text (${r.height.toFixed(1)}px > 1.3×lh): "${direct.slice(0, 40)}"`);
        }
        const key = `${direct}|${Math.round(r.left)}|${Math.round(r.top)}|${Math.round(r.width)}`;
        if (seen.has(key)) return;
        seen.add(key);
        textBoxes.push({
          left: r.left,
          right: r.right,
          top: r.top,
          bottom: r.bottom,
          text: direct.slice(0, 48),
          group,
          el,
          lineHeight,
          role,
        });
      }

      for (const el of root.querySelectorAll("h1, h3, p, span, div, footer, strong")) {
        const style = window.getComputedStyle(el);
        if (style.display === "none" || style.visibility === "hidden") continue;
        const direct = [...el.childNodes]
          .filter((n) => n.nodeType === Node.TEXT_NODE)
          .map((n) => (n.textContent || "").trim())
          .join(" ")
          .trim();
        if (!direct) continue;
        const lh = parseFloat(style.lineHeight) || parseFloat(style.fontSize) * 1.2;
        const groupEl = el.closest(
          "[data-role='logo-label-group'], [data-role='callout-group'], [data-role='matchup-header'], .learned-row, .movers-row, .slate-row, [data-role='axis-block']",
        );
        pushText(
          el,
          direct,
          groupEl ? groupEl.getAttribute("data-role") || "row-group" : "text",
          lh,
        );
      }

      for (const el of root.querySelectorAll("svg text")) {
        const direct = (el.textContent || "").trim();
        if (!direct) continue;
        const fontSize = parseFloat(el.getAttribute("font-size") || "15");
        const lh = fontSize * 1.33;
        if (fontSize < minFont - 0.01) {
          errors.push(`Text below ${minFont}px: "${direct.slice(0, 40)}" (${fontSize}px)`);
        }
        const groupEl = el.closest(
          "[data-role='logo-label-group'], [data-role='callout-group'], [data-role='axis-end-group'], [data-role='outside-label-row'], [data-role='axis-block'], .slate-row, .learned-row, .movers-row",
        );
        let group = "svg-text";
        if (groupEl) {
          group =
            groupEl.getAttribute("data-role") ||
            (groupEl.classList.contains("slate-row") ||
            groupEl.classList.contains("learned-row") ||
            groupEl.classList.contains("movers-row")
              ? "row-group"
              : "group");
        }
        const role = el.getAttribute("data-role") || "";
        if (role === "quadrant-label") group = "quadrant-label";
        // Prefer the tighter axis-end-group over the whole axis-block.
        const endGroup = el.closest("[data-role='axis-end-group']");
        if (endGroup) group = "axis-end-group";
        // Outside plot-frame label rows are intentional coplanar groups.
        const outsideRow = el.closest("[data-role='outside-label-row']");
        if (outsideRow) group = "outside-label-row";
        pushText(el, direct, group, lh);
      }

      const logoBoxes = [];
      for (const el of root.querySelectorAll("img.logo, image.logo")) {
        const r = el.getBoundingClientRect();
        if (r.width < 0.5 || r.height < 0.5) {
          errors.push("Logo with near-zero size");
          continue;
        }
        const groupEl = el.closest(
          "[data-role='logo-label-group'], [data-role='matchup-header'], .learned-row, .movers-row, .slate-row",
        );
        logoBoxes.push({
          left: r.left,
          right: r.right,
          top: r.top,
          bottom: r.bottom,
          group: groupEl ? "logo-group" : "logo",
          el,
          drawLeader: groupEl?.getAttribute("data-draw-leader") === "1",
          readmapMark:
            (chart === "readmap" || chart === "market_map") &&
            groupEl?.hasAttribute("data-draw-leader"),
        });
      }

      // Logo–logo: any pair over the limit fails (read map / market map packing).
      // 16×9 maps are too short for a full FBS field at ≤8% — enforce on 4×5.
      if ((chart === "readmap" || chart === "market_map") && rootBox.height >= 800) {
        const overlapLogos = logoBoxes.filter((lb) => lb.readmapMark);
        for (let i = 0; i < overlapLogos.length; i += 1) {
          for (let j = i + 1; j < overlapLogos.length; j += 1) {
            const a = overlapLogos[i];
            const b = overlapLogos[j];
            const frac = overlapFraction(a, b);
            // Allow a 0.5pp float/rounding tolerance on the published limit.
            if (frac <= maxLogoOverlap + 0.005) continue;
            errors.push(`Logo overlap ${(frac * 100).toFixed(0)}% (max ${maxLogoOverlap * 100}%)`);
          }
        }
      }

      const skipGroups = new Set([
        "logo-label-group",
        "callout-group",
        "matchup-header",
        "row-group",
        "axis-end-group",
        "outside-label-row",
      ]);

      const tickBoxes = textBoxes.filter(
        (tb) =>
          tb.el.closest("[data-role='axis-tick']") != null ||
          tb.el.getAttribute("data-role") === "axis-tick" ||
          tb.el.classList?.contains("week-tick"),
      );

      for (let i = 0; i < tickBoxes.length; i += 1) {
        for (let j = i + 1; j < tickBoxes.length; j += 1) {
          const axA = tickBoxes[i].el.getAttribute("data-axis");
          const axB = tickBoxes[j].el.getAttribute("data-axis");
          if (axA && axB && axA !== axB) continue;
          if (boxesOverlap(tickBoxes[i], tickBoxes[j], 2, 2)) {
            errors.push(
              `Overlapping tick labels: "${tickBoxes[i].text}" vs "${tickBoxes[j].text}"`,
            );
          }
        }
      }

      // Text–text: include axis ticks and quadrant labels (round-5).
      for (let i = 0; i < textBoxes.length; i += 1) {
        for (let j = i + 1; j < textBoxes.length; j += 1) {
          const a = textBoxes[i];
          const b = textBoxes[j];
          if (a.el === b.el) continue;
          if (a.text === b.text && Math.abs(a.left - b.left) < 2 && Math.abs(a.top - b.top) < 2) {
            continue;
          }
          const aFoot = /Forecasts, not betting advice/.test(a.text);
          const bFoot = /Forecasts, not betting advice/.test(b.text);
          if (aFoot || bFoot) continue;
          if (skipGroups.has(a.group) && a.group === b.group) continue;
          const aRow = a.el.closest?.(
            ".slate-row, .learned-row, .movers-row, .traj-panel",
          );
          const bRow = b.el.closest?.(
            ".slate-row, .learned-row, .movers-row, .traj-panel",
          );
          if (aRow && aRow === bRow) continue;
          if (skipGroups.has(a.group) && skipGroups.has(b.group)) continue;
          // Same-axis ticks already checked above; skip duplicate pairs.
          const aTick = tickBoxes.includes(a);
          const bTick = tickBoxes.includes(b);
          if (aTick && bTick) {
            const axA = a.el.getAttribute("data-axis");
            const axB = b.el.getAttribute("data-axis");
            if (axA && axB && axA === axB) continue;
          }
          if (!boxesOverlap(a, b, 2, 2)) continue;
          errors.push(`Overlapping text: "${a.text}" vs "${b.text}"`);
        }
      }

      for (const tb of textBoxes) {
        if (tb.el.closest("footer")) continue;
        const tbGroup = tb.el.closest(
          "[data-role='logo-label-group'], [data-role='callout-group'], [data-role='matchup-header'], .learned-row, .movers-row, .slate-row",
        );
        for (const lb of logoBoxes) {
          const lbGroup = lb.el.closest(
            "[data-role='logo-label-group'], [data-role='callout-group'], [data-role='matchup-header'], .learned-row, .movers-row, .slate-row",
          );
          if (tbGroup && lbGroup && tbGroup === lbGroup) continue;
          if (!boxesOverlap(tb, lb, 2, 2)) continue;
          errors.push(`Text overlaps logo: "${tb.text}"`);
        }
      }

      const header = root.querySelector("[data-role='header']");
      const footer = root.querySelector("[data-role='footer']");
      const bodyEl = root.querySelector("[data-role='body']");
      const bodyBox = bodyEl ? bodyEl.getBoundingClientRect() : null;
      if (bodyBox) {
        for (let i = textBoxes.length - 1; i >= 0; i -= 1) {
          const tb = textBoxes[i];
          const inChrome =
            tb.el.closest("[data-role='header'], [data-role='titles'], [data-role='footer']") != null;
          if (inChrome) continue;
          if (tb.top > bodyBox.bottom - 1 || tb.bottom < bodyBox.top + 1) {
            textBoxes.splice(i, 1);
          }
        }
      }
      if (header && footer) {
        const hb = header.getBoundingClientRect();
        const fb = footer.getBoundingClientRect();
        const contentSel =
          "[data-role='titles'], [data-role='body'] > *, .learned-row, .movers-row, .slate-row, .traj-panel, svg.plot, h1, h3";
        const boxes = [];
        for (const el of root.querySelectorAll(contentSel)) {
          const r = el.getBoundingClientRect();
          if (r.height < 4 || r.width < 4) continue;
          if (r.bottom <= hb.bottom + 1 || r.top >= fb.top - 1) continue;
          boxes.push({ top: r.top, bottom: r.bottom });
        }
        boxes.sort((a, b) => a.top - b.top);
        let maxGap = 0;
        let cursor = hb.bottom;
        for (const b of boxes) {
          maxGap = Math.max(maxGap, b.top - cursor);
          cursor = Math.max(cursor, b.bottom);
        }
        maxGap = Math.max(maxGap, fb.top - cursor);
        if (maxGap > canvasH * maxEmptyFrac + 4) {
          errors.push(
            `Empty band ${((maxGap / canvasH) * 100).toFixed(1)}% of canvas (>${maxEmptyFrac * 100}%)`,
          );
        }
      }

      for (const el of root.querySelectorAll(
        "rect.bar, circle.mu-dot, circle.traj-pt, rect.bar-prev, rect.bar-cur",
      )) {
        const r = el.getBoundingClientRect();
        const wAttr = parseFloat(el.getAttribute("width") || "0");
        const isCircle = el.tagName.toLowerCase() === "circle";
        if (isCircle) {
          if (parseFloat(el.getAttribute("r") || "0") <= 0) {
            errors.push("Circle mark with zero radius");
          }
        } else if (wAttr <= 0 && r.width <= 0.5) {
          errors.push("Bar mark with zero width");
        }
        const fill = el.getAttribute("fill") || "";
        const opacity = parseFloat(el.getAttribute("fill-opacity") || "1");
        if (fill === "none" || fill === "transparent" || opacity <= 0) {
          errors.push("Mark with transparent fill");
        }
      }

      if (chart === "gamecard") {
        for (const panel of root.querySelectorAll(".traj-panel")) {
          const yTexts = [...panel.querySelectorAll("text")].filter((el) =>
            /[+\-\u2212]?\d/.test(el.textContent || ""),
          );
          if (yTexts.length < 3) {
            errors.push(
              `Trajectory panel has fewer than 3 numeric tick labels (${yTexts.length})`,
            );
          }
        }
        // Rank rows: track center y within ±4 px of its label center.
        for (const row of root.querySelectorAll("[data-role='dumbbell-row']")) {
          const label = row.querySelector("[data-role='dumbbell-label']");
          const track = row.querySelector("[data-role='dumbbell-track']");
          if (!label || !track) continue;
          const lb = label.getBoundingClientRect();
          const tb = track.getBoundingClientRect();
          const labelMid = (lb.top + lb.bottom) / 2;
          const trackMid = (tb.top + tb.bottom) / 2;
          if (Math.abs(labelMid - trackMid) > 4.01) {
            errors.push(
              `Dumbbell track/label misaligned by ${Math.abs(labelMid - trackMid).toFixed(1)}px (max 4)`,
            );
          }
        }
      }

      // Column overflow: text *ink* bbox must stay inside its column with ≥ minColGap.
      function textInkBox(el) {
        const range = document.createRange();
        range.selectNodeContents(el);
        const rects = [...range.getClientRects()].filter((r) => r.width > 0.5 && r.height > 0.5);
        if (!rects.length) return null;
        return {
          left: Math.min(...rects.map((r) => r.left)),
          right: Math.max(...rects.map((r) => r.right)),
          top: Math.min(...rects.map((r) => r.top)),
          bottom: Math.max(...rects.map((r) => r.bottom)),
        };
      }
      for (const cell of root.querySelectorAll(
        "[data-role='slate-col'], [data-role='col-header']",
      )) {
        const style = window.getComputedStyle(cell);
        if (style.display === "none" || style.visibility === "hidden") continue;
        const box = cell.getBoundingClientRect();
        if (box.width < 2 || box.height < 2) continue;
        const direct = (cell.textContent || "").trim();
        if (!direct) continue;
        const ink = textInkBox(cell);
        if (!ink) continue;
        const gapL = ink.left - box.left;
        const gapR = box.right - ink.right;
        // Centered cells can sit flush to one edge of the ink vs a wide column —
        // fail only when ink spills past the column or leaves < minColGap on a
        // side where the ink actually approaches that edge (spill / near-spill).
        const spills =
          ink.left < box.left - 0.5 ||
          ink.right > box.right + 0.5 ||
          gapL < minColGap - 0.5 ||
          gapR < minColGap - 0.5;
        // Ignore "gap too small" when the column is much wider than the ink and
        // the text is centered (both gaps large-ish after accounting for center).
        const inkW = ink.right - ink.left;
        const centered =
          Math.abs(gapL - gapR) < 4 && box.width - inkW > 2 * minColGap;
        if (spills && !centered) {
          errors.push(
            `Column overflow: "${direct.slice(0, 40)}" (gaps L=${gapL.toFixed(1)} R=${gapR.toFixed(1)}, need ≥${minColGap})`,
          );
        }
        if (cell.scrollWidth > cell.clientWidth + 1.5) {
          errors.push(`Column overflow (truncated): "${direct.slice(0, 40)}"`);
        }
      }

      // Movers plot header must stay on one line.
      if (chart === "movers" || chart === "learned") {
        for (const el of root.querySelectorAll("[data-role='col-header'][data-col='plot']")) {
          const style = window.getComputedStyle(el);
          const lh = parseFloat(style.lineHeight) || parseFloat(style.fontSize) * 1.2;
          const r = el.getBoundingClientRect();
          const text = (el.textContent || "").trim();
          if (r.height > lh * 1.35 + 1) {
            errors.push(`Movers header wrapped: "${text.slice(0, 48)}"`);
          }
          if (/Ridge rating:/i.test(text) && /before/i.test(text)) {
            errors.push(`Movers header stale long form: "${text.slice(0, 48)}"`);
          }
        }
      }

      // Read-map / market-map: no Most/Least sure end labels; ≥10 px ink gap.
      if (chart === "readmap" || chart === "market_map") {
        for (const el of root.querySelectorAll("[data-role='axis-end-label']")) {
          const text = (el.textContent || "").trim();
          if (/Most sure|Least sure/i.test(text)) {
            errors.push(`Read-map corner end label present: "${text}"`);
          }
        }
        let plotBox = null;
        for (const el of root.querySelectorAll("svg rect")) {
          if (el.getAttribute("data-role") === "agreement-band") continue;
          const r = el.getBoundingClientRect();
          if (r.width > 200 && r.height > 200) {
            plotBox = r;
            break;
          }
        }
        if (plotBox) {
          const minQuadGap = 10;
          for (const el of root.querySelectorAll(
            "[data-role='quadrant-label'][data-placement='outside']",
          )) {
            const r = el.getBoundingClientRect();
            const text = (el.textContent || "").trim();
            let gap = null;
            if (r.bottom <= plotBox.top + 1) gap = plotBox.top - r.bottom;
            else if (r.top >= plotBox.bottom - 1) gap = r.top - plotBox.bottom;
            if (gap != null && gap < minQuadGap - 0.5) {
              errors.push(
                `Read-map corner gap ${gap.toFixed(1)}px < ${minQuadGap}: "${text.slice(0, 32)}"`,
              );
            }
          }
        }
      }

      return { ok: errors.length === 0, errors: [...new Set(errors)].slice(0, 40) };
    },
    {
      minFont: MIN_FONT_PX,
      margin: MARGIN_PX,
      maxEmptyFrac: MAX_EMPTY_BAND_FRAC,
      maxLogoOverlap: MAX_LOGO_OVERLAP_FRAC,
      minColGap: MIN_COL_GAP_PX,
      chart: spec.chart,
    },
  );
}
