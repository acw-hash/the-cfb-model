#!/usr/bin/env node
/**
 * SOC-2 renderer: spec JSON → static HTML → Playwright Chromium PNG.
 * Layout at CSS 1×; screenshot with deviceScaleFactor 2 by default.
 */
import {
  copyFileSync,
  mkdirSync,
  readFileSync,
  renameSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { performance } from "node:perf_hooks";
import { chromium } from "playwright";
import { renderChartHtml } from "./charts.mjs";
import { fontFaceCss, resolveFonts } from "./fonts.mjs";
import { runLayoutLint } from "./lint.mjs";
import { loadTokensCss } from "./tokens.mjs";

function parseArgs(argv) {
  const out = { spec: null, out: null, lint: true, final: false };
  for (let i = 2; i < argv.length; i += 1) {
    if (argv[i] === "--spec") out.spec = argv[++i];
    else if (argv[i] === "--out") out.out = argv[++i];
    else if (argv[i] === "--no-lint") out.lint = false;
    else if (argv[i] === "--final") out.final = true;
  }
  if (!out.spec || !out.out) {
    console.error(
      "Usage: node src/cli.mjs --spec <spec.json> --out <out.png> [--no-lint] [--final]",
    );
    process.exit(2);
  }
  if (out.final && !out.lint) {
    console.error("--no-lint is refused when producing final outputs (--final)");
    process.exit(2);
  }
  return out;
}

async function main() {
  const args = parseArgs(process.argv);
  const t0 = performance.now();
  const spec = JSON.parse(readFileSync(args.spec, "utf8"));
  const fonts = resolveFonts();
  const tokensCss = loadTokensCss();
  const body = renderChartHtml(spec);
  const width = spec.frame.width;
  const height = spec.frame.height;
  const dsf = Number(spec.frame.device_scale_factor || 2);

  const html = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=${width}, height=${height}" />
<style>
${fontFaceCss(fonts)}
${tokensCss}
* { box-sizing: border-box; }
html, body {
  margin: 0; padding: 0;
  width: ${width}px; height: ${height}px;
  background: var(--bg-primary);
  font-family: "${fonts.family}", var(--font-stack);
  -webkit-font-smoothing: antialiased;
}
.chart-root, .chart-root * {
  font-variant-numeric: tabular-nums lining-nums;
  font-feature-settings: "tnum" 1, "lnum" 1;
}
</style>
</head>
<body>
${body}
</body>
</html>`;

  const here = dirname(fileURLToPath(import.meta.url));
  const tmpDir = join(here, "../.tmp");
  mkdirSync(tmpDir, { recursive: true });
  const htmlPath = join(tmpDir, `render-${process.pid}.html`);
  writeFileSync(htmlPath, html, "utf8");

  const htmlOut = args.out.replace(/\.png$/i, ".html");
  writeFileSync(htmlOut, html, "utf8");

  const browser = await chromium.launch({
    headless: true,
    args: ["--disable-lcd-text", "--disable-font-subpixel-positioning"],
  });
  let lint = { ok: true, errors: [] };
  try {
    const page = await browser.newPage({
      viewport: { width, height },
      deviceScaleFactor: dsf,
      reducedMotion: "reduce",
    });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto(pathToFileURL(htmlPath).href, { waitUntil: "networkidle" });
    await page.evaluate(() => document.fonts.ready);
    await page.addStyleTag({
      content: `*, *::before, *::after { animation: none !important; transition: none !important; }`,
    });
    if (args.lint) {
      lint = await runLayoutLint(page, spec);
      if (!lint.ok) {
        const detail = (lint.errors || []).join("; ");
        throw new Error(`Layout lint failed [${spec.chart}]: ${detail}`);
      }
    }
    const root = await page.$("#root");
    if (!root) throw new Error("Missing #root in rendered HTML");
    // Write via temp then replace — Windows locks open PNGs in IDE preview.
    const tmpPng = join(tmpDir, `shot-${process.pid}-${Date.now()}.png`);
    await root.screenshot({
      path: tmpPng,
      type: "png",
      animations: "disabled",
      omitBackground: false,
    });
    try {
      renameSync(tmpPng, args.out);
    } catch {
      copyFileSync(tmpPng, args.out);
      try {
        unlinkSync(tmpPng);
      } catch {
        /* ignore */
      }
    }
  } finally {
    await browser.close();
  }

  const ms = Math.round(performance.now() - t0);
  console.log(
    JSON.stringify({
      ok: true,
      out: args.out,
      width,
      height,
      device_scale_factor: dsf,
      output_width: width * dsf,
      output_height: height * dsf,
      chart: spec.chart,
      font: fonts.family,
      engine: "playwright-chromium",
      render_ms: ms,
      lint_ok: lint.ok,
      lint_errors: lint.errors || [],
    }),
  );
}

main().catch((err) => {
  console.error(err?.stack || String(err));
  process.exit(1);
});
