import { describe, expect, it } from "vitest";

import { isSchemaVersionSupported, parseSchemaMajor } from "@/lib/artifacts/schema-version";

describe("schema_version handling (§1.7)", () => {
  it("accepts same major minor/patch", () => {
    expect(isSchemaVersionSupported("1.0.0")).toBe(true);
    expect(isSchemaVersionSupported("1.1.0")).toBe(true);
    expect(isSchemaVersionSupported("1.2.3")).toBe(true);
  });

  it("rejects major version mismatch — maintenance state", () => {
    expect(isSchemaVersionSupported("2.0.0")).toBe(false);
    expect(isSchemaVersionSupported("2.1.0")).toBe(false);
  });

  it("parses semver major", () => {
    expect(parseSchemaMajor("1.1.0")).toBe(1);
    expect(parseSchemaMajor("2.0.0")).toBe(2);
  });
});

describe("doctored fixture major mismatch", () => {
  it("would trigger maintenance for schema_version 2.0.0", () => {
    const doctoredMeta = {
      schema_version: "2.0.0",
      published_at: "2024-09-24T06:00:00Z",
      fixture: true,
    };
    expect(isSchemaVersionSupported(doctoredMeta.schema_version)).toBe(false);
  });
});
