import { describe, expect, it, vi } from "vitest";
import {
  applyInPlayCarryForward,
  consensusForMatch,
  fetchOddsApi,
  OddsApiError,
  type OddsSnapshot,
} from "../src/snapshot";
import type { MatchResult, OddsEvent } from "../src/match";
import { assertAllowedWriteKey, datedOddsKey, isAllowedWriteKey, latestOddsKey } from "../src/r2-keys";
import { revalidateHeaders, postRevalidate } from "../src/revalidate";

function booksWithSpread(n: number, homeSpread: number, homeName: string, awayName: string) {
  return Array.from({ length: n }, (_, i) => ({
    key: `book${i}`,
    markets: [
      {
        key: "spreads",
        outcomes: [
          { name: homeName, price: -110, point: homeSpread },
          { name: awayName, price: -110, point: -homeSpread },
        ],
      },
      {
        key: "totals",
        outcomes: [
          { name: "Over", price: -110, point: 52.5 },
          { name: "Under", price: -110, point: 52.5 },
        ],
      },
      {
        key: "h2h",
        outcomes: [
          { name: homeName, price: -150 },
          { name: awayName, price: 130 },
        ],
      },
    ],
  }));
}

describe("in-play exclusion + carry-forward", () => {
  it("carries prior pre-kickoff row and skips fresh in-play", () => {
    const nowMs = Date.parse("2026-09-27T02:00:00Z");
    const event: OddsEvent = {
      id: "ev1",
      home_team: "Alabama Crimson Tide",
      away_team: "Auburn Tigers",
      commence_time: "2026-09-27T00:00:00Z", // already started
      bookmakers: booksWithSpread(5, -7, "Alabama Crimson Tide", "Auburn Tigers"),
    };
    const match: MatchResult = {
      game_id: "g1",
      odds_event_id: "ev1",
      swapped: false,
      event,
    };
    const previous: OddsSnapshot = {
      schema_version: "1.0.0",
      fixture: false,
      season: 2026,
      week: 5,
      snapshot_at: "2026-09-26T13:00:00Z",
      source: { provider: "The Odds API", regions: "us", markets: ["h2h"], requests_remaining: 1, requests_used: 1, requests_last: 3 },
      consensus_method: "median_across_books_devigged_h2h",
      games: [
        {
          game_id: "g1",
          odds_event_id: "ev1",
          captured_at: "2026-09-26T13:00:00Z",
          carried_forward: false,
          spread_home_points: -6.5,
          market_home_margin: 6.5,
          spread_book_count: 7,
          total_points: 50,
          total_book_count: 7,
          p_win_home_market: 0.7,
          h2h_book_count: 7,
          market_thin: false,
        },
      ],
      unmatched: [],
    };
    // Fresh consensus would be -7; must not use it because in-play
    const fresh = [consensusForMatch(match, { capturedAtIso: "2026-09-27T02:00:00Z" })];
    const out = applyInPlayCarryForward([match], fresh, previous, {
      nowMs,
      capturedAtIso: "2026-09-27T02:00:00Z",
    });
    expect(out).toHaveLength(1);
    expect(out[0]!.carried_forward).toBe(true);
    expect(out[0]!.spread_home_points).toBe(-6.5);
    expect(out[0]!.market_home_margin).toBe(6.5);
  });
});

describe("swapped consensus flips spread sign", () => {
  it("flips home spread when match.swapped", () => {
    const event: OddsEvent = {
      id: "ev",
      home_team: "Arkansas Razorbacks",
      away_team: "Texas A&M Aggies",
      commence_time: "2026-09-27T00:00:00Z",
      bookmakers: booksWithSpread(4, -3.5, "Arkansas Razorbacks", "Texas A&M Aggies"),
    };
    const game = consensusForMatch(
      { game_id: "g", odds_event_id: "ev", swapped: true, event },
      { capturedAtIso: "2026-09-24T13:00:00Z" },
    );
    // Odds home Arkansas -3.5 → after flip for Ridge TAMU home: +3.5 book / margin -3.5
    expect(game.spread_home_points).toBe(3.5);
    expect(game.market_home_margin).toBe(-3.5);
  });
});

describe("key allowlist", () => {
  it("allows odds/, latest/odds_snapshot.json, sandbox/", () => {
    expect(isAllowedWriteKey("odds/2026/w5/2026-09-24T13.json")).toBe(true);
    expect(isAllowedWriteKey("latest/odds_snapshot.json")).toBe(true);
    expect(isAllowedWriteKey("sandbox/latest/odds_snapshot.json")).toBe(true);
    expect(isAllowedWriteKey("sandbox/odds/2026/w5/2026-09-24T13.json")).toBe(true);
  });

  it("rejects publish artifact keys", () => {
    expect(isAllowedWriteKey("latest/week_predictions.json")).toBe(false);
    expect(isAllowedWriteKey("latest/meta.json")).toBe(false);
    expect(isAllowedWriteKey("publish_history/x.json")).toBe(false);
    expect(() => assertAllowedWriteKey("latest/meta.json")).toThrow(/not allowlisted/);
  });

  it("builds dated + latest keys with sandbox prefix (minute resolution)", () => {
    const at = new Date("2026-09-24T13:07:09Z");
    expect(datedOddsKey(2026, 5, at, { sandbox: false })).toBe(
      "odds/2026/w5/2026-09-24T13:07.json",
    );
    expect(datedOddsKey(2026, 5, at, { sandbox: true })).toBe(
      "sandbox/odds/2026/w5/2026-09-24T13:07.json",
    );
    expect(latestOddsKey({ sandbox: true })).toBe("sandbox/latest/odds_snapshot.json");
  });
});

