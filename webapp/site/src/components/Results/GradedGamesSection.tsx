import type { ResultsSeason } from "@/lib/artifacts/types";
import {
  EMPTY_LIVE_COPY,
  FIXTURE_GRADES_COPY,
  LOCKBOX_NO_AGGREGATE_COPY,
} from "@/lib/results/copy";

import { GradedGameRow } from "./GradedGameRow";

import styles from "./GradedGamesSection.module.css";

interface GradedGamesSectionProps {
  results: ResultsSeason | null;
  /** Override empty copy (e.g. gallery empty-live demo). */
  emptyCopy?: string;
}

/** Per-game grades only — never aggregates hit rates over seasons ≤ 2025. */
export function GradedGamesSection({
  results,
  emptyCopy = EMPTY_LIVE_COPY,
}: GradedGamesSectionProps): React.ReactElement {
  if (results == null || results.games.length === 0) {
    return (
      <section className={styles.section} data-testid="graded-games-empty">
        <h2 className={styles.title}>Graded games</h2>
        <p className={styles.empty} role="status">
          {emptyCopy}
        </p>
        <p className={styles.lockbox}>{LOCKBOX_NO_AGGREGATE_COPY}</p>
      </section>
    );
  }

  const isFixture = results.fixture === true;
  const season = results.season;

  return (
    <section className={styles.section} data-testid="graded-games-section">
      <h2 className={styles.title}>Graded games · {season}</h2>
      {isFixture ? (
        <p className={styles.fixtureNote} data-testid="fixture-grades-note">
          {FIXTURE_GRADES_COPY}
        </p>
      ) : null}
      <p className={styles.lockbox} data-testid="lockbox-no-aggregate">
        {LOCKBOX_NO_AGGREGATE_COPY}
      </p>
      <div className={styles.list}>
        {results.games.map((game) => (
          <GradedGameRow key={game.game_id} game={game} />
        ))}
      </div>
    </section>
  );
}
