import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import type { GamePrediction, WeekPredictions } from "@/lib/artifacts/types";
import {
  compareByConviction,
  compareByKickoff,
  groupByConviction,
  groupByKickoffDay,
  parseSlateOrder,
} from "@/lib/this-week/sort";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE_PATH = path.resolve(__dirname, "../../fixtures/week_predictions.json");

function loadFixture(): WeekPredictions {
  return JSON.parse(fs.readFileSync(FIXTURE_PATH, "utf8")) as WeekPredictions;
}

function cloneGame(base: GamePrediction, overlay: Partial<GamePrediction>): GamePrediction {
  return { ...base, ...overlay };
}

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
});

describe("groupByConviction", () => {
  it("places the fixture's single strong_lean in its own top group", () => {
    const week = loadFixture();
    const groups = groupByConviction(week.games);
    const strong = groups[0];
    expect(strong.id).toBe("strong_lean");
    expect(strong.games).toHaveLength(1);
    expect(strong.games[0].game_id).toBe("401628378");
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
