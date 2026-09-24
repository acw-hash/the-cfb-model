import { AboutPage, selectAboutExampleGame } from "@/components/About/AboutPage";
import { loadArtifact } from "@/lib/artifacts/loader";
import type { MetaArtifact, WeekPredictions } from "@/lib/artifacts/types";

/**
 * Dynamic via root layout (PROD-500). About loads meta + week_predictions for
 * the live worked example and publish schedule.
 */
export const revalidate = 21600;

export const metadata = {
  title: "About — Ridge",
  description: "How Ridge forecasts work, data honesty, disclaimers, and responsible gambling",
};

/**
 * Methodology / About (§5.4, §6).
 */
export default async function AboutRoute(): Promise<React.ReactElement> {
  const year = new Date().getUTCFullYear();
  let publishSchedule: MetaArtifact["publish_schedule"] | null = null;
  let nextExpectedPublishUtc: string | null = null;
  let exampleGame = null;

  try {
    const meta = await loadArtifact<MetaArtifact>("meta");
    publishSchedule = meta.publish_schedule;
    nextExpectedPublishUtc = meta.next_expected_publish_utc ?? null;
  } catch {
    publishSchedule = null;
    nextExpectedPublishUtc = null;
  }

  try {
    const week = await loadArtifact<WeekPredictions>("week_predictions");
    exampleGame = selectAboutExampleGame(week.games);
  } catch {
    exampleGame = null;
  }

  return (
    <AboutPage
      year={year}
      publishSchedule={publishSchedule}
      nextExpectedPublishUtc={nextExpectedPublishUtc}
      exampleGame={exampleGame}
    />
  );
}
