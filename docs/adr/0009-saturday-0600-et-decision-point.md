# ADR 0009: Saturday 06:00 ET historical decision point

## Status

Accepted

## Context

DESIGN §9.8 and ADR 0002 name Saturday **06:00** ET as a production
daily-refresh decision point (with Thursday 06:00 ET). Task 5B's first
historical pull has room under the upgraded ~100k cycle quota to add one
Saturday wall-clock snapshot before spend locks the schedule.

A draft briefly registered `saturday_0900_et` (ADR 0008) as a mid-morning
variant. That diverged from DESIGN without a live-ops commitment to 09:00.
No historical spend occurred under 09:00.

## Decision

Register **`saturday_0600_et`** (Saturday 06:00 America/New_York → UTC via
ZoneInfo) as a pre-registered historical decision point alongside
`tuesday_0600_et` and `slot_close`. This mirrors DESIGN §9.8 / ADR 0002 exactly.
ADR 0008 (`saturday_0900_et`) is superseded; do not add a second Saturday time
without an ADR that accepts the comparability break.

## Consequences

- Historical ladder includes one Saturday request per CFB week (~77 × 30 ≈ 2,310
  credits for 2021–2025).
- Changing this schedule invalidates CLV comparability with runs under the prior
  two-DP schedule (documented in `docs/notes/05b.md`).
- Production live Thu–Sat refresh should use the same 06:00 ET Saturday wall
  clock for backtest↔live instrument parity.
