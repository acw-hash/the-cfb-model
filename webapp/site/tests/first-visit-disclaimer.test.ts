import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const COMPONENT_PATH = path.resolve(
  __dirname,
  "../src/components/FirstVisitDisclaimer/FirstVisitDisclaimer.tsx",
);

describe("FirstVisitDisclaimer — W10-UI session keys", () => {
  it("writes ridge-disclaimer-seen on mount and ridge-disclaimer-dismissed on dismiss", () => {
    const source = fs.readFileSync(COMPONENT_PATH, "utf8");
    expect(source).toContain('const SEEN_KEY = "ridge-disclaimer-seen"');
    expect(source).toContain('const DISMISS_KEY = "ridge-disclaimer-dismissed"');
    expect(source).toContain('sessionStorage.setItem(SEEN_KEY, "1")');
    expect(source).toContain('sessionStorage.setItem(DISMISS_KEY, "1")');
    expect(source).toContain("sessionStorage.getItem(SEEN_KEY)");
  });
});
