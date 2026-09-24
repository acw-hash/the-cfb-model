import { describe, expect, it } from "vitest";
import {
  americanToDecimal,
  consensusH2h,
  consensusSpread,
  consensusTotal,
  devigHomeWinProb,
  formatSpreadBookNotation,
  homeSpreadToMarketMargin,
  median,
} from "../src/consensus";

describe("de-vig", () => {
  it("converts american to decimal", () => {
    expect(americanToDecimal(-110)).toBeCloseTo(1.90909, 4);
    expect(americanToDecimal(150)).toBeCloseTo(2.5, 6);
  });

  it("de-vigs two-way moneyline to home win prob", () => {
    // -150 / +130 → fair home > 0.5
    const p = devigHomeWinProb(-150, 130);
    expect(p).toBeGreaterThan(0.55);
    expect(p).toBeLessThan(0.65);
    // Equal juice -110/-110 → 0.5
    expect(devigHomeWinProb(-110, -110)).toBeCloseTo(0.5, 6);
  });
});

describe("median", () => {
  it("odd count", () => {
    expect(median([3, 1, 2])).toBe(2);
  });
  it("even count averages middle pair", () => {
    expect(median([1, 2, 3, 4])).toBe(2.5);
  });
});

describe("sign conversion", () => {
  it("home favorite (negative book spread) → positive market margin", () => {
    expect(homeSpreadToMarketMargin(-3.5)).toBe(3.5);
    const c = consensusSpread([-3, -3.5, -4, -3.5, -3], 3)!;
    expect(c.spread_home_points).toBe(-3.5);
    expect(c.market_home_margin).toBe(3.5);
  });

  it("away favorite (positive home spread) → negative market margin", () => {
    expect(homeSpreadToMarketMargin(7)).toBe(-7);
  });

  it("pick'em → 0 / PK", () => {
    expect(homeSpreadToMarketMargin(0)).toBe(0);
    expect(formatSpreadBookNotation(0)).toBe("PK");
  });
});

describe("≥3-book threshold", () => {
  it("returns null when fewer than 3 books", () => {
    expect(consensusSpread([-3, -3.5], 3)).toBeNull();
    expect(consensusTotal([50, 51], 3)).toBeNull();
    expect(consensusH2h([{ home: -110, away: -110 }], 3)).toBeNull();
  });

  it("returns consensus at 3+ books", () => {
    expect(consensusTotal([50, 51, 52], 3)?.total_points).toBe(51);
    expect(
      consensusH2h(
        [
          { home: -110, away: -110 },
          { home: -110, away: -110 },
          { home: -105, away: -115 },
        ],
        3,
      )?.h2h_book_count,
    ).toBe(3);
  });
});
