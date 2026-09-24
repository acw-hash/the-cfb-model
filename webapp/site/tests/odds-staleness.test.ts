import { afterEach, describe, expect, it, vi } from "vitest";

import {
  buildOddsPageContext,
  evaluateOddsFreshness,
  isOddsSnapshotEnabled,
  ODDS_DELAYED_MS,
  ODDS_HIDE_MS,
} from "@/lib/odds/staleness";
import { oddsSnapshotR2Key } from "@/lib/odds/load";
import type { OddsSnapshot } from "@/lib/odds/types";

function baseSnap(over: Partial<OddsSnapshot> = {}): OddsSnapshot {
  return {
    schema_version: "1.0.0",
    fixture: true,
    season: 2024,
    week: 5,
    snapshot_at: "2026-09-24T13:00:00Z",
    source: {
      provider: "The Odds API",
      regions: "us",
      markets: ["h2h", "spreads", "totals"],
      requests_remaining: 1,
    },
    consensus_method: "median_across_books_devigged_h2h",
    games: [
      {
        game_id: "1",
        odds_event_id: "e",
        captured_at: "2026-09-24T13:00:00Z",
        carried_forward: false,
        spread_home_points: -3.5,
        market_home_margin: 3.5,
        spread_book_count: 7,
        total_points: 52.5,
        total_book_count: 7,
        p_win_home_market: 0.61,
        h2h_book_count: 6,
        market_thin: false,
      },
    ],
    unmatched: [],
    ...over,
  };
}

describe("odds freshness", () => {
  it("fresh within 30h", () => {
    const now = Date.parse("2026-09-24T13:00:00Z") + 10 * 60 * 60 * 1000;
    expect(evaluateOddsFreshness("2026-09-24T13:00:00Z", now)).toBe("fresh");
  });

  it("delayed between 30h and 72h", () => {
    const snap = Date.parse("2026-09-24T13:00:00Z");
    expect(evaluateOddsFreshness("2026-09-24T13:00:00Z", snap + ODDS_DELAYED_MS + 1)).toBe(
      "delayed",
    );
  });

  it("hide after 72h", () => {
    const snap = Date.parse("2026-09-24T13:00:00Z");
    expect(evaluateOddsFreshness("2026-09-24T13:00:00Z", snap + ODDS_HIDE_MS + 1)).toBe("hide");
  });

  it("buildOddsPageContext null when hide or unsupported major", () => {
    const snap = baseSnap();
    const now = Date.parse(snap.snapshot_at) + ODDS_HIDE_MS + 1000;
    expect(buildOddsPageContext(snap, now)).toBeNull();
    expect(buildOddsPageContext(baseSnap({ schema_version: "2.0.0" }), Date.now())).toBeNull();
  });

  it("delayed flag set when between 30h and 72h", () => {
    const snap = baseSnap();
    const now = Date.parse(snap.snapshot_at) + ODDS_DELAYED_MS + 1000;
    const ctx = buildOddsPageContext(snap, now);
    expect(ctx?.delayed).toBe(true);
    expect(ctx?.byGameId["1"]?.market_home_margin).toBe(3.5);
  });
});

describe("oddsSnapshotR2Key", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("defaults to live latest key when unset or empty", () => {
    // Explicit stubs — Preview deploys set ODDS_R2_PREFIX=sandbox in the env.
    vi.stubEnv("ODDS_R2_PREFIX", "");
    expect(oddsSnapshotR2Key()).toBe("latest/odds_snapshot.json");
    vi.stubEnv("ODDS_R2_PREFIX", "   ");
    expect(oddsSnapshotR2Key()).toBe("latest/odds_snapshot.json");
  });

  it("sandbox prefix reads sandbox latest", () => {
    vi.stubEnv("ODDS_R2_PREFIX", "sandbox");
    expect(oddsSnapshotR2Key()).toBe("sandbox/latest/odds_snapshot.json");
  });
});

describe("ODDS_SNAPSHOT_ENABLED flag", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("defaults off", () => {
    vi.stubEnv("ODDS_SNAPSHOT_ENABLED", "");
    expect(isOddsSnapshotEnabled()).toBe(false);
  });

  it("true enables", () => {
    vi.stubEnv("ODDS_SNAPSHOT_ENABLED", "true");
    expect(isOddsSnapshotEnabled()).toBe(true);
  });
});
