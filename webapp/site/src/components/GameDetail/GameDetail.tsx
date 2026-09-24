import type { GamePrediction } from "@/lib/artifacts/types";
import type { OddsGameView, OddsPageContext } from "@/lib/odds/types";
import {
  MARGIN_INTERVAL_ABSENT_REASON,
  TOTAL_INTERVAL_ABSENT_REASON,
} from "@/lib/game-detail/absence";
import type { RatingPoint } from "@/lib/game-detail/ratings";
import { formatTotal, nullReasonFootnote } from "@/lib/formatting/numbers";

import { ForecastBlock } from "./ForecastBlock";
import { MatchupHeader } from "./MatchupHeader";
import { ModelAndMarket } from "./ModelAndMarket";
import { MoreDetail } from "./MoreDetail";
import { PlainSummary } from "./PlainSummary";
import { ProvenanceStrip } from "./ProvenanceStrip";
import { RatingTrajectoryChart } from "./RatingTrajectoryChart";
import { RevisionBlock } from "./RevisionBlock";

import styles from "./GameDetail.module.css";

type OddsMetaFields = {
  snapshot_at: OddsPageContext["snapshot_at"];
  provider: OddsPageContext["provider"];
  consensus_method: OddsPageContext["consensus_method"];
};

interface GameDetailProps {
  game: GamePrediction;
  homeSeries: RatingPoint[];
  awaySeries: RatingPoint[];
  odds?: OddsGameView | null;
  oddsMeta?: OddsMetaFields | null;
}

/**
 * Full uncertainty presentation for one game (§5.2, clarity + W-ODDS).
 *
 * Cover/over probabilities were withdrawn in schema 1.2.0 (ADR 0015) and are
 * not available to collapse. Win chance lives in the plain summary; provenance
 * sits in More detail.
 */
export function GameDetail({
  game,
  homeSeries,
  awaySeries,
  odds = null,
  oddsMeta = null,
}: GameDetailProps): React.ReactElement {
  const marginAbsentReason =
    game.margin_interval_lo == null || game.margin_interval_hi == null
      ? (nullReasonFootnote(game.null_reason) ?? MARGIN_INTERVAL_ABSENT_REASON)
      : undefined;
  const totalAbsentReason =
    game.total_interval_lo == null || game.total_interval_hi == null
      ? TOTAL_INTERVAL_ABSENT_REASON
      : undefined;
  const totalLead =
    game.mu_total != null ? `Combined score: about ${formatTotal(game.mu_total)}` : null;

  return (
    <article className={styles.page} data-testid="game-detail">
      <MatchupHeader
        awayTeam={game.away_team}
        homeTeam={game.home_team}
        kickoffUtc={game.kickoff_utc}
        neutralSite={game.neutral_site}
      />
      <PlainSummary game={game} />
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
        intervalAbsentReason={marginAbsentReason}
        homeTeam={game.home_team}
        awayTeam={game.away_team}
        pWinHome={game.p_win_home}
      />
      {odds && oddsMeta ? (
        <ModelAndMarket
          homeTeam={game.home_team}
          awayTeam={game.away_team}
          muMargin={game.mu_margin}
          marginLo={game.margin_interval_lo}
          marginHi={game.margin_interval_hi}
          muTotal={game.mu_total}
          pWinHome={game.p_win_home}
          pWinHomeCredible={game.p_win_home_credible}
          odds={odds}
          snapshotAt={oddsMeta.snapshot_at}
          consensusMethod={oddsMeta.consensus_method}
          provider={oddsMeta.provider}
        />
      ) : null}
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
        intervalAbsentReason={totalAbsentReason}
        plainLead={totalLead}
      />
      <RevisionBlock
        convictionTier={game.conviction_tier}
        convictionLabel={game.conviction_label}
        tierPrimary={game.tier_primary}
        tierRevisedSincePrimary={game.tier_revised_since_primary}
      />
      {homeSeries.length > 0 || awaySeries.length > 0 ? (
        <RatingTrajectoryChart
          homeSchool={game.home_team}
          awaySchool={game.away_team}
          home={homeSeries}
          away={awaySeries}
          throughWeek={game.week}
        />
      ) : null}
      <MoreDetail note="Numbers below are model metadata for this publish.">
        <ProvenanceStrip game={game} />
      </MoreDetail>
    </article>
  );
}
