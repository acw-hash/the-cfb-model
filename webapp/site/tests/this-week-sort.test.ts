import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import type { GamePrediction, WeekPredictions } from "@/lib/artifacts/types";
import {
  compareByConviction,
  compareByKickoff,
  compareNullableStringAsc,
  groupByConviction,
  groupByKickoffDay,
  NO_KICKOFF_GROUP_ID,
  parseSlateOrder,
} from "@/lib/this-week/sort";

import { loadNullHeavyWeek } from "./null-heavy-slate";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE_PATH = path.resolve(__dirname, "../../fixtures/week_predictions.json");

function loadFixture(): WeekPredictions {
  return JSON.parse(fs.readFileSync(FIXTURE_PATH, "utf8")) as WeekPredictions;
}

function cloneGame(base: GamePrediction, overlay: Partial<GamePrediction>): GamePrediction {
  return { ...base, ...overlay };
}

/** Overlay that may set nullable sort keys to JSON null (fixture types are non-null). */
function cloneWithNulls(
  base: GamePrediction,
  overlay: Partial<{
    game_id: string | null;
    kickoff_utc: string | null;
    conviction_tier: GamePrediction["conviction_tier"];
    conviction_basis: GamePrediction["conviction_basis"];
  }>,
): GamePrediction {
  return { ...base, ...overlay } as GamePrediction;
}

describe("compareNullableStringAsc", () => {
  it("sorts null last without coercing", () => {
    expect(compareNullableStringAsc(null, "a")).toBe(1);
    expect(compareNullableStringAsc("a", null)).toBe(-1);
    expect(compareNullableStringAsc(null, null)).toBe(0);
    expect(compareNullableStringAsc("b", "a")).toBe(1);
  });
});

describe("parseSlateOrder", () => {
  it("defaults to kickoff", () => {
    expect(parseSlateOrder(undefined)).toBe("kickoff");
    expect(parseSlateOrder(null)).toBe("kickoff");
    expect(parseSlateOrder("nope")).toBe("kickoff");
  });

  it("accepts conviction", () => {
    expect(parseSlateOrder("conviction")).toBe("conviction");
  });
});

describe("compareByKickoff", () => {
  it("orders by kickoff_utc then game_id", () => {
    const week = loadFixture();
    const base = week.games[0];
    const a = cloneGame(base, { game_id: "b", kickoff_utc: "2024-09-28T16:00:00Z" });
    const b = cloneGame(base, { game_id: "a", kickoff_utc: "2024-09-28T16:00:00Z" });
    const c = cloneGame(base, { game_id: "c", kickoff_utc: "2024-09-28T19:30:00Z" });
    const sorted = [c, a, b].sort(compareByKickoff);
    expect(sorted.map((g) => g.game_id)).toEqual(["a", "b", "c"]);
  });

  it("is deterministic for identical kickoff on the fixture slate", () => {
    const week = loadFixture();
    const sortedOnce = [...week.games].sort(compareByKickoff);
    const sortedTwice = [...week.games].sort(compareByKickoff);
    expect(sortedOnce.map((g) => g.game_id)).toEqual(sortedTwice.map((g) => g.game_id));

    const sameKickoff = sortedOnce.filter((g) => g.kickoff_utc === "2024-09-28T16:00:00Z");
    expect(sameKickoff.length).toBeGreaterThan(1);
    const ids = sameKickoff.map((g) => g.game_id);
    expect(ids).toEqual([...ids].sort((x, y) => x.localeCompare(y)));
  });

  it("does not throw when kickoff_utc is null and sorts null last", () => {
    const week = loadFixture();
    const base = week.games[0];
    const dated = cloneWithNulls(base, {
      game_id: "a",
      kickoff_utc: "2024-09-28T16:00:00Z",
    });
    const missing = cloneWithNulls(base, {
      game_id: "b",
      kickoff_utc: null,
    });
    expect(() => [missing, dated].sort(compareByKickoff)).not.toThrow();
    expect([missing, dated].sort(compareByKickoff).map((g) => g.game_id)).toEqual(["a", "b"]);
  });

  it("does not throw when game_id is null", () => {
    const week = loadFixture();
    const base = week.games[0];
    const a = cloneWithNulls(base, { game_id: null, kickoff_utc: "2024-09-28T16:00:00Z" });
    const b = cloneWithNulls(base, { game_id: "z", kickoff_utc: "2024-09-28T16:00:00Z" });
    expect(() => [a, b].sort(compareByKickoff)).not.toThrow();
    expect([a, b].sort(compareByKickoff).map((g) => g.game_id)).toEqual(["z", null]);
  });
});

