import styles from "./typography.module.css";

type TypographyVariant = "t1" | "t2" | "t3" | "b1" | "b2" | "n1" | "n2" | "c1" | "c2";

interface FigureProps {
  children: React.ReactNode;
  variant?: TypographyVariant;
  className?: string;
  title?: string;
}

/**
 * Shared numeric display primitive — enforces tabular numerals (§4.2).
 * All formatted numbers must render inside this component.
 */
export function Figure({
  children,
  variant = "n1",
  className,
  title,
}: FigureProps): React.ReactElement {
  const classes = [styles.figure, styles[variant], className].filter(Boolean).join(" ");
  return (
    <span className={classes} title={title}>
      {children}
    </span>
  );
}
