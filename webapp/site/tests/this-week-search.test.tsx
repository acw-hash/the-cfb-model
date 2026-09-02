import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { TeamSearch } from "@/components/TeamSearch/TeamSearch";
import type { GamePrediction, WeekPredictions } from "@/lib/artifacts/types";
import { projectThisWeekGames } from "@/lib/this-week/project";
import {
  filterGamesByQuery,
  normalizeTeamSearchText,
  parseSearchQuery,
  tokenizeSearchQuery,
} from "@/lib/this-week/search";
import {
  groupByConviction,
  groupByKickoffDay,
  groupSlate,
  NO_KICKOFF_GROUP_ID,
} from "@/lib/this-week/sort";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE_PATH = path.resolve(__dirname, "../../fixtures/week_predictions.json");

function loadFixture(): WeekPredictions {
  return JSON.parse(fs.readFileSync(FIXTURE_PATH, "utf8")) as WeekPredictions;
}

function cloneGame(base: GamePrediction, overlay: Partial<GamePrediction>): GamePrediction {
  return { ...base, ...overlay };
}

/** Overlay that may set nullable team names to JSON null (fixture types are non-null). */
function cloneWithNullTeams(
  base: GamePrediction,
  overlay: Partial<{
    game_id: string;
    away_team: string | null;
    home_team: string | null;
  }>,
): GamePrediction {
  return { ...base, ...overlay } as GamePrediction;
}

describe("normalizeTeamSearchText", () => {
  it("is case-insensitive and trims surrounding whitespace", () => {
    expect(normalizeTeamSearchText("  Michigan  ")).toBe("michigan");
  });

  it("strips combining marks and removes apostrophe-like characters", () => {
    expect(normalizeTeamSearchText("Hawai\u02bbi")).toBe("hawaii");
    expect(normalizeTeamSearchText("Hawai\u02bci")).toBe("hawaii");
    expect(normalizeTeamSearchText("Hawai\u2019i")).toBe("hawaii");
    expect(normalizeTeamSearchText("Hawai'i")).toBe("hawaii");
    expect(normalizeTeamSearchText("São Paulo")).toBe("sao paulo");
  });

  it("does not throw on null or empty", () => {
    expect(normalizeTeamSearchText(null)).toBe("");
    expect(normalizeTeamSearchText(undefined)).toBe("");
    expect(normalizeTeamSearchText("")).toBe("");
  });
});

