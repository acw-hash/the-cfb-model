# Cadence shortfall alarm

Captured odds snapshot count fell below `expected − tolerance` within 24h (DESIGN §10).

## Threshold

Configured in `configs/pipeline.yaml`:

- `odds_snapshots_per_day` (default 6)
- `odds_cadence_tolerance` (default 1)
- Minimum acceptable in 24h: `odds_snapshots_per_day - odds_cadence_tolerance`

## Watchdog (S23)

Standalone flow `odds_cadence_watchdog` / deployment
`odds_cadence_watchdog/odds_cadence_watchdog` on work pool `default`, cron
`pipeline.odds_cadence_watchdog_cron` (default hourly). Independent of
`predict_publish`. Kept alive by `scripts/prefect_keepalive.ps1` via
`prefect worker start --pool default`.

## Immediate actions

1. Check `ingest_odds` Prefect deployment is running (`serve_ingest_odds` or keepalive).
2. Check `odds_cadence_watchdog` deployment is not paused and the default-pool worker is up.
3. Verify Odds API key and rate-limit headers in structlog.
4. Count raw files under `data/raw/odds_api/{date}/`.
5. Confirm off-machine backup still fresh per `odds_archive_backup.md`.

## Recovery

1. Restart Prefect keepalive / default-pool worker if deployment is stale.
2. Run manual ingest: `uv run ncaa-quant ingest odds --once`.
3. If API outage, expect STALE mode on next `predict_publish` — **suppress bets** until cadence recovers.

## Escalate when

- Shortfall persists > 24h
- STALE predictions published with age > `stale_odds_max_age_hours`
