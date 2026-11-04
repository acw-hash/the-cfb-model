# G-1A — New-machine setup (workstation sync)

**Date:** 2026-11-04  
**Source tip (main):** `61d9a9085d20ad88ca35e9c79262d633ce845c51`

Bring up a second clone that can **ingest / research / dry-run**. Do **not** enable export or publish until the one-machine rule below is understood.

## Rule: only ONE machine publishes

- Exactly one workstation may set `NCAA_QUANT_WEBAPP__EXPORT_ENABLED=true` and run publish / R2 `latest/` writes.
- `tier_state`, `tier_changes`, and `publish_history` are local append-only ledgers. Two machines publishing will fork history and corrupt idempotency.
- The Cloudflare Worker (`ridge-odds-refresh`) may refresh odds on its own schedule; that is separate from publish. Still: do not run Operator `/run` + publish from two laptops in the same window without coordinating.
- Default: leave export **false** on every new machine.

## Python / uv

| Tool | Required | Notes on source machine (2026-11-04) |
|------|----------|--------------------------------------|
| Python | **3.11.x** (`requires-python = ">=3.11,<3.12"`) | `.venv` → 3.11.15 via uv |
| uv | recent stable | 0.11.28 on source |

`python` on PATH may be 3.10 (Anaconda); ignore it — always use `uv` / `.venv`.

### Install

```bash
git clone <origin-url> the-cfb-model
cd the-cfb-model
git checkout main   # or a feature branch

# Pin interpreter, then sync deps (dev group includes pytest/ruff/mypy)
uv python install 3.11
uv sync --group dev
# equivalent: make install

# Sanity
uv run python --version   # expect 3.11.x
make test
```

Copy `.env.example` → `.env` and fill **names below** (values never in git).

Optional services (local processes only — no Docker in Phase 1):

```bash
uv run mlflow server   # if you need tracking UI; mlruns/ is gitignored
uv run prefect server start
```

Worker (Cloudflare) secrets live in Wrangler, not the laptop `.env`, except when calling `/run` from a script that reads `MANUAL_RUN_SECRET` from the environment.

## Env var NAMES (never commit values)

### SecretsSettings / workstation `.env` (from `src/ncaa_quant/config.py` + `.env.example`)

| Name | Role |
|------|------|
| `CFBD_API_KEY` | CollegeFootballData ingest |
| `ODDS_API_KEY` | The Odds API ingest / Worker |
| `R2_ACCESS_KEY_ID` | R2 writes (publish) |
| `R2_SECRET_ACCESS_KEY` | R2 writes (publish) |
| `WEBAPP_REVALIDATE_SECRET` | Bearer for `/api/revalidate` |
| `VERCEL_AUTOMATION_BYPASS_SECRET` | Preview/protection bypass when needed |
| `NTFY_AUTH_TOKEN` | Optional Prefect alerts |
| `TELEGRAM_BOT_TOKEN` | Optional Prefect alerts |

### Non-secret config (same `.env` / process env; nested via `NCAA_QUANT_` + `__`)

| Name | Role |
|------|------|
| `NCAA_QUANT_WEBAPP__EXPORT_ENABLED` | Must stay `false` unless this is the sole publish machine |
| `NCAA_QUANT_WEBAPP__R2_BUCKET` | e.g. ridge-artifacts |
| `NCAA_QUANT_WEBAPP__R2_ENDPOINT_URL` | R2 S3 endpoint |
| `NCAA_QUANT_WEBAPP__REVALIDATE_URL` | Vercel revalidate URL |
| `NCAA_QUANT_SOCIAL__ENABLED` | Local social sidecar (never R2) |
| `NCAA_QUANT_PIPELINE__NOTIFICATIONS__PROVIDER` | `null` / `ntfy` / `telegram` |
| `NCAA_QUANT_PIPELINE__NOTIFICATIONS__NTFY_TOPIC` | ntfy topic |
| `NCAA_QUANT_PIPELINE__NOTIFICATIONS__TELEGRAM_CHAT_ID` | Telegram chat |

### Worker / Wrangler secrets (not laptop git; needed to call `/run`)

| Name | Role |
|------|------|
| `MANUAL_RUN_SECRET` | `Authorization: Bearer` for Worker `POST …/run` |
| `ODDS_API_KEY` | Worker odds pull |
| `WEBAPP_REVALIDATE_SECRET` | Worker → site revalidate |
| `WEBAPP_REVALIDATE_URL` | Production revalidate URL |
| `VERCEL_AUTOMATION_BYPASS_SECRET` | Optional |
| `NTFY_*` / `TELEGRAM_*` | Optional Worker alerts |

### X / social posting

No X/Twitter API keys in `SecretsSettings`. Card/thread work is local (`scripts/_s6_*_card.py`, `data/social/`). Do not expect env-based X auth from this repo.

### Gitignore confirmation

`.env`, `.env.*` (except `.env.example`), `data/**` (except `.gitkeep` + force-added ledgers), `mlruns/`, `backup_latest_*/` are gitignored. Never force-add secrets.

## Runtime data: copy vs rebuild

Sizes measured on the source workstation 2026-11-04. All under `data/**` are gitignored unless noted.

