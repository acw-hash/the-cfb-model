/**
 * Phase 3 sandbox preview screenshots (live week + sandbox odds).
 * Requires next start with ARTIFACT_BASE_PATH=_phase3_sandbox_preview and
 * ODDS_SNAPSHOT_ENABLED=true.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(__dirname, "../docs/screenshots/odds/phase3-sandbox");
const BASE = process.env.RIDGE_SHOT_BASE ?? "http://localhost:3461";
const ARTIFACTS = path.resolve(
  __dirname,
  "../../fixtures/_phase3_sandbox_preview/week_predictions.json",
);

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

const week = JSON.parse(fs.readFileSync(ARTIFACTS, "utf8"));
const gameId = week.games[0].game_id;

const browser = await chromium.launch();
const page = await browser.newPage();

const shots = [
  { w: 380, h: 844, name: "380", path: "/" },
  { w: 1280, h: 900, name: "1280", path: "/" },
  { w: 380, h: 844, name: "380", path: `/game/${gameId}` },
  { w: 1280, h: 900, name: "1280", path: `/game/${gameId}` },
];

for (const shot of shots) {
  await page.setViewportSize({ width: shot.w, height: shot.h });
  for (const theme of ["light", "dark"]) {
    await goto(page, shot.path);
    await applyTheme(page, theme);
    await page.waitForTimeout(250);
    const slug = shot.path === "/" ? "this-week" : "game-detail";
    const file = `${slug}-${shot.name}-${theme}.png`;
    await page.screenshot({ path: path.join(OUT, file), fullPage: true });
    console.log("wrote", file);
  }
}

await browser.close();
console.log("Phase 3 sandbox screenshots in", OUT);