describe("filterGamesByQuery", () => {
  it("matches a substring on the home team", () => {
    const week = loadFixture();
    const match = week.games.find((g) => g.home_team === "Michigan");
    expect(match).toBeDefined();
    const filtered = filterGamesByQuery(week.games, "michigan");
    expect(filtered.some((g) => g.game_id === match!.game_id)).toBe(true);
    expect(
      filtered.every((g) => g.home_team.includes("Michigan") || g.away_team.includes("Michigan")),
    ).toBe(true);
  });

  it("matches a substring on the away team", () => {
    const week = loadFixture();
    const match = week.games.find((g) => g.away_team === "Ohio State");
    expect(match).toBeDefined();
    const filtered = filterGamesByQuery(week.games, "ohio state");
    expect(filtered.some((g) => g.game_id === match!.game_id)).toBe(true);
  });

  it("is case-insensitive and trims the query", () => {
    const week = loadFixture();
    const base = week.games[0];
    const game = cloneGame(base, {
      game_id: "case-test",
      away_team: "Akron",
      home_team: "Bowling Green",
    });
    expect(filterGamesByQuery([game], "  AKRON  ")).toEqual([game]);
  });

  it("requires every token across away or home (michigan ohio)", () => {
    const week = loadFixture();
    const matchup = week.games.find(
      (g) => g.away_team === "Ohio State" && g.home_team === "Michigan State",
    );
    expect(matchup).toBeDefined();
    const filtered = filterGamesByQuery(week.games, "michigan ohio");
    expect(filtered.map((g) => g.game_id)).toContain(matchup!.game_id);
  });

  it("excludes a game when a token matches neither side", () => {
    const week = loadFixture();
    const matchup = week.games.find(
      (g) => g.away_team === "Ohio State" && g.home_team === "Michigan State",
    );
    expect(matchup).toBeDefined();
    const filtered = filterGamesByQuery(week.games, "michigan ohio alabama");
    expect(filtered.map((g) => g.game_id)).not.toContain(matchup!.game_id);
  });

  it("matches ohio st via tokenized substrings", () => {
    const week = loadFixture();
    const match = week.games.find((g) => g.away_team === "Ohio State");
    expect(match).toBeDefined();
    const filtered = filterGamesByQuery(week.games, "ohio st");
    expect(filtered.some((g) => g.game_id === match!.game_id)).toBe(true);
  });

  it("matches diacritic and apostrophe variants", () => {
    const week = loadFixture();
    const base = week.games[0];
    const game = cloneGame(base, {
      game_id: "hawaii-test",
      away_team: "Boise State",
      home_team: "Hawai\u02bbi",
    });
    expect(filterGamesByQuery([game], "hawaii")).toEqual([game]);
    expect(filterGamesByQuery([game], "HAWAI\u2019I")).toEqual([game]);
  });

  it("matches plain Hawaii when the query uses okina or modifier apostrophe", () => {
    const week = loadFixture();
    const base = week.games[0];
    const game = cloneGame(base, {
      game_id: "hawaii-plain",
      away_team: "Boise State",
      home_team: "Hawaii",
    });
    expect(filterGamesByQuery([game], "Hawai\u02bbi")).toEqual([game]);
    expect(filterGamesByQuery([game], "Hawai\u02bci")).toEqual([game]);
  });

  it("matches okina team name when the query is plain hawaii", () => {
    const week = loadFixture();
    const base = week.games[0];
    const game = cloneGame(base, {
      game_id: "hawaii-okina",
      away_team: "Boise State",
      home_team: "Hawai\u02bbi",
    });
    expect(filterGamesByQuery([game], "hawaii")).toEqual([game]);
  });

  it("cross-matches straight apostrophe query and okina team name", () => {
    const week = loadFixture();
    const base = week.games[0];
    const okinaTeam = cloneGame(base, {
      game_id: "hawaii-okina-cross",
      away_team: "Boise State",
      home_team: "Hawai\u02bbi",
    });
    const straightTeam = cloneGame(base, {
      game_id: "hawaii-straight-cross",
      away_team: "Boise State",
      home_team: "Hawai'i",
    });
    expect(filterGamesByQuery([okinaTeam], "Hawai'i")).toEqual([okinaTeam]);
    expect(filterGamesByQuery([straightTeam], "Hawai\u02bbi")).toEqual([straightTeam]);
  });

  it("does not throw on null away_team and home_team", () => {
    const week = loadFixture();
    const base = week.games[0];
    const games = [
      cloneWithNullTeams(base, { game_id: "g-fix-1", away_team: null, home_team: null }),
      cloneWithNullTeams(base, { game_id: "g-fix-2", away_team: null, home_team: null }),
      cloneWithNullTeams(base, {
        game_id: "dated-a",
        away_team: "Akron",
        home_team: "Kent State",
      }),
    ];
    expect(() => filterGamesByQuery(games, "akron")).not.toThrow();
    expect(filterGamesByQuery(games, "akron").map((g) => g.game_id)).toEqual(["dated-a"]);
    expect(filterGamesByQuery(games, "missing")).toEqual([]);
    expect(() => filterGamesByQuery(games, "x")).not.toThrow();
  });

  it("returns the input array unchanged for an empty query", () => {
    const week = loadFixture();
    expect(filterGamesByQuery(week.games, "")).toBe(week.games);
    expect(filterGamesByQuery(week.games, "   ")).toBe(week.games);
  });
});

