/**
 * Probe: would round-2 lint fail on a synthetic round-1-style chart HTML?
 * Round-1 PNGs were overwritten; this encodes the known round-1 defects.
 */
import { chromium } from "playwright";
import { runLayoutLint } from "./lint.mjs";

const round1StyleHtml = `<!doctype html><html><body>
<div id="root" style="width:800px;height:1000px;position:relative;padding:24px 40px;box-sizing:border-box;font-family:sans-serif;">
  <header data-role="header">RIDGE</header>
  <div data-role="titles"><h1 style="font-size:34px;white-space:normal;">A very long wrapping headline that goes on and on past forty two characters easily</h1>
  <p style="font-size:17px;">Dek that is also quite long and will wrap on the canvas for sure beyond sixty</p></div>
  <main data-role="body">
    <svg class="plot" width="720" height="400" viewBox="0 0 320 200" preserveAspectRatio="none">
      <circle cx="40" cy="40" r="10" fill="#000"></circle>
      <circle cx="48" cy="42" r="10" fill="#111"></circle>
      <text x="10" y="20" font-size="11">tiny</text>
      <text x="100" y="390" font-size="15">clipped-label</text>
      <image class="logo" href="" x="30" y="30" width="40" height="40"></image>
      <image class="logo" href="" x="45" y="35" width="40" height="40"></image>
    </svg>
  </main>
  <footer data-role="footer" style="font-size:12px;">Ridge · Forecasts, not betting advice · the-cfb-model.vercel.app</footer>
</div>
</body></html>`;

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 800, height: 1000 }, deviceScaleFactor: 1 });
await page.setContent(round1StyleHtml);
const result = await runLayoutLint(page, { chart: "readmap" });
await browser.close();
console.log(JSON.stringify(result, null, 2));
