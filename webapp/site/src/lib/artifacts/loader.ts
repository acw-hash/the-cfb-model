import fs from "node:fs/promises";
import path from "node:path";

import { fetchR2Json, resolveR2Config } from "./r2";
import type { ArtifactName } from "./types";
import { ARTIFACT_FILES } from "./types";

const DEFAULT_FIXTURE_DIR = path.resolve(process.cwd(), "../fixtures");

export type ArtifactMode = "local" | "r2";

export class ArtifactSourceError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ArtifactSourceError";
  }
}

/**
 * Select artifact source.
 *
 * - Dev default: local fixtures (`ARTIFACT_SOURCE` unset / `fixtures` / `local`).
 * - Preview/prod: `ARTIFACT_SOURCE=r2` (or any Vercel deployment) requires private
 *   R2 credentials and refuses to fall back to fixtures.
 *
 * A misconfigured preview that would silently serve fixtures throws loudly.
 */
export function resolveArtifactBase(): { mode: ArtifactMode; base: string } {
  const explicit = process.env.ARTIFACT_SOURCE?.trim().toLowerCase();
  const onVercel = Boolean(process.env.VERCEL);
  const forceR2 = explicit === "r2" || explicit === "remote" || onVercel;

  if (forceR2) {
    // Loud failure: never serve fixtures from a Vercel / r2-configured deploy.
    resolveR2Config();
    const bucket = process.env.R2_BUCKET!.trim();
    return { mode: "r2", base: `r2://${bucket}/latest` };
  }

  if (explicit && explicit !== "fixtures" && explicit !== "local") {
    throw new ArtifactSourceError(
      `Unknown ARTIFACT_SOURCE=${explicit}; expected fixtures|local|r2`,
    );
  }

  const local = process.env.ARTIFACT_BASE_PATH?.trim() ?? DEFAULT_FIXTURE_DIR;
  return { mode: "local", base: local };
}

async function readLocalArtifact<T>(artifact: ArtifactName): Promise<T> {
  const { base } = resolveArtifactBase();
  const filePath = path.join(base, ARTIFACT_FILES[artifact]);
  const raw = await fs.readFile(filePath, "utf8");
  return JSON.parse(raw) as T;
}

async function fetchR2Artifact<T>(artifact: ArtifactName): Promise<T> {
  const fileName = ARTIFACT_FILES[artifact];
  return fetchR2Json<T>(`latest/${fileName}`);
}

/** Load a named artifact from local fixtures (dev) or private R2 (preview/prod). */
export async function loadArtifact<T>(artifact: ArtifactName): Promise<T> {
  const { mode } = resolveArtifactBase();
  if (mode === "r2") {
    return fetchR2Artifact<T>(artifact);
  }
  return readLocalArtifact<T>(artifact);
}

/** Exposed for tests — where artifacts are loaded from. */
export function getArtifactSource(): { mode: ArtifactMode; base: string } {
  return resolveArtifactBase();
}

/**
 * Load `results_<season>.json`. Season 2025 is the lockbox — refuse to load.
 * Missing file returns null (empty / not-yet state), not a throw.
 */
export async function loadResultsSeason<T>(season: number): Promise<T | null> {
  if (season === 2025) {
    throw new Error("Season 2025 is lockbox — results are never loaded or graded");
  }
  const { mode, base } = resolveArtifactBase();
  const fileName = `results_${season}.json`;
  if (mode === "r2") {
    try {
      return await fetchR2Json<T>(`latest/${fileName}`);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      if (message.includes("HTTP 404")) {
        return null;
      }
      throw err;
    }
  }
  const filePath = path.join(base, fileName);
  try {
    const raw = await fs.readFile(filePath, "utf8");
    return JSON.parse(raw) as T;
  } catch (err) {
    const code = (err as NodeJS.ErrnoException).code;
    if (code === "ENOENT") {
      return null;
    }
    throw err;
  }
}
