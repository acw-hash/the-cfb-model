import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/cache", () => ({
  revalidatePath: vi.fn(),
}));

import { GET, POST } from "@/app/api/revalidate/route";

const ORIGINAL = { ...process.env };

afterEach(() => {
  process.env = { ...ORIGINAL };
  vi.clearAllMocks();
});

describe("POST /api/revalidate", () => {
  it("refuses unauthenticated requests", async () => {
    process.env.WEBAPP_REVALIDATE_SECRET = "correct-secret";
    const response = await POST(new Request("http://localhost/api/revalidate", { method: "POST" }));
    expect(response.status).toBe(401);
    const body = (await response.json()) as { ok: boolean };
    expect(body.ok).toBe(false);
  });

  it("refuses wrong secret", async () => {
    process.env.WEBAPP_REVALIDATE_SECRET = "correct-secret";
    const response = await POST(
      new Request("http://localhost/api/revalidate", {
        method: "POST",
        headers: { Authorization: "Bearer wrong-secret" },
      }),
    );
    expect(response.status).toBe(401);
  });

  it("accepts bearer secret", async () => {
    process.env.WEBAPP_REVALIDATE_SECRET = "correct-secret";
    const response = await POST(
      new Request("http://localhost/api/revalidate", {
        method: "POST",
        headers: { Authorization: "Bearer correct-secret" },
      }),
    );
    expect(response.status).toBe(200);
    const body = (await response.json()) as { ok: boolean; revalidated: boolean };
    expect(body.ok).toBe(true);
    expect(body.revalidated).toBe(true);
  });

  it("rejects GET", async () => {
    const response = await GET();
    expect(response.status).toBe(405);
  });
});
