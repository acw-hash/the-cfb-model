import { formatSignedHomeMarginAria } from "@/lib/formatting/market-margin";

import styles from "./MarginNumberLine.module.css";

interface MarginNumberLineProps {
  homeTeam: string;
  awayTeam: string;
  mu: number | null;
  lo: number | null;
  hi: number | null;
  market: number | null;
}

/**
 * Monochrome margin number line: model 90% bracket + μ dot + hollow consensus tick.
 * Narrow §4.3 exception (ADR-ODDS-SNAPSHOT). Legible at 380px and in dark mode.
 */
export function MarginNumberLine({
  homeTeam,
  awayTeam,
  mu,
  lo,
  hi,
  market,
}: MarginNumberLineProps): React.ReactElement | null {
  const values = [mu, lo, hi, market].filter((v): v is number => v != null && Number.isFinite(v));
  if (values.length === 0) {
    return null;
  }

  const pad = 4;
  const min = Math.min(...values) - pad;
  const max = Math.max(...values) + pad;
  const span = max - min || 1;
  const toPct = (v: number) => `${((v - min) / span) * 100}%`;

  const ariaParts: string[] = [];
  if (mu != null) {
    ariaParts.push(`Model expected margin ${formatSignedHomeMarginAria(mu, homeTeam, awayTeam)}`);
  }
  if (lo != null && hi != null) {
    ariaParts.push(
      `model range ${formatSignedHomeMarginAria(lo, homeTeam, awayTeam)} to ${formatSignedHomeMarginAria(hi, homeTeam, awayTeam)} on the home-margin scale`,
    );
  }
  if (market != null) {
    ariaParts.push(`consensus line ${formatSignedHomeMarginAria(market, homeTeam, awayTeam)}`);
  }

  return (
    <div
      className={styles.wrap}
      data-testid="margin-number-line"
      role="img"
      aria-label={ariaParts.join(". ") + "."}
    >
      <div className={styles.axis}>
        {lo != null && hi != null ? (
          <span
            className={styles.bracket}
            style={{ left: toPct(lo), width: `calc(${toPct(hi)} - ${toPct(lo)})` }}
          />
        ) : null}
        {mu != null ? <span className={styles.mu} style={{ left: toPct(mu) }} /> : null}
        {market != null ? (
          <span className={styles.market} style={{ left: toPct(market) }} title="Consensus" />
        ) : null}
      </div>
      <div className={styles.ends}>
        <span>{awayTeam}</span>
        <span>{homeTeam}</span>
      </div>
    </div>
  );
}
