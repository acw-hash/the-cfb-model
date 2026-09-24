/**
 * Re-shoot only clarity-fix surfaces that changed.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(__dirname, "../../../docs/notes/_artifacts/webapp-clarity/shots");
const BASE = process.env.RIDGE_SHOT_BASE ?? "http://127.0.0.1:3462";

const PAGES = [
  { id: "home", path: "/" },
  { id: "game-home-fav", path: "/game/401628373" },
  { id: "game-away-fav", path: "/game/401628498" },
  { id: "about", path: "/about" },
];

const VIEWPORTS = [
  { id: "390", width: 390, height: 844 },
  { id: "desktop", width: 1280, height: 900 },
];

fs.mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();
const errors = [];

for (const route of PAGES) {
  for (const theme of ["light", "dark"]) {
    for (const vp of VIEWPORTS) {
      const context = await browser.newContext({
        viewport: { width: vp.width, height: vp.height },
        colorScheme: theme === "dark" ? "dark" : "light",
      });
      const page = await context.newPage();
      page.on("console", (msg) => {
        if (msg.type() === "error" && !msg.text().includes("404")) {
          errors.push(`${route.path}: ${msg.text()}`);
        }
      });
      const response = await page.goto(`${BASE}${route.path}`, {
        waitUntil: "networkidle",
        timeout: 90000,
      });
      if (!response?.ok()) {
        throw new Error(`GET ${route.path} -> ${response?.status()}`);
      }
      await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
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

// Suppressed σ from gallery
for (const theme of ["light", "dark"]) {
  for (const vp of VIEWPORTS) {
    const context = await browser.newContext({
      viewport: { width: vp.width, height: vp.height },
      colorScheme: theme === "dark" ? "dark" : "light",
    });
    const page = await context.newPage();
    const response = await page.goto(`${BASE}/gallery/game-detail-states`, {
      waitUntil: "networkidle",
      timeout: 90000,
    });
    if (!response?.ok()) {
      throw new Error(`gallery -> ${response?.status()}`);
    }
    await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
    const panel = page.getByTestId("state-suppressed");
    await panel.scrollIntoViewIfNeeded();
    const filename = `game-suppressed-sigma-LABELED-${theme}-${vp.id}.png`;
    await panel.screenshot({ path: path.join(OUT, filename) });
    console.log(`wrote ${filename}`);
    await context.close();
  }
}

await browser.close();
if (errors.length) {
  console.error(errors.join("\n"));
  process.exit(1);
}
console.log("Console errors: NONE");
