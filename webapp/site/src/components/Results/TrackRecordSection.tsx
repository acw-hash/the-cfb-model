import { Figure } from "@/components/Figure/Figure";
import type { TrackRecord, TrackRecordMetric } from "@/lib/artifacts/types";
import { NO_SINGLE_NUMBER_COPY } from "@/lib/results/copy";
import { EXPECTED_METRIC_IDS, metricById } from "@/lib/results/demo-states";

import { MetricRow } from "./MetricRow";

import styles from "./TrackRecordSection.module.css";

interface TrackRecordSectionProps {
  track: TrackRecord;
  /** When set, render these ids (for missing-metric demos). Default: EXPECTED_METRIC_IDS. */
  expectedIds?: readonly string[];
}

/** Recorded 23-readout metrics — no client recompute, no aggregate headline. */
export function TrackRecordSection({
  track,
  expectedIds = EXPECTED_METRIC_IDS,
}: TrackRecordSectionProps): React.ReactElement {
  return (
    <section className={styles.section} data-testid="track-record-section">
      <h2 className={styles.title}>Recorded results</h2>
      <p className={styles.noAggregate} data-testid="no-single-number">
        {NO_SINGLE_NUMBER_COPY}
      </p>
      {track.ensemble_scope_label ? (
        <p className={styles.scope}>
          Scope <Figure variant="c2">{track.ensemble_scope_label}</Figure>
          {track.vintage_labels && track.vintage_labels.length > 0
            ? ` · ${track.vintage_labels.join(", ")}`
            : null}
        </p>
      ) : null}
      <div className={styles.list} data-testid="metric-list">
        {expectedIds.map((id) => {
          const metric: TrackRecordMetric | undefined = metricById(track.metrics, id);
          return (
            <MetricRow
              key={id}
              metric={metric ?? null}
              expectedId={id}
              expectedLabel={metric?.label ?? id}
            />
          );
        })}
      </div>
    </section>
  );
}
