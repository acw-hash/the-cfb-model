/**
 * Ephemeral W-VIS evidence capture. Pattern: capture-about-screenshots.mjs.
 * Not part of the permanent test suite — delete after operator review if desired.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(__dirname, "../docs/screenshots/wvis");
const BASE = process.env.RIDGE_SHOT_BASE ?? "http://localhost:3464";

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

async function dismissDisclaimer(page) {
  const btn = page.getByRole("button", { name: /dismiss|got it|continue|ok/i });
  if ((await btn.count()) > 0) {
    await btn
      .first()
      .click()
      .catch(() => {});
  }
  await page.evaluate(() => {
    try {
      sessionStorage.setItem("ridge-disclaimer-dismissed", "1");
    } catch {
      /* ignore */
    }
  });
}

fs.mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();
const errors = [];

const page = await browser.newPage();
page.on("console", (msg) => {
  if (msg.type() === "error") {
    const text = msg.text();
    if (text.includes("404")) return;
    errors.push(`console.error: ${text}`);
  }
});
page.on("pageerror", (err) => {
  errors.push(`pageerror: ${err.message}`);
});

await page.addInitScript(() => {
  try {
    sessionStorage.setItem("ridge-disclaimer-dismissed", "1");
    sessionStorage.setItem("ridge-disclaimer-seen", "1");
  } catch {
    /* ignore */
  }
});

const viewports = [
  { name: "390", width: 390, height: 844 },
  { name: "desktop", width: 1280, height: 900 },
];
const themes = ["light", "dark"];

// 8 steps × 2 viewports × 2 themes
for (const vp of viewports) {
  await page.setViewportSize({ width: vp.width, height: vp.height });
  for (let step = 1; step <= 8; step++) {
    for (const theme of themes) {
      await goto(page, `/visual?step=${step}`);
      await dismissDisclaimer(page);
      await applyTheme(page, theme);
      await page.waitForTimeout(250);
      await page.screenshot({
        path: path.join(OUT, `step${step}-${vp.name}-${theme}.png`),
        fullPage: true,
      });
    }
  }
}

// Interactive states (desktop light)
await page.setViewportSize({ width: 1280, height: 900 });
await applyTheme(page, "light");

// Step 3 after "Run the season"
await goto(page, "/visual?step=3");
await dismissDisclaimer(page);
await applyTheme(page, "light");
await page.getByRole("button", { name: "Run the season" }).click();
await page.waitForTimeout(800);
await page.screenshot({
  path: path.join(OUT, "step3-after-run-season-desktop-light.png"),
  fullPage: true,
});

// Step 4 both models off
await goto(page, "/visual?step=4");
await dismissDisclaimer(page);
await applyTheme(page, "light");
const pressed = page.locator("button[aria-pressed='true']");
const n = await pressed.count();
for (let i = 0; i < n; i++) {
  await pressed.nth(0).click();
  await page.waitForTimeout(100);
}
await page.waitForTimeout(200);
await page.screenshot({
  path: path.join(OUT, "step4-both-off-desktop-light.png"),
  fullPage: true,
});

// Step 5 after Simulate
await goto(page, "/visual?step=5");
await dismissDisclaimer(page);
await applyTheme(page, "light");
await page.getByRole("button", { name: /Simulate/ }).click();
await page.waitForTimeout(600);
await page.screenshot({
  path: path.join(OUT, "step5-after-simulate-desktop-light.png"),
  fullPage: true,
});

// Step 6 "When it fails"
await goto(page, "/visual?step=6");
await dismissDisclaimer(page);
await applyTheme(page, "light");
await page.getByRole("button", { name: "When it fails" }).click();
await page.waitForTimeout(200);
await page.screenshot({
  path: path.join(OUT, "step6-when-it-fails-desktop-light.png"),
  fullPage: true,
});

// Step 8 "Stale inputs"
await goto(page, "/visual?step=8");
await dismissDisclaimer(page);
await applyTheme(page, "light");
await page.getByRole("button", { name: "Stale inputs" }).click();
await page.waitForTimeout(200);
await page.screenshot({
  path: path.join(OUT, "step8-stale-inputs-desktop-light.png"),
  fullPage: true,
});

// §4.4 anti-pattern checklist shots (desktop light, step 1 hero composition)
await goto(page, "/visual?step=1");
await dismissDisclaimer(page);
await applyTheme(page, "light");
await page.waitForTimeout(200);
const anti = [
  "no-default-shadcn",
  "no-purple-gradient-heroes",
  "no-emoji-cards",
  "no-wall-of-widgets",
  "no-gratuitous-glassmorphism",
  "no-filler-marketing-copy",
];
for (const name of anti) {
  await page.screenshot({
    path: path.join(OUT, `s44-${name}.png`),
    fullPage: true,
  });
}

// Zero-console pass: revisit /visual clean
await goto(page, "/visual");
await page.waitForTimeout(300);

await browser.close();

if (errors.length) {
  console.error("Browser errors on /visual:");
  for (const e of errors) console.error(e);
  process.exit(1);
}

console.log("W-VIS screenshots written to", OUT);
console.log("console_errors=", errors.length);
