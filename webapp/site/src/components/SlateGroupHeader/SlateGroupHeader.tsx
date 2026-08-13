import styles from "./SlateGroupHeader.module.css";

interface SlateGroupHeaderProps {
  label: string;
}

/** Quiet day / tier group header — scores-app section rule, not a widget. */
export function SlateGroupHeader({ label }: SlateGroupHeaderProps): React.ReactElement {
  return <h2 className={styles.header}>{label}</h2>;
}
