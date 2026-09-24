import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AboutPage, selectAboutExampleGame } from "@/components/About/AboutPage";
import { SiteFooter } from "@/components/SiteFooter/SiteFooter";
import * as aboutCopy from "@/lib/about/copy";
import {
  ATTRIBUTION_COPY,
  ATTRIBUTION_HEADING,
  CFBD_ATTRIBUTION,
  DISCLAIMER_TEMPLATE,
  HONESTY_COPY,
  RESPONSIBLE_GAMBLING_COPY,
  RIDGE_IDENTITY,
  WHAT_RIDGE_WONT_SHOW_PARAGRAPHS,
  disclaimerForYear,
} from "@/lib/about/copy";
import type { GamePrediction } from "@/lib/artifacts/types";

const APPROVED_ATTRIBUTION_SENTENCE_1 = "Ridge is an independent research project.";
const APPROVED_ATTRIBUTION_SENTENCE_2 = "It is not affiliated with any school or conference.";

/** Bracketed operator-to-supply copy of the W6 class. */
const OPERATOR_PLACEHOLDER_PATTERN = /\[[^[\]]*Operator to supply[^[\]]*\]/i;

function stripTags(html: string): string {
  return html
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function attributionSectionHtml(html: string): string {
  const match = html.match(
    /<section[^>]*data-testid="about-attribution"[^>]*>([\s\S]*?)<\/section>/,
  );
  expect(match, "expected an Attribution section").not.toBeNull();
  return match?.[1] ?? "";
}

function attributionHeadingText(sectionHtml: string): string {
  const match = sectionHtml.match(/<h2[^>]*>([\s\S]*?)<\/h2>/);
  expect(match, "expected an Attribution h2").not.toBeNull();
  return stripTags(match?.[1] ?? "");
}

function attributionParagraphText(sectionHtml: string): string {
  const paragraphs = [...sectionHtml.matchAll(/<p[^>]*>([\s\S]*?)<\/p>/g)].map((m) =>
    stripTags(m[1]),
  );
  return paragraphs.join(" ");
}

function renderedHeadingTexts(html: string): string[] {
  return [...html.matchAll(/<h[1-6][^>]*>([\s\S]*?)<\/h[1-6]>/gi)].map((m) => stripTags(m[1]));
}

function collectExportedStrings(value: unknown): string[] {
  if (typeof value === "string") {
    return [value];
  }
  if (typeof value === "function" || value == null) {
    return [];
  }
  if (Array.isArray(value)) {
    return value.flatMap(collectExportedStrings);
  }
  if (typeof value === "object") {
    return Object.values(value).flatMap(collectExportedStrings);
  }
  return [];
}

const SAMPLE_HOME: GamePrediction = {
  game_id: "test-home",
  season: 2024,
  week: 5,
  home_team: "Texas A&M",
  away_team: "Arkansas",
  home_team_id: 1,
  away_team_id: 2,
  kickoff_utc: "2024-09-28T19:30:00Z",
  neutral_site: true,
  conference_game: true,
  mu_margin: 8.9,
  sigma_margin: 16.7,
  sigma_margin_credible: true,
  margin_interval_lo: -20.2,
  margin_interval_hi: 34.9,
  margin_interval_nominal: 0.8,
  mu_total: 51.2,
  sigma_total: 16.9,
  sigma_total_credible: true,
  total_interval_lo: null,
  total_interval_hi: null,
  total_interval_nominal: null,
  p_win_home: 0.76,
  p_win_home_credible: true,
  conviction_tier: "clear_lean",
  conviction_team: "Texas A&M",
  conviction_label: "Clear lean Texas A&M",
  conviction_basis: null,
  tier_primary: null,
  tier_revised_since_primary: false,
  is_stale: false,
  stale_stamp: null,
  stale_sources: [],
  null_reason: null,
  vintage_label: "TEST",
  ensemble_scope_label: "TEST",
  feature_time_label: "TEST",
  published_at: "2024-09-24T10:00:00Z",
  refresh_kind: "tuesday_primary",
};

describe("About page — stranger test and §6 copy", () => {
  it("renders identity within the opening block", () => {
    const html = renderToStaticMarkup(<AboutPage year={2026} />);
    expect(html).toContain(RIDGE_IDENTITY);
    expect(html).toContain('data-testid="ridge-identity"');
  });

  it("does not reference the Results verdict in honesty copy", () => {
    const html = renderToStaticMarkup(<AboutPage year={2026} />);
    expect(html).toContain("Every forecast shows its uncertainty when there&#x27;s enough data");
    expect(html).toContain("no suggested wagers anywhere on the site");
    expect(html).not.toContain("NOT CURRENTLY FIT TO BET");
    expect(html).not.toContain("fit-to-bet");
  });

  it("renders a live worked example from artifact fields", () => {
    const html = renderToStaticMarkup(<AboutPage year={2026} exampleGame={SAMPLE_HOME} />);
    expect(html).toContain("Texas A&amp;M by 8.9");
    expect(html).toContain("Ridge gives Texas A&amp;M a 76% chance to win.");
    expect(html).toContain("Clear lean Texas A&amp;M");
    expect(html).toContain("between Arkansas by 20.2 and Texas A&amp;M by 34.9");
    expect(html).not.toContain("between Arkansas by 20.2 to Texas");
    expect(html).toContain("Even at 76%, Arkansas wins about 24 times in 100");
    expect(html).toContain("It&#x27;s not a recommendation.");
    expect(html).toContain("Labels are sticky");
    expect(html).not.toContain("14-point favorite");
    expect(html).not.toContain("in the model&#x27;s view");
    expect(html).toContain('data-testid="about-worked-example"');
  });

  it("uses the example nominal in how-it-works range copy", () => {
    const html = renderToStaticMarkup(<AboutPage year={2026} exampleGame={SAMPLE_HOME} />);
    expect(html).toContain(
      "The range is set so the final margin should land inside it about 8 times in 10.",
    );
    expect(html).toContain("the Results page shows which ones did");
  });

  it("omits Next update when the timestamp is in the past", () => {
    const html = renderToStaticMarkup(
      <AboutPage year={2026} nextExpectedPublishUtc="2020-01-01T00:00:00Z" timeZone="UTC" />,
    );
    expect(html).not.toContain("Next update:");
  });

  it("renders Next update in local time when the timestamp is in the future", () => {
    const html = renderToStaticMarkup(
      <AboutPage
        year={2026}
        nextExpectedPublishUtc="2099-06-15T12:00:00Z"
        timeZone="America/New_York"
      />,
    );
    expect(html).toContain("Next update:");
    expect(html).toContain('data-testid="next-update"');
  });

  it("falls back when no example game is available", () => {
    const html = renderToStaticMarkup(<AboutPage year={2026} exampleGame={null} />);
    expect(html).toContain("When a week of forecasts is available");
  });

  it("renders publish schedule from meta when provided", () => {
    const html = renderToStaticMarkup(
      <AboutPage
        year={2026}
        publishSchedule={{
          primary: "Tue 06:00 UTC",
          refresh: "Thu–Sat 06:00 UTC",
          postgame_ratings: "Sun 06:00 UTC",
        }}
      />,
    );
    expect(html).toContain("Primary publish is Tue 06:00 UTC");
    expect(html).toContain("Thu–Sat 06:00 UTC");
  });

  it("renders §6.1 disclaimer without weakening", () => {
    const html = renderToStaticMarkup(<AboutPage year={2026} />);
    const expected = disclaimerForYear(2026);
    expect(html).toContain(expected);
    expect(DISCLAIMER_TEMPLATE).toContain("not betting recommendations");
    expect(DISCLAIMER_TEMPLATE).toContain("does not publish sportsbook lines");
    expect(expected).toContain("© 2026 Ridge");
  });

  it("renders §6.2 responsible-gambling substance with 1-800-GAMBLER", () => {
    const html = renderToStaticMarkup(<AboutPage year={2026} />);
    expect(html).toContain("1-800-GAMBLER");
    expect(html).toContain("1-800-426-2537");
    expect(html).toContain("does not accept wagers");
    expect(RESPONSIBLE_GAMBLING_COPY).toContain("1-800-GAMBLER");
  });

  it("attributes CFBD unchanged and renders approved attribution in Attribution", () => {
    const html = renderToStaticMarkup(<AboutPage year={2026} />);
    const section = attributionSectionHtml(html);
    expect(ATTRIBUTION_HEADING).toBe("Attribution");
    expect(attributionHeadingText(section)).toBe("Attribution");
    for (const heading of renderedHeadingTexts(html)) {
      expect(heading).not.toMatch(/contact/i);
    }
    expect(ATTRIBUTION_COPY).toBe(
      `${APPROVED_ATTRIBUTION_SENTENCE_1} ${APPROVED_ATTRIBUTION_SENTENCE_2}`,
    );
    expect(attributionParagraphText(section)).toBe(`${CFBD_ATTRIBUTION} ${ATTRIBUTION_COPY}`);
    expect(section).not.toMatch(OPERATOR_PLACEHOLDER_PATTERN);
  });

  it("does not invent contact methods, repo links, or personal identity", () => {
    const html = renderToStaticMarkup(<AboutPage year={2026} />);
    const section = attributionSectionHtml(html);
    const withoutResponsibleGambling = html.replace(
      /<section[^>]*data-testid="about-responsible-gambling"[\s\S]*?<\/section>/,
      "",
    );
    expect(attributionParagraphText(section)).toBe(`${CFBD_ATTRIBUTION} ${ATTRIBUTION_COPY}`);
    expect(html).not.toMatch(/mailto:/i);
    expect(html).not.toMatch(/[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}/i);
    expect(html).not.toMatch(/(^|[^a-z0-9/])@[a-z0-9_]+/i);
    expect(html).not.toMatch(/github\.com/i);
    expect(withoutResponsibleGambling).not.toMatch(/tel:/i);
  });

  it("exports no bracketed operator-to-supply placeholder from about copy", () => {
    const exported = collectExportedStrings(aboutCopy);
    expect(exported.length).toBeGreaterThan(0);
    for (const value of exported) {
      expect(value).not.toMatch(OPERATOR_PLACEHOLDER_PATTERN);
    }
  });

  it("states what Ridge will not show, including the product refusal", () => {
    const html = renderToStaticMarkup(<AboutPage year={2026} />);
    expect(html).toContain("Ridge doesn&#x27;t publish picks");
    expect(html).toContain("stay off the site");
    expect(WHAT_RIDGE_WONT_SHOW_PARAGRAPHS[0]).toContain("doesn't publish picks");
  });

  it("does not invent age-gating, jurisdiction, or contact identity", () => {
    const html = renderToStaticMarkup(<AboutPage year={2026} />);
    expect(html).not.toMatch(/must be 21|18\+|terms of service|jurisdiction/i);
    expect(html).not.toMatch(/@gmail\.com|twitter\.com|github\.com\/[a-z]/i);
  });

  it("selectAboutExampleGame prefers a credible margin game", () => {
    expect(selectAboutExampleGame([SAMPLE_HOME])?.game_id).toBe("test-home");
    expect(selectAboutExampleGame([])).toBeNull();
  });
});

describe("Site footer — discoverability", () => {
  it("links Disclaimer and Responsible gambling into About anchors", () => {
    const html = renderToStaticMarkup(<SiteFooter />);
    expect(html).toContain('href="/about#disclaimer"');
    expect(html).toContain('href="/about#responsible-gambling"');
    expect(html).toContain("1-800-GAMBLER");
  });
});

describe("forbidden marketing / pick language outside explicit non-publish statements", () => {
  it("About markup has no pick-of-the-week or edge claim framing", () => {
    const html = renderToStaticMarkup(<AboutPage year={2026} />);
    expect(html).not.toMatch(/best bets|lock of the week|guaranteed|beat the books/i);
    expect(html).not.toMatch(/our edge is|positive EV|recommended wager/i);
  });
});
