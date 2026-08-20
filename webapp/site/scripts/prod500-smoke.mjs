/**
 * PROD-500 production-build smoke test.
 *
 * Runs `next build`, starts `next start`, POSTs on-demand revalidation (W7-2 /
 * publish path), THEN asserts HTTP 200 on all four public routes.
 *
 * First-request 200s are a cache HIT and prove nothing — the publish hook
 * invalidates ISR, which is where static-to-dynamic 500s appear.
 *
 * Usage (from webapp/site):
 *   npm run test:smoke
 *
 * Requires: prior `npm ci`, port 3099 free.
 */
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SITE_ROOT = path.resolve(__dirname, "..");
const FIXTURE_DIR = path.resolve(SITE_ROOT, "tests/fixtures-incomplete");
const PORT = 3099;
const BASE = `http://127.0.0.1:${PORT}`;
const SMOKE_REVALIDATE_SECRET = "prod500-smoke-revalidate-secret";

function run(cmd, args, env = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, {
      cwd: SITE_ROOT,
      env: { ...process.env, ...env },
      stdio: "inherit",
      shell: process.platform === "win32",
    });
    child.on("error", reject);
    child.on("exit", (code) => {
      if (code === 0) {
        resolve(undefined);
      } else {
        reject(new Error(`${cmd} ${args.join(" ")} exited ${code}`));
      }
    });
  });
}

async function waitForReady(url, timeoutMs = 60_000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      // Any response (including 500) means the server is listening.
      await fetch(url, { signal: AbortSignal.timeout(3000) });
      return;
    } catch {
      // retry
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`Server not ready at ${url} within ${timeoutMs}ms`);
}

async function statusFor(pathname) {
  const response = await fetch(`${BASE}${pathname}`, { signal: AbortSignal.timeout(30_000) });
  return response.status;
}

async function main() {
  if (!fs.existsSync(path.join(FIXTURE_DIR, "team_ratings_2024.json"))) {
    throw new Error(`Missing incomplete fixtures at ${FIXTURE_DIR}`);
  }

  const env = {
    NCAA_QUANT_WEBAPP__EXPORT_ENABLED: "false",
    ARTIFACT_SOURCE: "local",
    ARTIFACT_BASE_PATH: FIXTURE_DIR,
    // Must be set on process.env before `next start` so it wins over .env.local
    // (Next does not override existing keys; unset → REPLACE_ME from .env.local).
    WEBAPP_REVALIDATE_SECRET: SMOKE_REVALIDATE_SECRET,
    PORT: String(PORT),
  };
  process.env.WEBAPP_REVALIDATE_SECRET = SMOKE_REVALIDATE_SECRET;

  console.log("prod500-smoke: npm run build …");
  await run("npm", ["run", "build"], env);

  console.log(`prod500-smoke: next start on :${PORT} …`);
  const server = spawn("npm", ["run", "start", "--", "-p", String(PORT)], {
    cwd: SITE_ROOT,
    env: { ...process.env, ...env },
    stdio: ["ignore", "pipe", "pipe"],
    shell: process.platform === "win32",
  });

  let log = "";
  server.stdout?.on("data", (chunk) => {
    log += chunk.toString();
  });
  server.stderr?.on("data", (chunk) => {
    log += chunk.toString();
  });

  try {
    await waitForReady(`${BASE}/`);

    console.log("prod500-smoke: POST /api/revalidate (publish path) …");
    const rev = await fetch(`${BASE}/api/revalidate`, {
      method: "POST",
      headers: { Authorization: `Bearer ${SMOKE_REVALIDATE_SECRET}` },
      signal: AbortSignal.timeout(30_000),
    });
    const revBody = await rev.text();
    console.log(`/api/revalidate -> ${rev.status} ${revBody}`);
    if (rev.status !== 200) {
      console.error("server log:\n", log);
      throw new Error(`Revalidation failed: HTTP ${rev.status}`);
    }

    const routes = ["/", "/about", "/results", "/game/401628373"];
    const codes = {};
    console.log("prod500-smoke: asserting post-revalidation …");
    for (const route of routes) {
      codes[route] = await statusFor(route);
      console.log(`${route} -> ${codes[route]}`);
    }
    const failed = Object.entries(codes).filter(([, code]) => code !== 200);
    if (failed.length > 0) {
      console.error("server log:\n", log);
      throw new Error(`Non-200 post-revalidate: ${failed.map(([r, c]) => `${r}=${c}`).join(", ")}`);
    }
    console.log("prod500-smoke: all routes 200 post-revalidation");
  } finally {
    server.kill("SIGTERM");
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
