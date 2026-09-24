# ridge-odds-refresh

Cloudflare Worker that writes a daily **market odds snapshot** beside Ridge
forecasts. Spec: `docs/webapp/adr/ADR-ODDS-SNAPSHOT.md`.

## Why a Worker (not Prefect / Vercel Cron / GHA)

Runs independent of the workstation and of Vercel. Odds API key lives in a
Worker secret. R2 writes use a native binding (no S3 keys in the Worker).

## Team map (bundled)

`src/data/odds_team_map.json` is exported from `configs/team_names.yaml`
(`odds_api` map). **Bundled** so the Worker does not depend on a workstation
upload when the box is down. Regenerate after YAML edits:

```bash
npm run export-team-map
```

Matching is exact (casefold) + kickoff ±36 h; reversed pair for neutral-site
swaps. **Never fuzzy.**

## Credits

Each scheduled cron entry costs **~3 Odds API credits**
(`markets=h2h,spreads,totals` × `regions=us`). Cron list is in `wrangler.toml`:

```toml
[triggers]
crons = ["0 13 * * *"]
# Add e.g. "0 15 * * 6" for Saturday morning — +3 credits per added run.
```

## Secrets

```bash
wrangler secret put ODDS_API_KEY
wrangler secret put WEBAPP_REVALIDATE_SECRET
wrangler secret put VERCEL_AUTOMATION_BYPASS_SECRET
wrangler secret put MANUAL_RUN_SECRET
wrangler secret put WEBAPP_REVALIDATE_URL
# optional alerts:
wrangler secret put NTFY_TOPIC
wrangler secret put NTFY_AUTH_TOKEN
# optional: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, NTFY_SERVER
```

Vars in `wrangler.toml`: `SUPPORTED_SCHEMA_MAJOR`, `ODDS_REQUESTS_REMAINING_FLOOR`,
`FORCE_SANDBOX`.

**Phase 3 preview:** `FORCE_SANDBOX` is `"true"` until the operator flips to live
after L7. See `docs/runbooks/odds_refresh.md`.

## Local

```bash
npm install
npm test
npm run typecheck

# Scheduled handler against local Miniflare R2 (set FORCE_SANDBOX=true):
npx wrangler dev --test-scheduled
# Then POST http://localhost:8787/__scheduled  (wrangler prints the URL)
```

Manual trigger (deployed or local):

```bash
curl -X POST "$WORKER_URL/run" -H "Authorization: Bearer $MANUAL_RUN_SECRET"
```

## Write allowlist

Worker code refuses any R2 put outside:

- `odds/**`
- `latest/odds_snapshot.json`
- `sandbox/**`

Fixture / offseason / `FORCE_SANDBOX=true` → sandbox only. Revalidation uses
`PREVIEW_REVALIDATE_URL` when set; **never** falls back to
`WEBAPP_REVALIDATE_URL`. Leave `WEBAPP_REVALIDATE_URL` unset until L7 live flip.

## Do not

- Deploy the live cron without Phase 3 go-ahead
- Put `ODDS_API_KEY` in Vercel
- Write publish artifacts (`week_predictions`, `meta`, …)
- Store book names or prices
- Record in-play odds
