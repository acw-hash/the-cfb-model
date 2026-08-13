import { notFound } from "next/navigation";

import { GameRow } from "@/components/GameRow/GameRow";
import { IntervalBand } from "@/components/IntervalBand/IntervalBand";
import { MaintenanceState } from "@/components/MaintenanceState/MaintenanceState";
import { PublishedAtStamp } from "@/components/PublishedAtStamp/PublishedAtStamp";
import { RevisedMarker } from "@/components/RevisedMarker/RevisedMarker";
import { StaleBadge } from "@/components/StaleBadge/StaleBadge";
import { StalenessBanner } from "@/components/StalenessBanner/StalenessBanner";
import { TierChip } from "@/components/TierChip/TierChip";
import { loadArtifact } from "@/lib/artifacts/loader";
import type { GamePrediction, MetaArtifact, WeekPredictions } from "@/lib/artifacts/types";

import styles from "./gallery.module.css";
import { GalleryThemeToggle } from "./GalleryThemeToggle";

export const metadata = {
  title: "Ridge — Component Gallery",
  robots: { index: false, follow: false },
};

function findGame(
  games: GamePrediction[],
  tier: GamePrediction["conviction_tier"],
): GamePrediction {
  const match = games.find((g) => g.conviction_tier === tier);
  if (!match) {
    throw new Error(`No fixture game with conviction_tier=${String(tier)}`);
  }
  return match;
}

function demoStaleGame(base: GamePrediction): GamePrediction {
  return {
    ...base,
    is_stale: true,
    stale_stamp: "STALE(odds, 4.0h)",
    stale_sources: [
      {
        source: "odds",
        age_hours: 4.0,
        last_good_at: "2024-09-24T02:00:00Z",
      },
    ],
    conviction_tier: null,
    conviction_team: null,
    conviction_label: null,
  };
}

function demoRevisedGame(base: GamePrediction): GamePrediction {
  return {
    ...base,
    tier_primary: "lean",
    tier_revised_since_primary: true,
    conviction_tier: "clear_lean",
    conviction_label: base.conviction_label,
  };
}

function demoNullForecastGame(base: GamePrediction): GamePrediction {
  return {
    ...base,
    mu_margin: null,
    sigma_margin: null,
    sigma_margin_credible: false,
    margin_interval_lo: null,
    margin_interval_hi: null,
    p_win_home: null,
    p_win_home_credible: false,
    null_reason: "cold_start_insufficient",
    conviction_tier: null,
    conviction_team: null,
    conviction_label: null,
  };
}

/** Dev-only component gallery — excluded from production via 404. */
export default async function GalleryPage(): Promise<React.ReactElement> {
  if (process.env.NODE_ENV === "production") {
    notFound();
  }

  const [meta, weekPredictions] = await Promise.all([
    loadArtifact<MetaArtifact>("meta"),
    loadArtifact<WeekPredictions>("week_predictions"),
  ]);

  const games = weekPredictions.games;
  const leanGame = findGame(games, "lean");
  const clearGame = findGame(games, "clear_lean");
  const strongGame = findGame(games, "strong_lean");
  const tossUpGame = findGame(games, "toss_up");
  const neutralGame = games.find((g) => g.neutral_site) ?? leanGame;

  const staleDemo = demoStaleGame(leanGame);
  const revisedDemo = demoRevisedGame(clearGame);
  const nullDemo = demoNullForecastGame(tossUpGame);

  const staleMeta: MetaArtifact = {
    ...meta,
    published_at: "2024-09-20T06:00:00Z",
    next_expected_publish_utc: "2024-09-21T06:00:00Z",
  };

  return (
    <main className={styles.page} data-testid="gallery-root">
      <header className={styles.header}>
        <h1 className={styles.title}>Ridge Component Gallery</h1>
        <PublishedAtStamp publishedAt={meta.published_at} />
        <GalleryThemeToggle />
      </header>

      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>Game rows — fixture week {weekPredictions.week}</h2>
        <div className={styles.list}>
          <GameRow game={strongGame} />
          <GameRow game={clearGame} />
          <GameRow game={leanGame} />
          <GameRow game={tossUpGame} />
          <GameRow game={neutralGame} />
        </div>
      </section>

      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>Tier chips — conviction_label verbatim</h2>
        <div className={styles.inlineRow}>
          <TierChip convictionTier="strong_lean" convictionLabel={strongGame.conviction_label} />
          <TierChip convictionTier="clear_lean" convictionLabel={clearGame.conviction_label} />
          <TierChip convictionTier="lean" convictionLabel={leanGame.conviction_label} />
          <TierChip convictionTier="toss_up" convictionLabel={tossUpGame.conviction_label} />
          <TierChip convictionTier={null} convictionLabel={null} />
          <span className={styles.note}>(suppressed — no chip)</span>
        </div>
      </section>

      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>Interval bands</h2>
        <div className={styles.inlineRow}>
          <IntervalBand game={leanGame} />
          <IntervalBand game={nullDemo} />
        </div>
      </section>

      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>Badges &amp; markers</h2>
        <div className={styles.inlineRow}>
          <StaleBadge staleStamp={staleDemo.stale_stamp} sources={staleDemo.stale_sources} />
          <RevisedMarker
            tierRevisedSincePrimary={revisedDemo.tier_revised_since_primary}
            convictionTier={revisedDemo.conviction_tier}
            tierPrimary={revisedDemo.tier_primary}
          />
        </div>
        <GameRow game={staleDemo} />
        <GameRow game={revisedDemo} />
      </section>

      <section className={styles.section}>
        <h2 className={styles.sectionTitle}>Layout states (doctored meta)</h2>
        <div className={styles.statePanel}>
          <h3 className={styles.subTitle}>Site staleness banner</h3>
          <StalenessBanner publishedAt={staleMeta.published_at} />
        </div>
        <div className={styles.statePanel}>
          <h3 className={styles.subTitle}>Maintenance state</h3>
          <div className={styles.maintenanceFrame}>
            <MaintenanceState />
          </div>
        </div>
      </section>
    </main>
  );
}
