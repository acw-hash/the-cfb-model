# Odds archive off-machine backup (E-1 / DESIGN §10)

Unbackfillable Odds API snapshots live under two on-disk archives. Disk failure
or a house-level event destroys data money cannot re-buy (live capture) or that
embodies spent historical credits (backfill). Replicate each off-machine within
**24h of capture / materialization**; run a restore drill at least **quarterly**.

## Two first-class archives — separate dest roots

| archive | source | dest (this workstation) |
|---|---|---|
| live | `data/raw/odds_api` | `D:\ncaa-quant-backups\odds_api` |
| historical | `data/raw/odds_api_historical` | `D:\ncaa-quant-backups\odds_api_historical` |

**Never share one dest root across both sources.** Each dest has its own
`current/` mirror, `snapshots/`, and `backup_manifest.json`. Mixing sources
under one dest would overwrite `current/` and corrupt restore-drill semantics.

C: is the NVMe project disk; D: is a different HDD. That covers SSD death. It
does **not** cover house fire / theft — promote each archive to a versioned
S3-class remote (R2 / B2 / S3) when credentials are available by pointing
`--dest` at that mount/sync path. Do not treat same-chassis D: as the final
E-1 destination once cloud exists.

## Dest layout (per archive)

```text
{dest}/
  current/                  # live mirror of that source only
  snapshots/{utc_ts}/       # point-in-time copies
  backup_manifest.json      # SHA-256 per file + created_at
  restore_drills/{utc_ts}/  # last restore-drill output
```

## `--dest` is required

The CLI does **not** default `--dest` to the live backup root. A silent default
would write the wrong tree when `--source` is historical (or any non-live
path). Always pass `--dest` explicitly for the matching archive.

`ODDS_RAW_BACKUP_ROOT` remains a library fallback only; do not rely on it for
operator runs of two archives.

## Backup (after capture or at least daily)

Live:

```bash
uv run ncaa-quant ingest odds-backup --source data/raw/odds_api --dest D:\ncaa-quant-backups\odds_api --restore-drill
```

Historical:

```bash
uv run ncaa-quant ingest odds-backup --source data/raw/odds_api_historical --dest D:\ncaa-quant-backups\odds_api_historical --restore-drill
```

The command fails if the latest manifest is older than 24h after a successful
write (freshness gate). Wire that check into the weekly manifest when Task 24
lands.

## Quarterly restore drill

1. Run each archive with `--restore-drill` (copies that dest’s `current/` →
   `restore_drills/{ts}/` and verifies SHA-256 against the manifest and the
   live source tree).
2. Log the drill date, destination, and result below (or in `docs/notes/`).

| date (UTC) | archive | dest | result | notes |
|---|---|---|---|---|
| 2026-08-07 | live | `D:\ncaa-quant-backups\odds_api` | pass | Initial Phase 2 drill; 25 files |
| 2026-08-09T14:59:03Z | live | `D:\ncaa-quant-backups\odds_api` | pass | digests ok; 36 files / 6,476,869 bytes; restore `restore_drills\20260809T145903Z` |
| 2026-08-09T14:59:49Z | historical | `D:\ncaa-quant-backups\odds_api_historical` | pass | digests ok; 2,111 files / 181,921,266 bytes; restore `restore_drills\20260809T145949Z` |

## API-key scrub

Raw archive request metadata must not contain API keys (DESIGN §10). Covered by
ingestion tests; if a restore ever shows keys in JSON, quarantine the remote
copy and re-scrub before the next sync.
