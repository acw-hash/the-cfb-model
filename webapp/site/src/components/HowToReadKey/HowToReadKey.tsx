"use client";

import { useCallback, useEffect, useState } from "react";

import styles from "./HowToReadKey.module.css";

const SEEN_KEY = "ridge-howto-read-seen";

/**
 * Quiet one-line key above the This Week slate.
 * Session-dismissible like FirstVisitDisclaimer (sessionStorage seen flag).
 * Does not quote enter thresholds as hard floors — hysteresis can hold a tier
 * while the shown win chance sits below the enter band (DESIGN §2.3).
 */
export function HowToReadKey(): React.ReactElement | null {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    try {
      if (sessionStorage.getItem(SEEN_KEY) === "1") {
        return;
      }
    } catch {
      /* sessionStorage unavailable — still show once per mount */
    }
    setVisible(true);
  }, []);

  const dismiss = useCallback(() => {
    try {
      sessionStorage.setItem(SEEN_KEY, "1");
    } catch {
      /* ignore */
    }
    setVisible(false);
  }, []);

  if (!visible) {
    return null;
  }

  return (
    <p className={styles.key} data-testid="how-to-read-key">
      <span className={styles.label}>How to read this. </span>
      Each row names the favored team and the predicted margin (&quot;by X&quot; = expected winning
      margin), then the favored team&apos;s win chance when the model can show one. When market odds
      are shown, they use the same scale for context — not a bet suggestion. Conviction tiers
      (Strong lean, Clear lean, Lean, Toss-up) label how decisive the forecast looks. Tiers are
      sticky: a game near a line keeps its label until the chance moves clearly.{" "}
      <button
        type="button"
        className={styles.dismiss}
        onClick={dismiss}
        data-testid="dismiss-howto"
      >
        Dismiss
      </button>
    </p>
  );
}
