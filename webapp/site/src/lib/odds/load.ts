import fs from "node:fs/promises";
import path from "node:path";

import { fetchR2Json } from "@/lib/artifacts/r2";
import { getArtifactSource } from "@/lib/artifacts/loader";

import { buildOddsPageContext, isOddsSnapshotEnabled } from "./staleness";
import type { OddsPageContext, OddsSnapshot } from "./types";

/**
 * Optional odds snapshot load. Never throws into MaintenanceState.
 * Missing file / fetch error / JSON error / unsupported major / >72h stale → null + log.
 */
export async function loadOddsPageContext(
  nowMs: number = Date.now(),
): Promise<OddsPageContext | null> {
  if (!isOddsSnapshotEnabled()) {
    return null;
  }
  try {
    const snapshot = await readOddsSnapshotFile();
    if (snapshot == null) {
      console.error("odds_snapshot_missing");
      return null;
    }
    const ctx = buildOddsPageContext(snapshot, nowMs);
    if (ctx == null) {
      console.error("odds_snapshot_unsupported_or_stale", {
        schema_version: snapshot.schema_version,
        snapshot_at: snapshot.snapshot_at,
      });
    }
    return ctx;
  } catch (err) {
    console.error("odds_snapshot_load_failed", err instanceof Error ? err.message : String(err));
    return null;
  }
}

/**
 * R2 key for the odds snapshot.
 *
 * Phase 3 preview: set `ODDS_R2_PREFIX=sandbox` so the site reads
 * `sandbox/latest/odds_snapshot.json` while predictions stay on live `latest/`.
 * Production / unset → `latest/odds_snapshot.json`.
 */
export function oddsSnapshotR2Key(): string {
  const prefix = process.env.ODDS_R2_PREFIX?.trim().toLowerCase();
  if (prefix === "sandbox") {
    return "sandbox/latest/odds_snapshot.json";
  }
  return "latest/odds_snapshot.json";
}

async function readOddsSnapshotFile(): Promise<OddsSnapshot | null> {
  const { mode, base } = getArtifactSource();
  if (mode === "r2") {
    try {
      return await fetchR2Json<OddsSnapshot>(oddsSnapshotR2Key());
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      if (message.includes("HTTP 404")) {
        return null;
      }
      throw err;
    }
  }
  const filePath = path.join(base, "odds_snapshot.json");
  try {
    const raw = await fs.readFile(filePath, "utf8");
    return JSON.parse(raw) as OddsSnapshot;
  } catch (err) {
    const code = (err as NodeJS.ErrnoException).code;
    if (code === "ENOENT") {
      return null;
    }
    throw err;
  }
}
