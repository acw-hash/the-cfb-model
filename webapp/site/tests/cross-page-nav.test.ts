import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SRC = path.resolve(__dirname, "../src");

function read(rel: string): string {
  return fs.readFileSync(path.join(SRC, rel), "utf8");
}

describe("W6 site navigation", () => {
  it("root layout mounts SiteHeader, FirstVisitDisclaimer, and SiteFooter", () => {
    const layout = read("app/layout.tsx");
    expect(layout).toContain("<SiteHeader />");
    expect(layout).toContain("<FirstVisitDisclaimer />");
    expect(layout).toContain("<SiteFooter />");
    expect(layout).toContain("{children}");
  });

  it("SiteHeader links to canonical routes without query params", () => {
    const header = read("components/SiteHeader/SiteHeader.tsx");
    expect(header).toContain('href: "/"');
    expect(header).toContain('href: "/results"');
    expect(header).toContain('href: "/about"');
    expect(header).not.toMatch(/href:\s*["'][^"']*\?/);
    expect(header).toContain("Ridge");
  });

  it("This Week rows still wrap GameRow in a link to /game/{game_id}", () => {
    const slate = read("components/ThisWeekSlate/ThisWeekSlate.tsx");
    expect(slate).toMatch(/href=\{`\/game\/\$\{game\.game_id\}`\}/);
  });

  it("Game Detail relies on site header (no page-local back link)", () => {
    const page = read("components/GameDetail/GameDetail.tsx");
    expect(page).not.toContain('<Link href="/">This Week</Link>');
    expect(page).toContain("<MatchupHeader");
  });

  it("SiteFooter surfaces disclaimer and responsible-gambling anchors", () => {
    const footer = read("components/SiteFooter/SiteFooter.tsx");
    expect(footer).toContain('href="/about#disclaimer"');
    expect(footer).toContain('href="/about#responsible-gambling"');
    expect(footer).toContain("1-800-GAMBLER");
  });
});

describe("URL-state survival (intended behavior)", () => {
  it("Results tab sync is page-local; header Results href is bare /results", () => {
    const tabs = read("components/Results/ResultsTabs.tsx");
    expect(tabs).toContain('url.searchParams.set("tab", next)');
    expect(tabs).toContain("window.history.replaceState");
    const header = read("components/SiteHeader/SiteHeader.tsx");
    expect(header).toMatch(/href:\s*"\/results"/);
    expect(header).not.toMatch(/href:\s*["'][^"']*\?/);
  });
});
