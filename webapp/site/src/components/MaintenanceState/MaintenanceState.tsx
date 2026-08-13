import styles from "./MaintenanceState.module.css";

/** Site-wide maintenance when schema major version unsupported (§1.7, §3.2). */
export function MaintenanceState(): React.ReactElement {
  return (
    <main className={styles.main}>
      <h1 className={styles.title}>Ridge is updating — check back shortly.</h1>
      <p className={styles.body}>
        Published artifacts use a schema version this build does not support. No partial data is
        shown.
      </p>
    </main>
  );
}
