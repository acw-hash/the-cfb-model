import { Figure } from "@/components/Figure/Figure";
import { OddsTimestamp } from "@/components/OddsTimestamp/OddsTimestamp";
import type { OddsGameView } from "@/lib/odds/types";
import {
  formatConsensusSpreadBook,
  formatMarketTotal,
  formatUnsignedMarketMargin,
} from "@/lib/formatting/market-margin";
import { ABSENT, formatProbability, formatTotal } from "@/lib/formatting/numbers";
import {
  favoredSideFromMargin,
  favoredWinProbability,
  formatUnsignedMargin,
} from "@/lib/formatting/team-margin";

import { MarginNumberLine } from "./MarginNumberLine";

import styles from "./ModelAndMarket.module.css";

interface ModelAndMarketProps {
  homeTeam: string;
  awayTeam: string;
  muMargin: number | null;
  marginLo: number | null;
  marginHi: number | null;
  muTotal: number | null;
  pWinHome: number | null;
  pWinHomeCredible: boolean;
  odds: OddsGameView;
  snapshotAt: string;
  consensusMethod: string;
  provider: string;
  timeZone?: string;
}

function cellUnsigned(value: string | null): string {
  if (value == null) {
    return ABSENT;
  }
  return value;
}

/**
 * Game Detail “Model and market” block — unsigned figures; team names stay
 * in the matchup header. Footnotes collapse behind “About these odds”.
 */
export function ModelAndMarket({
  homeTeam,
  awayTeam,
  muMargin,
  marginLo,
  marginHi,
  muTotal,
  pWinHome,
  pWinHomeCredible,
  odds,
  snapshotAt,
  consensusMethod,
  provider,
  timeZone,
}: ModelAndMarketProps): React.ReactElement {
  const modelMargin = cellUnsigned(formatUnsignedMargin(muMargin, null));
  const marketMargin = cellUnsigned(formatUnsignedMarketMargin(odds.market_home_margin));
  const modelTotal = muTotal != null ? (formatTotal(muTotal) ?? ABSENT) : ABSENT;
  const marketTotal = formatMarketTotal(odds.total_points);

  const modelWinRaw =
    pWinHomeCredible && pWinHome != null ? favoredWinProbability(muMargin, pWinHome) : null;
  const modelWin = formatProbability(modelWinRaw) ?? ABSENT;

  const marketSide = favoredSideFromMargin(odds.market_home_margin, odds.p_win_home_market);
  let marketWin = ABSENT;
  if (odds.p_win_home_market != null && Number.isFinite(odds.p_win_home_market)) {
    if (odds.market_home_margin == null || !Number.isFinite(odds.market_home_margin)) {
      marketWin = formatProbability(odds.p_win_home_market) ?? ABSENT;
    } else if (marketSide === "away") {
      marketWin = formatProbability(1 - odds.p_win_home_market) ?? ABSENT;
    } else {
      marketWin = formatProbability(odds.p_win_home_market) ?? ABSENT;
    }
  }

  const bookSpread = formatConsensusSpreadBook(odds.spread_home_points, homeTeam);

  return (
    <section className={styles.block} data-testid="model-and-market">
      <h2 className={styles.title}>Model and market</h2>
      <table className={styles.table}>
        <thead>
          <tr>
            <th scope="col" />
            <th scope="col">Model</th>
            <th scope="col">Market</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <th scope="row">Margin</th>
            <td>
              <Figure variant="n1" className={styles.modelEmphasis}>
                {modelMargin}
              </Figure>
            </td>
            <td>
              <Figure variant="n2" className={styles.market}>
                {marketMargin}
              </Figure>
            </td>
          </tr>
          <tr>
            <th scope="row">Win %</th>
            <td>
              <Figure variant="n2">{modelWin}</Figure>
            </td>
            <td>
              <Figure variant="n2" className={styles.market}>
                {marketWin}
              </Figure>
            </td>
          </tr>
          <tr>
            <th scope="row">Total</th>
            <td>
              <Figure variant="n2">{modelTotal}</Figure>
            </td>
            <td>
              <Figure variant="n2" className={styles.market}>
                {marketTotal}
              </Figure>
            </td>
          </tr>
        </tbody>
      </table>

      <MarginNumberLine
        homeTeam={homeTeam}
        awayTeam={awayTeam}
        mu={muMargin}
        lo={marginLo}
        hi={marginHi}
        market={odds.market_home_margin}
      />

      {odds.carried_forward ? (
        <p className={styles.carried} data-testid="carried-forward-label">
          Last pre-kickoff snapshot
          {odds.captured_at ? (
            <>
              {" · "}
              <OddsTimestamp iso={odds.captured_at} timeZone={timeZone} />.
            </>
          ) : (
            "."
          )}
        </p>
      ) : null}

      <p className={styles.attribution} data-testid="odds-attribution">
        Odds: {provider} · as of <OddsTimestamp iso={snapshotAt} timeZone={timeZone} />
      </p>

      <details className={styles.about}>
        <summary className={styles.aboutSummary}>About these odds</summary>
        <div className={styles.aboutBody}>
          {bookSpread ? (
            <p className={styles.note}>
              Consensus spread: {bookSpread}
              {odds.spread_book_count > 0 ? ` · ${odds.spread_book_count} books` : ""}
              {odds.total_book_count > 0 ? ` · totals ${odds.total_book_count} books` : ""}
              {odds.h2h_book_count > 0 ? ` · moneyline ${odds.h2h_book_count} books` : ""}
            </p>
          ) : null}
          <p className={styles.footnote}>
            Consensus: {consensusMethod.replace(/_/g, " ")}. Snapshot{" "}
            <OddsTimestamp iso={snapshotAt} timeZone={timeZone} />. {provider} (the-odds-api.com).
            Consensus figures are shown for context. Ridge does not compare them to its forecasts to
            suggest wagers — see <a href="/results">Track record</a>.
          </p>
        </div>
      </details>
    </section>
  );
}
