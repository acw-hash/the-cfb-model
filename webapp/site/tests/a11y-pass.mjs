/**
 * W8-D D4 — axe-core a11y pass over public pages (both themes, 320 / 390 / desktop).
 * 320 CSS px is WCAG 2.1 AA 1.4.10 Reflow. Default BASE is a local production
 * `next start` (W8-A compared production-before to next-dev-after).
 * Uses transitive axe-core + playwright already present for screenshot scripts.
 * No new dependencies.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { chromium } from "playwright";

const require = createRequire(import.meta.url);
const axeSource = fs.readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(__dirname, "../../../docs/notes/_artifacts/webapp-w8d");
const BASE = process.env.RIDGE_A11Y_BASE || "http://127.0.0.1:3458";

const PAGES = [
  { id: "home", path: "/" },
  { id: "game", path: "/game/401628373" },
  { id: "results", path: "/results" },
  { id: "about", path: "/about" },
];

const WIDTHS = [
  { id: "320", width: 320, height: 844 },
  { id: "390", width: 390, height: 844 },
  { id: "desktop", width: 1280, height: 900 },
];

const THEMES = ["light", "dark"];

fs.mkdirSync(OUT, { recursive: true });

async function runAxe(page) {
  await page.evaluate(axeSource);
  return page.evaluate(async () => {
    // eslint-disable-next-line no-undef
    const results = await axe.run(document, {
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa"] },
    });
    return {
      violations: results.violations.map((v) => ({
        id: v.id,
        impact: v.impact,
        help: v.help,
        nodes: v.nodes.length,
      })),
      count: results.violations.length,
    };
  });
}

const browser = await chromium.launch();
const summary = [];

for (const route of PAGES) {
  for (const theme of THEMES) {
    for (const vp of WIDTHS) {
      const context = await browser.newContext({
        viewport: { width: vp.width, height: vp.height },
        colorScheme: theme === "dark" ? "dark" : "light",
      });
      const page = await context.newPage();
      await page.goto(`${BASE}${route.path}`, { waitUntil: "networkidle", timeout: 90000 });
      await page.evaluate((t) => {
        document.documentElement.setAttribute("data-theme", t);
      }, theme);
      // Dismiss first-visit disclaimer if present so it doesn't mask page issues
      const dismiss = page.getByTestId("dismiss-disclaimer");
      if (await dismiss.count()) {
        await dismiss.click().catch(() => {});
      }
      await page.waitForTimeout(200);
      const result = await runAxe(page);
      const overflow = await page.evaluate(() => {
        const root = document.documentElement;
        const viewportOverflow = root.scrollWidth - root.clientWidth;
        const overflowing = [];
        for (const el of document.querySelectorAll("body *")) {
          if (!(el instanceof HTMLElement)) {
            continue;
          }
          if (el.scrollWidth > el.clientWidth + 1) {
            overflowing.push({
              tag: el.tagName.toLowerCase(),
              testid: el.getAttribute("data-testid"),
              className: typeof el.className === "string" ? el.className.slice(0, 80) : "",
              scrollWidth: el.scrollWidth,
              clientWidth: el.clientWidth,
            });
          }
        }
        return {
          documentScrollWidth: root.scrollWidth,
          documentClientWidth: root.clientWidth,
          viewportOverflow,
          overflowing: overflowing.slice(0, 12),
        };
      });
      const row = {
        page: route.id,
        path: route.path,
        theme,
        width: vp.id,
        count: result.count,
        violations: result.violations,
        overflow,
      };
      summary.push(row);
      console.log(
        `${route.id}/${theme}/${vp.id}: ${result.count} violations` +
          (result.violations.length ? ` [${result.violations.map((v) => v.id).join(", ")}]` : "") +
          ` overflow=${overflow.viewportOverflow}px overflowing=${overflow.overflowing.length}`,
      );
      await context.close();
    }
  }
}

await browser.close();
const label = process.env.RIDGE_A11Y_LABEL || "after";
fs.writeFileSync(path.join(OUT, `a11y-${label}.json`), JSON.stringify(summary, null, 2) + "\n");
console.log(`Wrote a11y-${label}.json`);
