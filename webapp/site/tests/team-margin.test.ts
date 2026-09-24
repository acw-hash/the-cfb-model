import { describe, expect, it } from "vitest";

import {
  articleForPercentLabel,
  favoredSideFromMargin,
  formatFavoredWinChance,
  formatIntervalLandSentence,
  formatNominalCoveragePhrase,
  formatTeamNamedBound,
  formatTeamNamedInterval,
  formatTeamNamedMargin,
  formatTypicalMiss,
  formatUnsignedMargin,
  formatWinChanceSentence,
  isCleanTenthNominal,
  nominalAsNIn10,
} from "@/lib/formatting/team-margin";

describe("formatTeamNamedMargin", () => {
  it("formats a home favorite", () => {
    expect(formatTeamNamedMargin(4.2, "Michigan", "Rutgers", 13.8)).toBe("Michigan by 4.2");
  });

  it("formats an away favorite", () => {
    expect(formatTeamNamedMargin(-2.5, "Michigan", "Ohio State", 13.8)).toBe("Ohio State by 2.5");
  });

  it("uses §2.1 tie-break at zero margin", () => {
    expect(formatTeamNamedMargin(0, "Home U", "Away U", 13.8, 0.55)).toBe("Home U by 0.0");
    expect(formatTeamNamedMargin(0, "Home U", "Away U", 13.8, 0.4)).toBe("Away U by 0.0");
    expect(favoredSideFromMargin(0, 0.5)).toBe("home");
    expect(favoredSideFromMargin(0, 0.49)).toBe("away");
  });

  it("formats unsigned magnitude and PK", () => {
    expect(formatUnsignedMargin(15.6, 13.8)).toBe("15.6");
    expect(formatUnsignedMargin(-2.3, null)).toBe("2.3");
    expect(formatUnsignedMargin(0, 13.8)).toBe("PK");
    expect(formatUnsignedMargin(null)).toBeNull();
  });
});

describe("formatTeamNamedInterval", () => {
  it("names both ends when the interval crosses zero", () => {
    expect(formatTeamNamedInterval(-8.1, 16.5, "Michigan", "Rutgers", 13.8)).toBe(
      "Rutgers by 8.1 to Michigan by 16.5",
    );
  });

  it("joins with and for between-clauses", () => {
    expect(formatTeamNamedInterval(-8.1, 16.5, "Michigan", "Rutgers", 13.8, "and")).toBe(
      "Rutgers by 8.1 and Michigan by 16.5",
    );
  });

  it("keeps both ends on the home side", () => {
    expect(formatTeamNamedInterval(4.0, 17.0, "Michigan", "Rutgers", 13.8)).toBe(
      "Michigan by 4.0 to Michigan by 17.0",
    );
  });

  it("keeps both ends on the away side", () => {
    expect(formatTeamNamedInterval(-20.0, -3.0, "Purdue", "Ohio State", 13.8)).toBe(
      "Ohio State by 20.0 to Ohio State by 3.0",
    );
  });

  it("returns null when either bound is null", () => {
    expect(formatTeamNamedInterval(null, 16.5, "A", "B")).toBeNull();
    expect(formatTeamNamedInterval(-8, null, "A", "B")).toBeNull();
  });

  it("formats a zero bound as even", () => {
    expect(formatTeamNamedBound(0, "Michigan", "Rutgers", 1)).toBe("even (0.0)");
  });
});

describe("summary grammar — a/an and between/and", () => {
  it("uses an before 8, 11, 18, and 80–89", () => {
    expect(articleForPercentLabel("8%")).toBe("an");
    expect(articleForPercentLabel("11%")).toBe("an");
    expect(articleForPercentLabel("18%")).toBe("an");
    expect(articleForPercentLabel("80%")).toBe("an");
    expect(articleForPercentLabel("83%")).toBe("an");
    expect(articleForPercentLabel("89%")).toBe("an");
  });

  it("uses a before other percents", () => {
    expect(articleForPercentLabel("76%")).toBe("a");
    expect(articleForPercentLabel("9%")).toBe("a");
    expect(articleForPercentLabel("12%")).toBe("a");
    expect(articleForPercentLabel("70%")).toBe("a");
  });

  it("builds win-chance sentences with the correct article", () => {
    expect(formatWinChanceSentence("Ohio State", "89%")).toBe(
      "Ridge gives Ohio State an 89% chance to win.",
    );
    expect(formatWinChanceSentence("Texas A&M", "76%")).toBe(
      "Ridge gives Texas A&M a 76% chance to win.",
    );
  });

  it("uses between X and Y, not between X to Y", () => {
    const joined = formatTeamNamedInterval(-20.2, 34.9, "Texas A&M", "Arkansas", 16.7, "and")!;
    expect(joined).toContain(" and ");
    expect(joined).not.toMatch(/\bbetween\b/);
    const sentence = formatIntervalLandSentence(joined, 0.8);
    expect(sentence).toContain("between Arkansas by 20.2 and Texas A&M by 34.9");
    expect(sentence).not.toMatch(/between .+ to /);
  });
});

describe("nominal coverage phrasing", () => {
  it("treats clean tenths as N in 10", () => {
    expect(isCleanTenthNominal(0.8)).toBe(true);
    expect(isCleanTenthNominal(0.9)).toBe(true);
    expect(isCleanTenthNominal(0.85)).toBe(false);
    expect(nominalAsNIn10(0.8)).toBe(8);
    expect(nominalAsNIn10(0.9)).toBe(9);
    expect(nominalAsNIn10(0.85)).toBeNull();
    expect(nominalAsNIn10(null)).toBeNull();
  });

  it("phrases 0.80, 0.85, 0.90, and null", () => {
    expect(formatNominalCoveragePhrase(0.8)).toEqual({
      kind: "n_in_10",
      n: 8,
      label: "8 times in 10",
    });
    expect(formatNominalCoveragePhrase(0.85)).toEqual({
      kind: "percent",
      pctLabel: "85%",
      label: "85% of the time",
    });
    expect(formatNominalCoveragePhrase(0.9)).toEqual({
      kind: "n_in_10",
      n: 9,
      label: "9 times in 10",
    });
    expect(formatNominalCoveragePhrase(null)).toEqual({ kind: "absent" });
  });

  it("builds land sentences from nominal", () => {
    expect(formatIntervalLandSentence("A by 1.0 and B by 2.0", 0.8)).toBe(
      "8 times in 10, the final margin should land between A by 1.0 and B by 2.0.",
    );
    expect(formatIntervalLandSentence("A by 1.0 and B by 2.0", 0.85)).toBe(
      "85% of the time, the final margin should land between A by 1.0 and B by 2.0.",
    );
    expect(formatIntervalLandSentence("A by 1.0 and B by 2.0", null)).toBe(
      "Likely range: between A by 1.0 and B by 2.0.",
    );
  });
});

describe("formatTypicalMiss", () => {
  it("replaces sigma glyph with plain wording", () => {
    expect(formatTypicalMiss(13.8)).toBe("Typical miss: \u00b113.8 pts.");
    expect(formatTypicalMiss(null)).toBeNull();
  });

  it("formats favored win chance from p_win_home", () => {
    expect(formatFavoredWinChance(8.9, 0.76)).toBe("76%");
    expect(formatFavoredWinChance(-17.7, 0.11)).toBe("89%");
  });
});
