# ADR 0004: `event_time_estimated` on games (Task 5 schema widen)

## Status

Accepted

## Context

Amended Task 5 item 4 (AUDIT-6) requires game-result `event_time` to use a
completion timestamp when available, otherwise kickoff + 5h (longer when OT is
flagged), with `event_time_estimated=True` **recorded**. Task 5's scope line
still says "Implement `src/ncaa_quant/ingestion/cfbd.py` only," which conflicts
with persisting a new column under pandera `strict=True` schemas.

CFBD `/games` does not expose a completion timestamp (only `start_date`,
`completed`, line scores, notes). The completion-timestamp branch is therefore
a forward-compatible hook for payload fields that may appear later.

Task 3 notes already allow schema widening as endpoints are wired.

## Decision

1. Widen **`GamesSchema` only** with non-null `event_time_estimated: bool`.
   Plays / drives / advanced_box inherit the resolved `event_time` from the
   games row (via ingest-time `game_event_by_id`) and do not duplicate the flag.
2. Treat this schema edit as in-scope for amended Task 5 (same precedent as the
   original Task 5 `TalentSchema` widen), despite the "cfbd.py only" line.
3. OT flag: `home`/`away` line-score arrays longer than 4, or `notes` matching
   `\bOT\b|overtime` (case-insensitive). Durations: 5h regulation, 7h OT.

## Consequences

- Re-staging games partitions is required after this change (`--force` or wipe).
- Test fixtures that `write_partition("games", …)` must supply the new column.
- Great Expectations `games` column set in `quality/expectations/suites.py` must
  include the column (exact-match suite).
- If CFBD later adds a real completion timestamp field, normalizers flip
  `event_time_estimated` to `False` without a further schema change.
