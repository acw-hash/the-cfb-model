# Feature store & DVC workflow

Feature materialization (Task 9 / DESIGN §4.7) writes Parquet partitions under
`data/features/`:

```text
data/features/{feature_name}/v{version}/season={YYYY}/week={W}/part.parquet
data/features/{feature_name}/v{version}/season={YYYY}/week={W}/meta.json
```

`meta.json` records `spec_hash` (registry entry) and `content_hash` (sha256 of
`part.parquet`). Incremental builds skip a partition when both match.

Builders must only read history through `FeatureBuilder.as_of_join` /
`filter_event_time` (strict `event_time < as_of`). The leakage harness is
`ncaa_quant.features.pit_audit` — recompute a random sample with as-of-restricted
history and assert equality with stored values.

## DVC hooks

DVC is an **operator** tool, not a Python package dependency. After a feature
partition is written (or a whole feature version tree), track it:

```bash
# Single partition
dvc add data/features/<feature>/v<version>/season=<YYYY>/week=<W>

# Or the feature version root after a season build
dvc add data/features/<feature>/v<version>
```

In code, `ncaa_quant.features.materialize.dvc_add_partition(path)` returns the
`dvc add …` argv; pass `run=True` to shell out when `dvc` is on `PATH`.

Commit the generated `.dvc` sidecar (and `dvc.lock` / remote config when you
introduce a remote). Raw/staged trees follow the same pattern when you choose
to version them; feature outputs are the first required DVC surface for the
mapping layer.

Point-in-time queries against materialized frames can use
`ncaa_quant.data.asof.as_of_join` (builders) or
`ncaa_quant.features.materialize.duckdb_asof_join` (DuckDB ASOF, strict `<`).
