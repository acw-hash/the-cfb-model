import { afterEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import { GameRow } from "@/components/GameRow/GameRow";
import { GameDetail } from "@/components/GameDetail/GameDetail";
import { MaintenanceState } from "@/components/MaintenanceState/MaintenanceState";
import { loadOddsPageContext } from "@/lib/odds/load";
import { buildOddsPageContext } from "@/lib/odds/staleness";
import type { OddsSnapshot } from "@/lib/odds/types";
import type { GamePrediction } from "@/lib/artifacts/types";

const sampleGame: GamePrediction = {
  game_id: "401628373",
  season: 2024,
  week: 5,
  home_team: "Texas A&M",
  away_team: "Arkansas",
  home_team_id: 1,
  away_team_id: 2,
  kickoff_utc: "2024-10-05T00:00:00Z",
  neutral_site: false,
  conference_game: true,
  mu_margin: 4.2,
  sigma_margin: 13,
  sigma_margin_credible: true,
  margin_interval_lo: -8,
  margin_interval_hi: 17,
  margin_interval_nominal: 0.9,
  mu_total: 52,
  sigma_total: 10,
  sigma_total_credible: true,
  total_interval_lo: 40,
  total_interval_hi: 64,
  total_interval_nominal: 0.9,
  p_win_home: 0.64,
  p_win_home_credible: true,
  conviction_tier: "lean",
  conviction_team: "Texas A&M",
  conviction_label: "Lean Texas A&M",
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
  published_at: "2024-10-01T10:00:00Z",
  refresh_kind: "tuesday_primary",
};

describe("odds isolation", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it("flag off → loadOddsPageContext null (no odds DOM required)", async () => {
    vi.stubEnv("ODDS_SNAPSHOT_ENABLED", "false");
    expect(await loadOddsPageContext()).toBeNull();
    const html = renderToStaticMarkup(<GameRow game={sampleGame} odds={null} />);
    expect(html).not.toContain('data-testid="market-cell"');
  });

  it("missing / broken / unsupported do not imply MaintenanceState", () => {
    const maint = renderToStaticMarkup(<MaintenanceState />);
    expect(maint).toContain("Ridge is updating");

    const unsupported = buildOddsPageContext({
      schema_version: "2.0.0",
      fixture: true,
      season: 2024,
      week: 5,
      snapshot_at: new Date().toISOString(),
      source: { provider: "The Odds API", regions: "us", markets: [], requests_remaining: 1 },
      consensus_method: "x",
      games: [],
      unmatched: [],
    } satisfies OddsSnapshot);
    expect(unsupported).toBeNull();

    const html = renderToStaticMarkup(
      <GameDetail game={sampleGame} homeSeries={[]} awaySeries={[]} odds={null} oddsMeta={null} />,
    );
    expect(html).toContain('data-testid="game-detail"');
    expect(html).not.toContain("model-and-market");
    expect(html).not.toContain("Ridge is updating");
  });

  it("σ-suppressed game still renders market when present", () => {
    const suppressed: GamePrediction = {
      ...sampleGame,
      mu_margin: null,
      sigma_margin_credible: false,
      p_win_home_credible: false,
      p_win_home: null,
      null_reason: "cold_start_insufficient",
    };
    const odds = {
      game_id: suppressed.game_id,
      market_home_margin: 3.5,
      spread_home_points: -3.5,
      spread_book_count: 7,
      total_points: 52.5,
      total_book_count: 7,
      p_win_home_market: 0.61,
      h2h_book_count: 6,
      market_thin: false,
      carried_forward: false,
      captured_at: "2026-09-24T13:00:00Z",
    };
    const html = renderToStaticMarkup(
      <GameDetail
        game={suppressed}
        homeSeries={[]}
        awaySeries={[]}
        odds={odds}
        oddsMeta={{
          snapshot_at: "2026-09-24T13:00:00Z",
          provider: "The Odds API",
          consensus_method: "median_across_books_devigged_h2h",
        }}
      />,
    );
    expect(html).toContain("model-and-market");
    expect(html).toContain("Texas A&amp;M by 3.5");
  });

  it("carried_forward label", () => {
    const odds = {
      game_id: sampleGame.game_id,
      market_home_margin: 3.5,
      spread_home_points: -3.5,
      spread_book_count: 7,
      total_points: 52.5,
      total_book_count: 7,
      p_win_home_market: 0.61,
      h2h_book_count: 6,
      market_thin: false,
      carried_forward: true,
      captured_at: "2026-09-24T13:00:00Z",
    };
    const html = renderToStaticMarkup(
      <GameRow game={sampleGame} odds={odds} timeZone="America/New_York" />,
    );
    expect(html).toContain("Last pre-kickoff snapshot");
    expect(html).toContain('data-testid="carried-forward-label"');
  });

  it("number line has aria-label", () => {
    const odds = {
      game_id: sampleGame.game_id,
      market_home_margin: 3.5,
      spread_home_points: -3.5,
      spread_book_count: 7,
      total_points: 52.5,
      total_book_count: 7,
      p_win_home_market: 0.61,
      h2h_book_count: 6,
      market_thin: false,
      carried_forward: false,
      captured_at: "2026-09-24T13:00:00Z",
    };
    const html = renderToStaticMarkup(
      <GameDetail
        game={sampleGame}
        homeSeries={[]}
        awaySeries={[]}
        odds={odds}
        oddsMeta={{
          snapshot_at: "2026-09-24T13:00:00Z",
          provider: "The Odds API",
          consensus_method: "median_across_books_devigged_h2h",
        }}
      />,
    );
    expect(html).toContain("margin-number-line");
    expect(html).toContain("aria-label=");
    expect(html).toContain("consensus line");
  });
});
