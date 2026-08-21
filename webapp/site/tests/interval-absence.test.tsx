import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { GameDetail } from "@/components/GameDetail/GameDetail";
import { GameRow } from "@/components/GameRow/GameRow";
import type { WeekPredictions } from "@/lib/artifacts/types";
import {
  MARGIN_INTERVAL_ABSENT_REASON,
  TOTAL_INTERVAL_ABSENT_REASON,
} from "@/lib/game-detail/absence";
import { cloneNullMarginInterval } from "@/lib/game-detail/demo-states";
import { projectGameDetailGame } from "@/lib/game-detail/project";
import { ABSENT, formatMargin } from "@/lib/formatting/numbers";
import { projectThisWeekGame } from "@/lib/this-week/project";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE_DIR = path.resolve(__dirname, "../../fixtures");
const INTERVAL_BAND_CSS = fs.readFileSync(
  path.resolve(__dirname, "../src/components/IntervalBand/IntervalBand.module.css"),
  "utf8",
);
const GAME_ROW_CSS = fs.readFileSync(
  path.resolve(__dirname, "../src/components/GameRow/GameRow.module.css"),
  "utf8",
);

function loadWeek(): WeekPredictions {
  return JSON.parse(
    fs.readFileSync(path.join(FIXTURE_DIR, "week_predictions.json"), "utf8"),
  ) as WeekPredictions;
}

describe("W10-UI — §1.8 honest interval absence", () => {
  it("This Week renders — in the Primary slot when bounds are null", () => {
    const week = loadWeek();
    const source = week.games[0];
    const clone = cloneNullMarginInterval(source);
    const html = renderToStaticMarkup(<GameRow game={projectThisWeekGame(clone)} />);
    const muText = formatMargin(clone.mu_margin, clone.sigma_margin);
    expect(muText).not.toBeNull();
    expect(html).toContain(muText!);
    expect(html).toContain('data-testid="interval-absent"');
    expect(html).toContain(ABSENT);
    expect(html).not.toMatch(/\[\+|\[\u2212/);
  });

  it("This Week reserves the same interval line height for present and null bounds", () => {
    expect(INTERVAL_BAND_CSS).toContain("min-height: var(--type-n2-line)");
  });

  it("Game Detail renders — in the Primary interval slot for a null margin band", () => {
    const week = loadWeek();
    const source = week.games[0];
    const clone = cloneNullMarginInterval(source);
    const html = renderToStaticMarkup(
      <GameDetail game={projectGameDetailGame(clone)} homeSeries={[]} awaySeries={[]} />,
    );
    expect(html).toContain('data-testid="forecast-interval-absent"');
    expect(html).toContain(ABSENT);
    expect(html).toContain(MARGIN_INTERVAL_ABSENT_REASON);
    expect(html).toContain(TOTAL_INTERVAL_ABSENT_REASON);
    expect(html).toContain("Interval not computed");
    const muText = formatMargin(clone.mu_margin, clone.sigma_margin);
    expect(muText).not.toBeNull();
    expect(html).toContain(muText!);
  });

  it("Game row team names use B2 scale, not T3", () => {
    expect(GAME_ROW_CSS).toContain("font-size: var(--type-b2-size)");
    expect(GAME_ROW_CSS).not.toContain("var(--type-t3-size)");
  });
});
