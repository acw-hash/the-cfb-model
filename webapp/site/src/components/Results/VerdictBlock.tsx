import type { TrackRecord } from "@/lib/artifacts/types";
import { VERDICT_LAY_SUMMARY } from "@/lib/results/copy";

import styles from "./VerdictBlock.module.css";

interface VerdictBlockProps {
  verdict: TrackRecord["verdict"];
}

/**
 * NOT CURRENTLY FIT TO BET — finding tone, not apology (W5-2).
 * Label and artifact plain_language are verbatim from track_record.json (§5.3).
 * Lay summary is reachable in one tap via disclosure.
 */
export function VerdictBlock({ verdict }: VerdictBlockProps): React.ReactElement {
  return (
    <section className={styles.block} data-testid="verdict-block" aria-labelledby="verdict-label">
      <p className={styles.kicker}>Finding</p>
      <h2 id="verdict-label" className={styles.label}>
        {verdict.label}
      </h2>
      <p className={styles.plain} data-testid="verdict-plain-language">
        {verdict.plain_language}
      </p>
      <details className={styles.layDisclosure} data-testid="verdict-lay-disclosure">
        <summary className={styles.laySummary}>Readable summary</summary>
        <p className={styles.layBody}>{VERDICT_LAY_SUMMARY}</p>
      </details>
    </section>
  );
}
