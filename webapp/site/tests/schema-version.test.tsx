import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { MaintenanceState } from "@/components/MaintenanceState/MaintenanceState";
import { isSchemaVersionSupported, parseSchemaMajor } from "@/lib/artifacts/schema-version";

describe("schema_version handling (§1.7)", () => {
  it("accepts same major minor/patch", () => {
    expect(isSchemaVersionSupported("1.0.0")).toBe(true);
    expect(isSchemaVersionSupported("1.1.0")).toBe(true);
    expect(isSchemaVersionSupported("1.2.0")).toBe(true);
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

  it("throws on invalid schema_version (W7-1 loud failure)", () => {
    expect(() => parseSchemaMajor("not-a-version")).toThrow(/Invalid schema_version/);
  });
});

describe("doctored fixture major mismatch", () => {
  it("isSchemaVersionSupported rejects 2.0.0 without throwing", () => {
    expect(isSchemaVersionSupported("2.0.0")).toBe(false);
  });

  it("doctored schema_version 2.0.0 yields MaintenanceState, not a rendered page", () => {
    expect(isSchemaVersionSupported("2.0.0")).toBe(false);
    const html = renderToStaticMarkup(<MaintenanceState />);
    expect(html).toContain("Ridge is updating");
    expect(html).toContain("schema version this build does not support");
    expect(html).not.toContain('data-testid="results-page"');
    expect(html).not.toContain('data-testid="this-week-root"');
  });
});
