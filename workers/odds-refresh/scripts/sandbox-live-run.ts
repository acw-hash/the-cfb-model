/**
 * Live sandbox run harness — real Odds API + write sandbox keys to R2.
 * Invoked by: npm run sandbox:live  (requires parent .env ODDS_API_KEY + R2)
 *
 * Does not deploy the cron. Mirrors Worker run with FORCE_SANDBOX=true.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  S3Client,
  GetObjectCommand,
  PutObjectCommand,
} from "@aws-sdk/client-s3";
import { matchEventsToGames } from "../src/match";
import {
  applyInPlayCarryForward,
  buildSnapshot,
  commenceWindowFromGames,
  consensusForMatch,
  fetchOddsApi,
  type OddsSnapshot,
} from "../src/snapshot";
import { datedOddsKey, latestOddsKey, assertAllowedWriteKey } from "../src/r2-keys";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const seedDir = path.join(__dirname, "..", ".sandbox-seed");

function loadEnvFile(p: string): void {
  if (!fs.existsSync(p)) return;
  for (const line of fs.readFileSync(p, "utf8").split(/\r?\n/)) {
    const t = line.trim();
    if (!t || t.startsWith("#")) continue;
    const i = t.indexOf("=");
    if (i < 0) continue;
    const k = t.slice(0, i).trim();
    let v = t.slice(i + 1).trim();
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
      v = v.slice(1, -1);
    }
    if (!(k in process.env)) process.env[k] = v;
  }
}

loadEnvFile(path.join(__dirname, "..", "..", "..", ".env"));

async function streamToString(body: unknown): Promise<string> {
  if (body == null) return "";
  if (typeof body === "string") return body;
  // Node.js Readable
  const chunks: Buffer[] = [];
  for await (const chunk of body as AsyncIterable<Buffer>) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }
  return Buffer.concat(chunks).toString("utf8");
}

async function main(): Promise<void> {
  const apiKey = process.env.ODDS_API_KEY?.trim();
  if (!apiKey) throw new Error("ODDS_API_KEY missing");

  const endpoint =
    process.env.NCAA_QUANT_WEBAPP__R2_ENDPOINT_URL || process.env.R2_ENDPOINT_URL;
  const bucket = process.env.NCAA_QUANT_WEBAPP__R2_BUCKET || process.env.R2_BUCKET;
  const ak = process.env.R2_ACCESS_KEY_ID;
  const sk = process.env.R2_SECRET_ACCESS_KEY;
  if (!endpoint || !bucket || !ak || !sk) {
    throw new Error("R2 env incomplete");
  }

  const meta = JSON.parse(fs.readFileSync(path.join(seedDir, "meta.json"), "utf8"));
  const week = JSON.parse(
    fs.readFileSync(path.join(seedDir, "week_predictions.json"), "utf8"),
  );

  const window = commenceWindowFromGames(week.games);
  if (!window) throw new Error("no commence window");

  const api = await fetchOddsApi(apiKey, {
    commenceTimeFrom: window.from,
    commenceTimeTo: window.to,
  });

  const now = new Date();
  const nowMs = now.getTime();
  const { matches, unmatched } = matchEventsToGames(api.events, week.games, { nowMs });
  const capturedAtIso = now.toISOString().replace(/\.\d{3}Z$/, "Z");
  const fresh = matches
    .filter((m) => Date.parse(m.event.commence_time) > nowMs)
    .map((m) => consensusForMatch(m, { capturedAtIso }));

  const s3 = new S3Client({
    region: "auto",
    endpoint,
    credentials: { accessKeyId: ak, secretAccessKey: sk },
  });

  let previous: OddsSnapshot | null = null;
  try {
    const prev = await s3.send(
      new GetObjectCommand({ Bucket: bucket, Key: latestOddsKey({ sandbox: true }) }),
    );
    previous = JSON.parse(await streamToString(prev.Body)) as OddsSnapshot;
  } catch {
    previous = null;
  }

  const games = applyInPlayCarryForward(matches, fresh, previous, {
    nowMs,
    capturedAtIso,
  });

  const snapshot = buildSnapshot({
    fixture: true, // sandbox run always marks fixture
    season: week.season,
    week: week.week,
    snapshotAt: now,
    requestsRemaining: api.requestsRemaining,
    requestsUsed: api.requestsUsed,
    requestsLast: api.requestsLast,
    games,
    unmatched,
  });

  const dated = datedOddsKey(week.season, week.week, now, { sandbox: true });
  const latest = latestOddsKey({ sandbox: true });
  for (const key of [dated, latest]) {
    assertAllowedWriteKey(key);
    await s3.send(
      new PutObjectCommand({
        Bucket: bucket,
        Key: key,
        Body: JSON.stringify(snapshot),
        ContentType: "application/json",
      }),
    );
  }

  const ridgeUnmatched = unmatched.filter((u) => u.side === "ridge_game").length;
  const unmatchedPct = week.games.length
    ? (100 * ridgeUnmatched) / week.games.length
    : 0;

  const report = {
    ok: true,
    sandbox: true,
    season: week.season,
    week: week.week,
    meta_schema: meta.schema_version,
    week_schema: week.schema_version,
    ridge_games: week.games.length,
    odds_events: api.events.length,
    matched: games.length,
    unmatched_total: unmatched.length,
    unmatched_ridge_games: ridgeUnmatched,
    unmatched_ridge_pct: Number(unmatchedPct.toFixed(2)),
    unmatched_sample: unmatched.slice(0, 10),
    carried_forward: games.filter((g) => g.carried_forward).length,
    credits_this_call: api.requestsLast,
    requests_remaining: api.requestsRemaining,
    requests_used_cumulative: api.requestsUsed,
    keys: [dated, latest],
    snapshot_at: snapshot.snapshot_at,
    revalidate: "skipped (sandbox)",
    stop_if_unmatched_gt_5pct: unmatchedPct > 5,
  };
  console.log(JSON.stringify(report, null, 2));
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
