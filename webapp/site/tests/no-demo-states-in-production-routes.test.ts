/**
 * D2.5 — production routes must not import a demo-states module, even
 * transitively. Gallery is excluded: it is 404-gated in production and its
 * purpose is doctored clones. Chose a vitest import-graph walk over an
 * eslint-plugin-ridge rule because gallery must keep importing demo-states
 * and a lint rule on src/app/** would either false-fail gallery or miss
 * re-exports through lib/.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SRC = path.resolve(__dirname, "../src");
const APP = path.join(SRC, "app");

const IMPORT_RE = /(?:from|import)\s+["']([^"']+)["']/g;

function listProductionEntries(): string[] {
  const entries: string[] = [];
  function walk(dir: string): void {
    for (const name of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, name.name);
      const rel = path.relative(APP, full).split(path.sep).join("/");
      if (name.isDirectory()) {
        if (rel === "gallery" || rel.startsWith("gallery/")) {
          continue;
        }
        walk(full);
        continue;
      }
      if (!/\.(ts|tsx)$/.test(name.name)) {
        continue;
      }
      if (
        name.name === "page.tsx" ||
        name.name === "layout.tsx" ||
        name.name === "not-found.tsx" ||
        name.name === "route.ts" ||
        name.name === "robots.ts"
      ) {
        entries.push(full);
      }
    }
  }
  walk(APP);
  return entries;
}

function resolveImport(fromFile: string, spec: string): string | null {
  if (spec.startsWith("@/")) {
    return tryResolve(path.join(SRC, spec.slice(2)));
  }
  if (spec.startsWith("./") || spec.startsWith("../")) {
    return tryResolve(path.resolve(path.dirname(fromFile), spec));
  }
  return null;
}

function tryResolve(base: string): string | null {
  const candidates = [
    base,
    `${base}.ts`,
    `${base}.tsx`,
    path.join(base, "index.ts"),
    path.join(base, "index.tsx"),
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) {
      return candidate;
    }
  }
  return null;
}

function collectReachable(entry: string): string[] {
  const seen = new Set<string>();
  const stack = [entry];
  while (stack.length > 0) {
    const file = stack.pop() as string;
    if (seen.has(file)) {
      continue;
    }
    seen.add(file);
    const source = fs.readFileSync(file, "utf8");
    for (const match of source.matchAll(IMPORT_RE)) {
      const resolved = resolveImport(file, match[1]);
      if (resolved && resolved.startsWith(SRC)) {
        stack.push(resolved);
      }
    }
  }
  return [...seen];
}

describe("production routes do not import demo-states", () => {
  it("no src/app production route reaches a demo-states module", () => {
    const entries = listProductionEntries();
    expect(entries.length).toBeGreaterThan(0);
    const hits: string[] = [];
    for (const entry of entries) {
      for (const file of collectReachable(entry)) {
        if (file.replaceAll("\\", "/").includes("/demo-states.")) {
          hits.push(`${path.relative(SRC, entry)} -> ${path.relative(SRC, file)}`);
        }
      }
    }
    expect(hits).toEqual([]);
  });
});
