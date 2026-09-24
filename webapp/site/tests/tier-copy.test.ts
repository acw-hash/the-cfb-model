import { describe, expect, it } from "vitest";

import { DESIGN_TIER_ENTER, TIER_ENTER, tierExplainerSentence } from "@/lib/formatting/tier-copy";

describe("TIER_ENTER matches DESIGN §2.2", () => {
  it("does not drift from documented enter thresholds", () => {
    expect(TIER_ENTER.strong_lean).toBe(DESIGN_TIER_ENTER.strong_lean);
    expect(TIER_ENTER.clear_lean).toBe(DESIGN_TIER_ENTER.clear_lean);
    expect(TIER_ENTER.lean).toBe(DESIGN_TIER_ENTER.lean);
    expect(TIER_ENTER.strong_lean).toBe(0.85);
    expect(TIER_ENTER.clear_lean).toBe(0.7);
    expect(TIER_ENTER.lean).toBe(0.575);
  });
});

describe("tierExplainerSentence", () => {
  it("mentions sticky tiers instead of hard floors that contradict hysteresis", () => {
    const strong = tierExplainerSentence("strong_lean");
    expect(strong).toContain("sticky");
    expect(strong).not.toMatch(/at least 85%/);
    expect(tierExplainerSentence("clear_lean")).toContain("sticky");
    expect(tierExplainerSentence("lean")).toContain("sticky");
    expect(tierExplainerSentence("toss_up")).toContain("sticky");
  });
});
