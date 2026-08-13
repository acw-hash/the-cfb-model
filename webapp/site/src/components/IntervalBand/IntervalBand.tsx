import { Figure } from "@/components/Figure/Figure";
import {
  formatIntervalInline,
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

/** Text-only `μ [lo, hi]` band — quiet, no chart junk (§4.3). */
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
  const hasBand = margin_interval_lo != null && margin_interval_hi != null;

  return (
    <Figure variant="n2" className={styles.band}>
      {hasBand ? formatIntervalInline(parts) : muText}
    </Figure>
  );
}
