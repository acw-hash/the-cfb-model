# Data layout

On-disk conventions for the ncaa-quant pipeline (DESIGN §8 / §11). All paths
are relative to the repository root unless `paths.*` in config override them.

## Top-level directories

| Path | Role |
|---|---|
| `data/raw/{source}/{date}/` | Immutable raw JSON archives from each source. `{date}` is the UTC calendar date of the pull (`YYYY-MM-DD`). Sources include at least `cfbd`, `odds_api`, `odds_api_historical`, and `open_meteo`. |
| `data/staged/` | Typed Parquet tables written by the storage layer (`ParquetStore`). Hive partitions — see below. |
| `data/features/` | Materialized feature partitions; `{name}/v{version}/season=/week=` (see `docs/feature_store.md`). |
| `data/predictions/` | Model prediction artifacts for backtests and weekly runs. |

Raw archives are never mutated in place: a re-pull for the same source/date may
overwrite only after content-hash comparison at the ingestion layer (Task 5+).

## Staged Parquet partitions

Root: `data/staged/` (configurable via `paths.staged_dir`).

**Game-grained tables** — `games`, `plays`, `drives`, `advanced_box`,
`lines_historical`, `odds_snapshots`, `weather`:

```text
data/staged/{table}/season={YYYY}/week={W}/part.parquet
```

**Reference tables** — `teams`, `venues`, `coaches`, `rosters`, `talent`,
`returning_production`, `recruiting`, `portal`:

```text
data/staged/{table}/season={YYYY}/part.parquet
```

Writes go through `ncaa_quant.data.storage.ParquetStore`: atomic temp-file +
`os.replace`, idempotent when the normalized payload is byte-identical.

## Point-in-time columns

Every staged schema includes:

- `event_time` (UTC) — when the row's information became knowable
- `ingested_at` (UTC) — when the pipeline archived the row

Constraint: `event_time <= ingested_at`. Feature and rating code may only
consume rows via `ncaa_quant.data.asof.as_of_join` (strict
`event_time < as_of`).

## Source placeholders

```text
data/raw/cfbd/          # CFBD API archives (date dirs created at ingest)
data/raw/odds_api/      # Odds API snapshot archives
data/raw/open_meteo/    # Open-Meteo archive + forecast archives (Task 6)
data/staged/            # Parquet store root
data/features/
data/predictions/
```
