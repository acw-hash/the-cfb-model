/**
 * W10-D1 capture suite — against `next start` only (not `next dev`).
 *
 * Usage (from webapp/site, after `npm run build`):
 *   node tests/capture-w10-d1.mjs
 *
 * Starts its own `next start` on :3101 with ARTIFACT_SOURCE=local.
 * Writes to docs/notes/_artifacts/webapp-w10-ui/screenshots-d1/
 */
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SITE_ROOT = path.resolve(__dirname, "..");
const REPO_ROOT = path.resolve(SITE_ROOT, "../..");
const FIXTURES = path.resolve(REPO_ROOT, "webapp/fixtures");
const OUT = path.resolve(REPO_ROOT, "docs/notes/_artifacts/webapp-w10-ui/screenshots-d1");
const PORT = 3101;
const BASE = `http://127.0.0.1:${PORT}`;

const LONG_NAME = "Mississippi Valley State @ Sacramento State";

fs.mkdirSync(OUT, { recursive: true });

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function copyFixtures(dest, overrides = {}) {
  fs.mkdirSync(dest, { recursive: true });
  for (const name of fs.readdirSync(FIXTURES)) {
    if (name.endsWith(".json")) {
      fs.copyFileSync(path.join(FIXTURES, name), path.join(dest, name));
    }
  }
  for (const [name, src] of Object.entries(overrides)) {
    fs.copyFileSync(src, path.join(dest, name));
  }
}

async function waitForServer(url, attempts = 60) {
  for (let i = 0; i < attempts; i++) {
    try {
      const res = await fetch(url);
      if (res.ok || res.status === 500) {
        return;
      }
    } catch {
      /* retry */
    }
    await sleep(500);
  }
  throw new Error(`Server not ready at ${url}`);
}

function startServer(artifactBase) {
  const child = spawn("npx", ["next", "start", "-p", String(PORT)], {
    cwd: SITE_ROOT,
    env: {
      ...process.env,
      ARTIFACT_SOURCE: "local",
      ARTIFACT_BASE_PATH: artifactBase,
      PORT: String(PORT),
    },
    stdio: ["ignore", "pipe", "pipe"],
    shell: process.platform === "win32",
  });
  child.stdout.on("data", (d) => process.stdout.write(`[next] ${d}`));
  child.stderr.on("data", (d) => process.stderr.write(`[next] ${d}`));
  return child;
}

async function stopServer(child) {
  if (!child.pid) {
    return;
  }
  if (process.platform === "win32") {
    spawn("taskkill", ["/pid", String(child.pid), "/T", "/F"], { stdio: "ignore" });
  } else {
    child.kill("SIGTERM");
  }
  await sleep(1000);
}

async function dismissChrome(page) {
  const dismiss = page.getByTestId("dismiss-disclaimer");
  if (await dismiss.count()) {
    await dismiss.click().catch(() => {});
  }
}

async function shot(page, name, options = {}) {
  const file = path.join(OUT, name);
  await page.screenshot({ path: file, fullPage: true, ...options });
  console.log(`wrote ${name}`);
}

async function withServer(artifactBase, fn) {
  const child = startServer(artifactBase);
  try {
    await waitForServer(`${BASE}/`);
    await fn();
  } finally {
    await stopServer(child);
  }
}

const VIEWPORTS = [
  { id: "mobile", width: 390, height: 844 },
  { id: "desktop", width: 1280, height: 900 },
];
const THEMES = ["light", "dark"];

async function captureMatrix(browser, routes) {
  for (const route of routes) {
    for (const theme of THEMES) {
      for (const vp of VIEWPORTS) {
        const context = await browser.newContext({
          viewport: { width: vp.width, height: vp.height },
          colorScheme: theme === "dark" ? "dark" : "light",
        });
        const page = await context.newPage();
        const response = await page.goto(`${BASE}${route.path}`, {
          waitUntil: "networkidle",
          timeout: 90000,
        });
        if (!response || !response.ok()) {
          throw new Error(`GET ${route.path} -> ${response?.status()}`);
        }
        await page.evaluate((t) => {
          document.documentElement.setAttribute("data-theme", t);
        }, theme);
        await dismissChrome(page);
        if (route.prepare) {
          await route.prepare(page);
        }
        await sleep(200);
        await shot(page, `${route.id}-${vp.id}-${theme}.png`);
        await context.close();
      }
    }
  }
}

