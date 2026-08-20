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
  const verdict = track.verdict ?? {
    label: "Recorded finding unavailable",
    plain_language: "The track-record artifact did not include a verdict block.",
  };
  return (
    <article className={styles.page} data-testid="results-page">
      <header className={styles.header}>
        <h1 className={styles.title}>Results</h1>
        <p className={styles.subtitle}>Track record</p>
      </header>
      <VerdictBlock verdict={verdict} />
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
