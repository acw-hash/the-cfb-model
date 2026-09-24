# W-ODDS — Market odds snapshot (Phase 0–3 + production)

**Date:** 2026-09-24  
**Status:** Production odds go-live **2026-09-24** on **operator decision**,
before L7 counsel sign-off and before carry-forward / scheduled-cron proof.
Phase 3 cycles (b)–(d) remain open against live.

## Production go-live (operator override)

**Timestamp (UTC):** `2026-09-24T20:05:31Z` (live odds write) /
`2026-09-24T20:08Z` approx (production site aliased).  
**Decision:** Operator accepted incomplete L7 / carry-forward / cron proof for
this task only. Other FORBIDDEN items unchanged.

| Step | Result |
|------|--------|
| Pre-flight code on main | Was **not** merged; committed + pushed `dcecd89` before prod deploy |
| Live slate | 2026 w4, 71 games, `fixture` absent |
| Worker | `FORCE_SANDBOX=false`, `WEBAPP_REVALIDATE_URL` set (prod) |
| Live `/run` | keys `odds/2026/w4/2026-09-24T20:05.json` + `latest/odds_snapshot.json`; matched 71 / unmatched 0; credits remaining 99646; revalidate **200** |
| First `/run` after flip | briefly wrote sandbox (stale isolate); immediate redeploy + re-run wrote **live** |
| Vercel Production | `ODDS_SNAPSHOT_ENABLED=true`; `ODDS_R2_PREFIX` **not** on Production |
| Prod URL | https://the-cfb-model.vercel.app — Market column + Model and market; Odds as of Thu 8:05 PM UTC; footer new copy; spot-check margin 20.5 matches live history |
| Screenshots | `webapp/site/docs/screenshots/odds/production-golive/` (8 PNGs) |

### Rollback (do not run unless needed)

Pre-W-ODDS Ready deployment:
`https://the-cfb-model-fnlw8jpvl-alecs-projects-2eeacfd8.vercel.app`

**Primary:** `npx vercel rollback https://the-cfb-model-fnlw8jpvl-alecs-projects-2eeacfd8.vercel.app`
(or dashboard Instant Rollback).

**Secondary:** remove `ODDS_SNAPSHOT_ENABLED` from production, then redeploy from
**main via git push** — never `--archive` from a working copy.

After any Worker env/var change, verify with one `POST /run` before trusting cron
(stale-isolate note; see `docs/runbooks/odds_refresh.md`).

### Still open
- Cycle (b) ~23:45Z against **live** (carry-forward checks; rollback if fail)
- Cycle (c) Fri 13:00 UTC cron
- Cycle (d) failure drill — **local / sandbox-forced only**, never live ODDS_API_KEY overwrite

## Phase 3 continuation (2026-09-24 afternoon)

### Step 0–1
- Cloudflare OAuth + Vercel login OK; linked existing `the-cfb-model` (no new project).
- Worker secrets (names): `ODDS_API_KEY`, `WEBAPP_REVALIDATE_SECRET`,
  `VERCEL_AUTOMATION_BYPASS_SECRET`, `MANUAL_RUN_SECRET`, `NTFY_TOPIC`,
  `PREVIEW_REVALIDATE_URL`. **Not set:** `WEBAPP_REVALIDATE_URL`, `TELEGRAM_*`.
- Preview env only: `ODDS_SNAPSHOT_ENABLED`, `ODDS_R2_PREFIX` (absent on Production).
- Slate: season **2026** week **4**, 71 games; kickoffs
  `2026-09-24T23:30:00Z` … `2026-09-27T03:00:00Z`; all 71 after last sandbox
  `snapshot_at` (`2026-09-24T18:46:34Z`); week not completed.

### Step 2 code
- History keys: minute resolution `…T{HH}:{MM}.json`.
- `PREVIEW_REVALIDATE_URL` for sandbox revalidate; never falls back to prod URL;
  unset `WEBAPP_REVALIDATE_URL` is skip (no default).
- Site: unset `ODDS_R2_PREFIX` → live `latest/odds_snapshot.json`.
- Tests: Worker 26 pass + typecheck; site 229 pass + lint.

