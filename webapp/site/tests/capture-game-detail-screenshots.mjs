import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(__dirname, "../docs/screenshots");
const BASE = process.env.RIDGE_SHOT_BASE ?? "http://localhost:3458";

async function applyTheme(page, theme) {
  await page.emulateMedia({ colorScheme: theme });
  await page.evaluate((t) => {
    document.documentElement.setAttribute("data-theme", t);
  }, theme);
}

async function goto(page, pathname, allowNotFound = false) {
  const response = await page.goto(`${BASE}${pathname}`, { waitUntil: "networkidle" });
  const status = response?.status() ?? 0;
  if (!response || (!response.ok() && !(allowNotFound && status === 404))) {
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

const GAME = "/game/401628378";

await page.setViewportSize({ width: 390, height: 844 });
await goto(page, GAME);
await applyTheme(page, "light");
await page.waitForTimeout(200);
await page.screenshot({
  path: path.join(OUT, "game-detail-390-light.png"),
  fullPage: true,
});
const chart = page.getByTestId("trajectory-chart");
await chart.screenshot({ path: path.join(OUT, "trajectory-chart-390.png") });

await applyTheme(page, "dark");
await page.waitForTimeout(200);
await page.screenshot({
  path: path.join(OUT, "game-detail-390-dark.png"),
  fullPage: true,
});

await page.setViewportSize({ width: 1280, height: 900 });
await applyTheme(page, "light");
await page.waitForTimeout(200);
await page.screenshot({
  path: path.join(OUT, "game-detail-desktop-light.png"),
  fullPage: true,
});
await chart.screenshot({ path: path.join(OUT, "trajectory-chart-desktop.png") });

await goto(page, "/gallery/game-detail-states");
await applyTheme(page, "light");
await page.getByRole("button", { name: "light" }).click();
await page.waitForTimeout(200);

const twoBand = page.getByTestId("state-two-band");
await twoBand.scrollIntoViewIfNeeded();
await twoBand.screenshot({ path: path.join(OUT, "game-detail-two-band.png") });

const suppressed = page.getByTestId("state-suppressed");
await suppressed.scrollIntoViewIfNeeded();
await suppressed.screenshot({ path: path.join(OUT, "game-detail-suppressed.png") });

const stale = page.getByTestId("state-stale");
await stale.scrollIntoViewIfNeeded();
await stale.screenshot({ path: path.join(OUT, "game-detail-stale.png") });

const nullTotal = page.getByTestId("state-null-total");
await nullTotal.scrollIntoViewIfNeeded();
await nullTotal.screenshot({ path: path.join(OUT, "game-detail-null-total.png") });

const gapped = page.getByTestId("state-gapped-ratings");
await gapped.scrollIntoViewIfNeeded();
await gapped.screenshot({ path: path.join(OUT, "game-detail-gapped-ratings.png") });

await page.setViewportSize({ width: 1280, height: 900 });
await goto(page, "/gallery");
await applyTheme(page, "light");
await page.getByRole("button", { name: "light" }).click();
await page.waitForTimeout(200);
await page.screenshot({
  path: path.join(OUT, "gallery-w4-0-light-desktop.png"),
  fullPage: true,
});

await page.setViewportSize({ width: 390, height: 844 });
await page.screenshot({
  path: path.join(OUT, "gallery-w4-0-light-mobile.png"),
  fullPage: true,
});

await page.setViewportSize({ width: 390, height: 844 });
await goto(page, "/game/not-a-real-id", true);
await applyTheme(page, "light");
await page.waitForTimeout(200);
await page.screenshot({
  path: path.join(OUT, "game-detail-unknown.png"),
  fullPage: true,
});

await browser.close();

if (errors.length > 0) {
  console.error("Browser errors on Game Detail routes:");
  for (const err of errors) {
    console.error(err);
  }
  process.exit(1);
}

console.log("Screenshots saved to", OUT);
console.log("Zero console errors on captured routes.");
