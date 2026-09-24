import { Figure } from "@/components/Figure/Figure";
import type { OddsGameView } from "@/lib/odds/types";
import {
  formatConsensusSpreadBook,
  formatMarketTeamNamedMargin,
  formatMarketTotal,
} from "@/lib/formatting/market-margin";
import { formatProbability, formatTotal } from "@/lib/formatting/numbers";
import { formatTeamNamedMargin } from "@/lib/formatting/team-margin";

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
}

/**
 * Game Detail “Model and market” block — after Margin, before trajectories.
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
}: ModelAndMarketProps): React.ReactElement {
  const modelMargin = formatTeamNamedMargin(muMargin, homeTeam, awayTeam, null, pWinHome) ?? "—";
  const marketMargin = formatMarketTeamNamedMargin(
    odds.market_home_margin,
    homeTeam,
    awayTeam,
    odds.p_win_home_market,
  );
  const modelTotal = muTotal != null ? (formatTotal(muTotal) ?? "—") : "—";
  const marketTotal = formatMarketTotal(odds.total_points);
  const modelWin =
    pWinHomeCredible && pWinHome != null ? (formatProbability(pWinHome) ?? "—") : "—";
  const marketWin =
    odds.p_win_home_market != null ? (formatProbability(odds.p_win_home_market) ?? "—") : "—";
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
            <th scope="row">Expected margin</th>
            <td>
              <Figure variant="n1">{modelMargin}</Figure>
            </td>
            <td>
              <Figure variant="n2" className={styles.market}>
                {marketMargin}
              </Figure>
            </td>
          </tr>
          {bookSpread ? (
            <tr className={styles.sub}>
              <th scope="row" />
              <td />
              <td className={styles.note}>
                Consensus spread: {bookSpread}
                {odds.spread_book_count > 0 ? ` · ${odds.spread_book_count} books` : ""}
              </td>
            </tr>
          ) : null}
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
          <tr>
            <th scope="row">Win probability (home)</th>
            <td>
              <Figure variant="n2">{modelWin}</Figure>
            </td>
            <td>
              <Figure variant="n2" className={styles.market}>
                {marketWin}
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
          Last pre-kickoff snapshot.
        </p>
      ) : null}

      <p className={styles.footnote}>
        Consensus: {consensusMethod.replace(/_/g, " ")}. Snapshot {snapshotAt}. {provider}{" "}
        (the-odds-api.com). Consensus figures are shown for context. Ridge does not compare them to
        its forecasts to suggest wagers — see <a href="/results">Track record</a>.
      </p>
    </section>
  );
}