| Path | Size (approx) | Rebuild from git + API keys? | Action on new machine |
|------|---------------|------------------------------|------------------------|
| `data/raw/cfbd/` | **1,869 MB** (1298 files) | **Yes** — `ncaa-quant ingest cfbd` (slow, quota) | Prefer **copy**; else re-ingest seasons you need |
| `data/staged/{games,plays,drives,advanced_box}/` | **~54 MB** combined | **Yes** — produced by CFBD ingest/stage | Comes with raw copy or re-ingest |
| `data/raw/odds_api/` | **45 MB** | Partial — only **new** snapshots | Copy if you need history |
| `data/raw/odds_api_historical/` | **173 MB** | Partial — historical endpoint + credits | Copy recommended |
| `data/staged/odds_snapshots/` | **70.5 MB** (116 parquet; seasons 2021–2026) | **No for past wall-clock snapshots.** Historical decision-point backfill can recreate some slots; live cadence snapshots **before today cannot be rebuilt**. Needed by `price_at_post_check` / CLV | **Must copy** for settlement / post-check |
| `data/webapp/tier_state.json` | **0.05 MB** | **No** — live ledger, not in git | **Copy** if this machine will publish; else omit and do not publish |
| `data/webapp/tier_changes.jsonl` | **0.39 MB** | **No** | **Copy** with tier_state |
| `data/webapp/publish_history/` | **1.7 MB** (12 jsonl) | **No** — append-only; not in git | **Copy** if publishing or auditing prior publishes |
| `data/social/posted_calls_2026_w{4–9}.json` | ~4–11 KB each | **Tracked in git** (force-add) | `git pull` sufficient |
| `data/social/posted_calls_2026_w2.json` | ~4 KB | Live-only (untracked) | Copy if needed |
| `data/social/artifacts_cache/` | **4.6 MB** | Mostly **yes** — refresh from last publish / R2 `latest/` | Copy for offline cards; else re-pull artifacts |
| `data/social/` (rest: logos, season trees) | **~23 MB** total | Mixed; charts regenerable | Optional copy |
| `data/registry/` (+ model fit artifacts) | **33 MB** | **Yes** but expensive (re-fit) | **Copy** for parity with production identity |
| `data/backtests/` | **126 MB** | **Yes** — re-run evals | Optional |
| `data/pipeline_state/` (idempotency, dead_letter) | small + **~1 GB** `prefect_logs` | Idempotency: **copy if publishing**; logs disposable | Skip logs; copy `idempotency.json` if continuing same machine role |
| `mlruns/` | **29 MB** | Optional / rebuildable | Skip unless you care about old runs |
| `~/.prefect/` (DB, profiles) | local to user | **Rebuild** — `prefect server start` | Fresh OK |
| `out/social/` | **26 MB** | Regenerable from social render | Optional |
| `webapp/fixtures/*` | tracked | Fixture/demo only — **not** live R2 `latest/` | Already in git |

### Unrecoverable without a copy

1. **Odds snapshots with past `as_of` timestamps** used for price-at-post / CLV (especially live 6×/day cadence).  
2. **`data/webapp` ledgers** (`tier_state`, `tier_changes`, `publish_history`) — git does not hold them.  
3. Any **R2 `backup_latest_*`** folders kept only on disk (gitignored).

CFBD games/plays/drives/advanced **can** be re-ingested with `CFBD_API_KEY`. Prefer a filesystem copy to save days of quota and wall time.

## Suggested copy pack (publish-capable machine)

Copy these trees from the current publish workstation (rsync/robocopy), then clone git + `.env` names:

```
data/raw/
data/staged/
data/webapp/
data/registry/
data/social/          # optional but handy; posted_calls w4–w9 also come from git
data/pipeline_state/idempotency.json
```

Skip: `data/pipeline_state/prefect_logs/`, `mlruns/`, `out/`, `backup_latest_*/`.

## Prefect / Worker notes

- Prefect: local process only (`uv run prefect server start`). Flows use `data/pipeline_state` for idempotency/dead letters. A new machine gets a fresh Prefect DB under `~/.prefect` unless you deliberately migrate it (usually unnecessary).
- MLflow: local `mlflow server`; tracking dir `mlruns/` gitignored.
- Worker `ridge-odds-refresh`: deployed separately; laptop needs `MANUAL_RUN_SECRET` only to trigger `/run`. Do not redeploy Worker secrets from a second machine casually.
- Phase 1: **no Docker** for the Python pipeline.

## Branches pushed in G-1A (short hashes)

| Branch | Tip |
|--------|-----|
| `main` | `61d9a90` |
| `soc-3-market-map` | `b43247a` (already on origin) |
| `soc-2-recovered` | `3a75f06` |
| `soc-1-charts` / `soc-2-charts` / `model-hfa-7` | `40cd8b5` |
| `model-hfa-v4-wip` | `390dba3` |
| `keep/week4-local` | `ea0fc2e` |
| `w10-fix-kickoff` | `feecbc9` |
| `w10-ui` | `724c2ae` |
| `w9-d` | `95c85e1` |
| `w9-pub1` | `814a8b0` |
| `w9-pub1-resolve` | `6dc79bc` |

No merges; no force-push; `main` history untouched beyond normal fast-forward commits already on origin.
