import { notFound } from "next/navigation";

import { GameRow } from "@/components/GameRow/GameRow";
import { OffseasonState } from "@/components/OffseasonState/OffseasonState";
import { ThisWeekHeader } from "@/components/ThisWeekHeader/ThisWeekHeader";
import { ThisWeekSlate } from "@/components/ThisWeekSlate/ThisWeekSlate";
import { loadArtifact } from "@/lib/artifacts/loader";
import type { GamePrediction, MetaArtifact, WeekPredictions } from "@/lib/artifacts/types";
import {
  cloneEmptyTopTiers,
  cloneOffseason,
  cloneStale,
  cloneSuppressed,
  cloneTwoBandRevision,
} from "@/lib/this-week/demo-states";

import { GalleryThemeToggle } from "../GalleryThemeToggle";
import styles from "../gallery.module.css";

export const metadata = {
  title: "Ridge — This Week states",
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

/** Dev-only doctored-clone gallery for W3-3 states. Fixtures on disk are untouched. */
export default async function ThisWeekStatesPage(): Promise<React.ReactElement> {
  if (process.env.NODE_ENV === "production") {
    notFound();
  }

  const [meta, week] = await Promise.all([
    loadArtifact<MetaArtifact>("meta"),
    loadArtifact<WeekPredictions>("week_predictions"),
  ]);

  const emptyTop = cloneEmptyTopTiers(week.games);
  const offseason = cloneOffseason(week);
  const leanGames = week.games.filter((game) => game.conviction_tier === "lean");
  const staleBase = leanGames[0];
  const revisedBase = leanGames[1];
  if (!staleBase || !revisedBase) {
    throw new Error("Fixture week needs two lean games for stale/revised clones");
  }
  const staleRow = cloneStale(staleBase);
  const revisedRow = cloneTwoBandRevision(revisedBase);
  const suppressedRow = cloneSuppressed(
    requireGame(week.games, (game) => game.conviction_tier === "toss_up", "toss_up"),
  );

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <h1 className={styles.title}>This Week states (doctored clones)</h1>
        <GalleryThemeToggle />
      </header>

      <p className={styles.note}>
        Clones are in-memory. Committed fixtures are not modified. Numbers on cloned rows are
        fixture-verbatim except honest-absence nulls.
      </p>

      <section className={styles.section} data-testid="empty-top-tiers">
        <h2 className={styles.sectionTitle}>Empty top tiers (BY CONVICTION)</h2>
        <ThisWeekSlate
          season={meta.season}
          week={meta.week}
          publishedAt={meta.published_at}
          refreshKind={meta.refresh_kind}
          games={emptyTop}
          initialOrder="conviction"
        />
      </section>

      <section className={styles.section} data-testid="stale-revised">
        <h2 className={styles.sectionTitle}>Stale + revised + suppressed</h2>
        <div className={styles.list}>
          <GameRow game={staleRow} />
          <GameRow game={revisedRow} />
          <GameRow game={suppressedRow} />
        </div>
      </section>

      <section className={styles.section} data-testid="offseason">
        <h2 className={styles.sectionTitle}>Offseason / no slate</h2>
        <ThisWeekHeader
          season={meta.season}
          week={meta.week}
          publishedAt={meta.published_at}
          refreshKind={meta.refresh_kind}
        />
        {offseason.games.length === 0 ? <OffseasonState /> : null}
      </section>
    </main>
  );
}
