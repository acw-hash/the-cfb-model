import { Figure } from "@/components/Figure/Figure";
import type { GamePrediction } from "@/lib/artifacts/types";
import { probabilityIsCredible, probabilityValue } from "@/lib/game-detail/credibility";
import { formatProbability, nullReasonFootnote, renderNotComputed } from "@/lib/formatting/numbers";

import styles from "./ProbabilityList.module.css";

interface ProbabilityListProps {
  game: GamePrediction;
}

/**
 * Win probability only where the credibility boolean is true.
 * Cover/over withdrawn (ADR 0015). σ-gating is authoritative. No bars.
 */
export function ProbabilityList({ game }: ProbabilityListProps): React.ReactElement {
  const reason = nullReasonFootnote(game.null_reason);
  const shown = probabilityIsCredible(game, "p_win_home");
  const value = probabilityValue(game, "p_win_home");
  const formatted = shown ? formatProbability(value) : null;

  return (
    <section className={styles.block}>
      <h2 className={styles.label}>Probabilities</h2>
      <ul className={styles.list}>
        <li className={styles.row} data-field="p_win_home">
          <span className={styles.rowLabel}>Home win</span>
          {formatted ? (
            <Figure variant="n2" className={styles.value}>
              {formatted}
            </Figure>
          ) : (
            <span className={styles.absent} title={game.null_reason ?? undefined}>
              {renderNotComputed()}
              {reason ? <span className={styles.reason}> — {reason}</span> : null}
            </span>
          )}
        </li>
      </ul>
    </section>
  );
}
