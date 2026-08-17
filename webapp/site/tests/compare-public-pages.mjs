/**
 * Byte-compare two screenshot directories. D1 must be pixel-identical;
 * any delta is printed so it can be justified or reverted.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "../../../docs/notes/_artifacts/webapp-w8d");
const BEFORE = path.join(ROOT, "shots-before");
const AFTER = path.join(ROOT, "shots-after");

function listPng(dir) {
  return fs
    .readdirSync(dir)
    .filter((name) => name.endsWith(".png"))
    .sort();
}

if (!fs.existsSync(BEFORE) || !fs.existsSync(AFTER)) {
  console.error(`Missing ${BEFORE} or ${AFTER}`);
  process.exit(1);
}

const beforeFiles = listPng(BEFORE);
const afterFiles = listPng(AFTER);
const names = [...new Set([...beforeFiles, ...afterFiles])];
let deltas = 0;

for (const name of names) {
  const aPath = path.join(BEFORE, name);
  const bPath = path.join(AFTER, name);
  if (!fs.existsSync(aPath) || !fs.existsSync(bPath)) {
    console.log(`MISSING ${name} before=${fs.existsSync(aPath)} after=${fs.existsSync(bPath)}`);
    deltas += 1;
    continue;
  }
  const a = fs.readFileSync(aPath);
  const b = fs.readFileSync(bPath);
  if (a.equals(b)) {
    console.log(`IDENTICAL ${name} (${a.length} bytes)`);
  } else {
    console.log(`DELTA ${name} before=${a.length}b after=${b.length}b`);
    deltas += 1;
  }
}

console.log(`Compared ${names.length} files; deltas=${deltas}`);
process.exit(deltas === 0 ? 0 : 1);