describe("compareByConviction", () => {
  it("orders tier descending then p_favored descending then game_id", () => {
    const week = loadFixture();
    const base = week.games[0];
    const strong = cloneGame(base, {
      game_id: "2",
      conviction_tier: "strong_lean",
      conviction_basis: {
        ...base.conviction_basis!,
        p_favored: 0.9,
      },
    });
    const strongLower = cloneGame(base, {
      game_id: "1",
      conviction_tier: "strong_lean",
      conviction_basis: {
        ...base.conviction_basis!,
        p_favored: 0.86,
      },
    });
    const lean = cloneGame(base, {
      game_id: "3",
      conviction_tier: "lean",
      conviction_basis: {
        ...base.conviction_basis!,
        p_favored: 0.6,
      },
    });
    const sorted = [lean, strongLower, strong].sort(compareByConviction);
    expect(sorted.map((g) => g.game_id)).toEqual(["2", "1", "3"]);
  });

  it("tie-breaks identical tier and p_favored by game_id", () => {
    const week = loadFixture();
    const base = week.games[0];
    const a = cloneGame(base, {
      game_id: "401628999",
      conviction_tier: "lean",
      conviction_basis: { ...base.conviction_basis!, p_favored: 0.62 },
    });
    const b = cloneGame(base, {
      game_id: "401628111",
      conviction_tier: "lean",
      conviction_basis: { ...base.conviction_basis!, p_favored: 0.62 },
    });
    const sorted = [a, b].sort(compareByConviction);
    expect(sorted.map((g) => g.game_id)).toEqual(["401628111", "401628999"]);
  });

  it("does not throw when conviction_tier is null and sorts suppressed last", () => {
    const week = loadFixture();
    const base = week.games[0];
    const lean = cloneWithNulls(base, {
      game_id: "lean",
      conviction_tier: "lean",
      conviction_basis: { ...base.conviction_basis!, p_favored: 0.6 },
    });
    const suppressed = cloneWithNulls(base, {
      game_id: "sup",
      conviction_tier: null,
      conviction_basis: null,
    });
    expect(() => [suppressed, lean].sort(compareByConviction)).not.toThrow();
    expect([suppressed, lean].sort(compareByConviction).map((g) => g.game_id)).toEqual([
      "lean",
      "sup",
    ]);
  });

  it("does not throw when p_favored is null and sorts null last within tier", () => {
    const week = loadFixture();
    const base = week.games[0];
    const withP = cloneWithNulls(base, {
      game_id: "a",
      conviction_tier: "lean",
      conviction_basis: { ...base.conviction_basis!, p_favored: 0.7 },
    });
    const noP = cloneWithNulls(base, {
      game_id: "b",
      conviction_tier: "lean",
      conviction_basis: { ...base.conviction_basis!, p_favored: null as unknown as number },
    });
    expect(() => [noP, withP].sort(compareByConviction)).not.toThrow();
    expect([noP, withP].sort(compareByConviction).map((g) => g.game_id)).toEqual(["a", "b"]);
  });

  it("does not throw when game_id is null in conviction tie-break", () => {
    const week = loadFixture();
    const base = week.games[0];
    const a = cloneWithNulls(base, {
      game_id: null,
      conviction_tier: "lean",
      conviction_basis: { ...base.conviction_basis!, p_favored: 0.62 },
    });
    const b = cloneWithNulls(base, {
      game_id: "z",
      conviction_tier: "lean",
      conviction_basis: { ...base.conviction_basis!, p_favored: 0.62 },
    });
    expect(() => [a, b].sort(compareByConviction)).not.toThrow();
    expect([a, b].sort(compareByConviction).map((g) => g.game_id)).toEqual(["z", null]);
  });
});

describe("groupByKickoffDay", () => {
  it("groups fixture games by America/New_York calendar day", () => {
    const week = loadFixture();
    const groups = groupByKickoffDay(week.games, "America/New_York");
    expect(groups.length).toBeGreaterThan(1);
    expect(groups.every((g) => g.games.length > 0)).toBe(true);
    const ids = groups.flatMap((g) => g.games.map((game) => game.game_id));
    expect(new Set(ids).size).toBe(week.games.length);
  });

  it("places null kickoff games in a trailing group without throwing", () => {
    const week = loadFixture();
    const base = week.games[0];
    const dated = cloneWithNulls(base, {
      game_id: "dated",
      kickoff_utc: "2024-09-28T16:00:00Z",
    });
    const missing = cloneWithNulls(base, {
      game_id: "missing",
      kickoff_utc: null,
    });
    expect(() => groupByKickoffDay([missing, dated], "America/New_York")).not.toThrow();
    const groups = groupByKickoffDay([missing, dated], "America/New_York");
    expect(groups.at(-1)?.id).toBe(NO_KICKOFF_GROUP_ID);
    expect(groups.at(-1)?.games.map((g) => g.game_id)).toEqual(["missing"]);
  });
});

