/**
 * Pull live latest predictions + sandbox odds into a local dir for Phase 3
 * preview screenshots (no Vercel deploy required).
 *
 * Usage: npx tsx scripts/pull-sandbox-preview-artifacts.ts
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { GetObjectCommand, S3Client } from "@aws-sdk/client-s3";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(__dirname, "..", "..", "..");
const outDir = path.join(root, "webapp", "fixtures", "_phase3_sandbox_preview");

function loadEnvFile(p: string): void {
  if (!fs.existsSync(p)) return;
  for (const line of fs.readFileSync(p, "utf8").split(/\r?\n/)) {
    const t = line.trim();
    if (!t || t.startsWith("#")) continue;
    const i = t.indexOf("=");
    if (i < 0) continue;
    const k = t.slice(0, i).trim();
    let v = t.slice(i + 1).trim();
    if (
      (v.startsWith('"') && v.endsWith('"')) ||
      (v.startsWith("'") && v.endsWith("'"))
    ) {
      v = v.slice(1, -1);
    }
    if (!(k in process.env)) process.env[k] = v;
  }
}

loadEnvFile(path.join(root, ".env"));

async function streamToString(body: unknown): Promise<string> {
  if (body == null) return "";
  if (typeof body === "string") return body;
  const chunks: Buffer[] = [];
  for await (const chunk of body as AsyncIterable<Buffer>) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }
  return Buffer.concat(chunks).toString("utf8");
}

async function main(): Promise<void> {
  const endpoint =
    process.env.NCAA_QUANT_WEBAPP__R2_ENDPOINT_URL || process.env.R2_ENDPOINT_URL;
  const bucket = process.env.NCAA_QUANT_WEBAPP__R2_BUCKET || process.env.R2_BUCKET;
  const ak = process.env.R2_ACCESS_KEY_ID;
  const sk = process.env.R2_SECRET_ACCESS_KEY;
  if (!endpoint || !bucket || !ak || !sk) {
    throw new Error("R2 env incomplete");
  }

  const s3 = new S3Client({
    region: "auto",
    endpoint,
    credentials: { accessKeyId: ak, secretAccessKey: sk },
  });

  const keys: Record<string, string> = {
    "meta.json": "latest/meta.json",
    "week_predictions.json": "latest/week_predictions.json",
    "track_record.json": "latest/track_record.json",
    "odds_snapshot.json": "sandbox/latest/odds_snapshot.json",
  };

  fs.mkdirSync(outDir, { recursive: true });
  const summary: Record<string, unknown> = { outDir, fetched: {} };

  for (const [file, key] of Object.entries(keys)) {
    const obj = await s3.send(new GetObjectCommand({ Bucket: bucket, Key: key }));
    const body = await streamToString(obj.Body);
    fs.writeFileSync(path.join(outDir, file), body, "utf8");
    const parsed = JSON.parse(body) as Record<string, unknown>;
    summary.fetched[file] = {
      key,
      schema_version: parsed.schema_version,
      snapshot_at: parsed.snapshot_at,
      published_at: parsed.published_at,
      season: parsed.season,
      week: parsed.week,
      games: Array.isArray(parsed.games) ? parsed.games.length : undefined,
      fixture: parsed.fixture,
    };
  }

  // team_ratings optional
  for (const name of ["team_ratings_2026.json", "results_2026.json"]) {
    try {
      const obj = await s3.send(
        new GetObjectCommand({ Bucket: bucket, Key: `latest/${name}` }),
      );
      const body = await streamToString(obj.Body);
      fs.writeFileSync(path.join(outDir, name), body, "utf8");
      (summary.fetched as Record<string, unknown>)[name] = { key: `latest/${name}` };
    } catch {
      // optional
    }
  }

  console.log(JSON.stringify(summary, null, 2));
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
