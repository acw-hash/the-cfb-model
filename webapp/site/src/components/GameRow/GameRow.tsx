import { Figure } from "@/components/Figure/Figure";
import { IntervalBand } from "@/components/IntervalBand/IntervalBand";
import { KickoffTime } from "@/components/KickoffTime/KickoffTime";
import { RevisedMarker } from "@/components/RevisedMarker/RevisedMarker";
import { StaleBadge } from "@/components/StaleBadge/StaleBadge";
import { TierChip } from "@/components/TierChip/TierChip";
import type { GamePrediction, ThisWeekGame } from "@/lib/artifacts/types";
import { formatProbability } from "@/lib/formatting/numbers";
import { favoredWinProbability } from "@/lib/formatting/team-margin";

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
  | "p_win_home"
  | "p_win_home_credible"
  | "sigma_margin_credible"
>;

interface GameRowProps {
  game: GameRowModel | GamePrediction;
  /** Test injection — production resolves visitor TZ inside KickoffTime. */
  timeZone?: string;
}

function rowWinChance(game: GameRowModel | GamePrediction): string | null {
  if (!game.sigma_margin_credible || !game.p_win_home_credible || game.p_win_home == null) {
    return null;
  }
  const pFav = favoredWinProbability(game.mu_margin, game.p_win_home);
  return formatProbability(pFav);
}

/** Scores-app density game row (§4.3, clarity). */
export function GameRow({ game, timeZone }: GameRowProps): React.ReactElement {
  const matchup = `${game.away_team} @ ${game.home_team}`;
  const winChance = rowWinChance(game);

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
        <IntervalBand game={game} showRange={false} />
        {winChance ? (
          <Figure variant="n2" className={styles.winChance} data-testid="row-win-chance">
            {winChance}
          </Figure>
        ) : null}
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
