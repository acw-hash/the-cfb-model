/**
 * W12-2 G2b before/after screenshots on fixture data only.
 * RIDGE_SHOT_BASE (default http://127.0.0.1:3000), RIDGE_SHOT_PHASE=before|after
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PHASE = process.env.RIDGE_SHOT_PHASE ?? "after";
const OUT = path.resolve(
  __dirname,
  process.env.RIDGE_SHOT_OUT ??
    `../../../docs/notes/_artifacts/w12-2/screenshots/${PHASE}`,
);
const BASE = process.env.RIDGE_SHOT_BASE ?? "http://127.0.0.1:3000";
const GAME_PATH = process.env.RIDGE_SHOT_GAME_PATH ?? "/game/401628373";
const VISUAL_PATH = process.env.RIDGE_SHOT_VISUAL_PATH ?? "/visual?step=2";

fs.mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();
const page = await browser.newPage();

const shots = [
  { w: 1280, h: 900, name: "home-1280", path: "/" },
  { w: 380, h: 900, name: "home-380", path: "/" },
  { w: 1280, h: 1100, name: "game-detail-1280", path: GAME_PATH },
  { w: 1280, h: 1100, name: "visual-1280", path: VISUAL_PATH },
];

for (const shot of shots) {
  await page.setViewportSize({ width: shot.w, height: shot.h });
  const resp = await page.goto(`${BASE}${shot.path}`, {
    waitUntil: "networkidle",
    timeout: 60000,
  });
  if (!resp || !resp.ok()) {
    throw new Error(`GET ${shot.path} -> ${resp?.status()}`);
  }
  await page.waitForTimeout(400);
  const file = `${shot.name}.png`;
  await page.screenshot({ path: path.join(OUT, file), fullPage: false });
  console.log("wrote", path.join(OUT, file));
}

await browser.close();
console.log("screenshots in", OUT, "phase=", PHASE);
