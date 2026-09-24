import { sendAlert } from "./alert";
import type { Env } from "./env";
import { matchEventsToGames, type RidgeGame } from "./match";
import { assertAllowedWriteKey, datedOddsKey, latestOddsKey } from "./r2-keys";
import { postRevalidate, resolveRevalidateTarget } from "./revalidate";
import {
  applyInPlayCarryForward,
  buildSnapshot,
  commenceWindowFromGames,
  consensusForMatch,
  fetchOddsApi,
  type OddsSnapshot,
} from "./snapshot";

export type MetaArtifact = {
  schema_version?: string;
  season?: number;
  week?: number;
  fixture?: boolean;
};

export type WeekPredictions = {
  schema_version?: string;
  season: number;
  week: number;
  fixture?: boolean;
  games: RidgeGame[];
};

export type RunResult = {
  ok: boolean;
  sandbox: boolean;
  keys: string[];
  matched: number;
  unmatched: number;
  credits_remaining: number | null;
  revalidate_status: number | null;
  error?: string;
};

function parseMajor(schemaVersion: string | undefined): number | null {
  if (!schemaVersion) return null;
  const major = Number.parseInt(schemaVersion.split(".")[0] ?? "", 10);
  return Number.isFinite(major) ? major : null;
}

function forceSandbox(env: Env): boolean {
  return (env.FORCE_SANDBOX ?? "").toLowerCase() === "true";
}

async function readJson<T>(bucket: R2Bucket, key: string): Promise<T | null> {
  const obj = await bucket.get(key);
  if (!obj) return null;
  return (await obj.json()) as T;
}

async function putJson(bucket: R2Bucket, key: string, value: unknown): Promise<void> {
  assertAllowedWriteKey(key);
  await bucket.put(key, JSON.stringify(value), {
    httpMetadata: { contentType: "application/json" },
  });
}