async function measureRows(browser) {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    colorScheme: "light",
  });
  const page = await context.newPage();
  const response = await page.goto(`${BASE}/`, { waitUntil: "networkidle", timeout: 90000 });
  if (!response || !response.ok()) {
    throw new Error(`GET / -> ${response?.status()}`);
  }
  await page.evaluate(() => {
    document.documentElement.setAttribute("data-theme", "light");
  });
  await dismissChrome(page);
  await sleep(200);

  const metrics = await page.evaluate(() => {
    const rows = [...document.querySelectorAll('[data-testid="game-row"]')];
    const heights = rows.map((el) => Math.round(el.getBoundingClientRect().height));
    const slate = document.querySelector('[data-testid="slate"]') ?? document.body;
    const headers = [
      ...document.querySelectorAll('[data-testid="slate"] section[data-group] > h2'),
    ];
    const headerHeights = headers.map((el) => Math.round(el.getBoundingClientRect().height));
    return {
      rowCount: rows.length,
      rowHeightsPx: heights,
      minRowPx: heights.length ? Math.min(...heights) : null,
      maxRowPx: heights.length ? Math.max(...heights) : null,
      rowBodyHeightPx: heights.reduce((a, b) => a + b, 0),
      groupHeaderCount: headers.length,
      groupHeaderHeightsPx: headerHeights,
      groupHeaderHeightPx: headerHeights.reduce((a, b) => a + b, 0),
      slateHeightPx: Math.round(slate.getBoundingClientRect().height),
    };
  });

  fs.writeFileSync(path.join(OUT, "row-height-metrics.json"), JSON.stringify(metrics, null, 2));
  console.log("row-height-metrics:", JSON.stringify(metrics, null, 2));

  // Null-interval row: first row with absent mark, or clone via CSS probe on fixture
  const absent = page.locator('[data-testid="interval-absent"]').first();
  if ((await absent.count()) > 0) {
    await absent.scrollIntoViewIfNeeded();
    const row = page.locator('[data-testid="game-row"]').filter({ has: absent }).first();
    await row.screenshot({ path: path.join(OUT, "null-interval-row-mobile-light.png") });
    console.log("wrote null-interval-row-mobile-light.png");
  }

  await context.close();
  return metrics;
}

async function captureLongName(browser) {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    colorScheme: "light",
  });
  const page = await context.newPage();
  const response = await page.goto(`${BASE}/`, { waitUntil: "networkidle", timeout: 90000 });
  if (!response || !response.ok()) {
    throw new Error(`GET / (long-name slate) -> ${response?.status()}`);
  }
  await page.evaluate(() => {
    document.documentElement.setAttribute("data-theme", "light");
  });
  await dismissChrome(page);
  await sleep(200);

  const row = page.locator('[data-testid="game-row"]').filter({ hasText: LONG_NAME }).first();
  if ((await row.count()) === 0) {
    throw new Error(`Long-name pair not found: ${LONG_NAME}`);
  }
  await row.scrollIntoViewIfNeeded();

  const probe = await row.evaluate((el) => {
    const matchup = el.querySelector("h3");
    if (!matchup) {
      return { error: "no h3" };
    }
    const style = getComputedStyle(matchup);
    return {
      text: matchup.textContent?.trim() ?? "",
      clientWidth: matchup.clientWidth,
      scrollWidth: matchup.scrollWidth,
      whiteSpace: style.whiteSpace,
      textOverflow: style.textOverflow,
      wraps: matchup.clientHeight > parseFloat(style.lineHeight) * 1.5,
      truncates: matchup.scrollWidth > matchup.clientWidth + 1,
    };
  });

  fs.writeFileSync(path.join(OUT, "long-name-probe.json"), JSON.stringify(probe, null, 2));
  console.log("long-name probe:", JSON.stringify(probe, null, 2));
  await row.screenshot({ path: path.join(OUT, "long-name-pair-mobile-light.png") });
  console.log("wrote long-name-pair-mobile-light.png");
  await shot(page, "long-name-slate-mobile-light.png");
  await context.close();
  return probe;
}

async function captureNullHeavy(browser) {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    colorScheme: "light",
  });
  const page = await context.newPage();
  for (const [vp, name] of [
    [{ width: 390, height: 844 }, "null-heavy-slate-mobile-light.png"],
    [{ width: 1280, height: 900 }, "null-heavy-slate-desktop-light.png"],
  ]) {
    await page.setViewportSize(vp);
    const response = await page.goto(`${BASE}/`, { waitUntil: "networkidle", timeout: 90000 });
    if (!response || !response.ok()) {
      throw new Error(`GET / (null-heavy) -> ${response?.status()}`);
    }
    await page.evaluate(() => {
      document.documentElement.setAttribute("data-theme", "light");
    });
    await dismissChrome(page);
    await sleep(200);
    await shot(page, name);
  }

  // Conviction sort with nulls
  await page.setViewportSize({ width: 390, height: 844 });
  const response = await page.goto(`${BASE}/?order=conviction`, {
    waitUntil: "networkidle",
    timeout: 90000,
  });
  if (!response || !response.ok()) {
    throw new Error(`GET /?order=conviction -> ${response?.status()}`);
  }
  await page.evaluate(() => {
    document.documentElement.setAttribute("data-theme", "light");
  });
  await dismissChrome(page);
  await sleep(200);
  await shot(page, "conviction-sort-mobile-light.png");
  await context.close();
}

