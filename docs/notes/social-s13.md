# S13 — Stand up the Prefect worker

**Date:** 2026-09-08  
**Export gate:** left `False` (`NCAA_QUANT_WEBAPP__EXPORT_ENABLED` / `webapp.export_enabled`).

## What was stood up

1. Dedicated Prefect API: `uv run prefect server start --host 127.0.0.1 --port 4200`
2. CLI pointed at it: `prefect config set PREFECT_API_URL=http://127.0.0.1:4200/api`
3. Process work pool: `uv run prefect work-pool create "default" --type process --no-prompt`
4. Attached existing `ingest_odds/ingest_odds` to pool `default` with
   `working_dir` / `path` = repo root (process worker otherwise crashed resolving
   entrypoint from a temp dir).
5. Worker: `uv run prefect worker start --pool default --type process`

**Keep-alive:** foreground Cursor/shell background processes only — **not** a
Windows service or Task Scheduler job. **Not durable across reboot.**

## Manual ingest

- Failed first attempt (`utopian-gopher`, Crashed) before `working_dir` fix.
- Success: `honest-oarfish` Completed
  - raw: `data/raw/odds_api/2026-09-08/20260908T161218369254Z.json`
  - staged rows written: **1352** (2026 total 64214 → 65566)
  - quota: **3** credits (`x-requests-last=3`; remaining 99997 → 99994)

## Schedules

`ingest_odds` cron `0 0,4,8,12,16,20 * * *` (active). Next three from ~16:12Z:

| # | UTC | ET |
|---|-----|-----|
| 1 | 2026-09-08 20:00 | 2026-09-08 16:00 EDT |
| 2 | 2026-09-09 00:00 | 2026-09-08 20:00 EDT |
| 3 | 2026-09-09 04:00 | 2026-09-09 00:00 EDT |

## Registered vs config-only

| Deployment | Status |
|---|---|
| `ingest_odds` | **Registered** (pool `default`, schedule active) |
| `predict_publish` (tuesday + refresh) | config-only — **not** registered |
| `postgame_ingest` | config-only |
| `weekly_update` | config-only |
| `settle_clv` | config-only |
| `capture_slot_close` | config-only |

No `predict_publish` deployment existed, so nothing publish-related could fire
when the worker started.
