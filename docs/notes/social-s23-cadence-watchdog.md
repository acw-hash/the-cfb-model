# S23 — Standalone odds cadence watchdog

## Built

- `src/ncaa_quant/pipelines/cadence.py` — `check_odds_cadence` (moved out of
  `predict.py`), `odds_cadence_watchdog_flow`, `register_odds_cadence_watchdog`
  → Prefect work pool `default`, cron `pipeline.odds_cadence_watchdog_cron`
  (hourly UTC default).
- `predict.py` now imports `check_odds_cadence` from `cadence` (shared helper;
  publish path still calls it, but the watchdog never imports predict).
- `configs/pipeline.yaml` — `odds_cadence_watchdog_cron`, notifications
  provider switched to `ntfy` with topic set (token remains in `.env` only).
- `scripts/prefect_keepalive.ps1` — also keeps `prefect worker start --pool
  default` alive so the watchdog deployment is pulled after reboot/kill.
- Unit tests: `tests/unit/test_pipelines_cadence.py` (cadence math + AST
  guard that cadence.py does not import publish modules).

## Import surface (watchdog)

Quoted from `cadence.py` — no predict / webapp / social / betting provider:

- `ncaa_quant.config`
- `ncaa_quant.pipelines.notifications`
- `ncaa_quant.utils.logging`
- `prefect`

## Decisions

1. Cadence check lives in `cadence.py` rather than duplicating the raw-json
   mtime counter; predict re-exports via import to avoid drift.
2. Watchdog registers on the process work pool `default` (not `.serve()`), so
   it is independent of the ingest_odds serve process from S22.
3. Notification config is live (`provider: ntfy`); topic value is not recorded
   in this note.

## Does not cover

See S23 operator report — raw-archive count only; silent modes include paused
deployment, zero-row ingest, null `game_id` crosswalk, etc.
