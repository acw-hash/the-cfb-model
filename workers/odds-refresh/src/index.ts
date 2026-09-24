import type { Env } from "./env";
import { runOddsRefresh } from "./run";

export default {
  async scheduled(
    _controller: ScheduledController,
    env: Env,
    ctx: ExecutionContext,
  ): Promise<void> {
    ctx.waitUntil(
      runOddsRefresh(env).then((result) => {
        console.log("odds_refresh_scheduled", JSON.stringify(result));
      }),
    );
  },

  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    if (request.method === "POST" && url.pathname === "/run") {
      const auth = request.headers.get("authorization") ?? "";
      const token = auth.toLowerCase().startsWith("bearer ")
        ? auth.slice("bearer ".length).trim()
        : "";
      if (!env.MANUAL_RUN_SECRET || token !== env.MANUAL_RUN_SECRET) {
        return Response.json({ ok: false, error: "unauthorized" }, { status: 401 });
      }
      const result = await runOddsRefresh(env);
      return Response.json(result, { status: result.ok ? 200 : 500 });
    }
    if (request.method === "GET" && url.pathname === "/health") {
      return Response.json({ ok: true, service: "ridge-odds-refresh" });
    }
    void ctx;
    return Response.json({ ok: false, error: "not found" }, { status: 404 });
  },
};