### Step 3 (complete)
- Preview: `https://the-cfb-model-pbxr8twwc-alecs-projects-2eeacfd8.vercel.app`
- Worker deploy: `https://ridge-odds-refresh.ridge-cfb.workers.dev`
  - Cron: `0 13 * * *`
  - `FORCE_SANDBOX=true`
  - Note: dashboard subdomain registration had not stuck (API 10007); created
    account subdomain `ridge-cfb` via Workers API, then deploy succeeded.
- Security probes:
  - POST `/run` no auth → **401**
  - POST `/run` wrong secret → **401**
  - GET `/` → **404** (no leak)
  - GET `/run` → **404** (no leak)
- ntfy test alerts (earlier): Worker path **200**, workstation **200**
- `configs/pipeline.yaml` **tracked**; GitHub repo **public** → ntfy topic
  publicly discoverable (flagged; no change made).

### Step 4 cycle (a) — manual before kickoffs
| Field | Value |
|-------|--------|
| Trigger | manual `POST /run` |
| Timestamp | `2026-09-24T19:57:30Z` |
| HTTP | 200 |
| Credits remaining | 99655 (~3 used) |
| Matched / unmatched | 71 / 0 |
| `carried_forward` | (not in summary JSON; history key minute-res) |
| History key | `sandbox/odds/2026/w4/2026-09-24T19:57.json` |
| Latest | `sandbox/latest/odds_snapshot.json` |
| Revalidate | **200** (preview) |

Next: cycle (b) after Thursday night kickoffs (~23:45 UTC).

## Phase 3 earlier (harness-only, morning)

### Delivered without Cloudflare/Vercel CLI auth
- Runbook: `docs/runbooks/odds_refresh.md`
- Worker prep: `FORCE_SANDBOX = "true"` in `workers/odds-refresh/wrangler.toml`
- Site: `ODDS_R2_PREFIX=sandbox` → reads `sandbox/latest/odds_snapshot.json`
  (predictions remain on live `latest/`)
- Two sandbox refresh cycles via `npm run sandbox:live` (same path as
  `FORCE_SANDBOX` Worker; no cron deploy available on this workstation)

| Cycle | `snapshot_at` | Credits | Remaining | Matched | Unmatched ridge | Keys | Revalidate |
|------:|---------------|--------:|----------:|--------:|----------------:|------|------------|
| 1 | `2026-09-24T18:46:21Z` | 3 | 99661 | 71 | 0 (0%) | `sandbox/odds/2026/w4/2026-09-24T18.json`, `sandbox/latest/odds_snapshot.json` | skipped (sandbox) |
| 2 | `2026-09-24T18:46:34Z` | 3 | 99658 | 71 | 0 (0%) | same hour key + latest overwrite | skipped (sandbox) |

- Local preview render (live week 4 artifacts + sandbox odds, flag on):
  screenshots in `webapp/site/docs/screenshots/odds/phase3-sandbox/` (8 PNGs).
  HTML confirmed Market column + Game Detail "Model and market".

### Auth blockers (operator action required)
| Tool | State |
|------|--------|
| `wrangler whoami` | not authenticated |
| `RIDGE_WRITE_API` / `RIDGE_READ_API` (`cfat_*`) | CF API **401** on token verify — cannot deploy Worker |
| `vercel whoami` | logged out — cannot set Preview `ODDS_SNAPSHOT_ENABLED` / `ODDS_R2_PREFIX` |

### Operator checklist to finish Phase 3 preview on real Vercel
1. `wrangler login` (or fresh Cloudflare API token with Workers + R2 bind scope)
2. `cd workers/odds-refresh && npx wrangler deploy` (FORCE_SANDBOX already true)
3. Put secrets per runbook (`ODDS_API_KEY`, revalidate, bypass, manual run, URL)
4. `vercel login` → Preview env: `ODDS_SNAPSHOT_ENABLED=true`, `ODDS_R2_PREFIX=sandbox`
5. Two cron cycles (or `POST /run` twice) and paste Worker logs into this notes file
6. **STOP** — do **not** set production flag or `FORCE_SANDBOX=false` until L7 sign-off

