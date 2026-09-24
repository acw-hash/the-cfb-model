import type { ResultsSeason, TrackRecord } from "@/lib/artifacts/types";

import { GradedGamesSection } from "./GradedGamesSection";
import { ResultsTabs } from "./ResultsTabs";
import { ScopeSection } from "./ScopeSection";
import { TrackRecordSection } from "./TrackRecordSection";

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

/**
 * §5.3 Results / Track Record composition.
 * Verdict banner removed by operator decision (clarity pass) — track_record.verdict
 * remains in the artifact but is not rendered.
 */
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
      <header className={styles.header}>
        <h1 className={styles.title}>Results</h1>
        <p className={styles.subtitle}>Track record</p>
      </header>
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
