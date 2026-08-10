# ADR 0010: Ingest-time row quarantine for out-of-bounds odds lines

**Status:** Accepted
**Date:** 2026-08-08

## Context

Historical Odds API envelopes occasionally include genuine book garbage —
suspended/placeholder postings such as spreads of ±600 or totals of 17.5 —
that fail `OddsSnapshotsSchema.line_sanity` (`|spread| < 70`, totals in
`[20, 100]`, DESIGN §8). Pandera validation on `ParquetStore.write_partition`
then aborts the whole historical unit after the raw archive has already been
written.

Task 7 already provides a quarantine flow (`quality/quarantine.py` →
`validation_results`, `is_quarantined(table, season, week)`). That flow is
**post-hoc and partition-wide**: a hard failure marks an entire
`(table, season, week)` partition so downstream consumers skip it. Reusing it
at ingest would either leave the write crashing or quarantine an entire week's
`odds_snapshots` for a handful of junk book rows.

## Decision

1. **Row-level ingest quarantine** in `odds_api.py`: before writing
   `odds_snapshots`, split the frame with the same bounds as
   `OddsSnapshotsSchema.line_sanity`. Good rows write to `odds_snapshots` (pandera
   bounds remain). Bad rows append to a sidecar
   `data/staged/odds_snapshots_quarantine/season=/week=/part.parquet` with
   `quarantine_reason` (`spread_out_of_bounds` / `total_out_of_bounds`) and
   provenance (`raw_archive_path`, `requested_at`; returned `event_time` and
   `decision_point` stay on the snapshot columns). Never drop a row without
   writing it to quarantine.

2. **Task 7 partition quarantine stays as-is.** It continues to own post-hoc
   quality runs and partition-level skip semantics. The ingest sidecar is salvage
   so a few OOB book lines cannot erase an otherwise valid snapshot slot.

3. **Archive ≠ slot complete.** Presence of a raw historical archive alone must
   not skip parse-and-write. Skip the API only when the archive exists and staged
   rows are present for that slot's returned `event_time` (matched on
   `decision_point` + `snapshot_source='historical'`), or an explicit empty-slot
   marker was written after a successful parse of an empty envelope. Otherwise
   replay from the archive at zero credits. `mark_unit_complete` fires only after
   every `request_time` in the unit has a successful parse-and-write.

## Consequences

- Staged `odds_snapshots` remains §8-clean under pandera.
- Mid-slot schema crashes become resumable without re-billing archived slots.
- Downstream feature code continues to ignore Task 7 quarantined partitions; it
  does not need to read `odds_snapshots_quarantine` unless auditing book garbage.
- Operators inspecting coverage should treat quarantine row counts as data-quality
  findings, not as missing API spend.
