import { revalidatePath } from "next/cache";
import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function extractSecret(request: Request): string | null {
  const auth = request.headers.get("authorization");
  if (auth?.toLowerCase().startsWith("bearer ")) {
    return auth.slice("bearer ".length).trim();
  }
  const header = request.headers.get("x-revalidate-secret");
  return header?.trim() || null;
}

/**
 * On-demand ISR revalidation — triggered by workstation R2 push after meta.json.
 * Authenticated with WEBAPP_REVALIDATE_SECRET (server-only env).
 */
export async function POST(request: Request): Promise<NextResponse> {
  const expected = process.env.WEBAPP_REVALIDATE_SECRET?.trim();
  if (!expected) {
    return NextResponse.json(
      { ok: false, error: "revalidation secret not configured" },
      { status: 503 },
    );
  }

  const provided = extractSecret(request);
  if (!provided || provided !== expected) {
    return NextResponse.json({ ok: false, error: "unauthorized" }, { status: 401 });
  }

  // Layout + all pages share the artifact graph; invalidate the site tree.
  revalidatePath("/", "layout");
  revalidatePath("/");
  revalidatePath("/results");
  revalidatePath("/about");
  revalidatePath("/game", "layout");

  return NextResponse.json({
    ok: true,
    revalidated: true,
    paths: ["/", "/results", "/about", "/game"],
    at: new Date().toISOString(),
  });
}

export async function GET(): Promise<NextResponse> {
  return NextResponse.json({ ok: false, error: "method not allowed" }, { status: 405 });
}
