/**
 * Re-shoot /about only (clarity final copy round).
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(__dirname, "../../../docs/notes/_artifacts/webapp-clarity/shots");
const BASE = process.env.RIDGE_SHOT_BASE ?? "http://127.0.0.1:3463";

const VIEWPORTS = [
  { id: "390", width: 390, height: 844 },
  { id: "desktop", width: 1280, height: 900 },
];

fs.mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();
const errors = [];
const textOut = path.join(OUT, "about-rendered.txt");

for (const theme of ["light", "dark"]) {
  for (const vp of VIEWPORTS) {
    const context = await browser.newContext({
      viewport: { width: vp.width, height: vp.height },
      colorScheme: theme === "dark" ? "dark" : "light",
    });
    const page = await context.newPage();
    page.on("console", (msg) => {
      if (msg.type() === "error" && !msg.text().includes("404")) {
        errors.push(`${theme}/${vp.id}: ${msg.text()}`);
      }
    });
    const response = await page.goto(`${BASE}/about`, {
      waitUntil: "networkidle",
      timeout: 90000,
    });
    if (!response?.ok()) {
      throw new Error(`GET /about -> ${response?.status()}`);
    }
    await page.evaluate((t) => document.documentElement.setAttribute("data-theme", t), theme);
    const dismiss = page.getByTestId("dismiss-disclaimer");
    if (await dismiss.count()) {
      await dismiss.click().catch(() => {});
    }
    await page.waitForTimeout(250);

    if (theme === "light" && vp.id === "desktop") {
      const article = page.getByTestId("about-page");
      const text = await article.innerText();
      fs.writeFileSync(textOut, text, "utf8");
      console.log(`wrote ${path.basename(textOut)}`);
    }

    const filename = `about-${theme}-${vp.id}.png`;
    await page.screenshot({ path: path.join(OUT, filename), fullPage: true });
    console.log(`wrote ${filename}`);
    await context.close();
  }
}

await browser.close();
if (errors.length) {
  console.error("console errors:", errors);
  process.exit(1);
}
console.log("about-only capture ok");
