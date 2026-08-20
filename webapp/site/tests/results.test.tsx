import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { GradedGameRow } from "@/components/Results/GradedGameRow";
import { GradedGamesSection } from "@/components/Results/GradedGamesSection";
import { MetricRow } from "@/components/Results/MetricRow";
import { ResultsPage } from "@/components/Results/ResultsPage";
import { TrackRecordSection } from "@/components/Results/TrackRecordSection";
import { VerdictBlock } from "@/components/Results/VerdictBlock";
import type { ResultsSeason, TrackRecord, TrackRecordMetric } from "@/lib/artifacts/types";
import {
  ciIncludesFifty,
  formatRecordedCi,
  formatRecordedNumber,
  formatRecordedPercent,
  rateHasCi,
} from "@/lib/formatting/track-record";
import {
  FIXTURE_GRADES_COPY,
  LOCKBOX_NO_AGGREGATE_COPY,
  NO_SINGLE_NUMBER_COPY,
  SCOPE_COPY,
  VERDICT_LAY_SUMMARY,
} from "@/lib/results/copy";
import {
  cloneTrackRecordMissingMetric,
  cloneUngradedStatuses,
  emptyLiveResults,
} from "@/lib/results/demo-states";
import { EXPECTED_METRIC_IDS } from "@/lib/results/metrics";
import { GRADE_STATUS_LABEL, UNGRADED_STATUSES } from "@/lib/results/grade-status";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE_DIR = path.resolve(__dirname, "../../fixtures");

function loadTrack(): TrackRecord {
  return JSON.parse(
    fs.readFileSync(path.join(FIXTURE_DIR, "track_record.json"), "utf8"),
  ) as TrackRecord;
}

function loadResults(): ResultsSeason {
  return JSON.parse(
    fs.readFileSync(path.join(FIXTURE_DIR, "results_2024.json"), "utf8"),
  ) as ResultsSeason;
}

describe("track-record formatting — no meaning-changing rounding", () => {
  it("keeps 48.9 as 48.9%, not about 49%", () => {
    expect(formatRecordedPercent(48.9)).toBe("48.9%");
    expect(formatRecordedCi(47.5, 50.5, "percent")).toBe("[47.5%, 50.5%]");
  });

  it("keeps MAE/CRPS decimals as recorded", () => {
    const track = loadTrack();
    const mae = track.metrics.find((m) => m.id === "mae_margin_fund")!;
    expect(mae.value).toBe(14.53);
    const html = renderToStaticMarkup(<MetricRow metric={mae} expectedId={mae.id} />);
    expect(html).toContain("14.53");
    expect(html).not.toMatch(/\b15\b/);
    expect(html).not.toContain("about 15");
  });
});

describe("CI-required — rate cannot render without interval", () => {
  it("rateHasCi is false when a percent rate lacks bounds", () => {
    const broken: TrackRecordMetric = {
      id: "broken_ats",
      label: "Broken ATS",
      value: 48.9,
      unit: "percent",
      ci_lower: null,
      ci_upper: null,
      ci_kind: "none",
      n: 100,
      regime: "test",
      vintage: "TEST",
      run: "fundamental",
      notes: null,
    };
    expect(rateHasCi(broken)).toBe(false);
  });

  it("MetricRow renders honest absence for a percent rate without CI", () => {
    const broken: TrackRecordMetric = {
      id: "broken_ats",
      label: "Broken ATS",
      value: 51.0,
      unit: "percent",
      ci_lower: null,
      ci_upper: null,
      ci_kind: "none",
      n: 10,
      regime: null,
      vintage: "TEST",
      run: null,
      notes: null,
    };
    const html = renderToStaticMarkup(<MetricRow metric={broken} expectedId="broken_ats" />);
    expect(html).toContain('data-testid="metric-incomplete-broken_ats"');
    expect(html).toContain("Not in the recorded artifact");
  });

  it("every percent rate in the fixture renders with its CI beside it", () => {
    const track = loadTrack();
    for (const metric of track.metrics) {
      if (metric.unit !== "percent" || typeof metric.value !== "number") {
        continue;
      }
      const html = renderToStaticMarkup(<MetricRow metric={metric} expectedId={metric.id} />);
      expect(html).toContain(formatRecordedPercent(metric.value));
      expect(html).toContain(formatRecordedCi(metric.ci_lower!, metric.ci_upper!, "percent"));
      expect(html).toContain(`data-testid="metric-ci-${metric.id}"`);
    }
  });
});

describe("no aggregate — grep rendered output", () => {
  const FORBIDDEN = [
    /overall accuracy/i,
    /composite score/i,
    /model quality/i,
    /\baccuracy:\s*\d/i,
    /\b\d+(\.\d+)?%\s*(accurate|correct|overall)\b/i,
    /\b\d+\s*\/\s*\d+\s*(correct|hits|intervals?)\b/i,
  ];

  it("ResultsPage HTML has no invented aggregate accuracy figure", () => {
    const track = loadTrack();
    const results = loadResults();
    const html = renderToStaticMarkup(<ResultsPage track={track} results={results} />);
    const hits: string[] = [];
    for (const pattern of FORBIDDEN) {
      const match = html.match(pattern);
      if (match) {
        hits.push(`${pattern}: ${match[0]}`);
      }
    }
    // Grep evidence for notes
    // eslint-disable-next-line no-console
    console.log("no-aggregate grep patterns:", FORBIDDEN.map(String).join(" | "));
    // eslint-disable-next-line no-console
    console.log("no-aggregate hits:", hits.length === 0 ? "NONE" : hits.join("; "));
    expect(hits).toEqual([]);
    expect(html).toContain(NO_SINGLE_NUMBER_COPY);
    expect(html).toContain(LOCKBOX_NO_AGGREGATE_COPY);
    expect(FORBIDDEN.length).toBe(6);
  });

  it("GradedGamesSection does not compute interval-hit aggregates", () => {
    const results = loadResults();
    const html = renderToStaticMarkup(<GradedGamesSection results={results} />);
    expect(html).not.toMatch(/\b\d+\s*\/\s*\d+\s*(correct|hits|intervals?)\b/i);
    expect(html).not.toMatch(/\b\d+(\.\d+)?%\s*interval/i);
    expect(html).toContain(LOCKBOX_NO_AGGREGATE_COPY);
  });
});

