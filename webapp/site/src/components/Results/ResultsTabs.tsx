"use client";

import { useCallback, useEffect, useState } from "react";

import styles from "./ResultsTabs.module.css";

export type ResultsTab = "record" | "games";

interface ResultsTabsProps {
  initialTab?: ResultsTab;
  syncUrl?: boolean;
  children: {
    record: React.ReactNode;
    games: React.ReactNode;
  };
}

function parseTab(raw: string | null): ResultsTab {
  return raw === "games" ? "games" : "record";
}

/** §5.3 tabs — Recorded results | Graded games. */
export function ResultsTabs({
  initialTab = "record",
  syncUrl = false,
  children,
}: ResultsTabsProps): React.ReactElement {
  const [tab, setTab] = useState<ResultsTab>(initialTab);

  useEffect(() => {
    if (!syncUrl || typeof window === "undefined") {
      return;
    }
    const params = new URLSearchParams(window.location.search);
    setTab(parseTab(params.get("tab")));
  }, [syncUrl]);

  const select = useCallback(
    (next: ResultsTab) => {
      setTab(next);
      if (!syncUrl || typeof window === "undefined") {
        return;
      }
      const url = new URL(window.location.href);
      if (next === "record") {
        url.searchParams.delete("tab");
      } else {
        url.searchParams.set("tab", next);
      }
      window.history.replaceState(null, "", url.toString());
    },
    [syncUrl],
  );

  return (
    <div data-testid="results-tabs">
      <div className={styles.bar}>
        <p className={styles.legend} id="results-tab-label">
          View
        </p>
        <div className={styles.group} role="tablist" aria-labelledby="results-tab-label">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "record"}
            className={tab === "record" ? styles.selected : styles.option}
            data-testid="tab-record"
            onClick={() => select("record")}
          >
            Recorded results
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "games"}
            className={tab === "games" ? styles.selected : styles.option}
            data-testid="tab-games"
            onClick={() => select("games")}
          >
            Graded games
          </button>
        </div>
      </div>
      <div role="tabpanel" hidden={tab !== "record"} data-testid="panel-record">
        {children.record}
      </div>
      <div role="tabpanel" hidden={tab !== "games"} data-testid="panel-games">
        {children.games}
      </div>
    </div>
  );
}
