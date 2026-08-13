import Link from "next/link";

import { SCOPE_COPY } from "@/lib/results/copy";

import styles from "./ScopeSection.module.css";

/** What the recorded numbers cover and do not (W5-4). */
export function ScopeSection(): React.ReactElement {
  return (
    <section className={styles.section} data-testid="scope-section">
      <h2 className={styles.title}>Scope</h2>
      <p className={styles.copy}>{SCOPE_COPY}</p>
      <p className={styles.links}>
        Methodology detail lives in the project notes (<code>docs/notes/23-readout.md</code>
        ). Public summary: <Link href="/about">About</Link>.
      </p>
    </section>
  );
}
