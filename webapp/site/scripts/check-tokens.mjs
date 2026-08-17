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
    "--text-tertiary": "#75757a",
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
    "--text-tertiary": "#8e8e93",
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

/** WCAG 2.1 AA contrast threshold for normal text (1.4.3). */
const AA_NORMAL = 4.5;

function hexToRgb(hex) {
  const normalized = hex.replace("#", "");
  const n = Number.parseInt(normalized, 16);
  return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
}

function srgbChannel(c) {
  const s = c / 255;
  return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

function relativeLuminance(hex) {
  const { r, g, b } = hexToRgb(hex);
  return 0.2126 * srgbChannel(r) + 0.7152 * srgbChannel(g) + 0.0722 * srgbChannel(b);
}

function contrastRatio(foreground, background) {
  const l1 = relativeLuminance(foreground);
  const l2 = relativeLuminance(background);
  const lighter = Math.max(l1, l2);
  const darker = Math.min(l1, l2);
  return (lighter + 0.05) / (darker + 0.05);
}

/**
 * Every pair in the W8-A D5 ratio table. Threshold is AA normal 4.5:1 —
 * hex equality alone would miss a tertiary tweak that drops below 4.5.
 * Banner pair is --bg-primary text on --semantic-stale (W8-A solid fill).
 */
const CONTRAST_PAIRS = [
  { theme: "light", fg: "--text-primary", bg: "--bg-primary", min: AA_NORMAL },
  { theme: "light", fg: "--text-secondary", bg: "--bg-primary", min: AA_NORMAL },
  { theme: "light", fg: "--text-tertiary", bg: "--bg-primary", min: AA_NORMAL },
  { theme: "light", fg: "--accent", bg: "--bg-primary", min: AA_NORMAL },
  { theme: "light", fg: "--semantic-stale", bg: "--bg-primary", min: AA_NORMAL },
  { theme: "light", fg: "--text-primary", bg: "--bg-secondary", min: AA_NORMAL },
  { theme: "light", fg: "--text-secondary", bg: "--bg-secondary", min: AA_NORMAL },
  { theme: "dark", fg: "--text-primary", bg: "--bg-primary", min: AA_NORMAL },
  { theme: "dark", fg: "--text-secondary", bg: "--bg-primary", min: AA_NORMAL },
  { theme: "dark", fg: "--text-tertiary", bg: "--bg-primary", min: AA_NORMAL },
  { theme: "dark", fg: "--accent", bg: "--bg-primary", min: AA_NORMAL },
  {
    theme: "light",
    fg: "--bg-primary",
    bg: "--semantic-stale",
    min: AA_NORMAL,
    label: "banner text / semantic-stale",
  },
];

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

for (const pair of CONTRAST_PAIRS) {
  const vars = pair.theme === "dark" ? darkVars : rootVars;
  const fg = vars[pair.fg];
  const bg = vars[pair.bg];
  if (!fg || !bg) {
    mismatches.push(
      `contrast ${pair.theme} ${pair.label ?? `${pair.fg}/${pair.bg}`}: missing ${!fg ? pair.fg : pair.bg}`,
    );
    continue;
  }
  const ratio = contrastRatio(fg, bg);
  if (ratio + Number.EPSILON < pair.min) {
    mismatches.push(
      `contrast ${pair.theme} ${pair.label ?? `${pair.fg}/${pair.bg}`}: ${ratio.toFixed(2)}:1 < AA ${pair.min}:1`,
    );
  }
}

if (mismatches.length > 0) {
  console.error("Token diff-check FAILED:\n" + mismatches.join("\n"));
  process.exit(1);
}

console.log("Token diff-check PASSED — all §4.1/§4.2 values match tokens.css");
const ratioLines = CONTRAST_PAIRS.map((pair) => {
  const vars = pair.theme === "dark" ? darkVars : rootVars;
  const ratio = contrastRatio(vars[pair.fg], vars[pair.bg]);
  return `  ${pair.theme} ${pair.label ?? `${pair.fg}/${pair.bg}`}: ${ratio.toFixed(2)}:1 (>= ${pair.min})`;
});
console.log("Contrast ratios (AA normal " + AA_NORMAL + ":1):\n" + ratioLines.join("\n"));
