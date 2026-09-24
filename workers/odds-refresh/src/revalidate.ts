export type RevalidateResult = {
  ok: boolean;
  status: number;
  body: unknown;
};

export type RevalidateTarget =
  | { kind: "skip"; reason: string }
  | { kind: "url"; url: string; channel: "preview" | "production" };

/**
 * Choose revalidate URL. Sandbox/preview never falls back to production.
 * Unset production URL → skip (no default host).
 */
export function resolveRevalidateTarget(opts: {
  sandbox: boolean;
  previewRevalidateUrl?: string;
  webappRevalidateUrl?: string;
}): RevalidateTarget {
  if (opts.sandbox) {
    const preview = opts.previewRevalidateUrl?.trim();
    if (preview) {
      return { kind: "url", url: preview, channel: "preview" };
    }
    return { kind: "skip", reason: "sandbox_no_preview_url" };
  }
  const production = opts.webappRevalidateUrl?.trim();
  if (!production) {
    return { kind: "skip", reason: "production_url_unset" };
  }
  return { kind: "url", url: production, channel: "production" };
}

/**
 * POST existing /api/revalidate with Bearer AND x-vercel-protection-bypass.
 * W7 lesson: Bearer alone returns 401 behind Deployment Protection.
 */
export async function postRevalidate(
  url: string,
  opts: {
    bearerSecret: string;
    bypassSecret: string;
    fetchImpl?: typeof fetch;
  },
): Promise<RevalidateResult> {
  const { bearerSecret, bypassSecret } = opts;
  const fetchImpl = opts.fetchImpl ?? fetch;
  const headers: Record<string, string> = {
    Authorization: `Bearer ${bearerSecret}`,
    "Content-Type": "application/json",
    "x-vercel-protection-bypass": bypassSecret,
  };
  const resp = await fetchImpl(url, {
    method: "POST",
    headers,
    body: JSON.stringify({ source: "ridge_odds_refresh", paths: ["/", "/game"] }),
  });
  let body: unknown;
  try {
    body = await resp.json();
  } catch {
    body = await resp.text();
  }
  return { ok: resp.ok, status: resp.status, body };
}

/** Exported for tests — builds the exact header set. */
export function revalidateHeaders(bearerSecret: string, bypassSecret: string): Record<string, string> {
  return {
    Authorization: `Bearer ${bearerSecret}`,
    "Content-Type": "application/json",
    "x-vercel-protection-bypass": bypassSecret,
  };
}