export async function runOddsRefresh(
  env: Env,
  opts: { now?: Date; fetchImpl?: typeof fetch } = {},
): Promise<RunResult> {
  const now = opts.now ?? new Date();
  const fetchImpl = opts.fetchImpl ?? fetch;
  const keys: string[] = [];
  try {
    const meta = await readJson<MetaArtifact>(env.ARTIFACTS, "latest/meta.json");
    const week = await readJson<WeekPredictions>(env.ARTIFACTS, "latest/week_predictions.json");
    if (!meta || !week) {
      const msg = `missing latest artifacts meta=${!!meta} week=${!!week}`;
      await sendAlert(env, { title: "odds-refresh abort", body: msg, fetchImpl });
      return { ok: false, sandbox: true, keys, matched: 0, unmatched: 0, credits_remaining: null, revalidate_status: null, error: msg };
    }

    const supportedMajor = Number.parseInt(env.SUPPORTED_SCHEMA_MAJOR || "1", 10);
    const metaMajor = parseMajor(meta.schema_version);
    if (metaMajor !== null && metaMajor !== supportedMajor) {
      const msg = `unsupported meta.schema_version major=${metaMajor} supported=${supportedMajor}`;
      await sendAlert(env, { title: "odds-refresh abort", body: msg, fetchImpl });
      return { ok: false, sandbox: true, keys, matched: 0, unmatched: 0, credits_remaining: null, revalidate_status: null, error: msg };
    }

    const fixture = week.fixture === true || meta.fixture === true;
    const noGames = !Array.isArray(week.games) || week.games.length === 0;
    const sandbox = fixture || noGames || forceSandbox(env);

    const window = commenceWindowFromGames(week.games ?? []);
    if (!window) {
      const msg = "no kickoff window from week_predictions.games";
      await sendAlert(env, { title: "odds-refresh abort", body: msg, fetchImpl });
      return { ok: false, sandbox, keys, matched: 0, unmatched: 0, credits_remaining: null, revalidate_status: null, error: msg };
    }

    if (!env.ODDS_API_KEY) {
      const msg = "ODDS_API_KEY missing";
      await sendAlert(env, { title: "odds-refresh abort", body: msg, fetchImpl });
      return { ok: false, sandbox, keys, matched: 0, unmatched: 0, credits_remaining: null, revalidate_status: null, error: msg };
    }

    const api = await fetchOddsApi(env.ODDS_API_KEY, {
      commenceTimeFrom: window.from,
      commenceTimeTo: window.to,
      fetchImpl,
    });

    const nowMs = now.getTime();
    const { matches, unmatched } = matchEventsToGames(api.events, week.games, { nowMs });

    const capturedAtIso = now.toISOString().replace(/\.\d{3}Z$/, "Z");
    const fresh = matches
      .filter((m) => !isInPlayLocal(m.event.commence_time, nowMs))
      .map((m) => consensusForMatch(m, { capturedAtIso }));

    const prevKey = latestOddsKey({ sandbox });
    const previous = await readJson<OddsSnapshot>(env.ARTIFACTS, prevKey);

    const games = applyInPlayCarryForward(matches, fresh, previous, {
      nowMs,
      capturedAtIso,
    });

    const snapshot = buildSnapshot({
      fixture: fixture || sandbox,
      season: week.season,
      week: week.week,
      snapshotAt: now,
      requestsRemaining: api.requestsRemaining,
      requestsUsed: api.requestsUsed,
      requestsLast: api.requestsLast,
      games,
      unmatched,
    });

    const dated = datedOddsKey(week.season, week.week, now, { sandbox });
    const latest = latestOddsKey({ sandbox });
    await putJson(env.ARTIFACTS, dated, snapshot);
    keys.push(dated);
    await putJson(env.ARTIFACTS, latest, snapshot);
    keys.push(latest);

    let revalidateStatus: number | null = null;
    const target = resolveRevalidateTarget({
      sandbox,
      previewRevalidateUrl: env.PREVIEW_REVALIDATE_URL,
      webappRevalidateUrl: env.WEBAPP_REVALIDATE_URL,
    });
    if (target.kind === "url") {
      try {
        const rv = await postRevalidate(target.url, {
          bearerSecret: env.WEBAPP_REVALIDATE_SECRET,
          bypassSecret: env.VERCEL_AUTOMATION_BYPASS_SECRET,
          fetchImpl,
        });
        revalidateStatus = rv.status;
        if (!rv.ok) {
          await sendAlert(env, {
            title: "odds-refresh revalidate failed",
            body: `channel=${target.channel} status=${rv.status} body=${JSON.stringify(rv.body)}`,
            fetchImpl,
          });
        }
      } catch (e) {
        await sendAlert(env, {
          title: "odds-refresh revalidate error",
          body: `channel=${target.channel} ${e instanceof Error ? e.message : String(e)}`,
          fetchImpl,
        });
      }
    }

    const floor = Number.parseInt(env.ODDS_REQUESTS_REMAINING_FLOOR || "100", 10);
    const alerts: string[] = [];
    if (unmatched.length > 0) {
      alerts.push(`unmatched=${unmatched.length}`);
    }
    if (api.requestsRemaining !== null && api.requestsRemaining < floor) {
      alerts.push(`requests_remaining=${api.requestsRemaining} < floor=${floor}`);
    }
    if (alerts.length) {
      await sendAlert(env, {
        title: "odds-refresh warning",
        body: `${alerts.join("; ")} keys=${keys.join(",")}`,
        fetchImpl,
      });
    }

    return {
      ok: true,
      sandbox,
      keys,
      matched: games.length,
      unmatched: unmatched.length,
      credits_remaining: api.requestsRemaining,
      revalidate_status: revalidateStatus,
    };
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    await sendAlert(env, { title: "odds-refresh failure", body: msg, fetchImpl });
    return {
      ok: false,
      sandbox: true,
      keys,
      matched: 0,
      unmatched: 0,
      credits_remaining: null,
      revalidate_status: null,
      error: msg,
    };
  }
}

function isInPlayLocal(commenceTimeIso: string, nowMs: number): boolean {
  const kick = Date.parse(commenceTimeIso);
  return !Number.isNaN(kick) && kick <= nowMs;
}
