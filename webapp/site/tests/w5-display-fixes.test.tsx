import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import { GameDetail } from "@/components/GameDetail/GameDetail";
import type { GamePrediction } from "@/lib/artifacts/types";
import { makeScales, niceTicks, yDomain } from "@/lib/game-detail/geometry";
import type { RatingPoint } from "@/lib/game-detail/ratings";
import { teamRatingsArtifactFile } from "@/lib/artifacts/types";

const baseGame: GamePrediction = {
  game_id: "401858474",
  season: 2026,
  week: 5,
  home_team: "Minnesota",
  away_team: "Michigan",
  home_team_id: 1,
  away_team_id: 2,
  kickoff_utc: "2026-10-03T16:00:00Z",
  neutral_site: false,
  conference_game: true,
  mu_margin: 5.5,
  sigma_margin: 14,
  sigma_margin_credible: true,
  margin_interval_lo: -10,
  margin_interval_hi: 20,
  margin_interval_nominal: 0.8,
  mu_total: 48,
  sigma_total: 12,
  sigma_total_credible: true,
  total_interval_lo: null,
  total_interval_hi: null,
  total_interval_nominal: null,
  p_win_home: 0.62,
  p_win_home_credible: true,
  conviction_tier: "lean",
  conviction_team: "Minnesota",
  conviction_label: "Lean Minnesota",
  conviction_basis: null,
  tier_primary: "lean",
  tier_revised_since_primary: false,
  is_stale: false,
  stale_stamp: null,
  stale_sources: [],
  null_reason: null,
  vintage_label: "x",
  ensemble_scope_label: "x",
  feature_time_label: "x",
  published_at: "2026-10-02T12:22:20Z",
  refresh_kind: "tuesday_primary",
};

describe("teamRatingsArtifactFile", () => {
  it("names the season file without hardcoding a year at the call site", () => {
    expect(teamRatingsArtifactFile(2026)).toBe("team_ratings_2026.json");
    expect(teamRatingsArtifactFile(2024)).toBe("team_ratings_2024.json");
  });
});

describe("GameDetail ratings empty state", () => {
  it("renders honest empty copy for an unpublished season and never a chart", () => {
    const html = renderToStaticMarkup(
      <GameDetail
        game={baseGame}
        homeSeries={[]}
        awaySeries={[]}
        ratingsSeason={2026}
        ratingsUnavailable
      />,
    );
    expect(html).toContain("Rating history for 2026 isn&#x27;t published yet.");
    expect(html).toContain('data-testid="ratings-unavailable"');
    expect(html).not.toContain('data-testid="trajectory-chart"');
  });
});

describe("niceTicks clamp", () => {
  it("keeps every tick inside [min, max] and monotonic in value", () => {
    const domain = yDomain([
      [
        {
          week: 1,
          as_of_utc: "2026-09-01T00:00:00Z",
          off_epa: 0.1,
          def_epa: -0.05,
          off_sd: 0.08,
          def_sd: 0.08,
        },
        {
          week: 2,
          as_of_utc: "2026-09-08T00:00:00Z",
          off_epa: 0.2,
          def_epa: 0.0,
          off_sd: 0.07,
          def_sd: 0.07,
        },
      ] satisfies RatingPoint[],
    ]);
    const ticks = niceTicks(domain.min, domain.max);
    expect(ticks.length).toBeGreaterThan(0);
    for (const t of ticks) {
      expect(t).toBeGreaterThanOrEqual(domain.min - 1e-9);
      expect(t).toBeLessThanOrEqual(domain.max + 1e-9);
    }
    for (let i = 1; i < ticks.length; i += 1) {
      expect(ticks[i]).toBeGreaterThan(ticks[i - 1]);
    }
    const scales = makeScales(1, 5, domain.min, domain.max);
    const ys = ticks.map((t) => scales.y(t));
    for (let i = 1; i < ys.length; i += 1) {
      // SVG y grows downward — larger EPA → smaller y.
      expect(ys[i]).toBeLessThan(ys[i - 1]);
    }
    expect(ys.every((y) => y >= 0 && y <= 200)).toBe(true);
  });
});
