import type { ResultsSeason } from "@/lib/artifacts/types";
import {
  EMPTY_LIVE_COPY,
  FIXTURE_GRADES_COPY,
  gameNotFinalCountCopy,
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

  // game_not_final is the season-schedule flood — collapse to a count.
  // no_pre_kickoff_publish / postgame_missing stay as rows (distinct outcomes).
  const visibleGames = results.games.filter((g) => g.grade_status !== "game_not_final");
  const notFinalCount = results.games.filter((g) => g.grade_status === "game_not_final").length;

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
        {visibleGames.map((game) => (
          <GradedGameRow key={game.game_id} game={game} />
        ))}
      </div>
      {notFinalCount > 0 ? (
        <p
          className={styles.notFinalCount}
          role="status"
          data-testid="game-not-final-count"
          data-count={notFinalCount}
        >
          {gameNotFinalCountCopy(notFinalCount)}
        </p>
      ) : null}
    </section>
  );
}
