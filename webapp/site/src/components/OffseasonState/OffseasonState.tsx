import Link from "next/link";

import styles from "./OffseasonState.module.css";

/** Deliberate empty-slate copy per §5.1 / W3-3.6 — not an empty list. */
export function OffseasonState(): React.ReactElement {
  return (
    <div className={styles.state} role="status" data-testid="offseason-state">
      <p className={styles.copy}>
        Season complete — view <Link href="/results">Results</Link>.
      </p>
    </div>
  );
}

/** Missing latest/ week artifact per §5.1. */
export function PreFirstPublishState(): React.ReactElement {
  return (
    <div className={styles.state} role="status" data-testid="pre-first-publish">
      <p className={styles.copy}>Opening week forecasts publish Tuesday 06:00 UTC.</p>
    </div>
  );
}
