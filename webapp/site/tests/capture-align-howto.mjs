import fs from "node:fs";
import { chromium } from "playwright";

const BASE =
  process.env.RIDGE_SHOT_BASE ??
  "https://the-cfb-model-9cd5knuna-alecs-projects-2eeacfd8.vercel.app";
const BYPASS = process.env.VERCEL_AUTOMATION_BYPASS_SECRET ?? "";
const OUT = "docs/screenshots/scoreboard/align-howto";

fs.mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();
const context = await browser.newContext(
  BYPASS ? { extraHTTPHeaders: { "x-vercel-protection-bypass": BYPASS } } : {},
);
const page = await context.newPage();

async function frameIndiana() {
  await page.evaluate(() => {
    const el = [...document.querySelectorAll("[data-testid=game-row]")].find(
      (r) => r.textContent?.includes("Northwestern") && r.textContent?.includes("Indiana"),
    );
    if (el) {
      const y = el.getBoundingClientRect().top + window.scrollY - 260;
      window.scrollTo(0, Math.max(0, y));
    }
  });
  await page.waitForTimeout(300);
}

for (const [w, h, name] of [
  [380, 900, "380"],
  [1280, 1100, "1280"],
]) {
  await page.setViewportSize({ width: w, height: h });
  const resp = await page.goto(`${BASE}/`, { waitUntil: "networkidle", timeout: 60000 });
  if (!resp?.ok()) throw new Error(`GET / -> ${resp?.status()}`);
  await page.emulateMedia({ colorScheme: "light" });
  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "light"));
  // Ensure howto line is visible (fresh storage).
  await page.evaluate(() => {
    try {
      sessionStorage.removeItem("ridge-howto-read-seen");
    } catch {
      /* ignore */
    }
  });
  await page.reload({ waitUntil: "networkidle" });
  await page.emulateMedia({ colorScheme: "light" });
  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "light"));
  await frameIndiana();
  const howto = await page.locator("[data-testid=how-to-read-key]").count();
  const header = (await page.locator("[data-testid=slate-column-header]").innerText()).replace(
    /\s+/g,
    " ",
  );
  console.log(name, { howto, header });
  await page.screenshot({ path: `${OUT}/this-week-${name}-light.png` });
  console.log("wrote", name);
}

await browser.close();
