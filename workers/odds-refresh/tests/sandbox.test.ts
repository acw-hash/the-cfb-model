import { describe, expect, it } from "vitest";
import { datedOddsKey, latestOddsKey } from "../src/r2-keys";

/**
 * Fixture / sandbox routing: when week_predictions.fixture === true (or FORCE_SANDBOX),
 * writes go under sandbox/. Revalidation uses PREVIEW_REVALIDATE_URL only (never
 * WEBAPP_REVALIDATE_URL). Unit-test the key side; run.ts wires the flag.
 */
describe("fixture → sandbox routing", () => {
  it("prefixes all write keys with sandbox/", () => {
    const at = new Date("2026-09-24T13:00:07Z");
    expect(datedOddsKey(2026, 4, at, { sandbox: true })).toBe(
      "sandbox/odds/2026/w4/2026-09-24T13:00.json",
    );
    expect(latestOddsKey({ sandbox: true })).toBe("sandbox/latest/odds_snapshot.json");
    expect(latestOddsKey({ sandbox: false })).toBe("latest/odds_snapshot.json");
  });
});
