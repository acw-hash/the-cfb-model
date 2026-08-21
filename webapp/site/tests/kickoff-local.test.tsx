import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { MatchupHeader } from "@/components/GameDetail/MatchupHeader";
import { GameRow } from "@/components/GameRow/GameRow";
import { GradedGameRow } from "@/components/Results/GradedGameRow";
import type { ResultsSeason, WeekPredictions } from "@/lib/artifacts/types";
import { formatKickoffLocal } from "@/lib/formatting/time";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE_DIR = path.resolve(__dirname, "../../fixtures");

/** Indiana @ Maryland fixture — production bug: slate 12 PM ET, detail 4 PM. */
const INDIANA_MARYLAND_KICKOFF = "2024-09-28T16:00:00Z";

/**
 * 2026 week-1 UNLV / Memphis — Aug 30 02:00Z is still Aug 29 evening in every US zone.
 */
const UNLV_MEMPHIS_KICKOFF = "2026-08-30T02:00:00Z";

function loadWeek(): WeekPredictions {
  return JSON.parse(
    fs.readFileSync(path.join(FIXTURE_DIR, "week_predictions.json"), "utf8"),
  ) as WeekPredictions;
}

function loadResults(): ResultsSeason {
  return JSON.parse(
    fs.readFileSync(path.join(FIXTURE_DIR, "results_2024.json"), "utf8"),
  ) as ResultsSeason;
}

describe("formatKickoffLocal — visitor timezone (W10-FIX)", () => {
  it("Indiana/Maryland: America/New_York is 12:00 PM, not UTC 4:00 PM", () => {
    const ny = formatKickoffLocal(INDIANA_MARYLAND_KICKOFF, "America/New_York");
    const utcHost = formatKickoffLocal(INDIANA_MARYLAND_KICKOFF, "UTC");

    expect(ny.local).toMatch(/12:00\s*PM/);
    expect(utcHost.local).toMatch(/4:00\s*PM/);
    expect(ny.local).not.toBe(utcHost.local);
  });

  it("UNLV/Memphis Aug 30 02:00Z is still Aug 29 evening in US zones", () => {
    for (const tz of [
      "America/New_York",
      "America/Chicago",
      "America/Denver",
      "America/Los_Angeles",
    ] as const) {
      const { local } = formatKickoffLocal(UNLV_MEMPHIS_KICKOFF, tz);
      expect(local).toMatch(/Aug\s*29/);
      expect(local).not.toMatch(/Aug\s*30/);
    }
  });

  it("null kickoff stays absent", () => {
    expect(formatKickoffLocal(null, "America/New_York")).toEqual({ local: "—", utc: "—" });
  });
});

describe("kickoff surfaces share visitor-local formatting", () => {
  it("GameRow, MatchupHeader, and GradedGameRow agree for Indiana/Maryland in ET", () => {
    const tz = "America/New_York";
    const expected = formatKickoffLocal(INDIANA_MARYLAND_KICKOFF, tz).local;
    expect(expected).toMatch(/12:00\s*PM/);

    const week = loadWeek();
    const game = week.games.find((g) => g.game_id === "401628496");
    expect(game).toBeDefined();
    expect(game!.kickoff_utc).toBe(INDIANA_MARYLAND_KICKOFF);

    const graded = loadResults().games.find((g) => g.game_id === "401628496");
    expect(graded).toBeDefined();

    const row = renderToStaticMarkup(<GameRow game={game!} timeZone={tz} />);
    const detail = renderToStaticMarkup(
      <MatchupHeader
        awayTeam={game!.away_team}
        homeTeam={game!.home_team}
        kickoffUtc={game!.kickoff_utc}
        neutralSite={game!.neutral_site}
        timeZone={tz}
      />,
    );
    const gradedHtml = renderToStaticMarkup(<GradedGameRow game={graded!} timeZone={tz} />);

    expect(row).toContain(expected);
    expect(detail).toContain(expected);
    expect(gradedHtml).toContain(expected);
    // Visible local text is 12:00 PM; tooltip may still cite 4:00 PM UTC.
    expect(detail).toMatch(/>Sat, Sep 28, 12:00 PM</);
    expect(detail).not.toMatch(/>Sat, Sep 28, 4:00 PM</);
  });

  it("date-boundary kickoff stays Aug 29 on MatchupHeader in ET", () => {
    const tz = "America/New_York";
    const expected = formatKickoffLocal(UNLV_MEMPHIS_KICKOFF, tz).local;
    const html = renderToStaticMarkup(
      <MatchupHeader
        awayTeam="Memphis"
        homeTeam="UNLV"
        kickoffUtc={UNLV_MEMPHIS_KICKOFF}
        neutralSite={false}
        timeZone={tz}
      />,
    );
    expect(expected).toMatch(/Aug\s*29/);
    expect(html).toContain(expected);
  });
});
