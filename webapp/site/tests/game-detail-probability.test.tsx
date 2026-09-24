import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { GameDetail } from "@/components/GameDetail/GameDetail";
import { isSchemaVersionSupported } from "@/lib/artifacts/schema-version";
import type { WeekPredictions } from "@/lib/artifacts/types";
import { projectGameDetailGame } from "@/lib/game-detail/project";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE_DIR = path.resolve(__dirname, "../../fixtures");

function loadWeek(name: string): WeekPredictions {
  return JSON.parse(fs.readFileSync(path.join(FIXTURE_DIR, name), "utf8")) as WeekPredictions;
}

function renderGame(week: WeekPredictions): string {
  expect(isSchemaVersionSupported(week.schema_version)).toBe(true);
  const source = week.games.find((g) => g.game_id === "401628373");
  expect(source).toBeDefined();
  return renderToStaticMarkup(
    <GameDetail game={projectGameDetailGame(source!)} homeSeries={[]} awaySeries={[]} />,
  );
}

describe("Game Detail probability list after ADR 0015 withdrawal", () => {
  it("1.2.0 and 1.1.0 both show win chance in plain summary — no Cover/Over", () => {
    const current = loadWeek("week_predictions.json");
    const legacy = loadWeek("week_predictions.legacy-1.1.0.json");
    expect(current.schema_version).toBe("1.2.0");
    expect(legacy.schema_version).toBe("1.1.0");

    const html12 = renderGame(current);
    const html11 = renderGame(legacy);

    expect(html12).toContain('data-testid="plain-summary"');
    expect(html12).toContain("76%");
    expect(html11).toContain("68%");
    expect(html12).not.toContain("Cover");
    expect(html12).not.toContain("Over");
    expect(html11).not.toContain("Cover");
    expect(html11).not.toContain("Over");
    expect(html12).not.toContain("p_cover_home");
    expect(html11).not.toContain("p_cover_home");
    expect(html12).not.toContain("Ridge is updating");
    expect(html11).not.toContain("Ridge is updating");
    // Cover/over withdrawn — no Probabilities section; provenance in More detail.
    expect(html12).toContain('data-testid="more-detail"');
    expect(html12).toContain('data-testid="provenance"');
  });
});
