import { describe, expect, it } from "vitest";

import {
  formatConsensusSpreadBook,
  formatMarketMarginCases,
  formatMarketTeamNamedMargin,
  formatMarketTotal,
  formatMarketWinChance,
} from "@/lib/formatting/market-margin";

describe("market margin formatter", () => {
  it("home favorite", () => {
    expect(formatMarketMarginCases(3.5, "Texas A&M", "Arkansas")).toBe("Texas A&M by 3.5");
  });

  it("away favorite", () => {
    expect(formatMarketMarginCases(-7, "Home U", "Away U")).toBe("Away U by 7.0");
  });

  it("even / pick'em", () => {
    expect(formatMarketMarginCases(0, "A", "B")).toBe("Even");
    expect(formatConsensusSpreadBook(0, "Texas A&M")).toBe("Texas A&M PK");
  });

  it("null → em dash", () => {
    expect(formatMarketMarginCases(null, "A", "B")).toBe("—");
    expect(formatMarketTotal(null)).toBe("—");
    expect(formatMarketWinChance(null, null)).toBe("—");
  });

  it("consensus spread book notation", () => {
    expect(formatConsensusSpreadBook(-3.5, "Texas A&M")).toBe("Texas A&M −3.5");
    expect(formatConsensusSpreadBook(2.5, "Arkansas")).toBe("Arkansas +2.5");
  });

  it("team-named market uses same helper path", () => {
    expect(formatMarketTeamNamedMargin(4.2, "Michigan", "Ohio State")).toContain("Michigan by");
  });
});
