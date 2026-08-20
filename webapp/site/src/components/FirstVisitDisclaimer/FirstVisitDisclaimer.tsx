"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { disclaimerForYear } from "@/lib/about/copy";

import styles from "./FirstVisitDisclaimer.module.css";

const DISMISS_KEY = "ridge-disclaimer-dismissed";
const SEEN_KEY = "ridge-disclaimer-seen";

/**
 * §5.4 — full §6.1 disclaimer on the first page of a session only.
 * `ridge-disclaimer-seen` is written on mount so deep-link navigation before dismiss
 * still counts as shown. Dismiss hides the block for the rest of the session.
 * Full §6.1 text remains on /about#disclaimer after the first page.
 */
export function FirstVisitDisclaimer(): React.ReactElement | null {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    try {
      if (sessionStorage.getItem(SEEN_KEY) === "1") {
        return;
      }
      sessionStorage.setItem(SEEN_KEY, "1");
    } catch {
      /* sessionStorage unavailable — still show once per mount */
    }
    setVisible(true);
  }, []);

  const dismiss = useCallback(() => {
    try {
      sessionStorage.setItem(DISMISS_KEY, "1");
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
