import Link from "next/link";

import { FOOTER_DISCLAIMER_SHORT } from "@/lib/about/copy";

import styles from "./SiteFooter.module.css";

/**
 * Site-wide disclaimer discoverability (§6 / W6-2).
 * Short substance here; full §6.1 and responsible-gambling copy live on /about.
 * Chosen over a sticky legal bar: always present, never competing with the forecast.
 */
export function SiteFooter(): React.ReactElement {
  return (
    <footer className={styles.footer} data-testid="site-footer">
      <div className={styles.inner}>
        <p className={styles.disclaimer}>{FOOTER_DISCLAIMER_SHORT}</p>
        <p className={styles.links}>
          <Link href="/about#disclaimer">Disclaimer</Link>
          <span className={styles.sep} aria-hidden="true">
            ·
          </span>
          <Link href="/about#responsible-gambling">Responsible gambling</Link>
          <span className={styles.sep} aria-hidden="true">
            ·
          </span>
          <Link href="/about">About</Link>
        </p>
        <p className={styles.rg}>
          Gambling help: <a href="tel:18004262537">1-800-GAMBLER</a>
          {" — "}
          <Link href="/about#responsible-gambling">details on About</Link>
        </p>
      </div>
    </footer>
  );
}
