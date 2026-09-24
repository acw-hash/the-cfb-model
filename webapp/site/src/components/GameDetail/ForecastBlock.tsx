import { Figure } from "@/components/Figure/Figure";
import {
  formatTotal,
  formatTotalIntervalParts,
  renderAbsent,
  renderForecastUnavailable,
} from "@/lib/formatting/numbers";
import {
  formatTeamNamedInterval,
  formatTeamNamedMargin,
  formatTypicalMiss,
  formatNominalCoveragePhrase,
} from "@/lib/formatting/team-margin";

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
  homeTeam?: string;
  awayTeam?: string;
  pWinHome?: number | null;
  /** Optional plain lead line (e.g. "Combined score: about 51.2"). */
  plainLead?: string | null;
}

/**
 * Point forecast with team-named margin / plain total, range, and typical miss.
 * Coverage uses "N in 10" wording when nominal is present (§4.2, clarity).
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
  homeTeam,
  awayTeam,
  pWinHome,
  plainLead,
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

  let muText: string;
  let rangeText: string | null;
  if (signed && homeTeam != null && awayTeam != null) {
    muText = formatTeamNamedMargin(mu, homeTeam, awayTeam, sigma, pWinHome) ?? renderAbsent();
    rangeText = formatTeamNamedInterval(lo, hi, homeTeam, awayTeam, sigma);
  } else {
    const parts = formatTotalIntervalParts(mu, lo, hi, sigma);
    muText = plainLead ?? formatTotal(mu) ?? renderAbsent();
    rangeText =
      parts != null && parts.lo != null && parts.hi != null ? `${parts.lo} to ${parts.hi}` : null;
  }

  const sigmaText = formatTypicalMiss(sigma);
  const hasBand = rangeText != null;
  const coverage = hasBand ? formatNominalCoveragePhrase(nominal) : { kind: "absent" as const };

  return (
    <section className={`${styles.block} ${blockClass}`}>
      <h2 className={styles.label}>{label}</h2>
      <div className={styles.figures}>
        <Figure variant={muVariant} className={styles.mu}>
          {muText}
        </Figure>
        <span className={styles.intervalLine} data-testid="forecast-interval-line">
          {hasBand && rangeText != null ? (
            <Figure variant="n2" className={styles.range}>
              {rangeText}
            </Figure>
          ) : (
            <span className={styles.absentSlot}>
              <Figure
                variant={intervalVariant}
                className={styles.absent}
                data-testid="forecast-interval-absent"
              >
                {renderAbsent()}
              </Figure>
              <span className={styles.absentReason}>
                Interval not computed
                {intervalAbsentReason ? (
                  <span className={styles.reason}> — {intervalAbsentReason}</span>
                ) : null}
              </span>
            </span>
          )}
        </span>
        {sigmaText ? (
          <Figure variant="n2" className={styles.sigma}>
            {sigmaText}
          </Figure>
        ) : null}
      </div>
      {hasBand && coverage.kind !== "absent" ? (
        <p className={styles.coverage}>
          <Figure variant="c2">{coverage.label}</Figure>
          {coverage.kind === "n_in_10" ? " nominal coverage" : ""}
        </p>
      ) : hasBand ? (
        <p className={styles.coverage}>
          <Figure variant="c2">likely range</Figure>
        </p>
      ) : null}
    </section>
  );
}
