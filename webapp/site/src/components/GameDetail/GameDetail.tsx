import type { GamePrediction } from "@/lib/artifacts/types";
import {
  MARGIN_INTERVAL_ABSENT_REASON,
  TOTAL_INTERVAL_ABSENT_REASON,
} from "@/lib/game-detail/absence";
import type { RatingPoint } from "@/lib/game-detail/ratings";

import { ForecastBlock } from "./ForecastBlock";
import { MatchupHeader } from "./MatchupHeader";
import { ProbabilityList } from "./ProbabilityList";
import { ProvenanceStrip } from "./ProvenanceStrip";
import { RatingTrajectoryChart } from "./RatingTrajectoryChart";
import { RevisionBlock } from "./RevisionBlock";

import styles from "./GameDetail.module.css";

interface GameDetailProps {
  game: GamePrediction;
  homeSeries: RatingPoint[];
  awaySeries: RatingPoint[];
}

/** Full uncertainty presentation for one game (§5.2). */
export function GameDetail({ game, homeSeries, awaySeries }: GameDetailProps): React.ReactElement {
  return (
    <article className={styles.page} data-testid="game-detail">
      <MatchupHeader
        awayTeam={game.away_team}
        homeTeam={game.home_team}
        kickoffUtc={game.kickoff_utc}
        neutralSite={game.neutral_site}
      />
      <ForecastBlock
        label="Margin"
        billing="primary"
        mu={game.mu_margin}
        sigma={game.sigma_margin}
        lo={game.margin_interval_lo}
        hi={game.margin_interval_hi}
        nominal={game.margin_interval_nominal}
        signed
        nullReason={game.null_reason}
        intervalAbsentReason={
          game.margin_interval_lo == null || game.margin_interval_hi == null
            ? MARGIN_INTERVAL_ABSENT_REASON
            : undefined
        }
      />
      <ForecastBlock
        label="Total"
        billing="secondary"
        mu={game.mu_total}
        sigma={game.sigma_total}
        lo={game.total_interval_lo}
        hi={game.total_interval_hi}
        nominal={game.total_interval_nominal}
        signed={false}
        nullReason={game.null_reason}
        intervalAbsentReason={
          game.total_interval_lo == null || game.total_interval_hi == null
            ? TOTAL_INTERVAL_ABSENT_REASON
            : undefined
        }
      />
      <ProbabilityList game={game} />
      <RevisionBlock
        convictionTier={game.conviction_tier}
        convictionLabel={game.conviction_label}
        tierPrimary={game.tier_primary}
        tierRevisedSincePrimary={game.tier_revised_since_primary}
      />
      <RatingTrajectoryChart
        homeSchool={game.home_team}
        awaySchool={game.away_team}
        home={homeSeries}
        away={awaySeries}
        throughWeek={game.week}
      />
      <ProvenanceStrip game={game} />
    </article>
  );
}