describe("groupByConviction", () => {
  it("places the fixture strong_lean games in the top group", () => {
    const week = loadFixture();
    const groups = groupByConviction(week.games);
    const strong = groups[0];
    expect(strong.id).toBe("strong_lean");
    expect(strong.games).toHaveLength(9);
    expect(groups.some((g) => g.games.length === 0)).toBe(false);
  });

  it("omits empty top-tier headers when those tiers are absent", () => {
    const week = loadFixture();
    const withoutTop = week.games.filter(
      (g) => g.conviction_tier !== "strong_lean" && g.conviction_tier !== "clear_lean",
    );
    const groups = groupByConviction(withoutTop);
    expect(groups.map((g) => g.id)).not.toContain("strong_lean");
    expect(groups.map((g) => g.id)).not.toContain("clear_lean");
    expect(groups[0].id).toBe("lean");
  });
});

describe("null sort determinism", () => {
  it("produces identical order across repeated sorts with combined null fields", () => {
    const week = loadFixture();
    const base = week.games[0];
    const games = [
      cloneWithNulls(base, {
        game_id: "g-fix-2",
        kickoff_utc: null,
        conviction_tier: null,
        conviction_basis: null,
      }),
      cloneWithNulls(base, {
        game_id: "g-fix-1",
        kickoff_utc: null,
        conviction_tier: null,
        conviction_basis: null,
      }),
      cloneWithNulls(base, {
        game_id: "dated-a",
        kickoff_utc: "2024-09-28T16:00:00Z",
        conviction_tier: "lean",
        conviction_basis: { ...base.conviction_basis!, p_favored: 0.55 },
      }),
    ];

    const kickoffOnce = [...games].sort(compareByKickoff).map((g) => g.game_id);
    const kickoffTwice = [...games].sort(compareByKickoff).map((g) => g.game_id);
    expect(kickoffOnce).toEqual(kickoffTwice);
    expect(kickoffOnce).toEqual(["dated-a", "g-fix-1", "g-fix-2"]);

    const convictionOnce = [...games].sort(compareByConviction).map((g) => g.game_id);
    const convictionTwice = [...games].sort(compareByConviction).map((g) => g.game_id);
    expect(convictionOnce).toEqual(convictionTwice);
    expect(convictionOnce).toEqual(["dated-a", "g-fix-1", "g-fix-2"]);
  });
});

describe("null-heavy slate — W10-UI fixture", () => {
  it("is labeled fixture and has roughly 20% null conviction and margin intervals", () => {
    const week = loadNullHeavyWeek();
    expect(week.fixture).toBe(true);
    const nullConviction = week.games.filter((g) => g.conviction_tier == null).length;
    const nullInterval = week.games.filter(
      (g) => g.margin_interval_lo == null && g.margin_interval_hi == null,
    ).length;
    expect(nullConviction).toBeGreaterThanOrEqual(10);
    expect(nullInterval).toBeGreaterThanOrEqual(10);
    expect(nullConviction / week.games.length).toBeGreaterThanOrEqual(0.18);
  });

  it("does not throw when sorting by conviction with null tiers", () => {
    const week = loadNullHeavyWeek();
    expect(() => [...week.games].sort(compareByConviction)).not.toThrow();
    const sorted = [...week.games].sort(compareByConviction);
    const nullTier = sorted.filter((g) => g.conviction_tier == null);
    const tiered = sorted.filter((g) => g.conviction_tier != null);
    expect(sorted.slice(-nullTier.length).every((g) => g.conviction_tier == null)).toBe(true);
    expect(tiered.length + nullTier.length).toBe(week.games.length);
  });

  it("does not throw when grouping by conviction with null tiers", () => {
    const week = loadNullHeavyWeek();
    expect(() => groupByConviction(week.games)).not.toThrow();
    const groups = groupByConviction(week.games);
    const ids = groups.flatMap((g) => g.games.map((game) => game.game_id));
    expect(new Set(ids).size).toBe(week.games.length);
  });
});
