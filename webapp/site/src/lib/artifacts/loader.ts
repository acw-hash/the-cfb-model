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
