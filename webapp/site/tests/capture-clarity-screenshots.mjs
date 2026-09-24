/**
 * Clarity-pass screenshots: 390 + desktop × light + dark for public pages,
 * plus a labeled suppressed-σ Game Detail from the gallery (dev server).
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(__dirname, "../../../docs/notes/_artifacts/webapp-clarity/shots");
const BASE = process.env.RIDGE_SHOT_BASE ?? "http://127.0.0.1:3461";
const GALLERY_BASE = process.env.RIDGE_GALLERY_BASE ?? "http://127.0.0.1:3462";

const PAGES = [
  { id: "home", path: "/" },
  { id: "game-home-fav", path: "/game/401628373" },
  { id: "game-away-fav", path: "/game/401628498" },
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
const consoleErrors = [];

async function shot(base, route, theme, vp, filename) {
  const context = await browser.newContext({
    viewport: { width: vp.width, height: vp.height },
    colorScheme: theme === "dark" ? "dark" : "light",
  });
  const page = await context.newPage();
  page.on("console", (msg) => {
    if (msg.type() === "error") {
      const text = msg.text();
      if (!text.includes("404")) {
        consoleErrors.push(`${route.path} [${theme}/${vp.id}]: ${text}`);
      }
    }
  });
  page.on("pageerror", (err) => {
    consoleErrors.push(`${route.path} pageerror: ${err.message}`);
  });
  const response = await page.goto(`${base}${route.path}`, {
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
  const howto = page.getByTestId("dismiss-howto");
  if (await howto.count()) {
    // leave visible for home key shot once; dismiss on later themes
  }
  await page.waitForTimeout(300);
  await page.screenshot({ path: path.join(OUT, filename), fullPage: true });
  console.log(`wrote ${filename}`);
  await context.close();
}

for (const route of PAGES) {
  for (const theme of THEMES) {
    for (const vp of VIEWPORTS) {
      const filename = `${route.id}-${theme}-${vp.id}.png`;
      await shot(BASE, route, theme, vp, filename);
    }
  }
}

// Labeled suppressed-σ fixture from gallery (dev only).
for (const theme of THEMES) {
  for (const vp of VIEWPORTS) {
    const filename = `game-suppressed-sigma-LABELED-${theme}-${vp.id}.png`;
    const context = await browser.newContext({
      viewport: { width: vp.width, height: vp.height },
      colorScheme: theme === "dark" ? "dark" : "light",
    });
    const page = await context.newPage();
    const response = await page.goto(`${GALLERY_BASE}/gallery/game-detail-states`, {
      waitUntil: "networkidle",
      timeout: 90000,
    });
    if (!response || !response.ok()) {
      throw new Error(`GET gallery/game-detail-states -> ${response?.status()}`);
    }
    await page.evaluate((t) => {
      document.documentElement.setAttribute("data-theme", t);
    }, theme);
    const panel = page.getByTestId("state-suppressed");
    if ((await panel.count()) === 0) {
      throw new Error("missing state-suppressed gallery panel");
    }
    await panel.scrollIntoViewIfNeeded();
    await panel.screenshot({ path: path.join(OUT, filename) });
    console.log(`wrote ${filename}`);
    await context.close();
  }
}

await browser.close();

if (consoleErrors.length) {
  console.error("Console errors:");
  for (const e of consoleErrors) {
    console.error(e);
  }
  process.exit(1);
}

console.log(`Screenshots saved to ${OUT}`);
console.log("Console errors: NONE");
