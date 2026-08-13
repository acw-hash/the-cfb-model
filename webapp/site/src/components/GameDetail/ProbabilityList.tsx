import { Figure } from "@/components/Figure/Figure";
import type { GamePrediction } from "@/lib/artifacts/types";
import {
  probabilityIsCredible,
  probabilityValue,
  type ProbabilityField,
} from "@/lib/game-detail/credibility";
import { formatProbability, nullReasonFootnote, renderNotComputed } from "@/lib/formatting/numbers";

import styles from "./ProbabilityList.module.css";

interface ProbabilityListProps {
  game: GamePrediction;
}

const ROWS: { field: ProbabilityField; label: string }[] = [
  { field: "p_win_home", label: "Home win" },
  { field: "p_cover_home", label: "Cover (model ref)" },
  { field: "p_over", label: "Over (model ref)" },
];

/**
 * Probabilities only where the per-field credibility boolean is true.
 * σ-gating is authoritative. No fallback values. No probability bars.
 */
export function ProbabilityList({ game }: ProbabilityListProps): React.ReactElement {
  const reason = nullReasonFootnote(game.null_reason);

  return (
    <section className={styles.block}>
      <h2 className={styles.label}>Probabilities</h2>
      <ul className={styles.list}>
        {ROWS.map((row) => {
          const shown = probabilityIsCredible(game, row.field);
          const value = probabilityValue(game, row.field);
          const formatted = shown ? formatProbability(value) : null;
          return (
            <li key={row.field} className={styles.row} data-field={row.field}>
              <span className={styles.rowLabel}>{row.label}</span>
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
          );
        })}
      </ul>
    </section>
  );
}
