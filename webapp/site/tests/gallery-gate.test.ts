import { describe, expect, it } from "vitest";

import { isGalleryEnabled } from "@/app/gallery/gallery-gate";

describe("gallery production gate", () => {
  it("returns true in development (local next dev)", () => {
    const prev = process.env.NODE_ENV;
    process.env.NODE_ENV = "development";
    expect(isGalleryEnabled()).toBe(true);
    process.env.NODE_ENV = prev;
  });

  it("returns false in production (deployed / next start)", () => {
    const prev = process.env.NODE_ENV;
    process.env.NODE_ENV = "production";
    expect(isGalleryEnabled()).toBe(false);
    process.env.NODE_ENV = prev;
  });
});