async function captureZeroBanner(browser) {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    colorScheme: "light",
  });
  const page = await context.newPage();
  for (const [vp, name] of [
    [{ width: 390, height: 844 }, "zero-banner-mobile-light.png"],
    [{ width: 1280, height: 900 }, "zero-banner-desktop-light.png"],
  ]) {
    await page.setViewportSize(vp);
    const response = await page.goto(`${BASE}/`, { waitUntil: "networkidle", timeout: 90000 });
    if (!response || !response.ok()) {
      throw new Error(`GET / (zero-banner) -> ${response?.status()}`);
    }
    await page.evaluate(() => {
      document.documentElement.setAttribute("data-theme", "light");
      sessionStorage.setItem("ridge-disclaimer-seen", "1");
      sessionStorage.setItem("ridge-disclaimer-dismissed", "1");
    });
    await page.reload({ waitUntil: "networkidle" });
    await sleep(200);
    await shot(page, name);
  }
  await context.close();
}

const defaultDir = path.join(OUT, "_artifact-default");
const nullHeavyDir = path.join(OUT, "_artifact-null-heavy");
const zeroBannerDir = path.join(OUT, "_artifact-zero-banner");
const longNameDir = path.resolve(REPO_ROOT, "docs/notes/_artifacts/webapp-w9i/dry_export");

copyFixtures(defaultDir);

copyFixtures(nullHeavyDir, {
  "week_predictions.json": path.resolve(
    SITE_ROOT,
    "tests/fixtures/week_predictions_null_heavy.json",
  ),
});

copyFixtures(zeroBannerDir);
const zeroMeta = JSON.parse(fs.readFileSync(path.join(zeroBannerDir, "meta.json"), "utf8"));
zeroMeta.fixture = false;
zeroMeta.published_at = new Date().toISOString();
zeroMeta.next_expected_publish_utc = new Date(Date.now() + 7 * 864e5).toISOString();
fs.writeFileSync(path.join(zeroBannerDir, "meta.json"), JSON.stringify(zeroMeta, null, 2));

const browser = await chromium.launch();

console.log("=== default fixture slate (next start) ===");
await withServer(defaultDir, async () => {
  await captureMatrix(browser, [
    { id: "home", path: "/" },
    { id: "game-detail", path: "/game/401628373" },
    {
      id: "results-tab-a",
      path: "/results?tab=games",
      prepare: async (page) => {
        await page
          .getByTestId("tab-games")
          .click()
          .catch(() => {});
        await sleep(150);
      },
    },
    {
      id: "results-tab-b",
      path: "/results",
      prepare: async (page) => {
        await page
          .getByTestId("tab-record")
          .click()
          .catch(() => {});
        await sleep(150);
      },
    },
    { id: "about", path: "/about" },
  ]);
  await measureRows(browser);
});

console.log("=== null-heavy ===");
await withServer(nullHeavyDir, async () => {
  await captureNullHeavy(browser);
  // Absence row from null-heavy slate
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    colorScheme: "light",
  });
  const page = await context.newPage();
  await page.goto(`${BASE}/`, { waitUntil: "networkidle", timeout: 90000 });
  await page.evaluate(() => {
    document.documentElement.setAttribute("data-theme", "light");
  });
  await dismissChrome(page);
  const absent = page.locator('[data-testid="interval-absent"]').first();
  if ((await absent.count()) === 0) {
    throw new Error("null-heavy slate has no interval-absent row");
  }
  await absent.scrollIntoViewIfNeeded();
  const row = page.locator('[data-testid="game-row"]').filter({ has: absent }).first();
  await row.screenshot({ path: path.join(OUT, "null-interval-row-mobile-light.png") });
  console.log("wrote null-interval-row-mobile-light.png (null-heavy)");
  await context.close();
});

console.log("=== zero-banner ===");
await withServer(zeroBannerDir, async () => {
  await captureZeroBanner(browser);
});

console.log("=== long-name 2026 W1 ===");
await withServer(longNameDir, async () => {
  const probe = await captureLongName(browser);
  if (probe.wraps || probe.truncates) {
    console.error("STOP: long-name pair wraps or truncates", probe);
    process.exitCode = 2;
  }
});

await browser.close();

fs.writeFileSync(
  path.join(OUT, "CAPTURE.md"),
  [
    "# W10-D1 captures",
    "",
    "Produced by: `node tests/capture-w10-d1.mjs` against `next start` (port 3101).",
    "`ARTIFACT_SOURCE=local` with per-scenario `ARTIFACT_BASE_PATH`.",
    "Not `next dev`.",
    "",
  ].join("\n"),
);

console.log(`Done. Screenshots in ${OUT}`);
