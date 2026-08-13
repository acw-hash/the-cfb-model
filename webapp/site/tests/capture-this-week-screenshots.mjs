import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(__dirname, "../docs/screenshots");
const BASE = process.env.RIDGE_SHOT_BASE ?? "http://localhost:3457";

async function applyTheme(page, theme) {
  await page.emulateMedia({ colorScheme: theme });
  await page.evaluate((t) => {
    document.documentElement.setAttribute("data-theme", t);
  }, theme);
}

async function goto(page, pathname) {
  const response = await page.goto(`${BASE}${pathname}`, { waitUntil: "networkidle" });
  if (!response || !response.ok()) {
    throw new Error(`GET ${pathname} -> ${response?.status()}`);
  }
}

fs.mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();
const errors = [];

const page = await browser.newPage();
page.on("console", (msg) => {
  if (msg.type() === "error") {
    errors.push(`console.error: ${msg.text()}`);
  }
});
page.on("pageerror", (err) => {
  errors.push(`pageerror: ${err.message}`);
});

await page.setViewportSize({ width: 390, height: 844 });
await goto(page, "/");
await applyTheme(page, "light");
await page.waitForTimeout(200);
await page.screenshot({
  path: path.join(OUT, "this-week-full-390-light.png"),
  fullPage: true,
});

await applyTheme(page, "dark");
await page.waitForTimeout(200);
await page.screenshot({
  path: path.join(OUT, "this-week-full-390-dark.png"),
  fullPage: true,
});

await page.setViewportSize({ width: 1280, height: 900 });
await applyTheme(page, "light");
await page.waitForTimeout(200);
await page.screenshot({
  path: path.join(OUT, "this-week-full-desktop-light.png"),
  fullPage: true,
});

await page.getByTestId("sort-conviction").click();
await page.waitForTimeout(150);
await page.screenshot({
  path: path.join(OUT, "this-week-conviction-sparse.png"),
  fullPage: true,
});

await goto(page, "/gallery/this-week-states");
await applyTheme(page, "light");
await page.getByRole("button", { name: "light" }).click();
await page.waitForTimeout(200);

const emptyTop = page.getByTestId("empty-top-tiers");
await emptyTop.scrollIntoViewIfNeeded();
await emptyTop.screenshot({ path: path.join(OUT, "this-week-empty-top-tiers.png") });

const staleRevised = page.getByTestId("stale-revised");
await staleRevised.scrollIntoViewIfNeeded();
await staleRevised.screenshot({ path: path.join(OUT, "this-week-stale-revised.png") });

const offseason = page.getByTestId("offseason");
await offseason.scrollIntoViewIfNeeded();
await offseason.screenshot({ path: path.join(OUT, "this-week-offseason.png") });

await browser.close();

if (errors.length > 0) {
  console.error("Browser errors on This Week routes:");
  for (const err of errors) {
    console.error(err);
  }
  process.exit(1);
}

console.log("Screenshots saved to", OUT);
console.log("Zero console errors on captured routes.");
