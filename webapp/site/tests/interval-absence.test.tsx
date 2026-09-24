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
import { ABSENT } from "@/lib/formatting/numbers";
import { formatTeamNamedMargin } from "@/lib/formatting/team-margin";
import { projectThisWeekGame } from "@/lib/this-week/project";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE_DIR = path.resolve(__dirname, "../../fixtures");
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
  it("This Week shows team-named margin and omits the range line (clarity)", () => {
    const week = loadWeek();
    const source = week.games[0];
    const clone = cloneNullMarginInterval(source);
    const html = renderToStaticMarkup(<GameRow game={projectThisWeekGame(clone)} />);
    const muText = formatTeamNamedMargin(
      clone.mu_margin,
      clone.home_team,
      clone.away_team,
      clone.sigma_margin,
      clone.p_win_home,
    );
    expect(muText).not.toBeNull();
    expect(html).toContain(muText!.replace(/&/g, "&amp;"));
    // Range moved to Game Detail — phone-width team-named intervals do not fit.
    expect(html).not.toContain('data-testid="interval-line"');
    expect(html).not.toContain('data-testid="interval-absent"');
    expect(html).not.toMatch(/\[\+|\[\u2212/);
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
    const muText = formatTeamNamedMargin(
      clone.mu_margin,
      clone.home_team,
      clone.away_team,
      clone.sigma_margin,
      clone.p_win_home,
    );
    expect(muText).not.toBeNull();
    expect(html).toContain(muText!.replace(/&/g, "&amp;"));
  });

  it("Game row team names use B2 scale, not T3", () => {
    expect(GAME_ROW_CSS).toContain("font-size: var(--type-b2-size)");
    expect(GAME_ROW_CSS).not.toContain("var(--type-t3-size)");
  });
});
