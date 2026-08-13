import { Figure } from "@/components/Figure/Figure";
import { IntervalBand } from "@/components/IntervalBand/IntervalBand";
import { RevisedMarker } from "@/components/RevisedMarker/RevisedMarker";
import { StaleBadge } from "@/components/StaleBadge/StaleBadge";
import { TierChip } from "@/components/TierChip/TierChip";
import type { GamePrediction } from "@/lib/artifacts/types";
import { formatKickoffLocal } from "@/lib/formatting/time";

import styles from "./GameRow.module.css";

interface GameRowProps {
  game: GamePrediction;
}

/** Scores-app density game row (§4.3). */
export function GameRow({ game }: GameRowProps): React.ReactElement {
  const kickoff = formatKickoffLocal(game.kickoff_utc);
  const matchup = `${game.away_team} @ ${game.home_team}`;

  return (
    <article className={styles.row}>
      <div className={styles.kickoff}>
        <Figure variant="c2" className={styles.kickoffTime} title={`${kickoff.utc} UTC`}>
          {kickoff.local}
        </Figure>
      </div>

      <div className={styles.center}>
        <h3 className={styles.matchup}>
          {matchup}
          {game.neutral_site ? (
            <span className={styles.neutral} title="Neutral site">
              N
            </span>
          ) : null}
        </h3>
      </div>

      <div className={styles.right}>
        <IntervalBand game={game} />
        <TierChip convictionTier={game.conviction_tier} convictionLabel={game.conviction_label} />
        <RevisedMarker
          tierRevisedSincePrimary={game.tier_revised_since_primary}
          convictionTier={game.conviction_tier}
          tierPrimary={game.tier_primary}
        />
        <StaleBadge staleStamp={game.stale_stamp} sources={game.stale_sources} />
      </div>
    </article>
  );
}
