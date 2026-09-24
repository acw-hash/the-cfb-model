import { chromium } from "playwright";

const BASE =
  process.env.RIDGE_SHOT_BASE ??
  "https://the-cfb-model-56mrk4xac-alecs-projects-2eeacfd8.vercel.app";
const BYPASS = process.env.VERCEL_AUTOMATION_BYPASS_SECRET ?? "";
const OUT = "docs/screenshots/scoreboard/header-tweak";

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
      const y = el.getBoundingClientRect().top + window.scrollY - 240;
      window.scrollTo(0, Math.max(0, y));
    }
  });
  await page.waitForTimeout(250);
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
  await frameIndiana();
  const visible = await page.evaluate(() => {
    const rows = [...document.querySelectorAll("[data-testid=game-row]")];
    return rows.some((r) => {
      const b = r.getBoundingClientRect();
      return (
        b.top >= 0 &&
        b.bottom <= window.innerHeight &&
        r.textContent?.includes("Northwestern") &&
        r.textContent?.includes("Indiana")
      );
    });
  });
  console.log(name, "indianaVisible", visible);
  console.log("legend", await page.locator("[data-testid=slate-legend]").textContent());
  console.log(
    "header",
    (await page.locator("[data-testid=slate-column-header]").innerText()).replace(/\s+/g, " "),
  );
  await page.screenshot({ path: `${OUT}/this-week-${name}-light.png` });
  console.log("wrote", name);
}

await browser.close();
