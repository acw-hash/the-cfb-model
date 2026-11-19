import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import { GameDetail } from "@/components/GameDetail/GameDetail";
import { RatingTrajectoryChart } from "@/components/GameDetail/RatingTrajectoryChart";
import {
  RatingsDirectionHomeNote,
  RatingsDirectionNote,
} from "@/components/RatingsDirectionNote/RatingsDirectionNote";
import { ThisWeekHeader } from "@/components/ThisWeekHeader/ThisWeekHeader";
import type { GamePrediction } from "@/lib/artifacts/types";
import type { RatingPoint } from "@/lib/game-detail/ratings";
import PriorFigure from "@/components/ModelWalkthrough/figures/PriorFigure";
import KalmanFigure from "@/components/ModelWalkthrough/figures/KalmanFigure";

const series: RatingPoint[] = [
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
    off_epa: 0.15,
    def_epa: 0.0,
    off_sd: 0.07,
    def_sd: 0.07,
  },
];

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

describe("W12-2 G2b Higher is better", () => {
  it("renders on the homepage This Week header (SSR)", () => {
    const html = renderToStaticMarkup(
      <ThisWeekHeader
        season={2026}
        week={12}
        publishedAt="2026-11-18T12:00:00Z"
        refreshKind="tuesday_primary"
      />,
    );
    expect(html).toContain("Higher is better");
    expect(html).toContain('data-testid="ratings-direction-home"');
  });

  it("renders on shared offense/defense notes", () => {
    expect(renderToStaticMarkup(<RatingsDirectionNote dimension="offense" />)).toContain(
      "Higher is better",
    );
    expect(renderToStaticMarkup(<RatingsDirectionNote dimension="defense" />)).toContain(
      "Higher is better",
    );
    expect(renderToStaticMarkup(<RatingsDirectionHomeNote />)).toContain("Higher is better");
  });

  it("renders on game detail rating trajectory", () => {
    const chart = renderToStaticMarkup(
      <RatingTrajectoryChart
        home={series}
        away={series}
        homeSchool="Minnesota"
        awaySchool="Michigan"
        throughWeek={2}
      />,
    );
    expect(chart).toContain("Higher is better");
    expect(chart).toContain('data-testid="offense-direction"');
    expect(chart).toContain('data-testid="defense-direction"');

    const detail = renderToStaticMarkup(
      <GameDetail
        game={baseGame}
        homeSeries={series}
        awaySeries={series}
        ratingsSeason={2026}
        ratingsUnavailable={false}
      />,
    );
    expect(detail).toContain("Higher is better");
  });

  it("renders on Visual team rating figures", () => {
    const prior = renderToStaticMarkup(
      <PriorFigure returning={0.8} missing={false} onReturning={() => {}} onMissing={() => {}} />,
    );
    const kalman = renderToStaticMarkup(<KalmanFigure returning={0.8} missing={false} />);
    expect(prior).toContain("Higher is better");
    expect(kalman).toContain("Higher is better");
  });
});
