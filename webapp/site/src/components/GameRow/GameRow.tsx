import { Figure } from "@/components/Figure/Figure";
import { KickoffTime } from "@/components/KickoffTime/KickoffTime";
import { RevisedMarker } from "@/components/RevisedMarker/RevisedMarker";
import grid from "@/components/ScoreboardGrid/ScoreboardGrid.module.css";
import { StaleBadge } from "@/components/StaleBadge/StaleBadge";
import type { GamePrediction, ThisWeekGame } from "@/lib/artifacts/types";
import { formatConsensusSpreadBook } from "@/lib/formatting/market-margin";
import type { OddsGameView } from "@/lib/odds/types";
import {
  buildGameRowAriaLabel,
  buildScoreboardPlacement,
  type ScoreboardGroupPlacement,
} from "@/lib/this-week/scoreboard";

import styles from "./GameRow.module.css";

type GameRowModel = Pick<
  ThisWeekGame,
  | "kickoff_utc"
  | "away_team"
  | "home_team"
  | "neutral_site"
  | "mu_margin"
  | "sigma_margin"
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
  /** Optional market snapshot for this game (ODDS_SNAPSHOT_ENABLED). */
  odds?: OddsGameView | null;
  /** Test injection — production resolves visitor TZ inside KickoffTime. */
  timeZone?: string;
}

function MarginFigure({
  value,
  emphasis,
  testId,
  title,
}: {
  value: string;
  emphasis: "model" | "market";
  testId?: string;
  title?: string;
}): React.ReactElement {
  return (
    <Figure
      variant={emphasis === "model" ? "n1" : "n2"}
      className={emphasis === "model" ? styles.modelMargin : styles.marketNum}
      data-testid={testId}
      title={title}
    >
      {value}
    </Figure>
  );
}

function WinFigure({ value, testId }: { value: string; testId?: string }): React.ReactElement {
  return (
    <Figure variant="n2" className={styles.winNum} data-testid={testId}>
      {value}
    </Figure>
  );
}

/** One numeric column: away line / home line, or PK centered. */
function ValueColumn({
  placement,
  field,
  emphasis,
  group,
  marginTitle,
}: {
  placement: ScoreboardGroupPlacement;
  field: "margin" | "win";
  emphasis: "model" | "market";
  group: "model" | "market";
  marginTitle?: string;
}): React.ReactElement {
  const testId = `${group}-${field}`;
  if (placement.mid) {
    const midVal = field === "margin" ? (placement.mid.margin ?? "PK") : placement.mid.win;
    return (
      <div
        className={styles.pkCol}
        data-testid={field === "margin" ? `${group}-pk` : undefined}
        role="cell"
      >
        {midVal ? (
          field === "margin" ? (
            <MarginFigure value={midVal} emphasis={emphasis} testId={testId} title={marginTitle} />
          ) : (
            <WinFigure value={midVal} testId={testId} />
          )
        ) : (
          <span className={styles.numEmpty} aria-hidden="true" />
        )}
      </div>
    );
  }

  const awayVal = placement.away[field];
  const homeVal =
    field === "margin"
      ? (placement.home.margin ?? (placement.nullDash ? "—" : null))
      : placement.home.win;

  return (
    <div className={styles.colStack} role="cell">
      <div className={styles.colCell}>
        {awayVal ? (
          field === "margin" ? (
            <MarginFigure value={awayVal} emphasis={emphasis} testId={testId} title={marginTitle} />
          ) : (
            <WinFigure value={awayVal} testId={testId} />
          )
        ) : (
          <span className={styles.numEmpty} aria-hidden="true" />
        )}
      </div>
      <div className={styles.colCell}>
        {homeVal ? (
          field === "margin" ? (
            <MarginFigure
              value={homeVal}
              emphasis={emphasis}
              testId={testId}
              title={homeVal !== "—" ? marginTitle : undefined}
            />
          ) : (
            <WinFigure value={homeVal} testId={testId} />
          )
        ) : (
          <span className={styles.numEmpty} aria-hidden="true" />
        )}
      </div>
    </div>
  );
}

