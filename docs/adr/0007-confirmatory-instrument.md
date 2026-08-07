# ADR 0007: The 2026 paper trade is the confirmatory instrument; 2025 is a weakened secondary read

## Status

Accepted

## Context

DESIGN §7.2 item 9 reserves season 2025 as a lockbox, readable at most once per
year for a confirmatory report. ADR 0005's provenance work surfaced that the
reservation came too late: `docs/notes/D7.md` used 2025 weeks 1-4 as its
pre-registered confirmatory holdout on 2026-08-06, hours before the lockbox
designation was written. That read broke no rule at the time, and it is now
logged in `docs/lockbox_access.md`.

It does, however, weaken the instrument in a specific way. D7 was testing whether
the fundamental model's early-week combination weight against the closing line
replicates out of sample. 2025 returned `b2 = 0.376`, the largest early-week
effect of any season in the D7 table. A season that produced the strongest
version of a finding, and was used to confirm that finding, cannot then serve as
a clean test of a system built partly around it.

DESIGN §1.6 already names the intended answer: "live forward performance via
paper-trade (§16 item 2) is the confirmatory check on these success criteria —
backtest/promotion gates are necessary but not sufficient." The 2026 season is in
progress, so a genuinely untouched forward instrument is available.

## Decision

1. **The 2026 paper trade is the primary confirmatory instrument.** A full or
   half season of paper-traded, same-book, line-translated CLV under live
   information flow is what confirms or refutes §1.6, not any historical season.
   This makes §1.6's existing "confirmatory instrument" clause load-bearing
   rather than aspirational.
2. **2025 is demoted to a weakened secondary read.** It may still be read once
   after Phase 4 under the existing `lockbox_confirmatory_read` guard, but any
   report on it must state that weeks 1-4 were already used to refine the D7
   week-interaction finding, and must not be presented as an independent
   confirmation.
3. **2025 remains excluded from all development, HPO, ablation and promotion
   evaluation**, enforced by `evaluation/lockbox.py`. Demoting it does not
   release it into the training pool; a partially-read holdout is still worth
   more unread than read.
4. **Phase 4's authoritative walk-forward runs on 2019 and 2021-2024.** 2020 stays
   continuity-only per §7.2 item 5.

## Consequences

- The stop-rule adjudication in Phase 5 cannot lean on a lockbox confirmation.
  It has to be decided on the walk-forward seasons plus, if the numbers justify
  proceeding, live paper-traded CLV.
- Paper trading becomes schedule-critical rather than nice-to-have: the bet layer
  must be wired and capturing before 2026 Week 1, or the confirmatory instrument
  is lost for a year. This raises the priority of the Phase 5 bet-layer wiring
  relative to the original plan.
- The five-season walk-forward carries the stability gate that
  `configs/eval/encompassing.yaml` sets at 3 seasons positive. With 2025 removed
  the denominator is 5, not 6; the threshold was pre-registered as a count and is
  left unchanged rather than re-tuned after seeing which seasons remain.
- If the paper trade is inconclusive (too few bets, or a CI spanning zero), the
  honest outcome is "not yet confirmed" and another forward season, not a
  retroactive promotion of the 2025 read.
