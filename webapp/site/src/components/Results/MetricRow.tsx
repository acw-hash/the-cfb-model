import { Figure } from "@/components/Figure/Figure";
import type { TrackRecordMetric } from "@/lib/artifacts/types";
import {
  ciIncludesFifty,
  formatRecordedCi,
  formatRecordedNumber,
  formatRecordedPercent,
  rateHasCi,
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

/** One recorded metric row — value + CI required for rates; honest absence when missing. */
export function MetricRow({
  metric,
  expectedId,
  expectedLabel,
}: MetricRowProps): React.ReactElement {
  if (metric == null) {
    return (
      <tr
        className={styles.row}
        data-testid={`metric-missing-${expectedId}`}
        data-metric-id={expectedId}
      >
        <th scope="row" className={styles.label}>
          {expectedLabel ?? expectedId}
        </th>
        <td className={styles.absent} colSpan={3}>
          {MISSING_METRIC_COPY}
        </td>
      </tr>
    );
  }

  if (!rateHasCi(metric)) {
    return (
      <tr
        className={styles.row}
        data-testid={`metric-incomplete-${metric.id}`}
        data-metric-id={metric.id}
      >
        <th scope="row" className={styles.label}>
          {metric.label}
        </th>
        <td className={styles.absent} colSpan={3}>
          {MISSING_METRIC_COPY}
        </td>
      </tr>
    );
  }

  const valueText = formatValue(metric);
  const hasCi = metric.ci_kind !== "none" && metric.ci_lower != null && metric.ci_upper != null;
  const includesFifty = hasCi && ciIncludesFifty(metric);
  const details: string[] = [];
  if (metric.n != null) {
    details.push(`n ${metric.n}`);
  }
  if (metric.regime) {
    details.push(`Basis ${metric.regime}`);
  }
  details.push(`Vintage ${metric.vintage}`);
  if (metric.run) {
    details.push(`Run ${metric.run}`);
  }
  if (metric.ci_kind !== "none") {
    details.push(`CI ${metric.ci_kind.replace("_", " ")}`);
  }

  return (
    <tr className={styles.row} data-testid={`metric-${metric.id}`} data-metric-id={metric.id}>
      <th scope="row" className={styles.label}>
        {metric.label}
      </th>
      <td className={styles.valueCell}>
        <Figure variant="n1" className={styles.value}>
          {valueText}
        </Figure>
      </td>
      <td className={styles.ciCell}>
        {hasCi ? (
          <span data-testid={`metric-ci-${metric.id}`}>
            <Figure variant="n1" className={styles.ci}>
              {formatRecordedCi(metric.ci_lower!, metric.ci_upper!, metric.unit)}
            </Figure>
          </span>
        ) : (
          <span className={styles.absent}>—</span>
        )}
      </td>
      <td className={styles.details}>
        {includesFifty ? (
          <p className={styles.fifty} data-testid={`metric-includes-50-${metric.id}`}>
            <span className={styles.fiftyMark} aria-hidden="true" />
            <span>50 lies inside this interval</span>
          </p>
        ) : null}
        <p className={styles.metaLine}>{details.join(" · ")}</p>
        {metric.notes ? <p className={styles.notes}>{metric.notes}</p> : null}
      </td>
    </tr>
  );
}
