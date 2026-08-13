import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(__dirname, "../docs/screenshots");
const BASE = process.env.RIDGE_SHOT_BASE ?? "http://localhost:3460";

async function applyTheme(page, theme) {
  await page.emulateMedia({ colorScheme: theme });
  await page.evaluate((t) => {
    document.documentElement.setAttribute("data-theme", t);
  }, theme);
}

async function goto(page, pathname) {
  const response = await page.goto(`${BASE}${pathname}`, { waitUntil: "networkidle" });
  const status = response?.status() ?? 0;
  if (!response || !response.ok()) {
    throw new Error(`GET ${pathname} -> ${status}`);
  }
  return status;
}

fs.mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();
const errors = [];

const page = await browser.newPage();
page.on("console", (msg) => {
  if (msg.type() === "error") {
    const text = msg.text();
    if (text.includes("404")) {
      return;
    }
    errors.push(`console.error: ${text}`);
  }
});
page.on("pageerror", (err) => {
  errors.push(`pageerror: ${err.message}`);
});

// Clear session so first-visit disclaimer shows
await page.addInitScript(() => {
  try {
    sessionStorage.removeItem("ridge-disclaimer-dismissed");
  } catch {
    /* ignore */
  }
});

// --- /about full page ---
await page.setViewportSize({ width: 390, height: 844 });
await goto(page, "/about");
await applyTheme(page, "light");
await page.waitForTimeout(300);
await page.screenshot({
  path: path.join(OUT, "about-390-light.png"),
  fullPage: true,
});

await applyTheme(page, "dark");
await page.waitForTimeout(200);
await page.screenshot({
  path: path.join(OUT, "about-390-dark.png"),
  fullPage: true,
});

await page.setViewportSize({ width: 1280, height: 900 });
await applyTheme(page, "light");
await page.waitForTimeout(200);
await page.screenshot({
  path: path.join(OUT, "about-desktop-light.png"),
  fullPage: true,
});

await applyTheme(page, "dark");
await page.waitForTimeout(200);
await page.screenshot({
  path: path.join(OUT, "about-desktop-dark.png"),
  fullPage: true,
});

await applyTheme(page, "light");
const disclaimer = page.getByTestId("about-disclaimer");
await disclaimer.scrollIntoViewIfNeeded();
await disclaimer.screenshot({ path: path.join(OUT, "about-disclaimer.png") });

const rg = page.getByTestId("about-responsible-gambling");
await rg.scrollIntoViewIfNeeded();
await rg.screenshot({ path: path.join(OUT, "about-responsible-gambling.png") });

// Header at top + scrolled (non-sticky — scrolls away)
await page.evaluate(() => window.scrollTo(0, 0));
await page.waitForTimeout(150);
const header = page.getByTestId("site-header");
await header.screenshot({ path: path.join(OUT, "nav-header-top.png") });

await page.evaluate(() => window.scrollTo(0, 600));
await page.waitForTimeout(150);
await page.screenshot({
  path: path.join(OUT, "nav-header-scrolled.png"),
  fullPage: false,
});

// First-visit disclaimer alone (may already be visible)
const firstVisit = page.getByTestId("first-visit-disclaimer");
if (await firstVisit.count()) {
  await firstVisit.screenshot({ path: path.join(OUT, "about-first-visit-disclaimer.png") });
}

await browser.close();

if (errors.length) {
  console.error("Browser errors on About routes:");
  for (const e of errors) {
    console.error(e);
  }
  process.exit(1);
}

console.log("About screenshots written to", OUT);
console.log("Zero console errors.");
