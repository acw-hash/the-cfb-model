"use client";

import { useCallback, useEffect, useState } from "react";

import styles from "./HowToReadKey.module.css";

const SEEN_KEY = "ridge-howto-read-seen";

/**
 * Quiet dismissible C2 line under the This Week title.
 * Session-dismissible (sessionStorage). Tier / stickiness lives on About.
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
      Numbers sit beside the team they favor. Market odds are shown for context, not as bet
      suggestions.{" "}
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
