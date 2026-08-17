/**
 * D3 — Odds API exclusion: no market field names in published fixtures / shapes.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE_DIR = path.resolve(__dirname, "../../fixtures");

const MARKET_FORBIDDEN =
  /\b(spread|total_line|moneyline|book|market_implied|sportsbook|closing_line)\b/i;

function collectKeys(value: unknown, out: Set<string>): void {
  if (Array.isArray(value)) {
    for (const item of value) {
      collectKeys(item, out);
    }
    return;
  }
  if (value && typeof value === "object") {
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      out.add(k);
      collectKeys(v, out);
    }
  }
}

describe("artifact market-field exclusion (§1.2)", () => {
  it("fixture latest-equivalent JSON has no market field names", () => {
    const files = fs.readdirSync(FIXTURE_DIR).filter((f) => f.endsWith(".json"));
    expect(files.length).toBeGreaterThan(0);
    const keys = new Set<string>();
    for (const file of files) {
      const data = JSON.parse(fs.readFileSync(path.join(FIXTURE_DIR, file), "utf8"));
      collectKeys(data, keys);
    }
    const hits = [...keys].filter((k) => MARKET_FORBIDDEN.test(k));
    expect(hits).toEqual([]);
  });
});
