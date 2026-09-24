/**
 * Production post-promote verify + dark screenshots (380 + 1280).
 */
import fs from "node:fs";
import { chromium } from "playwright";

const BASE = process.env.RIDGE_SHOT_BASE ?? "https://the-cfb-model.vercel.app";
const BYPASS = process.env.VERCEL_AUTOMATION_BYPASS_SECRET ?? "";
const OUT = "docs/screenshots/scoreboard/production";

fs.mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();
const context = await browser.newContext(
  BYPASS ? { extraHTTPHeaders: { "x-vercel-protection-bypass": BYPASS } } : {},
);
const page = await context.newPage();

async function goto(path) {
  const resp = await page.goto(`${BASE}${path}`, { waitUntil: "networkidle", timeout: 60000 });
  if (!resp?.ok()) throw new Error(`GET ${path} -> ${resp?.status()}`);
  return resp;
}

const results = [];

// --- Header alignment + howto ---
await page.setViewportSize({ width: 1280, height: 900 });
await goto("/");
await page.emulateMedia({ colorScheme: "dark" });
await page.evaluate(() => {
  document.documentElement.setAttribute("data-theme", "dark");
  try {
    sessionStorage.removeItem("ridge-howto-read-seen");
  } catch {
    /* ignore */
  }
});
await page.reload({ waitUntil: "networkidle" });
await page.emulateMedia({ colorScheme: "dark" });
await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));

const howto = page.locator("[data-testid=how-to-read-key]");
results.push({ check: "howto visible", ok: (await howto.count()) === 1 });
const howtoText = ((await howto.textContent()) ?? "").trim();
results.push({
  check: "howto copy",
  ok: howtoText.includes("Numbers sit beside the team they favor"),
  detail: howtoText.slice(0, 120),
});

const header = page.locator("[data-testid=slate-column-header]");
const headerText = ((await header.innerText()) ?? "").replace(/\s+/g, " ");
results.push({
  check: "header has WINS BY WIN % TIER",
  ok: /WINS BY/i.test(headerText) && /WIN %/i.test(headerText) && /TIER/i.test(headerText),
  detail: headerText,
});

// Dismiss howto and confirm stays dismissed
await page.locator("[data-testid=dismiss-howto]").click();
await page.waitForTimeout(200);
results.push({ check: "howto dismissed", ok: (await howto.count()) === 0 });
await page.reload({ waitUntil: "networkidle" });
await page.emulateMedia({ colorScheme: "dark" });
await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
results.push({ check: "howto stays dismissed after reload", ok: (await howto.count()) === 0 });

// ?order=conviction
await goto("/?order=conviction");
await page.emulateMedia({ colorScheme: "dark" });
await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
const slate = page.locator("[data-testid=slate]");
const orderAttr = await slate.getAttribute("data-order");
results.push({ check: "order=conviction", ok: orderAttr === "conviction", detail: orderAttr });

// /game Model|Market + About these odds collapsed
const coastal = page.locator('a[href^="/game/"]').filter({ hasText: "Coastal Carolina" }).first();
let gamePath = "/game/401869941";
if ((await coastal.count()) > 0) {
  gamePath = (await coastal.getAttribute("href")) ?? gamePath;
}
await goto(gamePath);
await page.emulateMedia({ colorScheme: "dark" });
await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
const mam = page.locator("[data-testid=model-and-market]");
results.push({ check: "model-and-market present", ok: (await mam.count()) === 1 });
const details = mam.locator("details");
const detailsOpen = await details.evaluate((el) => el.open);
results.push({ check: "About these odds collapsed", ok: detailsOpen === false });
const aboutSummary = await mam.locator("summary").textContent();
results.push({
  check: "About these odds summary",
  ok: (aboutSummary ?? "").includes("About these odds"),
  detail: aboutSummary,
});
const attribution = page.locator("[data-testid=odds-attribution]");
results.push({ check: "odds attribution outside details", ok: (await attribution.count()) === 1 });

// Dark mode legibility spot-check: column rule exists + market num color token class
await goto("/");
await page.emulateMedia({ colorScheme: "dark" });
await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
const rule = await page
  .locator("[data-testid=game-row]")
  .first()
  .locator('[aria-hidden="true"]')
  .count();
results.push({ check: "row has aria-hidden rule candidate", ok: rule > 0 });
const marketMargin = page.locator("[data-testid=market-margin]").first();
results.push({ check: "market margin visible", ok: (await marketMargin.count()) === 1 });

// Screenshots dark 380 + 1280
for (const [w, h, name] of [
  [380, 900, "380"],
  [1280, 1100, "1280"],
]) {
  await page.setViewportSize({ width: w, height: h });
  await goto("/");
  await page.emulateMedia({ colorScheme: "dark" });
  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
  await page.evaluate(() => {
    const el = [...document.querySelectorAll("[data-testid=game-row]")].find(
      (r) => r.textContent?.includes("Northwestern") && r.textContent?.includes("Indiana"),
    );
    if (el) {
      const y = el.getBoundingClientRect().top + window.scrollY - 240;
      window.scrollTo(0, Math.max(0, y));
    }
  });
  await page.waitForTimeout(250);
  await page.screenshot({ path: `${OUT}/this-week-${name}-dark.png` });
  console.log("wrote", `${OUT}/this-week-${name}-dark.png`);
}

console.log("--- verify ---");
for (const r of results) {
  console.log(r.ok ? "PASS" : "FAIL", r.check, r.detail ?? "");
}
if (results.some((r) => !r.ok)) {
  process.exitCode = 1;
}

await browser.close();
console.log("deploy aliases https://the-cfb-model.vercel.app");
console.log("deployment https://the-cfb-model-ghlprnkhm-alecs-projects-2eeacfd8.vercel.app");
