import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import type { WeekPredictions } from "@/lib/artifacts/types";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE_PATH = path.resolve(__dirname, "fixtures/week_predictions_null_heavy.json");

/** W10-UI null-heavy slate (~20% null conviction + margin interval). */
export function loadNullHeavyWeek(): WeekPredictions {
  return JSON.parse(fs.readFileSync(FIXTURE_PATH, "utf8")) as WeekPredictions;
}

export const NULL_HEAVY_FIXTURE_PATH = FIXTURE_PATH;
