import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { GameDetail } from "@/components/GameDetail/GameDetail";
import { ResultsPage } from "@/components/Results/ResultsPage";
import type {
  TeamRatingEntry,
  TeamRatings,
  TrackRecord,
  WeekPredictions,
} from "@/lib/artifacts/types";
import { isSchemaVersionSupported } from "@/lib/artifacts/schema-version";
import { projectGameDetailGame } from "@/lib/game-detail/project";
import { lookupTeam, seriesForTeam } from "@/lib/game-detail/ratings";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE_DIR = path.resolve(__dirname, "../../fixtures");

function loadWeek(): WeekPredictions {
  return JSON.parse(
    fs.readFileSync(path.join(FIXTURE_DIR, "week_predictions.json"), "utf8"),
  ) as WeekPredictions;
}

function loadTrack(): TrackRecord {
  return JSON.parse(
    fs.readFileSync(path.join(FIXTURE_DIR, "track_record.json"), "utf8"),
  ) as TrackRecord;
}

const EMPTY_RATINGS: TeamRatings = {
  schema_version: "1.1.0",
  season: 2024,
  published_at: "2024-09-24T06:00:00Z",
  teams: {},
};

describe("PROD-500 — incomplete artifacts render without throw", () => {
  it("Game Detail survives empty team_ratings (teams: {})", () => {
    const week = loadWeek();
    const game = week.games[0];
    const homeSeries = seriesForTeam(
      lookupTeam(EMPTY_RATINGS, game.home_team_id),
      game.published_at,
      game.week,
    );
    const awaySeries = seriesForTeam(
      lookupTeam(EMPTY_RATINGS, game.away_team_id),
      game.published_at,
      game.week,
    );
    expect(homeSeries).toEqual([]);
    expect(awaySeries).toEqual([]);
    const html = renderToStaticMarkup(
      <GameDetail
        game={projectGameDetailGame(game)}
        homeSeries={homeSeries}
        awaySeries={awaySeries}
      />,
    );
    expect(html).toContain('data-testid="game-detail"');
    expect(html).not.toContain('data-testid="trajectory-chart"');
  });

  it("Game Detail survives team entry without weeks array", () => {
    const week = loadWeek();
    const game = week.games[0];
    const stub: TeamRatings = {
      ...EMPTY_RATINGS,
      teams: {
        [String(game.home_team_id)]: { school: game.home_team, weeks: [] },
        [String(game.away_team_id)]: { school: game.away_team, weeks: [] },
      },
    };
    const awayEntry = { school: game.away_team } as TeamRatingEntry;
    expect(() => seriesForTeam(awayEntry, game.published_at, game.week)).not.toThrow();
    const html = renderToStaticMarkup(
      <GameDetail
        game={projectGameDetailGame(game)}
        homeSeries={seriesForTeam(
          lookupTeam(stub, game.home_team_id),
          game.published_at,
          game.week,
        )}
        awaySeries={seriesForTeam(awayEntry, game.published_at, game.week)}
      />,
    );
    expect(html).toContain('data-testid="game-detail"');
  });

  it("Results survives track_record with empty metrics array", () => {
    const track = loadTrack();
    const broken: TrackRecord = {
      ...track,
      metrics: [],
    };
    const html = renderToStaticMarkup(<ResultsPage track={broken} results={null} />);
    expect(html).toContain('data-testid="results-page"');
    expect(html).toContain('data-testid="metric-missing-fund_ats_snapshots"');
  });

  it("Results survives percent metric present but CI absent", () => {
    const track = loadTrack();
    const broken: TrackRecord = {
      ...track,
      metrics: track.metrics.map((metric) =>
        metric.id === "fund_ats_snapshots"
          ? { ...metric, ci_lower: null, ci_upper: null, ci_kind: "none" as const }
          : metric,
      ),
    };
    const html = renderToStaticMarkup(<ResultsPage track={broken} results={null} />);
    expect(html).toContain('data-testid="metric-incomplete-fund_ats_snapshots"');
  });

  it("isSchemaVersionSupported gates invalid schema to maintenance without throwing", () => {
    expect(isSchemaVersionSupported(undefined)).toBe(false);
    expect(isSchemaVersionSupported("not-a-version")).toBe(false);
    expect(isSchemaVersionSupported("2.0.0")).toBe(false);
  });
});
