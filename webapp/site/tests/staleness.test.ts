import { describe, expect, it } from "vitest";

import { isSiteStale, stalenessBannerMessage } from "@/lib/formatting/time";

describe("site staleness banner (§3.2)", () => {
  it("engages when >36h past publish and past next expected slot", () => {
    const publishedAt = "2024-09-20T06:00:00Z";
    const nextExpected = "2024-09-21T06:00:00Z";
    const now = new Date("2024-09-24T12:00:00Z");
    expect(isSiteStale(publishedAt, nextExpected, now)).toBe(true);
  });

  it("does not engage before next expected slot", () => {
    const publishedAt = "2024-09-24T06:00:00Z";
    const nextExpected = "2024-09-26T06:00:00Z";
    const now = new Date("2024-09-25T12:00:00Z");
    expect(isSiteStale(publishedAt, nextExpected, now)).toBe(false);
  });

  it("formats banner copy per spec", () => {
    const message = stalenessBannerMessage("2024-09-20T06:00:00Z");
    expect(message).toMatch(/^Data may be stale — last updated /);
  });
});
