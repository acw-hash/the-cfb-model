# ADR 0016: Unplayed games keep `event_time = kickoff + duration`

## Status

Accepted (W9-L Amendment 1, D1).

## Context

DESIGN §8 required temporal sanity `event_time <= ingested_at` on every staged
row. CFBD `/games` has no completion timestamp, so `event_time` is kickoff + 5h
(7h if OT) with `event_time_estimated=True` (ADR 0004).

For **unplayed** future games that duration is after `ingested_at`. W9-I
clamped those rows to `event_time = ingested_at` so Pandera would accept the
2026 schedule. `WeekDecisionCalendar.from_games` treats `games.event_time` as
kickoff, so the clamp made the Tuesday decision clock follow the ingest civil
week and move on re-ingest (W9-L Phase 0.1).

The clamp is the Labor-Day week-align defect in a new form. Historical
completed rows were never clamped; their decision points must not move.

`event_time_estimated` is independent of the clamp. ADR 0004 records whether
`event_time` is a real completion timestamp or kickoff+duration. Removing it
would restage every games partition and supersede ADR 0004.

## Decision

1. **Revert the unplayed clamp.** `normalize_games_payload` leaves
   `event_time = kickoff + duration` for unplayed rows. Kickoff remains on
   `start_date`. `WeekDecisionCalendar` is unchanged and still reads
   `event_time`.
2. **Scope the check, do not drop it.** Pandera
   `_TimedModel.event_time_le_ingested_at` and quality
   `check_temporal_sanity` apply only to rows with `completed == True`.
   Tables without a `completed` column are still checked in full.
3. **Keep `event_time_estimated`.** ADR 0004 stands. The W9-I clamp test is
   replaced by an assertion that unplayed rows keep kickoff+duration and still
   validate.
4. **DESIGN §8** is amended: no `event_time > ingested_at` on **completed**
   rows. Unplayed schedule rows may post-date ingest.

## Consequences

- 2026 (and any later) unplayed `games` partitions must be re-normalized
  before a production calendar is built from staged `event_time`. Existing
  clamped rows still carry ingest timestamps until that rewrite.
- A later incremental ingest of an unplayed game does **not** move
  `event_time` (it is kickoff-derived, not ingest-derived).
- Historical completed partitions are unchanged; 2024 week-5 Tuesday remains
  `2024-09-24T10:00:00Z` when rebuilt from staged `event_time`.
