import { chromium } from "playwright";
import { readFileSync } from "fs";
import { pathToFileURL } from "url";
import { runLayoutLint } from "./lint.mjs";

const htmlPath = process.argv[2];
const chart = process.argv[3] || "readmap";
const html = readFileSync(htmlPath, "utf8");
const m = html.match(/width:(\d+)px;height:(\d+)px/);
const width = m ? Number(m[1]) : 800;
const height = m ? Number(m[2]) : 1000;

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 2 });
await page.setContent(html, { waitUntil: "networkidle" });
await page.evaluate(() => document.fonts.ready);
const detail = await page.evaluate(() => {
  const root = document.getElementById("root");
  const rootBox = root.getBoundingClientRect();
  const cs = getComputedStyle(root);
  const padT = parseFloat(cs.paddingTop);
  const padB = parseFloat(cs.paddingBottom);
  const contentBottom = rootBox.bottom - padB;
  const label = [...root.querySelectorAll("svg text")].find((t) =>
    (t.textContent || "").includes("Net rating"),
  );
  const r = label ? label.getBoundingClientRect() : null;
  const svg = root.querySelector("svg.plot");
  const sr = svg ? svg.getBoundingClientRect() : null;
  const hdr = root.querySelector("header")?.getBoundingClientRect();
  const titles = root.querySelector("[data-role='titles']")?.getBoundingClientRect();
  const main = root.querySelector("main")?.getBoundingClientRect();
  const footer = root.querySelector("footer")?.getBoundingClientRect();
  return { rootBox, padT, padB, contentBottom, label: r, svg: sr, hdr, titles, main, footer };
});
console.log(JSON.stringify(detail, null, 2));
const result = await runLayoutLint(page, { chart });
console.log("lint", JSON.stringify(result, null, 2));
await browser.close();
