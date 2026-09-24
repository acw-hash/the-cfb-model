import { describe, expect, it } from "vitest";

import { formatKickoffLocal } from "@/lib/formatting/time";

/**
 * Odds as-of / carry-forward labels must use the same local formatter as kickoffs
 * (UTC available separately for tooltips).
 */
describe("odds timestamps share kickoff local formatter", () => {
  it("formatKickoffLocal yields local display + UTC tooltip string", () => {
    const iso = "2026-09-24T20:05:31Z";
    const { local, utc } = formatKickoffLocal(iso, "America/New_York");
    expect(local).toMatch(/Sep/);
    expect(local).toMatch(/24/);
    expect(local).toMatch(/4:05|16:05/); // 20:05Z = 4:05 PM EDT
    expect(utc).toMatch(/UTC/);
    expect(utc).not.toEqual(local);
  });

  it("does not use the old OddsAsOf weekday-only UTC-labelled shape as the sole display", () => {
    const iso = "2026-09-24T20:05:31Z";
    const { local } = formatKickoffLocal(iso, "America/Chicago");
    // Kickoff-style includes calendar day; legacy OddsAsOf was "Thu, 3:05 PM CDT"
    // without month/day — local must include a month abbreviation.
    expect(local).toMatch(/Sep/);
  });
});
