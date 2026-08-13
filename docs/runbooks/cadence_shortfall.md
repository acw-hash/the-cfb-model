# Cadence shortfall alarm

Captured odds snapshot count fell below `expected − tolerance` within 24h (DESIGN §10).

## Threshold

Configured in `configs/pipeline.yaml`:

- `odds_snapshots_per_day` (default 6)
- `odds_cadence_tolerance` (default 1)
- Minimum acceptable in 24h: `odds_snapshots_per_day - odds_cadence_tolerance`

## Immediate actions

1. Check `ingest_odds` Prefect deployment is running (`serve_ingest_odds` or `serve_all`).
2. Verify Odds API key and rate-limit headers in structlog.
3. Count raw files: `find data/raw/odds_api -name '*.json' -mtime -1 | wc -l` (Unix) or inspect `data/raw/odds_api/{date}/`.
4. Confirm off-machine backup still fresh per `odds_archive_backup.md`.

## Recovery

1. Restart Prefect worker if deployment is stale.
2. Run manual ingest: `uv run ncaa-quant ingest odds --once`.
3. If API outage, expect STALE mode on next `predict_publish` — **suppress bets** until cadence recovers.

## Escalate when

- Shortfall persists > 24h
- STALE predictions published with age > `stale_odds_max_age_hours`
