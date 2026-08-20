import { MaintenanceState } from "@/components/MaintenanceState/MaintenanceState";
import { OffseasonState, PreFirstPublishState } from "@/components/OffseasonState/OffseasonState";
import { ThisWeekHeader } from "@/components/ThisWeekHeader/ThisWeekHeader";
import { ThisWeekSlate } from "@/components/ThisWeekSlate/ThisWeekSlate";
import { loadArtifact } from "@/lib/artifacts/loader";
import { isSchemaVersionSupported } from "@/lib/artifacts/schema-version";
import type { MetaArtifact, WeekPredictions } from "@/lib/artifacts/types";
import { projectThisWeekGames } from "@/lib/this-week/project";
import { DEFAULT_SLATE_ORDER } from "@/lib/this-week/sort";

import styles from "./this-week.module.css";

/**
 * Dynamic via root layout (PROD-500). Page also no-store-fetches meta +
 * week_predictions; revalidate export retained as documentation of the §3
 * 6h fallback intent for a future ISR restore (PROD-500-ISR).
 */
export const revalidate = 21600;

export const metadata = {
  title: "This Week — Ridge",
  description: "College football forecasts with uncertainty",
};

export default async function ThisWeekPage(): Promise<React.ReactElement> {
  const meta = await loadArtifact<MetaArtifact>("meta");

  let week: WeekPredictions;
  try {
    week = await loadArtifact<WeekPredictions>("week_predictions");
  } catch {
    return (
      <main className={styles.page}>
        <PreFirstPublishState />
      </main>
    );
  }

  if (!isSchemaVersionSupported(week.schema_version)) {
    return <MaintenanceState />;
  }

  if (week.games.length === 0) {
    return (
      <main className={styles.page} data-testid="this-week-root">
        <ThisWeekHeader
          season={meta.season}
          week={meta.week}
          publishedAt={meta.published_at}
          refreshKind={meta.refresh_kind}
        />
        <OffseasonState />
      </main>
    );
  }

  return (
    <main className={styles.page} data-testid="this-week-root">
      <ThisWeekSlate
        season={meta.season}
        week={meta.week}
        publishedAt={meta.published_at}
        refreshKind={meta.refresh_kind}
        games={projectThisWeekGames(week.games)}
        initialOrder={DEFAULT_SLATE_ORDER}
        syncUrl
      />
    </main>
  );
}
