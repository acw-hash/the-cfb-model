/**
 * D4 / D1 — RSC / HTML payload must not contain Odds API market field names.
 * This Week client props are a projected DTO (W8-D); market names remain forbidden.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ThisWeekSlate } from "@/components/ThisWeekSlate/ThisWeekSlate";
import type { WeekPredictions } from "@/lib/artifacts/types";
import { projectThisWeekGames } from "@/lib/this-week/project";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE_DIR = path.resolve(__dirname, "../../fixtures");

const MARKET_FORBIDDEN = [
  "spread",
  "total_line",
  "moneyline",
  "market_implied",
  "sportsbook",
  "closing_line",
] as const;

describe("rendered payload — market fields forbidden", () => {
  it("This Week slate HTML does not contain market field names", () => {
    const week = JSON.parse(
      fs.readFileSync(path.join(FIXTURE_DIR, "week_predictions.json"), "utf8"),
    ) as WeekPredictions;
    const html = renderToStaticMarkup(
      <ThisWeekSlate
        season={week.season}
        week={week.week}
        publishedAt={week.published_at}
        refreshKind={week.refresh_kind}
        games={projectThisWeekGames(week.games)}
      />,
    );
    for (const field of MARKET_FORBIDDEN) {
      expect(html.toLowerCase()).not.toContain(field);
    }
  });

  it("projected This Week games JSON omits p_cover_home and p_over", () => {
    const week = JSON.parse(
      fs.readFileSync(path.join(FIXTURE_DIR, "week_predictions.json"), "utf8"),
    ) as WeekPredictions;
    const payload = JSON.stringify(projectThisWeekGames(week.games));
    expect(payload).not.toContain("p_cover_home");
    expect(payload).not.toContain("p_over");
    expect(payload).not.toContain("conviction_basis");
  });

  it("This Week HTML is identical for GamePrediction[] and the projected DTO", () => {
    const week = JSON.parse(
      fs.readFileSync(path.join(FIXTURE_DIR, "week_predictions.json"), "utf8"),
    ) as WeekPredictions;
    const props = {
      season: week.season,
      week: week.week,
      publishedAt: week.published_at,
      refreshKind: week.refresh_kind,
    };
    const full = renderToStaticMarkup(<ThisWeekSlate {...props} games={week.games} />);
    const projected = renderToStaticMarkup(
      <ThisWeekSlate {...props} games={projectThisWeekGames(week.games)} />,
    );
    expect(projected).toBe(full);
  });
});
