import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(__dirname, "../docs/screenshots");
const BASE = "http://localhost:3456/gallery";

async function capture(page, name, width, theme) {
  await page.setViewportSize({ width, height: theme === "mobile" ? 844 : 900 });
  await page.goto(BASE, { waitUntil: "networkidle" });
  if (theme === "light") {
    await page.getByRole("button", { name: "light" }).click();
  } else if (theme === "dark") {
    await page.getByRole("button", { name: "dark" }).click();
  }
  await page.waitForTimeout(300);
  await page.screenshot({ path: path.join(OUT, name), fullPage: true });
}

fs.mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch();
const page = await browser.newPage();
await capture(page, "gallery-light-desktop.png", 1280, "light");
await capture(page, "gallery-dark-desktop.png", 1280, "dark");
await capture(page, "gallery-light-mobile.png", 390, "light");
await capture(page, "gallery-dark-mobile.png", 390, "dark");
await browser.close();
console.log("Screenshots saved to", OUT);
