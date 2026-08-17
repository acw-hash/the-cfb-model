import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import Ajv from "ajv";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";

import type {
  MetaArtifact,
  ResultsSeason,
  TeamRatings,
  TrackRecord,
  WeekPredictions,
} from "@/lib/artifacts/types";
import { isSchemaVersionSupported } from "@/lib/artifacts/schema-version";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "../../..");
const FIXTURE_DIR = path.join(REPO_ROOT, "webapp/fixtures");
const SCHEMA_DIR = path.join(REPO_ROOT, "src/ncaa_quant/webapp/schemas");

const ajv = new Ajv({ allErrors: true, strict: false });
addFormats(ajv);

function loadJson(filePath: string): unknown {
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function compileSchema(name: string) {
  const schema = loadJson(path.join(SCHEMA_DIR, name)) as Record<string, unknown>;
  const { $schema: _schema, ...withoutMetaSchema } = schema;
  return ajv.compile(withoutMetaSchema);
}

describe("types-schemas sync via committed JSON Schemas", () => {
  const validateMeta = compileSchema("meta.schema.json");
  const validateWeek = compileSchema("week_predictions.schema.json");
  const validateTrack = compileSchema("track_record.schema.json");
  const validateResults = compileSchema("results_season.schema.json");
  const validateRatings = compileSchema("team_ratings.schema.json");

  it("validates meta.json fixture against meta.schema.json", () => {
    const meta = loadJson(path.join(FIXTURE_DIR, "meta.json")) as MetaArtifact;
    expect(validateMeta(meta)).toBe(true);
    expect(meta.fixture).toBe(true);
    expect(typeof meta.schema_version).toBe("string");
  });

  it("validates week_predictions.json fixture against week_predictions.schema.json", () => {
    const week = loadJson(path.join(FIXTURE_DIR, "week_predictions.json")) as WeekPredictions;
    expect(validateWeek(week)).toBe(true);
    expect(week.fixture).toBe(true);
    expect(week.schema_version).toBe("1.2.0");
    expect(week.games.length).toBeGreaterThan(0);
    for (const game of week.games) {
      expect(typeof game.game_id).toBe("string");
      expect(["strong_lean", "clear_lean", "lean", "toss_up", null]).toContain(
        game.conviction_tier,
      );
    }
  });

  it("validates 1.1.0 legacy week_predictions fixture (withdrawn keys still present)", () => {
    const week = loadJson(
      path.join(FIXTURE_DIR, "week_predictions.legacy-1.1.0.json"),
    ) as WeekPredictions;
    expect(validateWeek(week)).toBe(true);
    expect(week.schema_version).toBe("1.1.0");
    expect(isSchemaVersionSupported(week.schema_version)).toBe(true);
  });

  it("validates track_record.json fixture", () => {
    const track = loadJson(path.join(FIXTURE_DIR, "track_record.json")) as TrackRecord;
    expect(validateTrack(track)).toBe(true);
    expect(track.metrics.length).toBeGreaterThan(0);
  });

  it("validates results_2024.json fixture", () => {
    const results = loadJson(path.join(FIXTURE_DIR, "results_2024.json")) as ResultsSeason;
    expect(validateResults(results)).toBe(true);
    expect(results.grading_rule).toBe("last_pre_kickoff_publish");
  });

  it("validates team_ratings_2024.json fixture", () => {
    const ratings = loadJson(path.join(FIXTURE_DIR, "team_ratings_2024.json")) as TeamRatings;
    expect(validateRatings(ratings)).toBe(true);
    expect(Object.keys(ratings.teams).length).toBeGreaterThan(0);
  });
});

describe("hand-derived TS types remain structurally compatible", () => {
  it("week_predictions games include required export fields", () => {
    const week = loadJson(path.join(FIXTURE_DIR, "week_predictions.json")) as WeekPredictions;
    const game = week.games[0];
    expect(game).toMatchObject({
      game_id: expect.any(String),
      home_team: expect.any(String),
      away_team: expect.any(String),
      kickoff_utc: expect.any(String),
      sigma_margin_credible: expect.any(Boolean),
      tier_revised_since_primary: expect.any(Boolean),
      is_stale: expect.any(Boolean),
    });
  });
});
