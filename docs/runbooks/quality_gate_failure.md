# Quality gate failure alarm

A staged partition failed Great Expectations suites or custom validators (DESIGN §8 step 2).

## Symptoms

- Alert: `quality_gate_failure` from `postgame_ingest` or manual `ncaa-quant quality run`
- Partition quarantined under `data/staged/_quarantine/`
- Report in `docs/quality/reports/`

## Immediate actions

1. Read the markdown report for the failing partition (table, season, week).
2. Do **not** promote models or confirm bets that depend on the quarantined data.
3. If the failure is in `odds_snapshots` or crosswalk, check live ingest health.

## Recovery

1. Fix upstream data or ingestion bug.
2. Re-ingest the affected partition.
3. Re-run quality: `uv run ncaa-quant quality run --seasons YYYY`.
4. Clear quarantine only after a passing re-validation.

## Escalate when

- Hard failure on `games` or `plays` during an in-season week
- PIT audit flags temporal violations (`ncaa-quant quality pit-audit`)
