/**
 * Probe: round-5 lint must fail on frozen round-4 learned + readmap HTML.
 * Expected failures:
 *   - learned: Truncated text ("Rating chan…")
 *   - readmap: Overlapping text (quad vs rank ticks) and/or logo overlap > 8%
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { runLayoutLint } from "./lint.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..", "..", "..");
const fix = join(root, "docs", "notes", "_artifacts", "social-charts-w6", "round4_lint");

async function lintHtml(path, chart) {
  const html = readFileSync(path, "utf8");
  const m = html.match(/width:(\d+)px;height:(\d+)px/);
  const width = m ? Number(m[1]) : 800;
  const height = m ? Number(m[2]) : 1000;
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({
    viewport: { width, height },
    deviceScaleFactor: 1,
  });
  await page.setContent(html, { waitUntil: "domcontentloaded" });
  const result = await runLayoutLint(page, { chart });
  await browser.close();
  return result;
}

const learned = await lintHtml(join(fix, "learned_w5_4x5.html"), "learned");
const readmap = await lintHtml(join(fix, "readmap_w6_4x5.html"), "readmap");
const out = { learned, readmap };
console.log(JSON.stringify(out, null, 2));

const okLearnedFail =
  learned.ok === false &&
  (learned.errors || []).some((e) => /Truncated text|Rating chan/i.test(e));
const okReadTextFail =
  readmap.ok === false &&
  (readmap.errors || []).some((e) => /Overlapping text/i.test(e));
const okReadLogoFail =
  readmap.ok === false &&
  (readmap.errors || []).some((e) => /Logo overlap/i.test(e));
const okReadFail = okReadTextFail || okReadLogoFail;

if (!okLearnedFail || !okReadFail) {
  console.error("EXPECTED_FAILURES_MISSING", {
    okLearnedFail,
    okReadTextFail,
    okReadLogoFail,
  });
  process.exit(1);
}
process.exit(0);