describe("filter composes with sort and grouping", () => {
  const tz = "America/New_York";

  it("filters before kickoff grouping with no empty day headers", () => {
    const week = loadFixture();
    const projected = projectThisWeekGames(week.games);
    const filtered = filterGamesByQuery(projected, "michigan");
    const groups = groupByKickoffDay(filtered, tz);
    expect(groups.every((g) => g.games.length > 0)).toBe(true);
    const ids = new Set(groups.flatMap((g) => g.games.map((game) => game.game_id)));
    expect(ids.size).toBe(filtered.length);
  });

  it("filters before conviction grouping with no empty tier headers", () => {
    const week = loadFixture();
    const projected = projectThisWeekGames(week.games);
    const filtered = filterGamesByQuery(projected, "ohio");
    const groups = groupByConviction(filtered);
    expect(groups.every((g) => g.games.length > 0)).toBe(true);
  });

  it("omits the kickoff-unavailable group when null-kickoff rows do not match", () => {
    const week = loadFixture();
    const base = week.games[0];
    const noKick = {
      ...base,
      game_id: "no-kick",
      kickoff_utc: null,
      away_team: "Team A",
      home_team: "Team B",
    } as unknown as GamePrediction;
    const dated = cloneGame(base, {
      game_id: "dated",
      away_team: "Akron",
      home_team: "Kent State",
      kickoff_utc: "2024-09-28T16:00:00Z",
    });
    const games = projectThisWeekGames([noKick, dated]);
    const filtered = filterGamesByQuery(games, "akron");
    const groups = groupSlate(filtered, "kickoff", tz);
    expect(groups.some((g) => g.id === NO_KICKOFF_GROUP_ID)).toBe(false);
    expect(groups.flatMap((g) => g.games.map((game) => game.game_id))).toEqual(["dated"]);
  });
});

describe("parseSearchQuery and ?q= hydration", () => {
  it("reads q from URLSearchParams", () => {
    const params = new URLSearchParams("?order=conviction&q=Ohio%20State");
    expect(parseSearchQuery(params.get("q"))).toBe("Ohio State");
    expect(parseSearchQuery(params.get("missing"))).toBe("");
  });

  it("hydrated query filters the projected slate like the live control", () => {
    const week = loadFixture();
    const projected = projectThisWeekGames(week.games);
    const params = new URLSearchParams("?q=Ohio%20State");
    const query = parseSearchQuery(params.get("q"));
    const filtered = filterGamesByQuery(projected, query);
    const expected = week.games.filter(
      (g) => g.away_team === "Ohio State" || g.home_team === "Ohio State",
    );
    expect(filtered.map((g) => g.game_id).sort()).toEqual(expected.map((g) => g.game_id).sort());
  });
});

describe("TeamSearch markup", () => {
  it("renders a labeled search input with placeholder", () => {
    const html = renderToStaticMarkup(
      <TeamSearch value="" onChange={() => undefined} onClear={() => undefined} />,
    );
    expect(html).toContain('type="search"');
    expect(html).toContain('placeholder="Search teams"');
    expect(html).toContain('for="team-search"');
    expect(html).toContain("Search teams");
  });

  it("shows a clear button only when the field is non-empty", () => {
    const empty = renderToStaticMarkup(
      <TeamSearch value="" onChange={() => undefined} onClear={() => undefined} />,
    );
    expect(empty).not.toContain("Clear search");

    const filled = renderToStaticMarkup(
      <TeamSearch value="ohio" onChange={() => undefined} onClear={() => undefined} />,
    );
    expect(filled).toContain('aria-label="Clear search"');
  });
});

describe("tokenizeSearchQuery", () => {
  it("returns no tokens for whitespace-only input", () => {
    expect(tokenizeSearchQuery("   ")).toEqual([]);
  });
});
