import { IntervalBand } from "@/components/IntervalBand/IntervalBand";
import { KickoffTime } from "@/components/KickoffTime/KickoffTime";
import { RevisedMarker } from "@/components/RevisedMarker/RevisedMarker";
import { StaleBadge } from "@/components/StaleBadge/StaleBadge";
import { TierChip } from "@/components/TierChip/TierChip";
import type { GamePrediction, ThisWeekGame } from "@/lib/artifacts/types";

import styles from "./GameRow.module.css";

type GameRowModel = Pick<
  ThisWeekGame,
  | "kickoff_utc"
  | "away_team"
  | "home_team"
  | "neutral_site"
  | "mu_margin"
  | "sigma_margin"
  | "margin_interval_lo"
  | "margin_interval_hi"
  | "null_reason"
  | "conviction_tier"
  | "conviction_label"
  | "tier_revised_since_primary"
  | "tier_primary"
  | "stale_stamp"
  | "stale_sources"
>;

interface GameRowProps {
  game: GameRowModel | GamePrediction;
  /** Test injection — production resolves visitor TZ inside KickoffTime. */
  timeZone?: string;
}

/** Scores-app density game row (§4.3). */
export function GameRow({ game, timeZone }: GameRowProps): React.ReactElement {
  const matchup = `${game.away_team} @ ${game.home_team}`;

  return (
    <article className={styles.row}>
      <div className={styles.kickoff}>
        <KickoffTime
          kickoffUtc={game.kickoff_utc}
          variant="c2"
          className={styles.kickoffTime}
          timeZone={timeZone}
        />
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
        <div className={styles.meta}>
          <TierChip convictionTier={game.conviction_tier} convictionLabel={game.conviction_label} />
          <RevisedMarker
            tierRevisedSincePrimary={game.tier_revised_since_primary}
            convictionTier={game.conviction_tier}
            tierPrimary={game.tier_primary}
          />
          <StaleBadge staleStamp={game.stale_stamp} sources={game.stale_sources} />
        </div>
      </div>
    </article>
  );
}
