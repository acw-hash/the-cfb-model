"use client";

import type { ReactNode } from "react";

import styles from "./MoreDetail.module.css";

interface MoreDetailProps {
  children: ReactNode;
  note?: string;
}

/**
 * Collapsed secondary details (provenance, model-internal notes).
 * Closed by default — main forecast stays above the fold.
 */
export function MoreDetail({ children, note }: MoreDetailProps): React.ReactElement {
  return (
    <details className={styles.details} data-testid="more-detail">
      <summary className={styles.summary}>More detail</summary>
      {note ? <p className={styles.note}>{note}</p> : null}
      <div className={styles.body}>{children}</div>
    </details>
  );
}
