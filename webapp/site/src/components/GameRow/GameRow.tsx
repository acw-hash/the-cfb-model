import { Figure } from "@/components/Figure/Figure";
import { IntervalBand } from "@/components/IntervalBand/IntervalBand";
import { RevisedMarker } from "@/components/RevisedMarker/RevisedMarker";
import { StaleBadge } from "@/components/StaleBadge/StaleBadge";
import { TierChip } from "@/components/TierChip/TierChip";
import type { GamePrediction, ThisWeekGame } from "@/lib/artifacts/types";
import { formatKickoffLocal } from "@/lib/formatting/time";

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
}

/**
 * D1 game row — Candidate A (operator 2026-08-20):
 * stacked away / @ home (T3), Primary rail (D1 μ + N2 interval),
 * kickoff · conviction on a compact third line (C2/C1). Uniform 77px at 390.
 */
export function GameRow({ game }: GameRowProps): React.ReactElement {
  const kickoff = formatKickoffLocal(game.kickoff_utc);
  const kickoffTitle = game.kickoff_utc == null ? undefined : `${kickoff.utc} UTC`;

  return (
    <article className={styles.row} data-testid="game-row" data-matchup-layout="stacked">
      <div className={styles.left}>
        <h3 className={styles.matchup}>
          <span className={styles.awayName}>{game.away_team}</span>
          <span className={styles.homeName}>
            <span className={styles.atSign} aria-hidden="true">
              @{" "}
            </span>
            {game.home_team}
            {game.neutral_site ? (
              <span className={styles.neutral} title="Neutral site">
                N
              </span>
            ) : null}
          </span>
        </h3>
        <div className={styles.meta}>
          <Figure variant="c2" className={styles.kickoffTime} title={kickoffTitle}>
            {kickoff.local}
          </Figure>
          <span className={styles.metaSep} aria-hidden="true">
            ·
          </span>
          <TierChip convictionTier={game.conviction_tier} convictionLabel={game.conviction_label} />
          <RevisedMarker
            tierRevisedSincePrimary={game.tier_revised_since_primary}
            convictionTier={game.conviction_tier}
            tierPrimary={game.tier_primary}
          />
          <StaleBadge staleStamp={game.stale_stamp} sources={game.stale_sources} />
        </div>
      </div>

      <div className={styles.right}>
        <IntervalBand game={game} />
      </div>
    </article>
  );
}
