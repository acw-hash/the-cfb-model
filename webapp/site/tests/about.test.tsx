import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AboutPage } from "@/components/About/AboutPage";
import { SiteFooter } from "@/components/SiteFooter/SiteFooter";
import * as aboutCopy from "@/lib/about/copy";
import {
  ATTRIBUTION_COPY,
  ATTRIBUTION_HEADING,
  CFBD_ATTRIBUTION,
  DISCLAIMER_TEMPLATE,
  HONESTY_COMMITMENTS,
  RESPONSIBLE_GAMBLING_COPY,
  RIDGE_IDENTITY,
  disclaimerForYear,
} from "@/lib/about/copy";

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

  it("states withheld uncertainty bands are deliberate", () => {
    const html = renderToStaticMarkup(<AboutPage year={2026} />);
    expect(html).toContain("Some games show no uncertainty band");
    expect(html).toContain("internally inconsistent");
    expect(html).toContain("withheld rather than shown");
    expect(html).toContain("That absence is deliberate");
    expect(html).not.toMatch(/\b15 of 99\b/);
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
