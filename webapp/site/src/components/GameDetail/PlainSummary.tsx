import type { GamePrediction } from "@/lib/artifacts/types";
import { buildPlainForecastSummary } from "@/lib/game-detail/plain-summary";

import styles from "./PlainSummary.module.css";

interface PlainSummaryProps {
  game: GamePrediction;
}

/** Lead plain-English forecast summary for Game Detail. */
export function PlainSummary({ game }: PlainSummaryProps): React.ReactElement {
  const summary = buildPlainForecastSummary(game);
  return (
    <section className={styles.block} data-testid="plain-summary">
      {summary.paragraphs.map((p) => (
        <p key={p.slice(0, 64)} className={styles.body}>
          {p}
        </p>
      ))}
    </section>
  );
}
