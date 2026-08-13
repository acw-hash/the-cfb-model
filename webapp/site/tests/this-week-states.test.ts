import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import type { WeekPredictions } from "@/lib/artifacts/types";
import {
  cloneEmptyTopTiers,
  cloneOffseason,
  cloneStale,
  cloneSuppressed,
  cloneTwoBandRevision,
} from "@/lib/this-week/demo-states";
import { groupByConviction } from "@/lib/this-week/sort";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE_PATH = path.resolve(__dirname, "../../fixtures/week_predictions.json");

function loadFixture(): WeekPredictions {
  return JSON.parse(fs.readFileSync(FIXTURE_PATH, "utf8")) as WeekPredictions;
}

describe("W3-3 doctored clones (fixtures untouched)", () => {
  it("SPARSE TOP TIER: fixture week 5 has exactly one strong_lean", () => {
    const week = loadFixture();
    expect(week.games).toHaveLength(56);
    const strong = week.games.filter((g) => g.conviction_tier === "strong_lean");
    expect(strong).toHaveLength(1);
  });

  it("EMPTY TOP TIERS: clone has zero strong_lean and zero clear_lean", () => {
    const week = loadFixture();
    const clone = cloneEmptyTopTiers(week.games);
    expect(clone.some((g) => g.conviction_tier === "strong_lean")).toBe(false);
    expect(clone.some((g) => g.conviction_tier === "clear_lean")).toBe(false);
    expect(clone.length).toBeGreaterThan(0);
    const groups = groupByConviction(clone);
    expect(groups[0].id).toBe("lean");
  });

  it("SUPPRESSED TIERS: sigma refusal nulls conviction fields", () => {
    const week = loadFixture();
    const source = week.games.find((g) => g.conviction_tier === "toss_up");
    expect(source).toBeDefined();
    const clone = cloneSuppressed(source!);
    expect(clone.sigma_margin_credible).toBe(false);
    expect(clone.sigma_margin).toBeNull();
    expect(clone.conviction_tier).toBeNull();
    expect(clone.conviction_label).toBeNull();
    expect(clone.null_reason).toBe("cold_start_insufficient");
    expect(clone.mu_margin).toBe(source!.mu_margin);
  });

  it("STALE GAMES: stamp is the artifact field; tier remains at 4.0h", () => {
    const week = loadFixture();
    const source = week.games.find((g) => g.conviction_tier === "lean")!;
    const clone = cloneStale(source);
    expect(clone.is_stale).toBe(true);
    expect(clone.stale_stamp).toBe("STALE(odds, 4.0h)");
    expect(clone.conviction_tier).toBe(source.conviction_tier);
    expect(clone.mu_margin).toBe(source.mu_margin);
  });

  it("REVISED: two-band jump keeps current label and names both endpoints", () => {
    const week = loadFixture();
    const source = week.games.find((g) => g.conviction_tier === "lean")!;
    const clone = cloneTwoBandRevision(source);
    expect(clone.tier_primary).toBe("strong_lean");
    expect(clone.conviction_tier).toBe("lean");
    expect(clone.tier_revised_since_primary).toBe(true);
    expect(clone.conviction_label).toBe(source.conviction_label);
    expect(clone.mu_margin).toBe(source.mu_margin);
  });

  it("OFFSEASON: empty games array, header fields intact", () => {
    const week = loadFixture();
    const clone = cloneOffseason(week);
    expect(clone.games).toEqual([]);
    expect(clone.season).toBe(week.season);
    expect(clone.week).toBe(week.week);
  });
});

describe("fixture file is unmodified", () => {
  it("still contains the week-5 strong_lean row after clone helpers run", () => {
    const week = loadFixture();
    cloneEmptyTopTiers(week.games);
    cloneOffseason(week);
    expect(week.games).toHaveLength(56);
    expect(week.games.some((g) => g.conviction_tier === "strong_lean")).toBe(true);
  });
});
