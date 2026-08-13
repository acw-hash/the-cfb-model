import { Figure } from "@/components/Figure/Figure";
import type { TrackRecordMetric } from "@/lib/artifacts/types";
import {
  assertRateHasCi,
  ciIncludesFifty,
  formatRecordedCi,
  formatRecordedNumber,
  formatRecordedPercent,
} from "@/lib/formatting/track-record";
import { MISSING_METRIC_COPY } from "@/lib/results/copy";

import styles from "./MetricRow.module.css";

interface MetricRowProps {
  metric: TrackRecordMetric | null;
  expectedId: string;
  expectedLabel?: string;
}

function formatValue(metric: TrackRecordMetric): string {
  if (typeof metric.value === "string") {
    return metric.value;
  }
  if (metric.unit === "percent") {
    return formatRecordedPercent(metric.value);
  }
  return formatRecordedNumber(metric.value);
}

/** One recorded metric — value + CI required for rates; honest absence when missing. */
export function MetricRow({
  metric,
  expectedId,
  expectedLabel,
}: MetricRowProps): React.ReactElement {
  if (metric == null) {
    return (
      <article
        className={styles.row}
        data-testid={`metric-missing-${expectedId}`}
        data-metric-id={expectedId}
      >
        <h3 className={styles.label}>{expectedLabel ?? expectedId}</h3>
        <p className={styles.absent}>{MISSING_METRIC_COPY}</p>
      </article>
    );
  }

  assertRateHasCi(metric);

  const valueText = formatValue(metric);
  const hasCi = metric.ci_kind !== "none" && metric.ci_lower != null && metric.ci_upper != null;
  const includesFifty = hasCi && ciIncludesFifty(metric);

  return (
    <article className={styles.row} data-testid={`metric-${metric.id}`} data-metric-id={metric.id}>
      <h3 className={styles.label}>{metric.label}</h3>
      <div className={styles.figures}>
        <Figure variant="n1" className={styles.value}>
          {valueText}
        </Figure>
        {hasCi ? (
          <span data-testid={`metric-ci-${metric.id}`}>
            <Figure variant="n1" className={styles.ci}>
              {formatRecordedCi(metric.ci_lower!, metric.ci_upper!, metric.unit)}
            </Figure>
          </span>
        ) : null}
      </div>
      {includesFifty ? (
        <p className={styles.fifty} data-testid={`metric-includes-50-${metric.id}`}>
          <span className={styles.fiftyMark} aria-hidden="true" />
          <span>50 lies inside this interval</span>
        </p>
      ) : null}
      <dl className={styles.meta}>
        {metric.n != null ? (
          <>
            <dt>n</dt>
            <dd>
              <Figure variant="c2">{metric.n}</Figure>
            </dd>
          </>
        ) : null}
        {metric.regime ? (
          <>
            <dt>Basis</dt>
            <dd>{metric.regime}</dd>
          </>
        ) : null}
        <dt>Vintage</dt>
        <dd>{metric.vintage}</dd>
        {metric.run ? (
          <>
            <dt>Run</dt>
            <dd>{metric.run}</dd>
          </>
        ) : null}
        {metric.ci_kind !== "none" ? (
          <>
            <dt>CI</dt>
            <dd>{metric.ci_kind.replace("_", " ")}</dd>
          </>
        ) : null}
      </dl>
      {metric.notes ? <p className={styles.notes}>{metric.notes}</p> : null}
    </article>
  );
}
