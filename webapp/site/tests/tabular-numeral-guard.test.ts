import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SRC_DIR = path.resolve(__dirname, "../src");

const ALLOWED = [path.join("lib", "formatting"), path.join("components", "Figure")];

const FORBIDDEN_PATTERNS = [/\.toFixed\s*\(/, /font-variant-numeric/];

function walk(dir: string): string[] {
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  const files: string[] = [];
  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      files.push(...walk(full));
    } else if (/\.(tsx|ts)$/.test(entry.name)) {
      files.push(full);
    }
  }
  return files;
}

function isAllowed(file: string): boolean {
  const rel = path.relative(SRC_DIR, file);
  return ALLOWED.some((segment) => rel.includes(segment.replace(/\\/g, path.sep)));
}

describe("tabular numeral guard", () => {
  it("Figure module defines tabular-nums and UI components avoid raw toFixed", () => {
    const figureCss = fs.readFileSync(
      path.join(SRC_DIR, "components/Figure/typography.module.css"),
      "utf8",
    );
    expect(figureCss).toContain("tabular-nums");

    const violations: string[] = [];
    for (const file of walk(SRC_DIR)) {
      if (isAllowed(file)) {
        continue;
      }
      const rel = path.relative(SRC_DIR, file);
      if (!rel.startsWith(`components${path.sep}`) && !rel.startsWith(`app${path.sep}`)) {
        continue;
      }
      const content = fs.readFileSync(file, "utf8");
      for (const pattern of FORBIDDEN_PATTERNS) {
        if (pattern.test(content)) {
          violations.push(`${rel}: matched ${pattern}`);
        }
      }
    }

    expect(violations).toEqual([]);
  });
});
