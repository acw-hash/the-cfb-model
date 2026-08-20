import { Figure } from "@/components/Figure/Figure";
import {
  formatIntervalParts,
  formatMargin,
  formatNominalCoverage,
  formatSigma,
  formatTotal,
  formatTotalIntervalParts,
  renderAbsent,
  renderForecastUnavailable,
} from "@/lib/formatting/numbers";

import styles from "./ForecastBlock.module.css";

interface ForecastBlockProps {
  label: string;
  billing: "primary" | "secondary";
  mu: number | null;
  sigma: number | null;
  lo: number | null;
  hi: number | null;
  nominal: number | null;
  signed: boolean;
  nullReason: string | null;
  intervalAbsentReason?: string;
}

/**
 * Point forecast with its interval, labeled coverage, and σ.
 * The band is N2 / secondary; μ is N1 on primary billing. Coverage is always
 * stated when bounds exist — an unlabeled band is not honest (§4.2, W4-2).
 */
export function ForecastBlock({
  label,
  billing,
  mu,
  sigma,
  lo,
  hi,
  nominal,
  signed,
  nullReason,
  intervalAbsentReason,
}: ForecastBlockProps): React.ReactElement {
  const muVariant = billing === "primary" ? "n1" : "n2";
  const intervalVariant = billing === "primary" ? "n1" : "n2";
  const blockClass = billing === "primary" ? styles.primary : styles.secondary;

  if (mu == null) {
    const unavailable = renderForecastUnavailable(nullReason);
    return (
      <section className={`${styles.block} ${blockClass}`}>
        <h2 className={styles.label}>{label}</h2>
        <Figure variant="n2" className={styles.muted} title={unavailable.title}>
          {unavailable.text}
        </Figure>
      </section>
    );
  }

  const parts = signed
    ? formatIntervalParts(mu, lo, hi, sigma)
    : formatTotalIntervalParts(mu, lo, hi, sigma);
  const muText = signed
    ? (formatMargin(mu, sigma) ?? renderAbsent())
    : (formatTotal(mu) ?? renderAbsent());
  const sigmaText = formatSigma(sigma);
  const hasBand = parts != null && parts.lo != null && parts.hi != null;
  const coverage = hasBand ? formatNominalCoverage(nominal) : null;
  const absentTitle = intervalAbsentReason
    ? `Interval not computed — ${intervalAbsentReason}`
    : "Interval not computed";

  return (
    <section className={`${styles.block} ${blockClass}`}>
      <h2 className={styles.label}>{label}</h2>
      <div className={styles.figures}>
        <Figure variant={muVariant} className={styles.mu}>
          {muText}
        </Figure>
        <span className={styles.intervalLine} data-testid="forecast-interval-line">
          {hasBand && parts != null && parts.lo != null && parts.hi != null ? (
            <>
              <span className={styles.bracket}>[</span>
              <Figure variant="n2" className={styles.range}>
                {parts.lo}
              </Figure>
              <span className={styles.boundSep}>, </span>
              <Figure variant="n2" className={styles.range}>
                {parts.hi}
              </Figure>
              <span className={styles.bracket}>]</span>
            </>
          ) : (
            <Figure
              variant={intervalVariant}
              className={styles.absent}
              title={absentTitle}
              data-testid="forecast-interval-absent"
            >
              {renderAbsent()}
            </Figure>
          )}
        </span>
        {sigmaText ? (
          <Figure variant="n2" className={styles.sigma}>
            {sigmaText}
          </Figure>
        ) : null}
      </div>
      {hasBand && coverage ? (
        <p className={styles.coverage}>
          <Figure variant="c2">{coverage}</Figure> nominal coverage
        </p>
      ) : null}
    </section>
  );
}
