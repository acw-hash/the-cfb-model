#!/usr/bin/env node
/**
 * Regenerate src/data/odds_team_map.json from configs/team_names.yaml.
 * Run from workers/odds-refresh: `npm run export-team-map`
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import YAML from "yaml";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..", "..", "..");
const yamlPath = path.join(repoRoot, "configs", "team_names.yaml");
const outPath = path.join(__dirname, "..", "src", "data", "odds_team_map.json");

const doc = YAML.parse(fs.readFileSync(yamlPath, "utf8"));
const map = doc?.odds_api;
if (!map || typeof map !== "object") {
  console.error("odds_api map missing in", yamlPath);
  process.exit(1);
}
const out = {};
for (const [k, v] of Object.entries(map)) {
  if (typeof k !== "string" || typeof v !== "string") {
    console.error("non-string entry", k, v);
    process.exit(1);
  }
  out[k] = v;
}
fs.writeFileSync(outPath, JSON.stringify(out, null, 2) + "\n", "utf8");
console.log(`wrote ${outPath} entries=${Object.keys(out).length}`);
