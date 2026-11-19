import { describe, expect, it } from "vitest";
import { matchEventsToGames, type OddsEvent, type RidgeGame } from "../src/match";
import { normalizeTeamName } from "../src/team-map";

const kick = "2026-09-27T00:00:00Z";

function event(partial: Partial<OddsEvent> & Pick<OddsEvent, "id" | "home_team" | "away_team">): OddsEvent {
  return {
    commence_time: kick,
    bookmakers: [],
    ...partial,
  };
}

describe("team map", () => {
  it("maps Odds API names to CFBD schools", () => {
    expect(normalizeTeamName("Texas A&M Aggies")).toBe("Texas A&M");
    expect(normalizeTeamName("alabama crimson tide")).toBe("Alabama");
  });

  // W12-2 — Odds unmatched "Wofford Terriers" / "Chattanooga Mocs" vs CFBD
  // 401856749 / 401856746. Exact map only (configs/team_names.yaml).
  it("maps Wofford Terriers and Chattanooga Mocs", () => {
    expect(normalizeTeamName("Wofford Terriers")).toBe("Wofford");
    expect(normalizeTeamName("Chattanooga Mocs")).toBe("Chattanooga");
  });
});

describe("W12-2 Wofford / Chattanooga crosswalk", () => {
  it("matches both FCS sides to CFBD game ids 401856749 / 401856746", () => {
    const games: RidgeGame[] = [
      {
        game_id: "401856749",
        home_team: "Ole Miss",
        away_team: "Wofford",
        kickoff_utc: kick,
      },
      {
        game_id: "401856746",
        home_team: "Alabama",
        away_team: "Chattanooga",
        kickoff_utc: kick,
      },
    ];
    const events = [
      event({
        id: "ev-wofford",
        home_team: "Ole Miss",
        away_team: "Wofford Terriers",
      }),
      event({
        id: "ev-chatt",
        home_team: "Alabama",
        away_team: "Chattanooga Mocs",
      }),
    ];
    const { matches, unmatched } = matchEventsToGames(events, games, { nowMs: 0 });
    expect(matches.map((m) => m.game_id).sort()).toEqual(["401856746", "401856749"]);
    expect(unmatched).toHaveLength(0);
  });
});

describe("neutral-site reversed match", () => {
  it("matches swapped home/away and sets swapped=true", () => {
    const games: RidgeGame[] = [
      {
        game_id: "401628373",
        home_team: "Texas A&M",
        away_team: "Arkansas",
        kickoff_utc: kick,
        neutral_site: true,
      },
    ];
    // Odds lists Arkansas as home (swap vs CFBD)
    const events = [
      event({
        id: "ev1",
        home_team: "Arkansas Razorbacks",
        away_team: "Texas A&M Aggies",
      }),
    ];
    const { matches, unmatched } = matchEventsToGames(events, games, {
      nowMs: Date.parse("2026-09-20T00:00:00Z"),
    });
    expect(matches).toHaveLength(1);
    expect(matches[0]!.swapped).toBe(true);
    expect(matches[0]!.game_id).toBe("401628373");
    expect(unmatched.filter((u) => u.side === "ridge_game")).toHaveLength(0);
  });

  it("exact ordered match sets swapped=false", () => {
    const games: RidgeGame[] = [
      {
        game_id: "1",
        home_team: "Alabama",
        away_team: "Auburn",
        kickoff_utc: kick,
      },
    ];
    const events = [
      event({
        id: "ev2",
        home_team: "Alabama Crimson Tide",
        away_team: "Auburn Tigers",
      }),
    ];
    const { matches } = matchEventsToGames(events, games, { nowMs: 0 });
    expect(matches[0]!.swapped).toBe(false);
  });

  it("never fuzzy-matches", () => {
    const games: RidgeGame[] = [
      {
        game_id: "1",
        home_team: "Michigan",
        away_team: "Ohio State",
        kickoff_utc: kick,
      },
    ];
    const events = [
      event({
        id: "ev3",
        home_team: "Michigan State Spartans",
        away_team: "Ohio State Buckeyes",
      }),
    ];
    const { matches, unmatched } = matchEventsToGames(events, games, { nowMs: 0 });
    expect(matches).toHaveLength(0);
    expect(unmatched.length).toBeGreaterThanOrEqual(2);
  });
});
