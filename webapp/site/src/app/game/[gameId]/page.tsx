import { notFound } from "next/navigation";

import { GameDetail } from "@/components/GameDetail/GameDetail";
import { MaintenanceState } from "@/components/MaintenanceState/MaintenanceState";
import { loadArtifact, loadTeamRatingsSeason } from "@/lib/artifacts/loader";
import { isSchemaVersionSupported } from "@/lib/artifacts/schema-version";
import type { WeekPredictions } from "@/lib/artifacts/types";
import { lookupTeam, seriesForTeam } from "@/lib/game-detail/ratings";
import { projectGameDetailGame } from "@/lib/game-detail/project";
import { loadOddsPageContext } from "@/lib/odds/load";

/**
 * ISR fallback 6h (§3) — same as This Week. Primary freshness is on-demand
 * revalidation after R2 push.
 *
 * force-dynamic: R2 artifact fetch uses `cache: "no-store"`. Without this,
 * a production build that could not pre-render game paths (no R2 creds at
 * build time) throws "Page changed from static to dynamic at runtime" on
 * first `/game/[id]` request under `next start` (PROD-500).
 */
export const dynamic = "force-dynamic";
export const revalidate = 21600;

interface GamePageProps {
  params: Promise<{ gameId: string }>;
}

export async function generateStaticParams(): Promise<{ gameId: string }[]> {
  try {
    const week = await loadArtifact<WeekPredictions>("week_predictions");
    return week.games.map((game) => ({ gameId: game.game_id }));
  } catch {
    return [];
  }
}

export async function generateMetadata({ params }: GamePageProps): Promise<{ title: string }> {
  const { gameId } = await params;
  try {
    const week = await loadArtifact<WeekPredictions>("week_predictions");
    const game = week.games.find((row) => row.game_id === gameId);
    if (!game) {
      return { title: "Game not found — Ridge" };
    }
    return { title: `${game.away_team} @ ${game.home_team} — Ridge` };
  } catch {
    return { title: "Game — Ridge" };
  }
}

/**
 * Game Detail — `/game/[gameId]`.
 * game_id is the CFBD stable key (§1.2). Dynamic segment matches §5.2.
 */
export default async function GamePage({ params }: GamePageProps): Promise<React.ReactElement> {
  const { gameId } = await params;
  const week = await loadArtifact<WeekPredictions>("week_predictions");

  if (!isSchemaVersionSupported(week.schema_version)) {
    return <MaintenanceState />;
  }

  const game = week.games.find((row) => row.game_id === gameId);
  if (!game) {
    notFound();
  }

  const ratings = await loadTeamRatingsSeason(game.season);
  const homeEntry = ratings ? lookupTeam(ratings, game.home_team_id) : undefined;
  const awayEntry = ratings ? lookupTeam(ratings, game.away_team_id) : undefined;
  const ratingsUnavailable = ratings == null || (homeEntry == null && awayEntry == null);
  const homeSeries = ratingsUnavailable
    ? []
    : seriesForTeam(homeEntry, game.published_at, game.week);
  const awaySeries = ratingsUnavailable
    ? []
    : seriesForTeam(awayEntry, game.published_at, game.week);

  const oddsCtx = await loadOddsPageContext();
  const oddsGame = oddsCtx?.byGameId[game.game_id] ?? null;

  return (
    <GameDetail
      game={projectGameDetailGame(game)}
      homeSeries={homeSeries}
      awaySeries={awaySeries}
      ratingsSeason={game.season}
      ratingsUnavailable={ratingsUnavailable}
      odds={oddsGame}
      oddsMeta={
        oddsCtx
          ? {
              snapshot_at: oddsCtx.snapshot_at,
              provider: oddsCtx.provider,
              consensus_method: oddsCtx.consensus_method,
            }
          : null
      }
    />
  );
}
