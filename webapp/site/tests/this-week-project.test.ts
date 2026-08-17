import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import type { GamePrediction, WeekPredictions } from "@/lib/artifacts/types";
import {
  THIS_WEEK_GAME_KEYS,
  projectThisWeekGame,
  projectThisWeekGames,
} from "@/lib/this-week/project";
import {
  compareByConviction,
  compareByKickoff,
  groupByConviction,
  groupByKickoffDay,
} from "@/lib/this-week/sort";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE_PATH = path.resolve(__dirname, "../../fixtures/week_predictions.json");

function loadFixture(): WeekPredictions {
  return JSON.parse(fs.readFileSync(FIXTURE_PATH, "utf8")) as WeekPredictions;
}

/** GamePrediction keys that must not appear on the client DTO. */
const FORBIDDEN_DTO_KEYS = [
  "season",
  "week",
  "home_team_id",
  "away_team_id",
  "conference_game",
  "sigma_margin_credible",
  "margin_interval_nominal",
  "mu_total",
  "sigma_total",
  "sigma_total_credible",
  "total_interval_lo",
  "total_interval_hi",
  "total_interval_nominal",
  "p_win_home",
  "p_win_home_credible",
  "p_cover_home",
  "p_cover_home_credible",
  "p_over",
  "p_over_credible",
  "conviction_team",
  "conviction_basis",
  "is_stale",
  "vintage_label",
  "ensemble_scope_label",
  "feature_time_label",
  "published_at",
  "refresh_kind",
] as const;

describe("projectThisWeekGame", () => {
  it("carries exactly the consumption allowlist, including derived p_favored", () => {
    const week = loadFixture();
    const source = week.games.find((g) => g.conviction_basis != null);
    expect(source).toBeDefined();
    const dto = projectThisWeekGame(source as GamePrediction);
    expect(Object.keys(dto).sort()).toEqual([...THIS_WEEK_GAME_KEYS].sort());
    expect(dto.p_favored).toBe(source!.conviction_basis!.p_favored);
    expect(dto.game_id).toBe(source!.game_id);
  });

  it("sets p_favored null when conviction_basis is absent", () => {
    const week = loadFixture();
    const base = week.games[0];
    const dto = projectThisWeekGame({ ...base, conviction_basis: null });
    expect(dto.p_favored).toBeNull();
  });

  it("does not serialize fields outside the allowlist", () => {
    const week = loadFixture();
    const payload = JSON.stringify(projectThisWeekGames(week.games));
    for (const key of FORBIDDEN_DTO_KEYS) {
      expect(payload).not.toContain(`"${key}"`);
    }
    expect(payload).not.toContain("mu_sigma_ratio");
    expect(payload).not.toContain("hysteresis_applied");
  });
});

describe("projected sort matches full GamePrediction sort", () => {
  it("kickoff and conviction order are identical", () => {
    const week = loadFixture();
    const projected = projectThisWeekGames(week.games);
    expect([...projected].sort(compareByKickoff).map((g) => g.game_id)).toEqual(
      [...week.games].sort(compareByKickoff).map((g) => g.game_id),
    );
    expect([...projected].sort(compareByConviction).map((g) => g.game_id)).toEqual(
      [...week.games].sort(compareByConviction).map((g) => g.game_id),
    );
  });

  it("kickoff-day and conviction groups match", () => {
    const week = loadFixture();
    const projected = projectThisWeekGames(week.games);
    const tz = "America/New_York";
    expect(
      groupByKickoffDay(projected, tz).map((g) => [g.id, g.games.map((row) => row.game_id)]),
    ).toEqual(
      groupByKickoffDay(week.games, tz).map((g) => [g.id, g.games.map((row) => row.game_id)]),
    );
    expect(
      groupByConviction(projected).map((g) => [g.id, g.games.map((row) => row.game_id)]),
    ).toEqual(groupByConviction(week.games).map((g) => [g.id, g.games.map((row) => row.game_id)]));
  });
});
