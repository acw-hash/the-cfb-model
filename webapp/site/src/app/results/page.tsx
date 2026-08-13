import { MaintenanceState } from "@/components/MaintenanceState/MaintenanceState";
import { ResultsPage } from "@/components/Results/ResultsPage";
import { loadArtifact, loadResultsSeason } from "@/lib/artifacts/loader";
import { isSchemaVersionSupported } from "@/lib/artifacts/schema-version";
import type { MetaArtifact, ResultsSeason, TrackRecord } from "@/lib/artifacts/types";

export const revalidate = 21600;

export const metadata = {
  title: "Results — Ridge",
  description: "Honest track record — recorded metrics with confidence intervals, graded games",
};

/**
 * Results / Track Record (§5.3).
 * Track record from frozen 23-readout artifact. Graded games from results_<season>
 * for the current meta season when available — never season 2025.
 */
export default async function ResultsRoute(): Promise<React.ReactElement> {
  const [meta, track] = await Promise.all([
    loadArtifact<MetaArtifact>("meta"),
    loadArtifact<TrackRecord>("track_record"),
  ]);

  if (
    !isSchemaVersionSupported(meta.schema_version) ||
    !isSchemaVersionSupported(track.schema_version)
  ) {
    return <MaintenanceState />;
  }

  let results: ResultsSeason | null = null;
  if (meta.season !== 2025) {
    results = await loadResultsSeason<ResultsSeason>(meta.season);
    if (results && !isSchemaVersionSupported(results.schema_version)) {
      return <MaintenanceState />;
    }
  }

  return <ResultsPage track={track} results={results} syncUrl />;
}
