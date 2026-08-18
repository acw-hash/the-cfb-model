/**
 * Blocking W0∪W8-D union grep over published site-copy surfaces (W9-1 A1).
 *
 * Canonical union (do not narrow) — same pattern as
 * scripts/check_betting_language.py published.
 *
 * Vercel / npm run guard cannot see repo-root Python or webapp/fixtures.
 * export.py + committed fixtures are scanned by the Python published runner
 * in .github/workflows/ci.yml.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SITE_ROOT = path.resolve(__dirname, "..");
const SRC_ROOT = path.join(SITE_ROOT, "src");

const UNION =
  /best bet|yes bet|\bplay\b|edge vs market|\bunits\b|lock it in|must bet|recommended bet/i;

/** Explicit list. Adding a copy.ts under src without adding it here fails. */
const PUBLISHED_COPY_SURFACES = [
  "src/lib/about/copy.ts",
  "src/lib/results/copy.ts",
  "src/lib/game-detail/absence.ts",
  "src/lib/game-detail/provenance.ts",
];

function walkCopyModules(dir, acc = []) {
  if (!fs.existsSync(dir)) {
    return acc;
  }
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      walkCopyModules(full, acc);
      continue;
    }
    const stem = path.parse(entry.name).name;
    const ext = path.extname(entry.name).toLowerCase();
    if (stem === "copy" && (ext === ".ts" || ext === ".tsx")) {
      acc.push(path.relative(SITE_ROOT, full).split(path.sep).join("/"));
    }
  }
  return acc;
}

function scanFile(rel) {
  const abs = path.join(SITE_ROOT, rel);
  const text = fs.readFileSync(abs, "utf8");
  const hits = [];
  const lines = text.split(/\r?\n/);
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    if (line.match(UNION)) {
      hits.push(`${rel}:${i + 1}:${line}`);
    }
  }
  return hits;
}

const listed = new Set(PUBLISHED_COPY_SURFACES);
const copyModules = walkCopyModules(SRC_ROOT);
const unlisted = copyModules.filter((p) => !listed.has(p));
if (unlisted.length > 0) {
  console.error(`copy module not on PUBLISHED_COPY_SURFACES: ${unlisted.join(", ")}`);
  process.exit(1);
}

const missing = PUBLISHED_COPY_SURFACES.filter((rel) => !fs.existsSync(path.join(SITE_ROOT, rel)));
if (missing.length > 0) {
  console.error(`listed published-copy surface missing: ${missing.join(", ")}`);
  process.exit(1);
}

const hits = PUBLISHED_COPY_SURFACES.flatMap(scanFile);
for (const hit of hits) {
  console.log(hit);
}
if (hits.length > 0) {
  console.error(
    `union_grep published matches=${hits.length} surfaces=${PUBLISHED_COPY_SURFACES.length}`,
  );
  console.error("published-copy union grep failed: betting-language hit on a published surface");
  process.exit(1);
}

console.error(`union_grep published matches=0 surfaces=${PUBLISHED_COPY_SURFACES.length}`);
process.exit(0);
