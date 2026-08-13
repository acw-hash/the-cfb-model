import { notFound } from "next/navigation";

import { GameDetail } from "@/components/GameDetail/GameDetail";
import { MaintenanceState } from "@/components/MaintenanceState/MaintenanceState";
import { loadArtifact } from "@/lib/artifacts/loader";
import { isSchemaVersionSupported } from "@/lib/artifacts/schema-version";
import type { TeamRatings, WeekPredictions } from "@/lib/artifacts/types";
import { lookupTeam, seriesForTeam } from "@/lib/game-detail/ratings";

/**
 * ISR fallback 6h (§3) — same as This Week. Primary freshness is on-demand
 * revalidation after R2 push.
 */
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

  let homeSeries = seriesForTeam(undefined, game.published_at, game.week);
  let awaySeries = homeSeries;
  try {
    const ratings = await loadArtifact<TeamRatings>("team_ratings_2024");
    homeSeries = seriesForTeam(
      lookupTeam(ratings, game.home_team_id),
      game.published_at,
      game.week,
    );
    awaySeries = seriesForTeam(
      lookupTeam(ratings, game.away_team_id),
      game.published_at,
      game.week,
    );
  } catch {
    homeSeries = [];
    awaySeries = [];
  }

  return <GameDetail game={game} homeSeries={homeSeries} awaySeries={awaySeries} />;
}
