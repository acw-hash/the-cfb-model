import { afterEach, describe, expect, it, vi } from "vitest";

import { getArtifactSource, resolveArtifactBase } from "@/lib/artifacts/loader";

const ORIGINAL = { ...process.env };

afterEach(() => {
  process.env = { ...ORIGINAL };
  vi.restoreAllMocks();
});

describe("artifact source selection (W7)", () => {
  it("defaults to local fixtures off Vercel", () => {
    delete process.env.VERCEL;
    delete process.env.ARTIFACT_SOURCE;
    delete process.env.ARTIFACT_BASE_PATH;
    delete process.env.R2_BUCKET;
    const source = resolveArtifactBase();
    expect(source.mode).toBe("local");
    expect(source.base).toContain("fixtures");
  });

  it("refuses Vercel deploy without R2 credentials (no silent fixtures)", () => {
    process.env.VERCEL = "1";
    delete process.env.ARTIFACT_SOURCE;
    delete process.env.R2_BUCKET;
    delete process.env.R2_ACCESS_KEY_ID;
    delete process.env.R2_SECRET_ACCESS_KEY;
    delete process.env.R2_ENDPOINT_URL;
    delete process.env.R2_ACCOUNT_ID;
    expect(() => resolveArtifactBase()).toThrow(/Missing required env/);
  });

  it("selects r2 when ARTIFACT_SOURCE=r2 and credentials present", () => {
    delete process.env.VERCEL;
    process.env.ARTIFACT_SOURCE = "r2";
    process.env.R2_BUCKET = "ridge-preview";
    process.env.R2_ACCESS_KEY_ID = "ak";
    process.env.R2_SECRET_ACCESS_KEY = "sk";
    process.env.R2_ACCOUNT_ID = "acct";
    const source = getArtifactSource();
    expect(source.mode).toBe("r2");
    expect(source.base).toBe("r2://ridge-preview/latest");
  });
});
