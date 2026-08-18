import { afterEach, describe, expect, it, vi } from "vitest";

import { isGalleryEnabled } from "@/app/gallery/gallery-gate";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("gallery production gate", () => {
  it("returns true in development (local next dev)", () => {
    vi.stubEnv("NODE_ENV", "development");
    expect(isGalleryEnabled()).toBe(true);
  });

  it("returns false in production (deployed / next start)", () => {
    vi.stubEnv("NODE_ENV", "production");
    expect(isGalleryEnabled()).toBe(false);
  });
});
