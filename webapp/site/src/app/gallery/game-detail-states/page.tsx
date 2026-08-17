import Link from "next/link";

import { GameDetail } from "@/components/GameDetail/GameDetail";
import { loadArtifact } from "@/lib/artifacts/loader";
import type { GamePrediction, TeamRatings, WeekPredictions } from "@/lib/artifacts/types";
import {
  cloneNullTotalInterval,
  cloneRatingsMissingWeek,
  cloneStale,
  cloneSuppressedSigma,
  cloneTwoBandRevision,
} from "@/lib/game-detail/demo-states";
import { lookupTeam, seriesForTeam } from "@/lib/game-detail/ratings";
import { projectGameDetailGame } from "@/lib/game-detail/project";

import { assertGalleryAllowed } from "../gallery-gate";
import { GalleryThemeToggle } from "../GalleryThemeToggle";
import styles from "../gallery.module.css";

export const metadata = {
  title: "Ridge — Game Detail states",
  robots: { index: false, follow: false },
};

function requireGame(
  games: GamePrediction[],
  predicate: (game: GamePrediction) => boolean,
  label: string,
): GamePrediction {
  const match = games.find(predicate);
  if (!match) {
    throw new Error(`No fixture game for ${label}`);
  }
  return match;
}

function seriesPair(
  ratings: TeamRatings,
  game: GamePrediction,
): { home: ReturnType<typeof seriesForTeam>; away: ReturnType<typeof seriesForTeam> } {
  return {
    home: seriesForTeam(lookupTeam(ratings, game.home_team_id), game.published_at, game.week),
    away: seriesForTeam(lookupTeam(ratings, game.away_team_id), game.published_at, game.week),
  };
}

/** Dev-only doctored-clone gallery for W4-5. Fixtures on disk are untouched. */
export default async function GameDetailStatesPage(): Promise<React.ReactElement> {
  assertGalleryAllowed();

  const [week, ratings] = await Promise.all([
    loadArtifact<WeekPredictions>("week_predictions"),
    loadArtifact<TeamRatings>("team_ratings_2024"),
  ]);

  const leanGames = week.games.filter((game) => game.conviction_tier === "lean");
  const twoBandBase = leanGames[0];
  const staleBase = leanGames[1];
  if (!twoBandBase || !staleBase) {
    throw new Error("Fixture week needs two lean games for W4 clones");
  }

  const twoBand = cloneTwoBandRevision(twoBandBase);
  const stale = cloneStale(staleBase);
  const suppressed = cloneSuppressedSigma(
    requireGame(week.games, (game) => game.conviction_tier === "toss_up", "toss_up"),
  );
  const nullTotal = cloneNullTotalInterval(twoBandBase);
  const gappedRatings = cloneRatingsMissingWeek(
    ratings,
    twoBandBase.home_team_id,
    twoBandBase.away_team_id,
    3,
  );

  const twoBandSeries = seriesPair(ratings, twoBand);
  const staleSeries = seriesPair(ratings, stale);
  const suppressedSeries = seriesPair(ratings, suppressed);
  const gappedSeries = seriesPair(gappedRatings, twoBandBase);

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <h1 className={styles.title}>Game Detail states (doctored clones)</h1>
        <GalleryThemeToggle />
      </header>

      <p className={styles.note}>
        Clones are in-memory. Committed fixtures are not modified. Labeled as doctored in this
        gallery only.
      </p>

      <section className={styles.section} data-testid="state-two-band">
        <h2 className={styles.sectionTitle}>Two-band revision (strong_lean → lean)</h2>
        <p className={styles.note}>
          Doctored: tier_primary=strong_lean, current fixture-verbatim lean. No clear_lean step.
        </p>
        <GameDetail
          game={projectGameDetailGame(twoBand)}
          homeSeries={twoBandSeries.home}
          awaySeries={twoBandSeries.away}
        />
      </section>

      <section className={styles.section} data-testid="state-suppressed">
        <h2 className={styles.sectionTitle}>Suppressed sigma</h2>
        <p className={styles.note}>
          Doctored: sigma_margin_credible=false; probabilities and tier absent.
        </p>
        <GameDetail
          game={projectGameDetailGame(suppressed)}
          homeSeries={suppressedSeries.home}
          awaySeries={suppressedSeries.away}
        />
      </section>

      <section className={styles.section} data-testid="state-stale">
        <h2 className={styles.sectionTitle}>Stale game</h2>
        <p className={styles.note}>Doctored: stale_stamp=STALE(odds, 4.0h).</p>
        <GameDetail
          game={projectGameDetailGame(stale)}
          homeSeries={staleSeries.home}
          awaySeries={staleSeries.away}
        />
      </section>

      <section className={styles.section} data-testid="state-null-total">
        <h2 className={styles.sectionTitle}>Null total interval</h2>
        <p className={styles.note}>
          v1 export: total_interval_* are null on every fixture game. Clone labeled; numbers
          fixture-verbatim.
        </p>
        <GameDetail
          game={projectGameDetailGame(nullTotal)}
          homeSeries={twoBandSeries.home}
          awaySeries={twoBandSeries.away}
        />
      </section>

      <section className={styles.section} data-testid="state-gapped-ratings">
        <h2 className={styles.sectionTitle}>Missing mid-season rating week</h2>
        <p className={styles.note}>Doctored ratings clone: week 3 removed for both teams.</p>
        <GameDetail
          game={projectGameDetailGame(twoBandBase)}
          homeSeries={gappedSeries.home}
          awaySeries={gappedSeries.away}
        />
      </section>

      <section className={styles.section} data-testid="state-unknown-note">
        <h2 className={styles.sectionTitle}>Unknown game_id</h2>
        <p className={styles.note}>
          Route <Link href="/game/not-a-real-id">/game/not-a-real-id</Link> renders the in-layout
          not-found state (not a redirect to /).
        </p>
      </section>
    </main>
  );
}
