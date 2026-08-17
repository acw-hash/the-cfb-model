/**
 * W8-A D5 — axe-core a11y pass over public pages (both themes, 390 + desktop).
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
const OUT = path.resolve(__dirname, "../../../docs/notes/_artifacts/webapp-w8a");
const BASE = process.env.RIDGE_A11Y_BASE || "https://the-cfb-model.vercel.app";

const PAGES = [
  { id: "home", path: "/" },
  { id: "game", path: "/game/401628373" },
  { id: "results", path: "/results" },
  { id: "about", path: "/about" },
];

const WIDTHS = [
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
      const row = {
        page: route.id,
        path: route.path,
        theme,
        width: vp.id,
        count: result.count,
        violations: result.violations,
      };
      summary.push(row);
      console.log(
        `${route.id}/${theme}/${vp.id}: ${result.count} violations` +
          (result.violations.length ? ` [${result.violations.map((v) => v.id).join(", ")}]` : ""),
      );
      await context.close();
    }
  }
}

await browser.close();
const label = process.env.RIDGE_A11Y_LABEL || "after";
fs.writeFileSync(path.join(OUT, `a11y-${label}.json`), JSON.stringify(summary, null, 2) + "\n");
console.log(`Wrote a11y-${label}.json`);
