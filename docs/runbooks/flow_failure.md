# Flow failure alarm

Prefect flow entered `Failed` state or raised after exhausting retries (DESIGN §10).

## Symptoms

- ntfy/Telegram alert: `flow_failure` with flow name and state message
- Prefect UI shows red run; structlog JSON contains `*_failed` event

## Immediate actions

1. Open Prefect UI (`http://127.0.0.1:4200`) — **never expose off-host without auth**
   (see `ui_exposure.md`).
2. Identify the failing task from the run graph and stack trace.
3. Check whether idempotency already committed a partial partition:
   `data/pipeline_state/idempotency.json`.
4. If poisoned, inspect `data/pipeline_state/dead_letter/` for the partition key.

## Recovery

| Flow | Recovery |
|---|---|
| `ingest_odds` | Re-run manually: `uv run python -c "from ncaa_quant.pipelines.odds import ingest_odds_flow; ingest_odds_flow()"` |
| `postgame_ingest` | Re-run with explicit season/week after CFBD API is healthy |
| `predict_publish` | If odds ingest failed, confirm STALE stamp on outputs; do **not** confirm bets until fresh ingest succeeds |
| Others | Re-run the named flow; idempotency skips completed partitions |

## Escalate when

- Same partition fails 3× (dead-letter entry exists)
- STALE mode persists > `pipeline.stale_odds_max_age_hours`
- Quality gate also fired on the same partition
