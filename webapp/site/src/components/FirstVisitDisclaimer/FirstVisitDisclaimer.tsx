"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { disclaimerForYear } from "@/lib/about/copy";

import styles from "./FirstVisitDisclaimer.module.css";

const STORAGE_KEY = "ridge-disclaimer-dismissed";

/**
 * §5.4 — disclaimer visible before the fold on first visit, dismissible per session.
 * Mounted site-wide under the header so a shared /game/ landing still surfaces it.
 * Full §6.1 text remains on /about#disclaimer after dismiss.
 */
export function FirstVisitDisclaimer(): React.ReactElement | null {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    try {
      if (sessionStorage.getItem(STORAGE_KEY) === "1") {
        return;
      }
    } catch {
      /* sessionStorage unavailable — still show once per mount */
    }
    setVisible(true);
  }, []);

  const dismiss = useCallback(() => {
    try {
      sessionStorage.setItem(STORAGE_KEY, "1");
    } catch {
      /* ignore */
    }
    setVisible(false);
  }, []);

  if (!visible) {
    return null;
  }

  const year = new Date().getUTCFullYear();

  return (
    <aside className={styles.banner} data-testid="first-visit-disclaimer" aria-label="Disclaimer">
      <div className={styles.inner}>
        <p className={styles.kicker}>Disclaimer</p>
        <p className={styles.body}>{disclaimerForYear(year)}</p>
        <div className={styles.actions}>
          <Link href="/about#responsible-gambling">Responsible gambling</Link>
          <button type="button" onClick={dismiss} data-testid="dismiss-disclaimer">
            Dismiss for this session
          </button>
        </div>
      </div>
    </aside>
  );
}
