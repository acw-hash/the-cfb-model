import styles from "./FixtureBanner.module.css";

/** Persistent fixture-data banner — layout-level, impossible to omit per-page (§1.7). */
export function FixtureBanner(): React.ReactElement {
  return (
    <div className={styles.banner} role="status">
      FIXTURE DATA — development artifacts only; not live publishes.
    </div>
  );
}
