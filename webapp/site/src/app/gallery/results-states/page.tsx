import Link from "next/link";

import { GradedGameRow } from "@/components/Results/GradedGameRow";
import { GradedGamesSection } from "@/components/Results/GradedGamesSection";
import { MetricRow } from "@/components/Results/MetricRow";
import { ResultsPage } from "@/components/Results/ResultsPage";
import { TrackRecordSection } from "@/components/Results/TrackRecordSection";
import { VerdictBlock } from "@/components/Results/VerdictBlock";
import { loadArtifact } from "@/lib/artifacts/loader";
import type { ResultsSeason, TrackRecord } from "@/lib/artifacts/types";
import { EMPTY_LIVE_COPY } from "@/lib/results/copy";
import {
  cloneTrackRecordMissingMetric,
  cloneUngradedStatuses,
  emptyLiveResults,
  EXPECTED_METRIC_IDS,
} from "@/lib/results/demo-states";

import { assertGalleryAllowed } from "../gallery-gate";
import { GalleryThemeToggle } from "../GalleryThemeToggle";
import styles from "../gallery.module.css";

export const metadata = {
  title: "Ridge — Results states",
  robots: { index: false, follow: false },
};

/** Dev-only doctored-clone gallery for W5-5. Fixtures on disk are untouched. */
export default async function ResultsStatesPage(): Promise<React.ReactElement> {
  assertGalleryAllowed();

  const [track, results] = await Promise.all([
    loadArtifact<TrackRecord>("track_record"),
    loadArtifact<ResultsSeason>("results_2024"),
  ]);

  const missGame = results.games.find((g) => g.margin_interval_hit === false);
  if (!missGame) {
    throw new Error("Fixture results need an interval-miss row");
  }

  const emptyLive = emptyLiveResults("2026-08-13T12:00:00Z");
  const ungraded = cloneUngradedStatuses();
  const missingMetric = cloneTrackRecordMissingMetric(track, "fund_ats_snapshots");

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <div>
          <p className={styles.note}>
            <Link href="/gallery">Gallery</Link> · Results states (dev)
          </p>
          <h1 className={styles.title}>Results states</h1>
        </div>
        <GalleryThemeToggle />
      </header>

      <section className={styles.section} data-testid="state-verdict">
        <h2 className={styles.sectionTitle}>Verdict</h2>
        <div className={styles.statePanel}>
          <VerdictBlock verdict={track.verdict} />
        </div>
      </section>

      <section className={styles.section} data-testid="state-ci">
        <h2 className={styles.sectionTitle}>CI treatment</h2>
        <div className={styles.statePanel}>
          <table>
            <tbody>
              <MetricRow
                metric={track.metrics.find((m) => m.id === "fund_ats_snapshots") ?? null}
                expectedId="fund_ats_snapshots"
              />
            </tbody>
          </table>
        </div>
      </section>

      <section className={styles.section} data-testid="state-empty-live">
        <h2 className={styles.sectionTitle}>Empty live record (2026)</h2>
        <div className={styles.statePanel}>
          <GradedGamesSection results={emptyLive} emptyCopy={EMPTY_LIVE_COPY} />
        </div>
      </section>

      <section className={styles.section} data-testid="state-interval-miss">
        <h2 className={styles.sectionTitle}>Interval miss</h2>
        <div className={styles.statePanel}>
          <GradedGameRow game={missGame} />
        </div>
      </section>

      <section className={styles.section} data-testid="state-ungraded">
        <h2 className={styles.sectionTitle}>Ungraded statuses</h2>
        <div className={styles.statePanel}>
          {ungraded.map((game) => (
            <GradedGameRow key={game.game_id} game={game} />
          ))}
        </div>
      </section>

      <section className={styles.section} data-testid="state-missing-metric">
        <h2 className={styles.sectionTitle}>Missing track_record metric</h2>
        <div className={styles.statePanel}>
          <TrackRecordSection
            track={missingMetric}
            expectedIds={EXPECTED_METRIC_IDS.filter(
              (id) => id === "fund_ats_snapshots" || id === "fund_ats_2019",
            )}
          />
        </div>
      </section>

      <section className={styles.section} data-testid="state-fixture-page">
        <h2 className={styles.sectionTitle}>Fixture-only page (truncated)</h2>
        <div className={styles.statePanel}>
          <ResultsPage track={track} results={results} />
        </div>
      </section>
    </main>
  );
}
