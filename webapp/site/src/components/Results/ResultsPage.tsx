import Link from "next/link";

import type { ResultsSeason, TrackRecord } from "@/lib/artifacts/types";

import { GradedGamesSection } from "./GradedGamesSection";
import { ResultsTabs } from "./ResultsTabs";
import { ScopeSection } from "./ScopeSection";
import { TrackRecordSection } from "./TrackRecordSection";
import { VerdictBlock } from "./VerdictBlock";

import styles from "./ResultsPage.module.css";

interface ResultsPageProps {
  track: TrackRecord;
  results: ResultsSeason | null;
  initialTab?: "record" | "games";
  syncUrl?: boolean;
  /** Gallery / demo: override expected metric ids for missing-metric demos. */
  expectedMetricIds?: readonly string[];
  emptyCopy?: string;
}

/** §5.3 Results / Track Record composition. */
export function ResultsPage({
  track,
  results,
  initialTab = "record",
  syncUrl = false,
  expectedMetricIds,
  emptyCopy,
}: ResultsPageProps): React.ReactElement {
  return (
    <article className={styles.page} data-testid="results-page">
      <p className={styles.nav}>
        <Link href="/">This Week</Link>
        <span className={styles.navSep} aria-hidden="true">
          ·
        </span>
        <span className={styles.navCurrent}>Results</span>
      </p>
      <header className={styles.header}>
        <h1 className={styles.title}>Results</h1>
        <p className={styles.subtitle}>Track record</p>
      </header>
      <VerdictBlock verdict={track.verdict} />
      <ResultsTabs initialTab={initialTab} syncUrl={syncUrl}>
        {{
          record: <TrackRecordSection track={track} expectedIds={expectedMetricIds} />,
          games: <GradedGamesSection results={results} emptyCopy={emptyCopy} />,
        }}
      </ResultsTabs>
      <ScopeSection />
    </article>
  );
}
