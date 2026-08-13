import { SUPPORTED_SCHEMA_MAJOR } from "./types";

/** Parse semver major from artifact schema_version (§1.7). */
export function parseSchemaMajor(schemaVersion: string): number {
  const major = schemaVersion.split(".")[0];
  const parsed = Number.parseInt(major, 10);
  if (Number.isNaN(parsed)) {
    throw new Error(`Invalid schema_version: ${schemaVersion}`);
  }
  return parsed;
}

/** True when artifact major version is supported by this frontend build. */
export function isSchemaVersionSupported(schemaVersion: string): boolean {
  return parseSchemaMajor(schemaVersion) === SUPPORTED_SCHEMA_MAJOR;
}
