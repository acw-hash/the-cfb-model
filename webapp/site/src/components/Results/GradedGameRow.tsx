import { Figure } from "@/components/Figure/Figure";
import { IntervalBand } from "@/components/IntervalBand/IntervalBand";
import { KickoffTime } from "@/components/KickoffTime/KickoffTime";
import { TierChip } from "@/components/TierChip/TierChip";
import type { GradedGame } from "@/lib/artifacts/types";
import {
  ABSENT,
  formatActualMargin,
  formatFinalScore,
  formatTotal,
} from "@/lib/formatting/numbers";
import { formatAbsoluteUtc } from "@/lib/formatting/time";
import { formatGradeStatus } from "@/lib/results/grade-status";
import { formatRefreshKind } from "@/lib/this-week/refresh-kind";

import styles from "./GradedGameRow.module.css";

interface GradedGameRowProps {
  game: GradedGame;
  /** Test injection — production resolves visitor TZ inside KickoffTime. */
  timeZone?: string;
}

function intervalHitLabel(hit: boolean | null): string {
  if (hit === true) {
    return "Interval hit";
  }
  if (hit === false) {
    return "Interval missed";
  }
  return "Interval not graded";
}

/** Per-game grade row — prediction locked before kickoff; ungraded statuses explicit. */
export function GradedGameRow({ game, timeZone }: GradedGameRowProps): React.ReactElement {
  const isGraded = game.grade_status === "graded";
  const hitClass =
    game.margin_interval_hit === false
      ? styles.miss
      : game.margin_interval_hit === true
        ? styles.hit
        : styles.ungradedHit;
  const scoreText = formatFinalScore(game.away_points, game.home_points) ?? ABSENT;
  const marginText = formatActualMargin(game.actual_margin) ?? ABSENT;
  const totalText = game.actual_total == null ? ABSENT : (formatTotal(game.actual_total) ?? ABSENT);

  return (
    <article
      className={styles.row}
      data-testid={`graded-game-${game.game_id}`}
      data-grade-status={game.grade_status}
      data-margin-interval-hit={
        game.margin_interval_hit == null ? "null" : String(game.margin_interval_hit)
      }
    >
      <div className={styles.top}>
        <KickoffTime
          kickoffUtc={game.kickoff_utc}
          variant="c2"
          className={styles.kickoff}
          timeZone={timeZone}
        />
        <span className={styles.status} data-testid={`grade-status-${game.game_id}`}>
          {formatGradeStatus(game.grade_status)}
        </span>
      </div>
      <p className={styles.matchup}>
        <span className={styles.teams}>
          {game.away_team} @ {game.home_team}
        </span>
        <Figure variant="n2" className={styles.score}>
          {scoreText}
        </Figure>
      </p>
      {isGraded ? (
        <>
          <div className={styles.forecast}>
            <span className={styles.forecastLabel}>Pre-kickoff</span>
            <IntervalBand
              game={{
                mu_margin: game.mu_margin,
                sigma_margin: game.sigma_margin,
                margin_interval_lo: game.margin_interval_lo,
                margin_interval_hi: game.margin_interval_hi,
                null_reason: null,
              }}
            />
            <TierChip
              convictionTier={game.conviction_tier}
              convictionLabel={game.conviction_label}
            />
          </div>
          <div className={styles.actuals}>
            <span>
              Actual margin <Figure variant="n2">{marginText}</Figure>
            </span>
            <span>
              Actual total <Figure variant="n2">{totalText}</Figure>
            </span>
          </div>
          <p
            className={`${styles.hitLine} ${hitClass}`}
            data-testid={`interval-hit-${game.game_id}`}
          >
            {intervalHitLabel(game.margin_interval_hit)}
            {game.total_interval_hit == null
              ? " · Total interval not graded"
              : game.total_interval_hit
                ? " · Total interval hit"
                : " · Total interval missed"}
          </p>
          {game.graded_from ? (
            <p className={styles.gradedFrom} data-testid={`graded-from-${game.game_id}`}>
              Graded from {formatRefreshKind(game.graded_from.refresh_kind)} ·{" "}
              <Figure variant="c2">{formatAbsoluteUtc(game.graded_from.published_at)}</Figure>
              {" · locked before kickoff"}
            </p>
          ) : (
            <p className={styles.gradedFrom}>Graded-from publish not recorded</p>
          )}
        </>
      ) : (
        <p className={styles.ungradedBody} data-testid={`ungraded-body-${game.game_id}`}>
          {game.grade_status === "game_not_final"
            ? "Final score not available — row kept visible; not treated as a miss."
            : game.grade_status === "no_pre_kickoff_publish"
              ? "No publish existed before kickoff — excluded from grading, not counted as zero."
              : "Postgame fields missing — cannot grade against a locked forecast."}
        </p>
      )}
    </article>
  );
}
