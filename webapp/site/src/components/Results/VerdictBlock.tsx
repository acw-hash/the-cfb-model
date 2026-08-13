import type { TrackRecord } from "@/lib/artifacts/types";
import { VERDICT_LAY_SUMMARY } from "@/lib/results/copy";

import styles from "./VerdictBlock.module.css";

interface VerdictBlockProps {
  verdict: TrackRecord["verdict"];
}

/**
 * NOT CURRENTLY FIT TO BET — finding tone, not apology (W5-2).
 * Label and artifact plain_language are verbatim from track_record.json.
 * Lay summary leads for scanning readers.
 */
export function VerdictBlock({ verdict }: VerdictBlockProps): React.ReactElement {
  return (
    <section className={styles.block} data-testid="verdict-block" aria-labelledby="verdict-label">
      <p className={styles.kicker}>Finding</p>
      <h2 id="verdict-label" className={styles.label}>
        {verdict.label}
      </h2>
      <p className={styles.lay}>{VERDICT_LAY_SUMMARY}</p>
      <p className={styles.recorded}>
        <span className={styles.recordedLabel}>Recorded finding</span>
        {verdict.plain_language}
      </p>
    </section>
  );
}
