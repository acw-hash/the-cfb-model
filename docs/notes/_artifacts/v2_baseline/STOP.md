# V2-BASELINE STOP — market-feature leak

**Disposition:** STOP (do not publish market-aware; do not widen guard).

## Named leak(s)

- feature=`mkt_spread` game_id=401282809 season=2021 week=2 reason=`feature_event_time_at_or_after_kickoff` feature_et=2021-09-11T19:55:00+00:00 feature_row=d4f417860685715403d2026699ffd7b1 grade_row=9b41a47af717b584baaaee2f4662aeff
- feature=`mkt_total` game_id=401282809 season=2021 week=2 reason=`feature_event_time_at_or_after_kickoff` feature_et=2021-09-11T19:55:00+00:00 feature_row=d4f417860685715403d2026699ffd7b1 grade_row=9b41a47af717b584baaaee2f4662aeff
- feature=`mkt_n_books` game_id=401282809 season=2021 week=2 reason=`feature_event_time_at_or_after_kickoff` feature_et=2021-09-11T19:55:00+00:00 feature_row=d4f417860685715403d2026699ffd7b1 grade_row=9b41a47af717b584baaaee2f4662aeff
- feature=`mkt_is_missing` game_id=401282809 season=2021 week=2 reason=`feature_event_time_at_or_after_kickoff` feature_et=2021-09-11T19:55:00+00:00 feature_row=d4f417860685715403d2026699ffd7b1 grade_row=9b41a47af717b584baaaee2f4662aeff
- feature=`mkt_spread` game_id=401282066 season=2021 week=2 reason=`feature_event_time_at_or_after_kickoff` feature_et=2021-09-11T19:55:00+00:00 feature_row=6a4e27efe65f0e67b01af4ceab9df336 grade_row=76f7cd180c763a9e0b82f9f535309631
- feature=`mkt_total` game_id=401282066 season=2021 week=2 reason=`feature_event_time_at_or_after_kickoff` feature_et=2021-09-11T19:55:00+00:00 feature_row=6a4e27efe65f0e67b01af4ceab9df336 grade_row=76f7cd180c763a9e0b82f9f535309631
- feature=`mkt_n_books` game_id=401282066 season=2021 week=2 reason=`feature_event_time_at_or_after_kickoff` feature_et=2021-09-11T19:55:00+00:00 feature_row=6a4e27efe65f0e67b01af4ceab9df336 grade_row=76f7cd180c763a9e0b82f9f535309631
- feature=`mkt_is_missing` game_id=401282066 season=2021 week=2 reason=`feature_event_time_at_or_after_kickoff` feature_et=2021-09-11T19:55:00+00:00 feature_row=6a4e27efe65f0e67b01af4ceab9df336 grade_row=76f7cd180c763a9e0b82f9f535309631
- feature=`mkt_spread` game_id=401282189 season=2021 week=2 reason=`feature_event_time_at_or_after_kickoff` feature_et=2021-09-12T02:05:00+00:00 feature_row=1cfc3c1bd2a352460f298a9aed2f6bd9 grade_row=eb33289908d19f9fe085759989812b09
- feature=`mkt_total` game_id=401282189 season=2021 week=2 reason=`feature_event_time_at_or_after_kickoff` feature_et=2021-09-12T02:05:00+00:00 feature_row=1cfc3c1bd2a352460f298a9aed2f6bd9 grade_row=eb33289908d19f9fe085759989812b09

## Resolution path

Feature ladder: `resolve_lines_for_games(..., closing=False)` at decision `as_of`.
Grading ladder: `resolve_lines_for_games(..., closing=True)` at kickoff.

## Blast radius

- Unpublished market-aware RERUN_V2 exception rate (52.71%) — not a graded table.
- Any future market-aware publish that would consume the leaking feature.
- A3/A6 RERUN_V2 are market-off / CFBD-feature paths; blast radius for snapshot
  `mkt_*` leaks is market-aware stacks only.

## Fix scope (separate session)

Do **not** fix in V2-BASELINE. Scope a dedicated session to correct the
resolution path named above, re-audit, then reconsider publish.
