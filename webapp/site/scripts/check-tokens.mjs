import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const tokensPath = path.resolve(__dirname, "../src/styles/tokens.css");
const tokens = fs.readFileSync(tokensPath, "utf8");

/** §4.1 expected values — single source for diff-check evidence. */
const EXPECTED = {
  light: {
    "--bg-primary": "#ffffff",
    "--bg-secondary": "#f5f5f7",
    "--text-primary": "#1d1d1f",
    "--text-secondary": "#6e6e73",
    "--text-tertiary": "#aeaeb2",
    "--accent": "#0071e3",
    "--semantic-stale": "#bf4800",
    "--semantic-revised": "#6e6e73",
    "--semantic-positive": "#1d1d1f",
    "--border-subtle": "#d2d2d7",
  },
  dark: {
    "--bg-primary": "#000000",
    "--bg-secondary": "#1c1c1e",
    "--text-primary": "#f5f5f7",
    "--text-secondary": "#98989d",
    "--text-tertiary": "#636366",
    "--accent": "#0a84ff",
    "--semantic-stale": "#ff9f0a",
    "--semantic-revised": "#98989d",
    "--semantic-positive": "#f5f5f7",
    "--border-subtle": "#38383a",
  },
  type: {
    "--type-t1-size": "28px",
    "--type-t1-line": "32px",
    "--type-t2-size": "20px",
    "--type-t2-line": "24px",
    "--type-t3-size": "17px",
    "--type-t3-line": "22px",
    "--type-b1-size": "17px",
    "--type-b1-line": "22px",
    "--type-b2-size": "15px",
    "--type-b2-line": "20px",
    "--type-n1-size": "17px",
    "--type-n1-line": "22px",
    "--type-n2-size": "15px",
    "--type-n2-line": "20px",
    "--type-c1-size": "13px",
    "--type-c1-line": "16px",
    "--type-c2-size": "11px",
    "--type-c2-line": "14px",
  },
};

function extractBlock(css, selector) {
  const start = css.indexOf(selector);
  if (start === -1) {
    return "";
  }
  const brace = css.indexOf("{", start);
  const end = css.indexOf("}", brace);
  return css.slice(brace + 1, end);
}

function readVars(block) {
  const vars = {};
  for (const line of block.split("\n")) {
    const match = line.match(/(--[\w-]+)\s*:\s*([^;]+);/);
    if (match) {
      vars[match[1]] = match[2].trim().toLowerCase();
    }
  }
  return vars;
}

const rootBlock = extractBlock(tokens, ":root");
const darkBlock = extractBlock(tokens, '[data-theme="dark"]');
const rootVars = readVars(rootBlock);
const darkVars = readVars(darkBlock);

const mismatches = [];

for (const [token, value] of Object.entries(EXPECTED.light)) {
  if (rootVars[token] !== value) {
    mismatches.push(`light ${token}: expected ${value}, got ${rootVars[token] ?? "missing"}`);
  }
}

for (const [token, value] of Object.entries(EXPECTED.dark)) {
  if (darkVars[token] !== value) {
    mismatches.push(`dark ${token}: expected ${value}, got ${darkVars[token] ?? "missing"}`);
  }
}

for (const [token, value] of Object.entries(EXPECTED.type)) {
  if (rootVars[token] !== value) {
    mismatches.push(`type ${token}: expected ${value}, got ${rootVars[token] ?? "missing"}`);
  }
}

if (mismatches.length > 0) {
  console.error("Token diff-check FAILED:\n" + mismatches.join("\n"));
  process.exit(1);
}

console.log("Token diff-check PASSED — all §4.1/§4.2 values match tokens.css");