describe("verdict and scope", () => {
  it("renders NOT CURRENTLY FIT TO BET with §1.4 plain_language primary and lay summary in disclosure", () => {
    const track = loadTrack();
    const html = renderToStaticMarkup(<VerdictBlock verdict={track.verdict} />);
    expect(html).toContain("NOT CURRENTLY FIT TO BET");
    expect(html).toContain('data-testid="verdict-plain-language"');
    expect(html).toContain(track.verdict.plain_language);
    expect(html).toContain('data-testid="verdict-lay-disclosure"');
    expect(html).toContain(VERDICT_LAY_SUMMARY);
    expect(html).not.toContain("Recorded finding");
  });

  it("states scope: walk-forward, lockbox 2025, live 2026", () => {
    const track = loadTrack();
    const html = renderToStaticMarkup(<ResultsPage track={track} results={loadResults()} />);
    expect(html).toContain(SCOPE_COPY);
    expect(html).toContain("2019");
    expect(html).toContain("2025");
    expect(html).toContain("2026");
  });
});

describe("graded games", () => {
  it("shows graded_from publish per graded row", () => {
    const results = loadResults();
    const graded = results.games.find((g) => g.grade_status === "graded")!;
    const html = renderToStaticMarkup(<GradedGameRow game={graded} />);
    expect(html).toContain("Tuesday primary");
    expect(html).toContain("locked before kickoff");
    expect(html).toContain(`data-testid="graded-from-${graded.game_id}"`);
  });

  it("fixture note distinguishes fixture grades from live record", () => {
    const html = renderToStaticMarkup(<GradedGamesSection results={loadResults()} />);
    expect(html).toContain(FIXTURE_GRADES_COPY);
    expect(html).toContain('data-testid="fixture-grades-note"');
  });

  it("interval-miss row is an explicit state", () => {
    const miss = loadResults().games.find((g) => g.margin_interval_hit === false)!;
    const html = renderToStaticMarkup(<GradedGameRow game={miss} />);
    expect(html).toContain("Interval missed");
    expect(html).toContain('data-margin-interval-hit="false"');
  });

  it("every ungraded status renders as its own explicit state", () => {
    for (const status of UNGRADED_STATUSES) {
      expect(GRADE_STATUS_LABEL[status].length).toBeGreaterThan(0);
    }
    const clones = cloneUngradedStatuses();
    expect(clones.map((g) => g.grade_status).sort()).toEqual([...UNGRADED_STATUSES].sort());
    for (const game of clones) {
      const html = renderToStaticMarkup(<GradedGameRow game={game} />);
      expect(html).toContain(GRADE_STATUS_LABEL[game.grade_status]);
      expect(html).toContain(`data-grade-status="${game.grade_status}"`);
      expect(html).toContain(`data-testid="ungraded-body-${game.game_id}"`);
    }
    // Fixture includes game_not_final
    const fixtureNotFinal = loadResults().games.find((g) => g.grade_status === "game_not_final");
    expect(fixtureNotFinal).toBeDefined();
    const html = renderToStaticMarkup(<GradedGameRow game={fixtureNotFinal!} />);
    expect(html).toContain("Game not final");
  });

  it("empty live record reads as not yet", () => {
    const html = renderToStaticMarkup(
      <GradedGamesSection results={emptyLiveResults("2026-08-13T12:00:00Z")} />,
    );
    expect(html).toContain('data-testid="graded-games-empty"');
    expect(html).toContain("empty launch state");
    expect(html).not.toContain("error");
  });
});

describe("missing metric — honest absence", () => {
  it("renders absence for a dropped metric id, not a skipped row", () => {
    const track = cloneTrackRecordMissingMetric(loadTrack(), "fund_ats_snapshots");
    const html = renderToStaticMarkup(
      <TrackRecordSection track={track} expectedIds={["fund_ats_snapshots", "fund_ats_2019"]} />,
    );
    expect(html).toContain('data-testid="metric-missing-fund_ats_snapshots"');
    expect(html).toContain("Not in the recorded artifact");
    expect(html).toContain('data-testid="metric-fund_ats_2019"');
  });

  it("expected metric ids cover the fixture table", () => {
    const track = loadTrack();
    for (const id of EXPECTED_METRIC_IDS) {
      expect(track.metrics.some((m) => m.id === id)).toBe(true);
    }
  });
});

describe("lockbox loader guard", () => {
  it("documents season 2025 refusal in loader source", () => {
    const loader = fs.readFileSync(
      path.resolve(__dirname, "../src/lib/artifacts/loader.ts"),
      "utf8",
    );
    expect(loader).toContain("Season 2025 is lockbox");
    expect(loader).toContain("season === 2025");
  });
});
