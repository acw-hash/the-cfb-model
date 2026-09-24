/**
 * Scoreboard after shots — scroll so Liberty@Coastal and Howard@Rutgers
 * are in frame when present. Uses RIDGE_SHOT_BASE / RIDGE_SHOT_OUT / BYPASS.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(
  __dirname,
  process.env.RIDGE_SHOT_OUT ?? "../docs/screenshots/scoreboard/after",
);
const BASE = process.env.RIDGE_SHOT_BASE ?? "https://the-cfb-model.vercel.app";
const BYPASS = process.env.VERCEL_AUTOMATION_BYPASS_SECRET ?? "";

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

// Discover Liberty @ Coastal game id for /game shot.
await page.setViewportSize({ width: 1280, height: 900 });
const homeResp = await page.goto(`${BASE}/`, { waitUntil: "networkidle", timeout: 60000 });
if (!homeResp || !homeResp.ok()) {
  throw new Error(`GET / -> ${homeResp?.status()}`);
}
const coastalLink = page
  .locator('a[href^="/game/"]')
  .filter({ hasText: "Coastal Carolina" })
  .first();
const howardLink = page.locator('a[href^="/game/"]').filter({ hasText: "Howard" }).first();
let gamePath = process.env.RIDGE_SHOT_GAME_ID ? `/game/${process.env.RIDGE_SHOT_GAME_ID}` : null;
if ((await coastalLink.count()) > 0) {
  const href = await coastalLink.getAttribute("href");
  if (href) gamePath = href;
  console.log("coastal href", href);
}
if ((await howardLink.count()) > 0) {
  console.log("howard href", await howardLink.getAttribute("href"));
}
if (!gamePath) {
  gamePath = "/game/401864576";
}

const shots = [
  { w: 380, h: 900, name: "380", path: "/" },
  { w: 1280, h: 1100, name: "1280", path: "/" },
  { w: 380, h: 900, name: "380", path: gamePath },
];

for (const shot of shots) {
  await page.setViewportSize({ width: shot.w, height: shot.h });
  for (const theme of ["light", "dark"]) {
    const resp = await page.goto(`${BASE}${shot.path}`, {
      waitUntil: "networkidle",
      timeout: 60000,
    });
    if (!resp || !resp.ok()) {
      throw new Error(`GET ${shot.path} -> ${resp?.status()}`);
    }
    await applyTheme(page, theme);
    await page.waitForTimeout(400);
    if (shot.path === "/") {
      // Frame Thursday openers: Liberty @ Coastal (split) + Howard @ Rutgers.
      const coastal = page.locator("text=Coastal Carolina").first();
      if ((await coastal.count()) > 0) {
        await coastal.scrollIntoViewIfNeeded();
        await page.evaluate(() => {
          const el = [...document.querySelectorAll("[data-testid=game-row]")].find((r) =>
            r.textContent?.includes("Coastal Carolina"),
          );
          if (el) {
            const y = el.getBoundingClientRect().top + window.scrollY - 200;
            window.scrollTo(0, Math.max(0, y));
          }
        });
        await page.waitForTimeout(200);
      }
    }
    const slug = shot.path === "/" ? "this-week" : "game-detail";
    const file = `${slug}-${shot.name}-${theme}.png`;
    await page.screenshot({ path: path.join(OUT, file), fullPage: false });
    console.log("wrote", file);
  }
}

await browser.close();
console.log("screenshots in", OUT);
