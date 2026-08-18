import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  GAME_FIELD_CONSUMERS,
  PUBLISHED_GAME_PREDICTION_KEYS,
  WITHDRAWN_FIELDS,
  assertConsumedOrWithdrawn,
  assertPublishedGameKeys,
} from "@/lib/artifacts/published-keys";
import { THIS_WEEK_GAME_KEYS, projectThisWeekGames } from "@/lib/this-week/project";
import { projectGameDetailGame } from "@/lib/game-detail/project";
import type { WeekPredictions } from "@/lib/artifacts/types";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE_DIR = path.resolve(__dirname, "../../fixtures");

function loadWeek(name: string): WeekPredictions {
  return JSON.parse(fs.readFileSync(path.join(FIXTURE_DIR, name), "utf8")) as WeekPredictions;
}

describe("published key allowlist (ADR 0015)", () => {
  it("GAME_FIELD_CONSUMERS covers the published key set exactly", () => {
    expect(Object.keys(GAME_FIELD_CONSUMERS).sort()).toEqual(
      [...PUBLISHED_GAME_PREDICTION_KEYS].sort(),
    );
  });

  it("1.2.0 fixture games have exactly the published keys", () => {
    const week = loadWeek("week_predictions.json");
    expect(week.schema_version).toBe("1.2.0");
    for (const game of week.games) {
      expect(Object.keys(game).sort()).toEqual([...PUBLISHED_GAME_PREDICTION_KEYS].sort());
    }
  });

  it("1.1.0 extra keys are only WITHDRAWN_FIELDS; remaining keys are published", () => {
    const week = loadWeek("week_predictions.legacy-1.1.0.json");
    expect(week.schema_version).toBe("1.1.0");
    const allowed = new Set<string>([...PUBLISHED_GAME_PREDICTION_KEYS, ...WITHDRAWN_FIELDS]);
    for (const game of week.games) {
      const keys = Object.keys(game);
      const extra = keys.filter((k) => !allowed.has(k));
      expect(extra).toEqual([]);
      const withdrawnPresent = keys.filter((k) =>
        (WITHDRAWN_FIELDS as readonly string[]).includes(k),
      );
      expect(withdrawnPresent.sort()).toEqual([...WITHDRAWN_FIELDS].sort());
    }
  });

  it("fails on a key that is neither a named consumer nor withdrawn", () => {
    const week = loadWeek("week_predictions.json");
    const poisoned = { ...week.games[0], unsanctioned_edge: 0.03 };
    expect(() => assertPublishedGameKeys(poisoned)).toThrow(/unsanctioned_edge/);
    expect(() => assertConsumedOrWithdrawn(poisoned)).toThrow(/unsanctioned_edge/);
    assertPublishedGameKeys(week.games[0]);
    assertConsumedOrWithdrawn(week.games[0]);
    const legacy = loadWeek("week_predictions.legacy-1.1.0.json");
    assertConsumedOrWithdrawn(legacy.games[0]);
  });

  it("This Week DTO key set is exact", () => {
    const week = loadWeek("week_predictions.json");
    const dto = projectThisWeekGames(week.games)[0];
    expect(Object.keys(dto).sort()).toEqual([...THIS_WEEK_GAME_KEYS].sort());
  });

  it("Game Detail DTO key set is exact (strips 1.1.0 withdrawn keys)", () => {
    const current = loadWeek("week_predictions.json");
    const legacy = loadWeek("week_predictions.legacy-1.1.0.json");
    const from12 = projectGameDetailGame(current.games[0]);
    const from11 = projectGameDetailGame(legacy.games[0]);
    expect(Object.keys(from12).sort()).toEqual([...PUBLISHED_GAME_PREDICTION_KEYS].sort());
    expect(Object.keys(from11).sort()).toEqual([...PUBLISHED_GAME_PREDICTION_KEYS].sort());
    expect(JSON.stringify(from11)).not.toContain("p_cover_home");
    expect(JSON.stringify(from11)).not.toContain("p_over");
  });
});