### Still blocked for production
- L7 counsel (DESIGN L7 OPEN)
- Operator flips Worker to live keys + production `ODDS_SNAPSHOT_ENABLED=true`


## Phase 2

### Delivered
- `ODDS_SNAPSHOT_ENABLED` (default false) gates all odds UI
- `lib/odds/*` optional load with failure isolation (never MaintenanceState)
- This Week Market column + page-level odds as-of
- Game Detail Model and market / consensus block + margin number line
- Copy: §6.1 disclaimer, What Ridge won't show, footer short line
- Fixture: `webapp/fixtures/odds_snapshot.json` (`fixture: true`, real Odds API consensus numbers remapped onto fixture game_ids)
- Screenshots: `webapp/site/docs/screenshots/odds/` (8 files)
- `npm test` 227 pass; `npm run build` green; copy grep clean

### Public copy strings changed
- `DISCLAIMER_TEMPLATE` — consensus odds for context; no picks/edges/expected profits
- `WHAT_RIDGE_WONT_SHOW_PARAGRAPHS` — same substance
- `FOOTER_DISCLAIMER_SHORT` — "Consensus odds for context only — no picks or edge claims."
- How-to-read key — "by X = expected winning margin" + market context note
- Game Detail More detail note — dropped "odds lines" phrasing

### Self-review vs §4 / anti-patterns
- Tokens only (`--text-secondary` for market figures); no new colors
- No sportsbook logos/names/affiliate links; no green/red agreement encoding
- No model-minus-market delta column
- Number line monochrome; documented §4.3 exception
- Flag off → zero odds DOM

### Still blocked for production
- L7 counsel; Phase 3 Worker cron + `ODDS_SNAPSHOT_ENABLED=true` on Vercel


## Phase 0

- ADR: `docs/webapp/adr/ADR-ODDS-SNAPSHOT.md` (Accepted)
- DESIGN / TASKS redlines applied (L7 OPEN for counsel)

## Phase 1

### Delivered

- `workers/odds-refresh/` — TypeScript Worker, vitest, wrangler.toml cron `0 13 * * *`
- Team map bundled from `configs/team_names.yaml` → `src/data/odds_team_map.json` (184 entries)
- Write allowlist: `odds/`, `latest/odds_snapshot.json`, `sandbox/`
- Tests: 22 passed (`npm test`)

### Sandbox live run (real Odds API)

`wrangler login` was not available on this workstation, so the equivalent harness
`npm run sandbox:live` ran the same match/consensus/write path with
`FORCE_SANDBOX` semantics (fixture=true, no revalidate).

| Metric | Value |
|--------|-------|
| Season / week | 2026 / 4 |
| Ridge games | 71 |
| Odds events returned | 71 |
| Matched (snapshot games) | 63 |
| Unmatched ridge games | **8 (11.3%)** |
| Credits this call (`x-requests-last`) | **3** |
| Remaining (after second sandbox run) | 99667 |
| Keys written | `sandbox/odds/2026/w4/2026-09-24T16.json`, `sandbox/latest/odds_snapshot.json` |
| Revalidate | skipped (sandbox) |

### STOP — unmatched > 5% (resolved 2026-09-24)

Root cause: FCS opponent display names from The Odds API keep uncommon mascot
suffixes that are **not** in `normalize_team_name`'s strip list and **not** in
`configs/team_names.yaml` (e.g. `Howard Bison` vs Ridge/CFBD `Howard`). Exact
match fails. Same behavior on the Python workstation path today.

**Operator chose option 1:** extend `configs/team_names.yaml` with FCS aliases
(W-ODDS block). Regenerated Worker bundle via `npm run export-team-map`.

**Re-run (sandbox:live):** matched **71/71**, unmatched ridge **0%**, credits **3**,
keys `sandbox/odds/2026/w4/2026-09-24T17.json` + `sandbox/latest/odds_snapshot.json`.
Gate cleared — Phase 2 unblocked pending operator go-ahead.
