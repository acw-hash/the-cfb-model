import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import { GameRow } from "@/components/GameRow/GameRow";
import type { GamePrediction } from "@/lib/artifacts/types";
import type { OddsGameView } from "@/lib/odds/types";
import { buildScoreboardPlacement } from "@/lib/this-week/scoreboard";

const base: GamePrediction = {
  game_id: "401628373",
  season: 2024,
  week: 5,
  home_team: "Coastal Carolina",
  away_team: "Liberty",
  home_team_id: 1,
  away_team_id: 2,
  kickoff_utc: "2024-10-05T23:30:00Z",
  neutral_site: false,
  conference_game: true,
  mu_margin: 15.6,
  sigma_margin: 13.8,
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
  p_win_home: 0.85,
  p_win_home_credible: true,
  conviction_tier: "strong_lean",
  conviction_team: "Coastal Carolina",
  conviction_label: "Strong lean Coastal Carolina",
  conviction_basis: null,
  tier_primary: "strong_lean",
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

function odds(partial: Partial<OddsGameView>): OddsGameView {
  return {
    game_id: base.game_id,
    market_home_margin: null,
    spread_home_points: null,
    spread_book_count: 0,
    total_points: null,
    total_book_count: 0,
    p_win_home_market: null,
    h2h_book_count: 0,
    market_thin: false,
    carried_forward: false,
    captured_at: "2026-09-24T13:00:00Z",
    ...partial,
  };
}

describe("buildScoreboardPlacement", () => {
  it("home favorite: model numbers on home line", () => {
    const p = buildScoreboardPlacement({
      muMargin: 15.6,
      sigmaMargin: 13.8,
      pWinHome: 0.85,
      pWinHomeCredible: true,
      sigmaMarginCredible: true,
      convictionTier: "strong_lean",
      showMarket: false,
    });
    expect(p.model.home.margin).toBe("15.6");
    expect(p.model.home.win).toBe("85%");
    expect(p.model.away.margin).toBeNull();
    expect(p.tierWord).toBe("Strong");
    expect(p.tierSide).toBe("home");
  });

  it("away favorite: model numbers on away line", () => {
    const p = buildScoreboardPlacement({
      muMargin: -7.2,
      sigmaMargin: 13.8,
      pWinHome: 0.28,
      pWinHomeCredible: true,
      sigmaMarginCredible: true,
      convictionTier: "clear_lean",
      showMarket: false,
    });
    expect(p.model.away.margin).toBe("7.2");
    expect(p.model.away.win).toBe("72%");
    expect(p.model.home.margin).toBeNull();
    expect(p.tierSide).toBe("away");
    expect(p.tierWord).toBe("Clear");
  });

  it("split favorites: model home / market away land on different lines", () => {
    const p = buildScoreboardPlacement({
      muMargin: 15.6,
      sigmaMargin: 13.8,
      pWinHome: 0.85,
      pWinHomeCredible: true,
      sigmaMarginCredible: true,
      convictionTier: "strong_lean",
      showMarket: true,
      marketHomeMargin: -2.3,
      pWinHomeMarket: 0.46,
    });
    expect(p.model.home.margin).toBe("15.6");
    expect(p.market?.away.margin).toBe("2.3");
    expect(p.market?.away.win).toBe("54%");
    expect(p.market?.home.margin).toBeNull();
  });

  it("PK centers between lines", () => {
    const p = buildScoreboardPlacement({
      muMargin: 0,
      sigmaMargin: 13.8,
      pWinHome: 0.5,
      pWinHomeCredible: true,
      sigmaMarginCredible: true,
      convictionTier: "toss_up",
      showMarket: false,
    });
    expect(p.model.mid?.margin).toBe("PK");
    expect(p.model.away.margin).toBeNull();
    expect(p.model.home.margin).toBeNull();
    expect(p.tierCentered).toBe(true);
  });

  it("null model → dash on home line only", () => {
    const p = buildScoreboardPlacement({
      muMargin: null,
      pWinHome: null,
      pWinHomeCredible: false,
      sigmaMarginCredible: false,
      convictionTier: null,
      showMarket: true,
      marketHomeMargin: 3.5,
      pWinHomeMarket: 0.61,
    });
    expect(p.model.nullDash).toBe(true);
    expect(p.model.away.margin).toBeNull();
    expect(p.model.home.margin).toBeNull();
    expect(p.market?.home.margin).toBe("3.5");
    expect(p.tierWord).toBeNull();
  });

  it("null market → dash on home line; model still places", () => {
    const p = buildScoreboardPlacement({
      muMargin: 4.2,
      sigmaMargin: 13.8,
      pWinHome: 0.64,
      pWinHomeCredible: true,
      sigmaMarginCredible: true,
      convictionTier: "lean",
      showMarket: true,
      marketHomeMargin: null,
      pWinHomeMarket: null,
    });
    expect(p.market?.nullDash).toBe(true);
    expect(p.model.home.margin).toBe("4.2");
  });
});

describe("GameRow scoreboard markup", () => {
  it("home fav renders unsigned margin without repeating the team name", () => {
    const html = renderToStaticMarkup(
      <GameRow
        game={base}
        odds={odds({ market_home_margin: 3.5, p_win_home_market: 0.61, total_points: 52.5 })}
        timeZone="America/New_York"
      />,
    );
    expect(html).toContain("15.6");
    expect(html).toContain("85%");
    // Visible cells are unsigned; team-named wording lives only in aria-label.
    expect(html).toContain('data-testid="model-margin">15.6<');
    expect(html).not.toContain("Strong lean Coastal Carolina");
    expect(html).toContain("Strong");
    expect(html).not.toContain("MARKET");
    expect(html).not.toContain("O/U");
    expect(html).toContain("aria-label=");
    expect(html).toContain("Liberty at Coastal Carolina");
    // Time only — no weekday/date wrap in the kickoff cell.
    expect(html).toMatch(/>7:30\s*PM</);
    expect(html).not.toMatch(/>Sat,/);
  });

  it("split favorites expose model on home and market on away", () => {
    const html = renderToStaticMarkup(
      <GameRow
        game={base}
        odds={odds({ market_home_margin: -2.3, p_win_home_market: 0.46 })}
        timeZone="America/New_York"
      />,
    );
    expect(html).toContain('data-testid="model-group"');
    expect(html).toContain('data-testid="market-cell"');
    expect(html).toContain("15.6");
    expect(html).toContain("2.3");
    expect(html).toContain("54%");
    expect(html).toMatch(/Model: Coastal Carolina by 15\.6/);
    expect(html).toMatch(/Market: Liberty by 2\.3/);
  });

  it("σ-suppressed model empty with market present", () => {
    const suppressed: GamePrediction = {
      ...base,
      mu_margin: null,
      sigma_margin_credible: false,
      p_win_home_credible: false,
      p_win_home: null,
      conviction_tier: null,
      conviction_label: null,
      null_reason: "cold_start_insufficient",
    };
    const html = renderToStaticMarkup(
      <GameRow
        game={suppressed}
        odds={odds({ market_home_margin: 3.5, p_win_home_market: 0.61 })}
        timeZone="America/New_York"
      />,
    );
    expect(html).toContain("—");
    expect(html).toContain("3.5");
    expect(html).not.toContain('data-testid="tier-short"');
  });

  it("PK renders mid cell", () => {
    const html = renderToStaticMarkup(
      <GameRow
        game={{
          ...base,
          mu_margin: 0,
          p_win_home: 0.5,
          conviction_tier: "toss_up",
          conviction_label: "Toss-up",
        }}
        timeZone="America/New_York"
      />,
    );
    expect(html).toContain('data-testid="model-pk"');
    expect(html).toContain("PK");
    expect(html).toContain("Toss-up");
  });

  it("mobile meta includes time and short tier", () => {
    const html = renderToStaticMarkup(<GameRow game={base} timeZone="America/New_York" />);
    expect(html).toContain('data-testid="mobile-meta"');
    expect(html).toContain('data-testid="tier-short-mobile"');
    expect(html).toContain("Strong");
  });

  it("market margin carries consensus-spread tooltip and aria", () => {
    const html = renderToStaticMarkup(
      <GameRow
        game={{
          ...base,
          away_team: "Howard",
          home_team: "Rutgers",
          mu_margin: 33.4,
          sigma_margin: 13.8,
          p_win_home: 0.98,
        }}
        odds={odds({
          market_home_margin: 43.5,
          p_win_home_market: null,
          spread_home_points: -43.5,
        })}
        timeZone="America/New_York"
      />,
    );
    expect(html).toContain('title="Consensus spread: Rutgers \u221243.5"');
    expect(html).toContain("Consensus spread: Rutgers \u221243.5");
  });

  it("null market win % still places margin (Howard @ Rutgers shape)", () => {
    const html = renderToStaticMarkup(
      <GameRow
        game={{
          ...base,
          away_team: "Howard",
          home_team: "Rutgers",
          mu_margin: 28.1,
          sigma_margin: 13.8,
          p_win_home: 0.95,
        }}
        odds={odds({ market_home_margin: 38.5, p_win_home_market: null })}
        timeZone="America/New_York"
      />,
    );
    expect(html).toContain("38.5");
    expect(html).toContain("28.1");
    // Market win absent — no stray em-dash win from market group required.
    expect(html).toMatch(/Market: Rutgers by 38\.5\./);
  });
});
