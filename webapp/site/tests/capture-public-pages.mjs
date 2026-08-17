/**
 * Capture the four public pages at 390 + desktop, both themes, for D1
 * before/after pixel comparison. Requires a running production server.
 *
 * RIDGE_SHOT_BASE default http://127.0.0.1:3458
 * RIDGE_SHOT_LABEL before | after
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const LABEL = process.env.RIDGE_SHOT_LABEL || "after";
const OUT = path.resolve(__dirname, `../../../docs/notes/_artifacts/webapp-w8d/shots-${LABEL}`);
const BASE = process.env.RIDGE_SHOT_BASE ?? "http://127.0.0.1:3458";

const PAGES = [
  { id: "home", path: "/" },
  { id: "game", path: "/game/401628373" },
  { id: "results", path: "/results" },
  { id: "about", path: "/about" },
];

const VIEWPORTS = [
  { id: "390", width: 390, height: 844 },
  { id: "desktop", width: 1280, height: 900 },
];

const THEMES = ["light", "dark"];

fs.mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();

for (const route of PAGES) {
  for (const theme of THEMES) {
    for (const vp of VIEWPORTS) {
      const context = await browser.newContext({
        viewport: { width: vp.width, height: vp.height },
        colorScheme: theme === "dark" ? "dark" : "light",
      });
      const page = await context.newPage();
      const response = await page.goto(`${BASE}${route.path}`, {
        waitUntil: "networkidle",
        timeout: 90000,
      });
      if (!response || !response.ok()) {
        throw new Error(`GET ${route.path} -> ${response?.status()}`);
      }
      await page.evaluate((t) => {
        document.documentElement.setAttribute("data-theme", t);
      }, theme);
      const dismiss = page.getByTestId("dismiss-disclaimer");
      if (await dismiss.count()) {
        await dismiss.click().catch(() => {});
      }
      await page.waitForTimeout(250);
      const filename = `${route.id}-${theme}-${vp.id}.png`;
      await page.screenshot({ path: path.join(OUT, filename), fullPage: true });
      console.log(`wrote ${filename}`);
      await context.close();
    }
  }
}

await browser.close();
console.log(`Screenshots saved to ${OUT}`);
