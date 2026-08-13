import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import type { TeamRatingEntry, TeamRatings, WeekPredictions } from "@/lib/artifacts/types";
import { TOTAL_INTERVAL_ABSENT_REASON } from "@/lib/game-detail/absence";
import { probabilityIsCredible } from "@/lib/game-detail/credibility";
import {
  cloneNullTotalInterval,
  cloneRatingsMissingWeek,
  cloneSuppressedSigma,
  cloneTwoBandRevision,
} from "@/lib/game-detail/demo-states";
import { makeScales, pathsForSeries, yDomain } from "@/lib/game-detail/geometry";
import {
  contiguousSegments,
  omitWeek,
  seriesForTeam,
  trajectoryCaption,
} from "@/lib/game-detail/ratings";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE_DIR = path.resolve(__dirname, "../../fixtures");

function loadWeek(): WeekPredictions {
  return JSON.parse(
    fs.readFileSync(path.join(FIXTURE_DIR, "week_predictions.json"), "utf8"),
  ) as WeekPredictions;
}

function loadRatings(): TeamRatings {
  return JSON.parse(
    fs.readFileSync(path.join(FIXTURE_DIR, "team_ratings_2024.json"), "utf8"),
  ) as TeamRatings;
}

describe("seriesForTeam — PIT and collapse", () => {
  it("drops snapshots after published_at and caps at current week", () => {
    const week = loadWeek();
    const ratings = loadRatings();
    const game = week.games.find((g) => g.game_id === "401628378");
    expect(game).toBeDefined();
    const entry = ratings.teams[String(game!.home_team_id)];
    expect(entry).toBeDefined();
    const series = seriesForTeam(entry, game!.published_at, game!.week);
    expect(series.every((p) => p.week <= game!.week)).toBe(true);
    expect(series.every((p) => Date.parse(p.as_of_utc) <= Date.parse(game!.published_at))).toBe(
      true,
    );
    const weeks = series.map((p) => p.week);
    expect(new Set(weeks).size).toBe(weeks.length);
    expect(series.some((p) => p.week >= 6)).toBe(false);
  });

  it("collapses duplicate week rows to one point", () => {
    const entry: TeamRatingEntry = {
      school: "Test",
      weeks: [
        {
          week: 1,
          as_of_utc: "2024-09-01T00:00:00Z",
          off_epa: 0.1,
          def_epa: 0.0,
          pace: 0,
          off_sd: 0.05,
          def_sd: 0.05,
        },
        {
          week: 1,
          as_of_utc: "2024-09-01T00:00:00Z",
          off_epa: 0.1,
          def_epa: 0.0,
          pace: 0,
          off_sd: 0.05,
          def_sd: 0.05,
        },
        {
          week: 2,
          as_of_utc: "2024-09-08T00:00:00Z",
          off_epa: 0.12,
          def_epa: -0.01,
          pace: 0,
          off_sd: 0.04,
          def_sd: 0.04,
        },
      ],
    };
    const series = seriesForTeam(entry, "2024-09-24T06:00:00Z", 5);
    expect(series).toHaveLength(2);
    expect(series.map((p) => p.week)).toEqual([1, 2]);
  });

  it("does not interpolate a missing mid-season week", () => {
    const ratings = loadRatings();
    const week = loadWeek();
    const game = week.games.find((g) => g.game_id === "401628373")!;
    const gapped = cloneRatingsMissingWeek(ratings, game.home_team_id, game.away_team_id, 3);
    const home = seriesForTeam(
      gapped.teams[String(game.home_team_id)],
      game.published_at,
      game.week,
    );
    expect(home.some((p) => p.week === 3)).toBe(false);
    const segments = contiguousSegments(home);
    expect(segments.length).toBeGreaterThan(1);
    const domain = yDomain([home]);
    const scales = makeScales(1, game.week, domain.min, domain.max);
    const paths = pathsForSeries(home, "off", scales);
    const joined = paths.line.join(" ");
    const moveCount = (joined.match(/M/g) ?? []).length;
    expect(moveCount).toBeGreaterThanOrEqual(2);
    expect(home.map((p) => p.week)).not.toContain(3);
  });
});

