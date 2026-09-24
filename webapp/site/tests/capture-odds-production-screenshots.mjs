/**
 * Production go-live screenshots — / and one /game at 380 + 1280, light + dark.
 * Requires RIDGE_SHOT_BASE and optional Vercel bypass via cookie/header in page.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(__dirname, "../docs/screenshots/odds/production-golive");
const BASE = process.env.RIDGE_SHOT_BASE ?? "https://the-cfb-model.vercel.app";
const BYPASS = process.env.VERCEL_AUTOMATION_BYPASS_SECRET ?? "";
const GAME_ID = process.env.RIDGE_SHOT_GAME_ID ?? "401864576";

async function applyTheme(page, theme) {
  await page.emulateMedia({ colorScheme: theme });
  await page.evaluate((t) => {
    document.documentElement.setAttribute("data-theme", t);
  }, theme);
}

fs.mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();
const context = await browser.newContext();
if (BYPASS) {
  await context.setExtraHTTPHeaders({
    "x-vercel-protection-bypass": BYPASS,
  });
}
const page = await context.newPage();

const shots = [
  { w: 380, h: 844, name: "380", path: "/" },
  { w: 1280, h: 900, name: "1280", path: "/" },
  { w: 380, h: 844, name: "380", path: `/game/${GAME_ID}` },
  { w: 1280, h: 900, name: "1280", path: `/game/${GAME_ID}` },
];

for (const shot of shots) {
  await page.setViewportSize({ width: shot.w, height: shot.h });
  for (const theme of ["light", "dark"]) {
    const resp = await page.goto(`${BASE}${shot.path}`, { waitUntil: "networkidle" });
    if (!resp || !resp.ok()) throw new Error(`GET ${shot.path} -> ${resp?.status()}`);
    await applyTheme(page, theme);
    await page.waitForTimeout(300);
    const slug = shot.path === "/" ? "this-week" : "game-detail";
    const file = `${slug}-${shot.name}-${theme}.png`;
    await page.screenshot({ path: path.join(OUT, file), fullPage: true });
    console.log("wrote", file);
  }
}

await browser.close();
console.log("production screenshots in", OUT);