describe("resolveRevalidateTarget", () => {
  it("sandbox uses PREVIEW_REVALIDATE_URL and never production", async () => {
    const { resolveRevalidateTarget } = await import("../src/revalidate");
    const t = resolveRevalidateTarget({
      sandbox: true,
      previewRevalidateUrl: "https://preview.example/api/revalidate",
      webappRevalidateUrl: "https://prod.example/api/revalidate",
    });
    expect(t).toEqual({
      kind: "url",
      url: "https://preview.example/api/revalidate",
      channel: "preview",
    });
  });

  it("sandbox with only production URL skips (no fallback)", async () => {
    const { resolveRevalidateTarget } = await import("../src/revalidate");
    const t = resolveRevalidateTarget({
      sandbox: true,
      webappRevalidateUrl: "https://prod.example/api/revalidate",
    });
    expect(t).toEqual({ kind: "skip", reason: "sandbox_no_preview_url" });
  });

  it("unset WEBAPP_REVALIDATE_URL in live mode skips with no default URL", async () => {
    const { resolveRevalidateTarget } = await import("../src/revalidate");
    const t = resolveRevalidateTarget({ sandbox: false, webappRevalidateUrl: "" });
    expect(t).toEqual({ kind: "skip", reason: "production_url_unset" });
    const t2 = resolveRevalidateTarget({ sandbox: false });
    expect(t2).toEqual({ kind: "skip", reason: "production_url_unset" });
  });

  it("live mode uses WEBAPP_REVALIDATE_URL when set", async () => {
    const { resolveRevalidateTarget } = await import("../src/revalidate");
    const t = resolveRevalidateTarget({
      sandbox: false,
      webappRevalidateUrl: "https://prod.example/api/revalidate",
      previewRevalidateUrl: "https://preview.example/api/revalidate",
    });
    expect(t).toEqual({
      kind: "url",
      url: "https://prod.example/api/revalidate",
      channel: "production",
    });
  });
});

describe("revalidate headers", () => {
  it("sends both Bearer and x-vercel-protection-bypass", async () => {
    const headers = revalidateHeaders("sec", "bypass");
    expect(headers.Authorization).toBe("Bearer sec");
    expect(headers["x-vercel-protection-bypass"]).toBe("bypass");

    const fetchImpl = vi.fn(async (_url: string, init?: RequestInit) => {
      const h = init?.headers as Record<string, string>;
      expect(h.Authorization).toBe("Bearer sec");
      expect(h["x-vercel-protection-bypass"]).toBe("bypass");
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    }) as unknown as typeof fetch;

    const result = await postRevalidate("https://example.test/api/revalidate", {
      bearerSecret: "sec",
      bypassSecret: "bypass",
      fetchImpl,
    });
    expect(result.ok).toBe(true);
    expect(fetchImpl).toHaveBeenCalledOnce();
  });
});

describe("retry policy", () => {
  it("retries 5xx then succeeds", async () => {
    let n = 0;
    const fetchImpl = vi.fn(async () => {
      n += 1;
      if (n < 3) {
        return new Response("nope", {
          status: 503,
          headers: { "x-requests-remaining": "10" },
        });
      }
      return new Response(JSON.stringify([]), {
        status: 200,
        headers: { "x-requests-remaining": "9", "x-requests-used": "1" },
      });
    }) as unknown as typeof fetch;

    const result = await fetchOddsApi("k", {
      commenceTimeFrom: "2026-09-24T00:00:00Z",
      commenceTimeTo: "2026-09-29T00:00:00Z",
      fetchImpl,
      maxAttempts: 3,
    });
    expect(result.events).toEqual([]);
    expect(n).toBe(3);
  });

  it("does not retry 401", async () => {
    const fetchImpl = vi.fn(
      async () => new Response("unauthorized", { status: 401 }),
    ) as unknown as typeof fetch;

    await expect(
      fetchOddsApi("k", {
        commenceTimeFrom: "2026-09-24T00:00:00Z",
        commenceTimeTo: "2026-09-29T00:00:00Z",
        fetchImpl,
        maxAttempts: 3,
      }),
    ).rejects.toBeInstanceOf(OddsApiError);
    expect(fetchImpl).toHaveBeenCalledOnce();
  });
});
