import fs from "node:fs/promises";
import path from "node:path";

import type { ArtifactName } from "./types";
import { ARTIFACT_FILES } from "./types";

const DEFAULT_FIXTURE_DIR = path.resolve(process.cwd(), "../fixtures");

function resolveArtifactBase(): { mode: "local" | "remote"; base: string } {
  const remote = process.env.ARTIFACT_BASE_URL?.trim();
  if (remote) {
    return { mode: "remote", base: remote.replace(/\/$/, "") };
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

async function fetchRemoteArtifact<T>(artifact: ArtifactName): Promise<T> {
  const { base } = resolveArtifactBase();
  const url = `${base}/${ARTIFACT_FILES[artifact]}`;
  const response = await fetch(url, { next: { revalidate: 21600 } });
  if (!response.ok) {
    throw new Error(`Failed to fetch artifact ${artifact}: ${response.status}`);
  }
  return (await response.json()) as T;
}

/** Load a named artifact from local fixtures (dev) or R2 public URL (prod). */
export async function loadArtifact<T>(artifact: ArtifactName): Promise<T> {
  const { mode } = resolveArtifactBase();
  if (mode === "remote") {
    return fetchRemoteArtifact<T>(artifact);
  }
  return readLocalArtifact<T>(artifact);
}

/** Exposed for tests — where artifacts are loaded from. */
export function getArtifactSource(): { mode: "local" | "remote"; base: string } {
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
  if (mode === "remote") {
    const response = await fetch(`${base}/${fileName}`, { next: { revalidate: 21600 } });
    if (response.status === 404) {
      return null;
    }
    if (!response.ok) {
      throw new Error(`Failed to fetch ${fileName}: ${response.status}`);
    }
    return (await response.json()) as T;
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
