# MLflow / Prefect UI exposure

DESIGN §10: MLflow and Prefect UIs must **never** be exposed off-host without authentication.

## Policy

- Default bind: `127.0.0.1` only (`mlflow server`, `prefect server start`).
- No port forwarding, reverse proxy, or tunnel to the public internet without auth (OAuth, VPN, or mTLS).
- Treat experiment metadata and flow-run logs as sensitive (game IDs, bet candidates, model paths).

## Verification (quarterly)

1. From an external network, confirm ports 4200 (Prefect) and 5000 (MLflow) are **not** reachable.
2. Review any `ssh -L` or cloud tunnel configs in operator notes.
3. Document result in `docs/notes/` or the quarterly restore-drill table in `odds_archive_backup.md`.

## If exposed

1. Stop the exposed service immediately.
2. Rotate API keys if request logs may have leaked.
3. Review Prefect/MLflow access logs for unauthorized reads.
