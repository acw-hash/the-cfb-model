# API-key scrub verification

DESIGN §10: raw odds archive request metadata must not contain API keys.

## What is enforced

- Live archival stores **response body only** (`archive_raw_response`) — no URL query params with `apiKey`.
- `ncaa_quant.pipelines.metadata.verify_raw_archive_scrub` scans archived files for forbidden patterns.
- Unit tests: `test_raw_archive_body_has_no_api_key`, `test_scrub_*`.

## Operator check (weekly or after restore drill)

```bash
uv run python -c "
from pathlib import Path
from ncaa_quant.pipelines.metadata import verify_raw_archive_scrub
for root in [Path('data/raw/odds_api'), Path('data/raw/odds_api_historical')]:
    if root.is_dir():
        v = verify_raw_archive_scrub(root)
        print(root, 'OK' if not v else v)
"
```

## If a violation is found

1. **Quarantine** the affected remote backup copy — do not sync further.
2. Identify whether the leak is in response body (upstream API bug) or operator tooling writing request metadata.
3. Delete or re-scrub offending files; re-run backup with `ncaa-quant ingest odds-backup --restore-drill`.
4. Rotate `ODDS_API_KEY` if the secret appeared in any off-machine copy.

See also: `odds_archive_backup.md` (replication + quarterly restore drill).
