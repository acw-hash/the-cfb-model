/**
 * Load DESIGN §4.1 tokens from webapp/site/src/styles/tokens.css (read-only).
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
export const TOKENS_CSS_PATH = join(
  here,
  "../../../webapp/site/src/styles/tokens.css",
);

export function loadTokensCss() {
  return readFileSync(TOKENS_CSS_PATH, "utf8");
}
