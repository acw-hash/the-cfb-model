import { createHash, createHmac } from "node:crypto";

import { describe, expect, it } from "vitest";

import { signedR2GetUrl } from "@/lib/artifacts/r2";

describe("signed R2 GET", () => {
  it("produces AWS4 Authorization header", () => {
    const { url, headers } = signedR2GetUrl(
      "latest/meta.json",
      {
        bucket: "ridge-preview",
        endpointUrl: "https://abc123.r2.cloudflarestorage.com",
        accessKeyId: "AKIAEXAMPLE",
        secretAccessKey: "secret",
        region: "auto",
      },
      new Date("2026-08-13T12:00:00Z"),
    );
    expect(url).toBe("https://abc123.r2.cloudflarestorage.com/ridge-preview/latest/meta.json");
    expect(headers.Authorization).toMatch(/^AWS4-HMAC-SHA256 Credential=/);
    expect(headers["x-amz-date"]).toBe("20260813T120000Z");

    // Sanity: signature length is 64 hex chars
    const sig = headers.Authorization.split("Signature=")[1];
    expect(sig).toHaveLength(64);
    expect(createHash("sha256").update("").digest("hex")).toHaveLength(64);
    expect(createHmac("sha256", "x").update("y").digest("hex")).toHaveLength(64);
  });
});