/** Scoreboard-density game row — unsigned figures beside the favored team. */
export function GameRow({ game, odds, timeZone }: GameRowProps): React.ReactElement {
  const showMarket = Boolean(odds);
  const placement = buildScoreboardPlacement({
    muMargin: game.mu_margin,
    sigmaMargin: game.sigma_margin,
    pWinHome: game.p_win_home,
    pWinHomeCredible: game.p_win_home_credible,
    sigmaMarginCredible: game.sigma_margin_credible,
    marketHomeMargin: odds?.market_home_margin,
    pWinHomeMarket: odds?.p_win_home_market,
    convictionTier: game.conviction_tier,
    showMarket,
  });

  const ariaLabel = buildGameRowAriaLabel({
    awayTeam: game.away_team,
    homeTeam: game.home_team,
    kickoffUtc: game.kickoff_utc,
    timeZone,
    muMargin: game.mu_margin,
    sigmaMargin: game.sigma_margin,
    pWinHome: game.p_win_home,
    pWinHomeCredible: game.p_win_home_credible,
    sigmaMarginCredible: game.sigma_margin_credible,
    convictionTier: game.conviction_tier,
    marketHomeMargin: odds?.market_home_margin,
    pWinHomeMarket: odds?.p_win_home_market,
    spreadHomePoints: odds?.spread_home_points,
    showMarket,
  });

  const marketSpreadTitle = odds
    ? (() => {
        const book = formatConsensusSpreadBook(odds.spread_home_points, game.home_team);
        return book ? `Consensus spread: ${book}` : undefined;
      })()
    : undefined;

  const revised = (
    <RevisedMarker
      tierRevisedSincePrimary={game.tier_revised_since_primary}
      convictionTier={game.conviction_tier}
      tierPrimary={game.tier_primary}
    />
  );
  const stale = <StaleBadge staleStamp={game.stale_stamp} sources={game.stale_sources} />;

  const tierOnAway =
    placement.tierWord != null && !placement.tierCentered && placement.tierSide === "away";
  const tierOnHome =
    placement.tierWord != null && !placement.tierCentered && placement.tierSide === "home";
  const tierMid = placement.tierWord != null && placement.tierCentered;

  const tierCluster = (word: string): React.ReactElement => (
    <span className={styles.tierCluster} data-testid="tier-short">
      <span className={styles.tierWord}>{word}</span>
      {revised}
      {stale}
    </span>
  );

  const rowClass = [
    showMarket ? grid.withMarket : grid.modelOnly,
    styles.row,
    odds?.carried_forward ? styles.carriedRow : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <article className={rowClass} data-testid="game-row" role="row" aria-label={ariaLabel}>
      <div className={styles.mobileMeta} data-testid="mobile-meta">
        <KickoffTime
          kickoffUtc={game.kickoff_utc}
          variant="c2"
          className={styles.kickoffTime}
          timeOnly
          timeZone={timeZone}
        />
        {placement.tierWord ? (
          <>
            <span className={styles.metaSep} aria-hidden="true">
              ·
            </span>
            <span className={styles.tierWord} data-testid="tier-short-mobile">
              {placement.tierWord}
            </span>
            {revised}
          </>
        ) : null}
        {stale}
      </div>

      <div className={styles.kickoffDesktop} role="cell">
        <KickoffTime
          kickoffUtc={game.kickoff_utc}
          variant="c2"
          className={styles.kickoffTime}
          timeOnly
          timeZone={timeZone}
        />
      </div>

      <div className={styles.teams} role="cell">
        <div className={styles.teamLine}>
          <span className={styles.teamName}>
            {game.away_team}
            {game.neutral_site ? (
              <span className={styles.neutral} title="Neutral site">
                N
              </span>
            ) : null}
          </span>
        </div>
        <div className={styles.teamLine}>
          <span className={styles.teamName}>{game.home_team}</span>
        </div>
      </div>

      <div className={styles.modelMarginCol} data-testid="model-group">
        <ValueColumn placement={placement.model} field="margin" emphasis="model" group="model" />
      </div>

      <div className={styles.modelWinCol}>
        <ValueColumn placement={placement.model} field="win" emphasis="model" group="model" />
      </div>

      <div className={styles.tierGroup} role="cell" data-testid="tier-group">
        {tierMid && placement.tierWord ? (
          <div className={styles.tierMid}>{tierCluster(placement.tierWord)}</div>
        ) : (
          <div className={styles.tierStack}>
            <div className={styles.tierLine}>
              {tierOnAway && placement.tierWord ? tierCluster(placement.tierWord) : null}
            </div>
            <div className={styles.tierLine}>
              {tierOnHome && placement.tierWord ? tierCluster(placement.tierWord) : null}
            </div>
          </div>
        )}
      </div>

      {showMarket ? <div className={styles.rule} aria-hidden="true" /> : null}

      {showMarket ? (
        <div
          className={styles.marketMarginCol}
          data-testid="market-cell"
          data-carried-forward={odds?.carried_forward ? "true" : "false"}
        >
          <ValueColumn
            placement={placement.market!}
            field="margin"
            emphasis="market"
            group="market"
            marginTitle={marketSpreadTitle}
          />
          {odds?.carried_forward ? (
            <span className={styles.carried} data-testid="carried-forward-label">
              Last pre-kickoff snapshot
            </span>
          ) : null}
        </div>
      ) : null}

      {showMarket ? (
        <div className={styles.marketWinCol}>
          <ValueColumn placement={placement.market!} field="win" emphasis="market" group="market" />
        </div>
      ) : null}
    </article>
  );
}
