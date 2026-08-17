/**
 * W8-A D5 — keyboard focus evidence for interactive controls.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(__dirname, "../../../docs/notes/_artifacts/webapp-w8a/focus");
const BASE = process.env.RIDGE_A11Y_BASE || "http://localhost:3470";

fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
await page.evaluate(() => document.documentElement.setAttribute("data-theme", "light"));

async function shot(name) {
  const el = await page.evaluateHandle(() => document.activeElement);
  await page.screenshot({ path: path.join(OUT, name), fullPage: false });
  await el.dispose();
  console.log(
    "saved",
    name,
    "active=",
    await page.evaluate(
      () =>
        document.activeElement?.tagName +
        ":" +
        (document.activeElement?.getAttribute("data-testid") ||
          document.activeElement?.textContent?.slice(0, 40)),
    ),
  );
}

// First-visit disclaimer dismiss
const dismiss = page.getByTestId("dismiss-disclaimer");
if (await dismiss.count()) {
  await dismiss.focus();
  await shot("focus-disclaimer-dismiss.png");
  await dismiss.click();
}

// SiteHeader links
await page.keyboard.press("Tab");
await shot("focus-header-1.png");
await page.keyboard.press("Tab");
await shot("focus-header-2.png");
await page.keyboard.press("Tab");
await shot("focus-header-3.png");

// SortControl
await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
await page.evaluate(() => {
  try {
    sessionStorage.setItem("ridge-disclaimer-dismissed", "1");
  } catch {}
  document.documentElement.setAttribute("data-theme", "light");
});
await page.reload({ waitUntil: "networkidle" });
await page.getByTestId("sort-kickoff").focus();
await shot("focus-sort-kickoff.png");
await page.getByTestId("sort-conviction").focus();
await shot("focus-sort-conviction.png");

// Results tabs
await page.goto(`${BASE}/results`, { waitUntil: "networkidle" });
await page.evaluate(() => {
  try {
    sessionStorage.setItem("ridge-disclaimer-dismissed", "1");
  } catch {}
});
await page.reload({ waitUntil: "networkidle" });
await page.getByTestId("tab-record").focus();
await shot("focus-results-tab-record.png");
await page.getByTestId("tab-games").focus();
await shot("focus-results-tab-games.png");

await browser.close();
console.log("Focus screenshots in", OUT);
