#!/usr/bin/env node
/**
 * Live gallery of SOC-2 specs under out/social/<season>/w<week>/specs/.
 * Renders at 1× CSS size; 4:5 and 16:9 side by side; reloads on change.
 */
import { createServer } from "node:http";
import { existsSync, readdirSync, readFileSync, statSync, watch } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { renderChartHtml } from "./charts.mjs";
import { fontFaceCss, resolveFonts } from "./fonts.mjs";
import { loadTokensCss } from "./tokens.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "../../..");
const defaultSpecs = resolve(repoRoot, "out/social");

function findSpecDirs(root) {
  const out = [];
  if (!existsSync(root)) return out;
  for (const season of readdirSync(root)) {
    const seasonPath = join(root, season);
    if (!statSync(seasonPath).isDirectory()) continue;
    for (const week of readdirSync(seasonPath)) {
      const specs = join(seasonPath, week, "specs");
      if (existsSync(specs) && statSync(specs).isDirectory()) out.push(specs);
    }
  }
  return out.sort();
}

function listSpecs(specsRoot) {
  const dirs = specsRoot ? [specsRoot] : findSpecDirs(defaultSpecs);
  const files = [];
  for (const d of dirs) {
    for (const name of readdirSync(d)) {
      if (name.endsWith(".json")) files.push(join(d, name));
    }
  }
  return files.sort();
}

function groupByChart(files) {
  /** @type {Map<string, {a?: string, b?: string}>} */
  const map = new Map();
  for (const f of files) {
    const base = f.replace(/\\/g, "/").split("/").pop().replace(/\.json$/, "");
    const size = base.endsWith("_16x9") ? "16x9" : base.endsWith("_4x5") ? "4x5" : "other";
    const key = base.replace(/_4x5$/, "").replace(/_16x9$/, "");
    if (!map.has(key)) map.set(key, {});
    const row = map.get(key);
    if (size === "4x5") row.a = f;
    else if (size === "16x9") row.b = f;
    else row.a = f;
  }
  return map;
}

function renderPage(files) {
  const fonts = resolveFonts();
  const tokensCss = loadTokensCss();
  const groups = groupByChart(files);
  let cards = "";
  for (const [key, pair] of groups) {
    const panes = [];
    for (const path of [pair.a, pair.b].filter(Boolean)) {
      const spec = JSON.parse(readFileSync(path, "utf8"));
      // Preview always at 1× (ignore device_scale_factor for speed).
      const html = renderChartHtml(spec);
      panes.push(`
        <div class="pane">
          <div class="pane-label">${esc(relative(repoRoot, path))} · ${spec.frame.width}×${spec.frame.height}</div>
          <div class="pane-frame" style="width:${spec.frame.width}px;height:${spec.frame.height}px;">${html}</div>
        </div>`);
    }
    cards += `<section class="card"><h2>${esc(key)}</h2><div class="row">${panes.join("")}</div></section>`;
  }
  return `<!doctype html>
<html lang="en"><head>
<meta charset="utf-8" />
<title>Ridge SOC-2 preview</title>
<style>
${fontFaceCss(fonts)}
${tokensCss}
body { margin: 0; font-family: "${fonts.family}", var(--font-stack); background: #111; color: #eee; }
h1 { padding: 16px 24px; margin: 0; font-size: 18px; }
h2 { font-size: 14px; margin: 0 0 8px; color: #aaa; }
.card { padding: 16px 24px 32px; border-bottom: 1px solid #333; }
.row { display: flex; flex-wrap: wrap; gap: 24px; align-items: flex-start; }
.pane-label { font-size: 11px; color: #888; margin-bottom: 6px; }
.pane-frame { background: #fff; box-shadow: 0 0 0 1px #333; overflow: hidden; }
.pane-frame .chart-root { transform-origin: top left; }
</style>
<script>
const es = new EventSource("/events");
es.onmessage = () => location.reload();
</script>
</head>
<body>
<h1>SOC-2 live preview · ${files.length} specs · reload on change</h1>
${cards || "<p style='padding:24px'>No specs found under out/social/*/w*/specs/</p>"}
</body></html>`;
}

function esc(s) {
  return String(s).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
}

const port = Number(process.env.PORT || 4173);
const clients = new Set();

const server = createServer((req, res) => {
  if (req.url === "/events") {
    res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    });
    res.write("\n");
    clients.add(res);
    req.on("close", () => clients.delete(res));
    return;
  }
  const files = listSpecs(process.env.SPECS_DIR ? resolve(process.env.SPECS_DIR) : null);
  const html = renderPage(files);
  res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
  res.end(html);
});

server.listen(port, () => {
  console.log(`SOC-2 preview http://127.0.0.1:${port}`);
});

function broadcast() {
  for (const res of clients) res.write("data: reload\n\n");
}

const watchRoots = [join(here), defaultSpecs];
for (const root of watchRoots) {
  if (!existsSync(root)) continue;
  try {
    watch(root, { recursive: true }, () => {
      clearTimeout(broadcast._t);
      broadcast._t = setTimeout(broadcast, 200);
    });
  } catch {
    // Windows may not support recursive watch on all paths.
  }
}
