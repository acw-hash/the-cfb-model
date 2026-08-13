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

// --- /results full page ---
await page.setViewportSize({ width: 390, height: 844 });
await goto(page, "/results");
await applyTheme(page, "light");
await page.waitForTimeout(200);
await page.screenshot({
  path: path.join(OUT, "results-390-light.png"),
  fullPage: true,
});

await applyTheme(page, "dark");
await page.waitForTimeout(200);
await page.screenshot({
  path: path.join(OUT, "results-390-dark.png"),
  fullPage: true,
});

await page.setViewportSize({ width: 1280, height: 900 });
await applyTheme(page, "light");
await page.waitForTimeout(200);
await page.screenshot({
  path: path.join(OUT, "results-desktop-light.png"),
  fullPage: true,
});

const verdict = page.getByTestId("verdict-block");
await verdict.screenshot({ path: path.join(OUT, "results-verdict.png") });

const ciMetric = page.getByTestId("metric-fund_ats_snapshots");
await ciMetric.scrollIntoViewIfNeeded();
await ciMetric.screenshot({ path: path.join(OUT, "results-ci-treatment.png") });

// --- gallery states ---
await page.setViewportSize({ width: 1280, height: 900 });
await goto(page, "/gallery/results-states");
await applyTheme(page, "light");
await page.getByRole("button", { name: "light" }).click();
await page.waitForTimeout(200);

const empty = page.getByTestId("state-empty-live");
await empty.scrollIntoViewIfNeeded();
await empty.screenshot({ path: path.join(OUT, "results-empty-live.png") });

const miss = page.getByTestId("state-interval-miss");
await miss.scrollIntoViewIfNeeded();
await miss.screenshot({ path: path.join(OUT, "results-interval-miss.png") });

await browser.close();

if (errors.length) {
  console.error("Console errors:\n" + errors.join("\n"));
  process.exit(1);
}
console.log("Screenshots written to", OUT);
console.log("Zero console errors.");
