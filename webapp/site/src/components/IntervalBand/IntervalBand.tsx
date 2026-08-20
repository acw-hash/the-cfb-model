import { Figure } from "@/components/Figure/Figure";
import {
  formatIntervalParts,
  formatMargin,
  renderAbsent,
  renderForecastUnavailable,
} from "@/lib/formatting/numbers";
import type { GamePrediction } from "@/lib/artifacts/types";

import styles from "./IntervalBand.module.css";

interface IntervalBandProps {
  game: Pick<
    GamePrediction,
    "mu_margin" | "sigma_margin" | "margin_interval_lo" | "margin_interval_hi" | "null_reason"
  >;
}

/** N1 headline μ with quiet N2 `[lo, hi]` on its own line — no chart junk (§4.3). */
export function IntervalBand({ game }: IntervalBandProps): React.ReactElement {
  const { mu_margin, sigma_margin, margin_interval_lo, margin_interval_hi, null_reason } = game;

  if (mu_margin == null) {
    const unavailable = renderForecastUnavailable(null_reason);
    return (
      <Figure variant="n2" className={styles.unavailable} title={unavailable.title}>
        {unavailable.text}
      </Figure>
    );
  }

  const parts = formatIntervalParts(
    mu_margin,
    margin_interval_lo,
    margin_interval_hi,
    sigma_margin,
  );
  if (!parts) {
    return (
      <Figure variant="n2" className={styles.muted}>
        {renderAbsent()}
      </Figure>
    );
  }

  const muText = formatMargin(mu_margin, sigma_margin) ?? renderAbsent();
  const hasBand = parts.lo != null && parts.hi != null;

  return (
    <span className={styles.wrap} data-testid="interval-band">
      <Figure variant="n1" className={styles.mu}>
        {muText}
      </Figure>
      <span className={styles.intervalLine} data-testid="interval-line">
        {hasBand ? (
          <>
            <span className={styles.bracket}>[</span>
            <Figure variant="n2" className={styles.bound}>
              {parts.lo}
            </Figure>
            <span className={styles.boundSep}>, </span>
            <Figure variant="n2" className={styles.bound}>
              {parts.hi}
            </Figure>
            <span className={styles.bracket}>]</span>
          </>
        ) : (
          <Figure variant="n1" className={styles.absent} data-testid="interval-absent">
            {renderAbsent()}
          </Figure>
        )}
      </span>
    </span>
  );
}
