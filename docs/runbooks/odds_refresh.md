# Odds refresh Worker (`ridge-odds-refresh`)

Daily market odds snapshot beside Ridge forecasts. Spec:
`docs/webapp/adr/ADR-ODDS-SNAPSHOT.md`. Worker code: `workers/odds-refresh/`.

The Worker reads live `latest/meta.json` + `latest/week_predictions.json`, calls
The Odds API once (~3 credits), writes allowlisted odds keys, then POSTs
`/api/revalidate` (Bearer **and** `x-vercel-protection-bypass`). Sandbox /
`FORCE_SANDBOX=true` / fixture meta → writes under `sandbox/` only and
revalidates **`PREVIEW_REVALIDATE_URL` only** (never production).

## Add / remove cron times

Edit `workers/odds-refresh/wrangler.toml`:

```toml
[triggers]
crons = ["0 13 * * *"]
# Example Saturday extra run (+~3 credits):
# crons = ["0 13 * * *", "0 15 * * 6"]
```

Redeploy:

```bash
cd workers/odds-refresh
npx wrangler deploy
```

Each cron expression fires one Odds API call (~3 credits:
`markets=h2h,spreads,totals` × `regions=us`). Do not add crons casually.

## Rotate `ODDS_API_KEY`

1. Create a new key in The Odds API dashboard.
2. `cd workers/odds-refresh && npx wrangler secret put ODDS_API_KEY`
3. Confirm a manual run succeeds (below).
4. Revoke the old key in the Odds API dashboard.
5. Update workstation `.env` `ODDS_API_KEY` for harness / ingest — **never** put
   this key in Vercel.

## Manual `POST /run`

```bash
curl -X POST "$WORKER_URL/run" \
  -H "Authorization: Bearer $MANUAL_RUN_SECRET"
```

`MANUAL_RUN_SECRET` is a Worker secret (`wrangler secret put MANUAL_RUN_SECRET`).
Response JSON includes `ok`, `sandbox`, `keys`, `matched`, `unmatched`,
`credits_remaining`, `revalidate_status`.

Local equivalent without a deployed Worker (writes sandbox only):

```bash
cd workers/odds-refresh
npm run sandbox:live
```

## Disable odds on the site (instant)

Unset or set `ODDS_SNAPSHOT_ENABLED=false` on the Vercel project (Preview and/or
Production). Redeploy or wait for the next deploy. The site hides all odds UI
immediately — no Worker change required.

## Pause the cron (stop API spend)

Cloudflare dashboard → Workers → `ridge-odds-refresh` → Triggers → disable or
remove cron schedules. Or deploy with an empty cron list, then restore later.

`FORCE_SANDBOX=true` (Phase 3 preview) still spends Odds API credits on each
run; it redirects writes to `sandbox/` and revalidates **only**
`PREVIEW_REVALIDATE_URL` (never `WEBAPP_REVALIDATE_URL`).

History keys use **minute** resolution
(`odds/{season}/w{week}/{YYYY-MM-DD}T{HH}:{MM}.json`) so a manual `/run` does
not overwrite the same hour's cron object.

## Secrets checklist

```bash
npx wrangler secret put ODDS_API_KEY
npx wrangler secret put WEBAPP_REVALIDATE_SECRET
npx wrangler secret put VERCEL_AUTOMATION_BYPASS_SECRET
npx wrangler secret put MANUAL_RUN_SECRET
npx wrangler secret put NTFY_TOPIC
# Phase 3 sandbox → preview only:
npx wrangler secret put PREVIEW_REVALIDATE_URL
# Production only after L7 — leave unset during Phase 3:
# npx wrangler secret put WEBAPP_REVALIDATE_URL
# optional: NTFY_SERVER, NTFY_AUTH_TOKEN, TELEGRAM_*
```

## Flip sandbox → live (operator + L7 only)

After L7 counsel sign-off:

1. Set `FORCE_SANDBOX = "false"` in `wrangler.toml` and redeploy.
2. Confirm secrets: `ODDS_API_KEY`, `WEBAPP_REVALIDATE_SECRET`,
   `VERCEL_AUTOMATION_BYPASS_SECRET`, `WEBAPP_REVALIDATE_URL`,
   `MANUAL_RUN_SECRET`.
3. Set `ODDS_SNAPSHOT_ENABLED=true` on **production** Vercel.
4. Remove `ODDS_R2_PREFIX=sandbox` from preview (production reads
   `latest/odds_snapshot.json`).
5. Trigger one manual `/run` and verify `sandbox: false`, revalidate 2xx, and
   live site odds as-of.

## Phase 3 preview wiring

| Knob | Value |
|------|--------|
| Worker `FORCE_SANDBOX` | `true` |
| Vercel Preview `ODDS_SNAPSHOT_ENABLED` | `true` |
| Vercel Preview `ODDS_R2_PREFIX` | `sandbox` |
| Production flag | **leave false** until L7 |

## Alerts (what each means)

| Alert | Meaning | Action |
|-------|---------|--------|
| `odds-refresh abort` — missing latest artifacts | `meta` or `week_predictions` absent in R2 | Check last Ridge publish; do not invent odds |
| Unsupported schema major | `week_predictions` / meta major ≠ Worker `SUPPORTED_SCHEMA_MAJOR` | Align Worker var or fix publish schema |
| Odds API HTTP error / quota | Upstream failure or credits exhausted | Check Odds dashboard; pause cron if out of credits |
| `ODDS_REQUESTS_REMAINING_FLOOR` breach | Remaining credits below floor (default 100) | Top up plan or reduce cron density |
| High unmatched ridge games | Match rate bad (team map / schedule) | Extend `configs/team_names.yaml`, `npm run export-team-map`, redeploy; stop if >5% |
| Revalidate non-2xx | Preview/prod cache hook failed | Check `PREVIEW_REVALIDATE_URL` (sandbox) or `WEBAPP_REVALIDATE_URL` (live) + Bearer + bypass |

Optional channels: `NTFY_TOPIC` / `NTFY_AUTH_TOKEN`, or
`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` (Worker secrets).

R2 writes use the Worker **binding** (no S3 keys in the Worker). Do not put
`ODDS_API_KEY` in Vercel.
