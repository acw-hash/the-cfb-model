/**
 * D4 — RSC / HTML payload must not contain Odds API market field names.
 * Broader non-rendered field leaks (conviction_basis, run_id, …) via ThisWeekSlate
 * are reported in webapp-w8a.md and deferred to successor W8-C (requires page.tsx).
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ThisWeekSlate } from "@/components/ThisWeekSlate/ThisWeekSlate";
import type { WeekPredictions } from "@/lib/artifacts/types";

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
        games={week.games}
      />,
    );
    for (const field of MARKET_FORBIDDEN) {
      expect(html.toLowerCase()).not.toContain(field);
    }
  });
});
