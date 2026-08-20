/**
 * W10-D1-NAMES Phase 1 — long-name overflow probe on 2026 W1 dry export.
 * Requires prior `npm run build`. Starts next start on :3103.
 */
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SITE_ROOT = path.resolve(__dirname, "..");
const REPO_ROOT = path.resolve(SITE_ROOT, "../..");
const ARTIFACTS = path.resolve(REPO_ROOT, "docs/notes/_artifacts/webapp-w9i/dry_export");
const OUT = path.resolve(
  REPO_ROOT,
  "docs/notes/_artifacts/webapp-w10-ui/screenshots-d1/long-name-probe-2.json",
);
const PORT = 3103;
const BASE = `http://127.0.0.1:${PORT}`;
const MATCHUP_BOX_PX = 234; // measured side-by-side left column at 390px (D1)

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

const child = spawn("npx", ["next", "start", "-p", String(PORT)], {
  cwd: SITE_ROOT,
  env: {
    ...process.env,
    ARTIFACT_SOURCE: "local",
    ARTIFACT_BASE_PATH: ARTIFACTS,
  },
  stdio: ["ignore", "pipe", "pipe"],
  shell: true,
});

async function waitReady() {
  for (let i = 0; i < 60; i++) {
    try {
      const res = await fetch(`${BASE}/`);
      if (res.ok) return;
    } catch {
      /* retry */
    }
    await sleep(500);
  }
  throw new Error("server not ready");
}

try {
  await waitReady();
  const browser = await chromium.launch();
  const page = await browser.newPage({
    viewport: { width: 390, height: 844 },
  });
  await page.goto(`${BASE}/`, { waitUntil: "networkidle", timeout: 120000 });
  await page.evaluate(() => {
    document.documentElement.setAttribute("data-theme", "light");
  });
  const dismiss = page.getByTestId("dismiss-disclaimer");
  if (await dismiss.count()) {
    await dismiss.click().catch(() => {});
  }
  await sleep(300);

  // Inject a measurement overlay that does not depend on live layout classes.
  const probe = await page.evaluate((boxPx) => {
    const style = document.createElement("style");
    style.textContent = `
      .probe-host { position: absolute; left: -9999px; top: 0; visibility: hidden; }
      .probe-line {
        font-family: "SF Pro Display", "SF Pro Text", -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
        font-size: 15px;
        line-height: 20px;
        font-weight: 600;
        white-space: nowrap;
        display: inline-block;
      }
      .probe-box {
        width: ${boxPx}px;
        overflow: hidden;
      }
    `;
    document.head.appendChild(style);
    const host = document.createElement("div");
    host.className = "probe-host";
    document.body.appendChild(host);

    function measureText(text) {
      const el = document.createElement("span");
      el.className = "probe-line";
      el.textContent = text;
      host.appendChild(el);
      const scrollWidth = el.scrollWidth;
      el.remove();
      return scrollWidth;
    }

    const rows = [...document.querySelectorAll('[data-testid="game-row"]')];
    const pairs = rows.map((row) => {
      const h3 = row.querySelector("h3");
      const raw = (h3?.textContent ?? "").replace(/\s*N\s*$/, "").trim();
      const at = raw.lastIndexOf(" @ ");
      const away = at >= 0 ? raw.slice(0, at) : raw;
      const home = at >= 0 ? raw.slice(at + 3) : "";
      const pair = `${away} @ ${home}`;
      const pairScroll = measureText(pair);
      const awayScroll = measureText(away);
      const homeScroll = measureText(home);
      const liveMatchup = h3;
      const liveClient = liveMatchup?.clientWidth ?? null;
      const liveScroll = liveMatchup?.scrollWidth ?? null;
      return {
        pair,
        away,
        home,
        pairScrollWidth: pairScroll,
        awayScrollWidth: awayScroll,
        homeScrollWidth: homeScroll,
        pairOverflowsBox: pairScroll > boxPx,
        awayOverflowsBox: awayScroll > boxPx,
        homeOverflowsBox: homeScroll > boxPx,
        liveClientWidth: liveClient,
        liveScrollWidth: liveScroll,
        liveOverflows: liveScroll != null && liveClient != null && liveScroll > liveClient + 1,
      };
    });

    host.remove();
    style.remove();

    const overflowing = pairs.filter((p) => p.pairOverflowsBox || p.liveOverflows);
    const byPairLen = [...pairs].sort(
      (a, b) => b.pairScrollWidth - a.pairScrollWidth || b.pair.length - a.pair.length,
    );
    const topFive = byPairLen.slice(0, 5).map((p) => ({
      pair: p.pair,
      pairScrollWidth: p.pairScrollWidth,
      away: p.away,
      home: p.home,
      awayScrollWidth: p.awayScrollWidth,
      homeScrollWidth: p.homeScrollWidth,
      awayOverflows234: p.awayOverflowsBox,
      homeOverflows234: p.homeOverflowsBox,
      eitherNameOverflows234: p.awayOverflowsBox || p.homeOverflowsBox,
    }));

    return {
      viewportPx: 390,
      matchupBoxPx: boxPx,
      t3: "15px / 20px / 600",
      rowCount: pairs.length,
      overflowCount: overflowing.length,
      overflowingPairs: overflowing.map((p) => ({
        pair: p.pair,
        pairScrollWidth: p.pairScrollWidth,
        liveClientWidth: p.liveClientWidth,
        liveScrollWidth: p.liveScrollWidth,
      })),
      topFiveLongest: topFive,
      anySingleNameOverflows234: topFive.some((p) => p.eitherNameOverflows234),
      candidateAViable: !topFive.some((p) => p.eitherNameOverflows234),
    };
  }, MATCHUP_BOX_PX);

  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  fs.writeFileSync(OUT, JSON.stringify(probe, null, 2));
  console.log(JSON.stringify(probe, null, 2));
  await browser.close();
} finally {
  spawn("taskkill", ["/pid", String(child.pid), "/T", "/F"], { stdio: "ignore" });
  await sleep(800);
}