describe("trajectory caption", () => {
  it("names both teams and does not invent numbers", () => {
    const points = [
      {
        week: 1,
        as_of_utc: "2024-09-01T00:00:00Z",
        off_epa: 0.1,
        def_epa: 0.0,
        off_sd: 0.05,
        def_sd: 0.05,
      },
      {
        week: 4,
        as_of_utc: "2024-09-21T00:00:00Z",
        off_epa: 0.2,
        def_epa: -0.05,
        off_sd: 0.04,
        def_sd: 0.04,
      },
    ];
    const caption = trajectoryCaption("Texas A&M", "Arkansas", points, points, 5);
    expect(caption).toContain("Texas A&M");
    expect(caption).toContain("Arkansas");
    expect(caption).toContain("+0.20");
    expect(caption).toContain("up from week 1");
  });
});

describe("omitWeek does not write fixtures", () => {
  it("leaves the on-disk ratings file unchanged", () => {
    const before = fs.readFileSync(path.join(FIXTURE_DIR, "team_ratings_2024.json"), "utf8");
    const ratings = loadRatings();
    omitWeek(Object.values(ratings.teams)[0], 3);
    const after = fs.readFileSync(path.join(FIXTURE_DIR, "team_ratings_2024.json"), "utf8");
    expect(after).toBe(before);
  });
});

describe("W4-5 doctored clones", () => {
  it("suppressed sigma hides probabilities and tier", () => {
    const week = loadWeek();
    const source = week.games.find((g) => g.conviction_tier === "toss_up")!;
    const clone = cloneSuppressedSigma(source);
    expect(clone.sigma_margin_credible).toBe(false);
    expect(clone.conviction_tier).toBeNull();
    expect(probabilityIsCredible(clone, "p_win_home")).toBe(false);
    expect(probabilityIsCredible(clone, "p_cover_home")).toBe(false);
    expect(probabilityIsCredible(clone, "p_over")).toBe(false);
    expect(clone.null_reason).toBe("cold_start_insufficient");
    expect(clone.mu_margin).toBe(source.mu_margin);
  });

  it("two-band revision is strong_lean → lean with no clear_lean", () => {
    const week = loadWeek();
    const source = week.games.find((g) => g.conviction_tier === "lean")!;
    const clone = cloneTwoBandRevision(source);
    expect(clone.tier_primary).toBe("strong_lean");
    expect(clone.conviction_tier).toBe("lean");
    expect(clone.tier_revised_since_primary).toBe(true);
    expect(clone.conviction_label).toBe(source.conviction_label);
    expect(clone.conviction_tier).not.toBe("clear_lean");
    expect(clone.tier_primary).not.toBe("clear_lean");
  });

  it("null total interval clone keeps μ and leaves bounds null", () => {
    const week = loadWeek();
    const source = week.games[0];
    const clone = cloneNullTotalInterval(source);
    expect(clone.mu_total).toBe(source.mu_total);
    expect(clone.total_interval_lo).toBeNull();
    expect(clone.total_interval_hi).toBeNull();
    expect(TOTAL_INTERVAL_ABSENT_REASON.length).toBeGreaterThan(0);
  });
});

describe("σ-gating is authoritative", () => {
  it("treats p_win as not credible when sigma_margin_credible is false even if flag true", () => {
    const week = loadWeek();
    const source = week.games[0];
    const gated = { ...source, sigma_margin_credible: false };
    expect(source.p_win_home_credible).toBe(true);
    expect(probabilityIsCredible(gated, "p_win_home")).toBe(false);
  });

  it("Liberty fixture has cover/over not credible with no fallback", () => {
    const week = loadWeek();
    const liberty = week.games.find((g) => g.game_id === "401640992")!;
    expect(probabilityIsCredible(liberty, "p_win_home")).toBe(true);
    expect(probabilityIsCredible(liberty, "p_cover_home")).toBe(false);
    expect(liberty.p_cover_home).toBeNull();
  });
});

describe("forbidden copy", () => {
  it("Game Detail UI files do not mention pick, bet, edge, or market", () => {
    const srcDir = path.resolve(__dirname, "../src");
    const roots = [path.join(srcDir, "components", "GameDetail"), path.join(srcDir, "app", "game")];
    const forbidden = /\b(picks?|bets?|betting|edges?|sportsbook|market)\b/i;
    const hits: string[] = [];
    function walk(dir: string): void {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) {
          walk(full);
        } else if (entry.name.endsWith(".tsx")) {
          const text = fs.readFileSync(full, "utf8");
          if (forbidden.test(text)) {
            hits.push(path.relative(srcDir, full));
          }
        }
      }
    }
    for (const root of roots) {
      walk(root);
    }
    expect(hits).toEqual([]);
  });
});
