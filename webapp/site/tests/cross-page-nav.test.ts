import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SRC = path.resolve(__dirname, "../src");

function read(rel: string): string {
  return fs.readFileSync(path.join(SRC, rel), "utf8");
}

describe("W5-0 cross-page nav", () => {
  it("This Week rows wrap GameRow in a link to /game/{game_id}", () => {
    const slate = read("components/ThisWeekSlate/ThisWeekSlate.tsx");
    expect(slate).toContain('from "next/link"');
    expect(slate).toMatch(/href=\{`\/game\/\$\{game\.game_id\}`\}/);
    expect(slate).toContain("<GameRow game={game} />");
    const linkIdx = slate.indexOf("href={`/game/${game.game_id}`}");
    const rowIdx = slate.indexOf("<GameRow game={game} />");
    expect(linkIdx).toBeGreaterThan(-1);
    expect(rowIdx).toBeGreaterThan(linkIdx);
  });

  it("Game Detail leads with a quiet This Week header link to /", () => {
    const page = read("components/GameDetail/GameDetail.tsx");
    expect(page).toContain('<Link href="/">This Week</Link>');
    const backIdx = page.indexOf('<Link href="/">This Week</Link>');
    const matchupIdx = page.indexOf("<MatchupHeader");
    expect(backIdx).toBeGreaterThan(-1);
    expect(matchupIdx).toBeGreaterThan(backIdx);
    expect(page).not.toMatch(/<button[\s\S]*This Week/);

    const css = read("components/GameDetail/GameDetail.module.css");
    expect(css).toMatch(/\.back\s*\{/);
    expect(css).toContain("font-size: var(--type-c1-size)");
    expect(css).toContain("color: var(--text-secondary)");
    expect(css).not.toMatch(/position:\s*(fixed|sticky)/);
  });

  it("unknown game_id not-found also links back to This Week", () => {
    const missing = read("app/game/[gameId]/not-found.tsx");
    expect(missing).toContain('<Link href="/">This Week</Link>');
    const css = read("app/game/[gameId]/not-found.module.css");
    expect(css).not.toMatch(/position:\s*(fixed|sticky)/);
  });

  it("root layout has no site chrome that would replace the page-local back link", () => {
    const layout = read("app/layout.tsx");
    expect(layout).not.toMatch(/<nav[\s>]/);
    expect(layout).not.toMatch(/<header[\s>]/);
    expect(layout).toContain("{children}");
  });
});
