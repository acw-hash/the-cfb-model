import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AboutPage } from "@/components/About/AboutPage";
import { SiteFooter } from "@/components/SiteFooter/SiteFooter";
import {
  ATTRIBUTION_PLACEHOLDER,
  CFBD_ATTRIBUTION,
  DISCLAIMER_TEMPLATE,
  HONESTY_COMMITMENTS,
  RESPONSIBLE_GAMBLING_COPY,
  RIDGE_IDENTITY,
  disclaimerForYear,
} from "@/lib/about/copy";

describe("About page — stranger test and §6 copy", () => {
  it("renders identity within the opening block", () => {
    const html = renderToStaticMarkup(<AboutPage year={2026} />);
    expect(html).toContain(RIDGE_IDENTITY);
    expect(html).toContain('data-testid="ridge-identity"');
  });

  it("lists honesty commitments including fit-to-bet verdict", () => {
    const html = renderToStaticMarkup(<AboutPage year={2026} />);
    for (const item of HONESTY_COMMITMENTS) {
      expect(html).toContain(item);
    }
    expect(html).toContain("NOT CURRENTLY FIT TO BET");
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

  it("attributes CFBD and marks operator attribution as placeholder", () => {
    const html = renderToStaticMarkup(<AboutPage year={2026} />);
    expect(html).toContain(CFBD_ATTRIBUTION);
    expect(html).toContain(ATTRIBUTION_PLACEHOLDER);
    expect(html).toContain('data-testid="attribution-placeholder"');
  });

  it("states market lines are not published and why", () => {
    const html = renderToStaticMarkup(<AboutPage year={2026} />);
    expect(html).toContain("never published on this site");
    expect(html).toContain("invite edge claims the public record does not support");
  });

  it("glosses technical terms when used", () => {
    const html = renderToStaticMarkup(<AboutPage year={2026} />);
    expect(html).toMatch(/state-space[\s\S]*Kalman-style/);
    expect(html).toMatch(/reduced ensemble:/);
    expect(html).toMatch(/conformal calibration layer/);
  });

  it("does not invent age-gating, jurisdiction, or contact identity", () => {
    const html = renderToStaticMarkup(<AboutPage year={2026} />);
    expect(html).not.toMatch(/must be 21|18\+|terms of service|jurisdiction/i);
    expect(html).not.toMatch(/@gmail\.com|twitter\.com|github\.com\/[a-z]/i);
    expect(html).not.toMatch(/Alec|Inc\.|LLC/i);
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
