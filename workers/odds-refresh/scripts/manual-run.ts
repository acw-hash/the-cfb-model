import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..");

function loadEnv(p: string): Record<string, string> {
  const env: Record<string, string> = {};
  for (const line of fs.readFileSync(p, "utf8").split(/\r?\n/)) {
    const t = line.trim();
    if (!t || t.startsWith("#") || !t.includes("=")) continue;
    const i = t.indexOf("=");
    const k = t.slice(0, i).trim();
    let v = t.slice(i + 1).trim();
    if (
      (v.startsWith('"') && v.endsWith('"')) ||
      (v.startsWith("'") && v.endsWith("'"))
    ) {
      v = v.slice(1, -1);
    }
    env[k] = v;
  }
  return env;
}

const env = loadEnv(path.join(root, ".env"));
const secret = env.MANUAL_RUN_SECRET;
if (!secret) throw new Error("MANUAL_RUN_SECRET missing");

const started = new Date().toISOString();
const r = await fetch("https://ridge-odds-refresh.ridge-cfb.workers.dev/run", {
  method: "POST",
  headers: { Authorization: `Bearer ${secret}` },
});
const text = await r.text();
const safe = text.split(secret).join("[redacted]");
let body: unknown = safe;
try {
  body = JSON.parse(safe);
} catch {
  /* keep string */
}
console.log(JSON.stringify({ started, http_status: r.status, body }, null, 2));
