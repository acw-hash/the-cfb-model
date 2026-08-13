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

/** N1 headline μ with quiet N2 `[lo, hi]` band — no chart junk (§4.3). */
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
    <span className={styles.wrap}>
      <Figure variant="n1" className={styles.mu}>
        {muText}
      </Figure>
      {hasBand ? (
        <Figure variant="n2" className={styles.range}>
          [{parts.lo}, {parts.hi}]
        </Figure>
      ) : null}
    </span>
  );
}
