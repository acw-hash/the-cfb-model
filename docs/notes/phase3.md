# Phase 3 — Task 5B historical Odds API pull

Date: 2026-08-07 (opened) → 2026-08-09 (closed)

**Status: COMPLETE.** Full 2021–2025 historical ladder pulled under the
pre-spend lock of **56,400** credits. Live capture ran uninterrupted
throughout (11,322 → **30,804** rows). Acceptance evidence in
`docs/notes/05b.md`.

## What ran

| Step | When | Result |
|---|---|---|
| Estimate gate (tue + slot_close only) | 2026-08-07 | **54,090** > 20k ceiling → STOP |
| Credit-unit probe (h2h=10, 3-markets=30) | 2026-08-07 | PASS; formula confirmed |
| Dual ceiling raise **20000 → 60000** | 2026-08-07 | `configs/data.yaml` + `DataConfig` |
| Add `saturday_0600_et` (ADR 0009; supersedes ADR 0008 `saturday_0900_et`) | 2026-08-07 | Locked re-estimate **56,400** |
| Calibration probe (2021 W1 tuesday) | 2026-08-07 | `x-requests-last=30`; remaining 99,958 |
| Ingest quarantine + archive-replay (ADR 0010) | 2026-08-08 | Mid-run crash fix; 0 API spend; 796 staged / 4 quarantined replayed |
| Authorized resume backfill 2021–2025 | 2026-08-08 | exit 0; lifetime spend **56,400** exact; remaining **43,579** |
| Post-backfill acceptance report | 2026-08-08 | 2021–2024 metrics; 2025 hygiene-only |
| Historical + live backup / restore drill | 2026-08-09 | both PASS (`docs/runbooks/odds_archive_backup.md`) |
| Lockbox hygiene rule amend (option a) | 2026-08-09 | `docs/lockbox_access.md`; audit PASS |

### `saturday_0600_et` decision

DESIGN §9.8 Saturday morning DP registered as **`saturday_0600_et`** (Sat 06:00
America/New_York), not the earlier ADR 0008 `saturday_0900_et` proposal.
ADR 0009 records the switch **before any Saturday spend**. Changing DPs later
invalidates backtest comparability.

### Dual ceiling raise

`odds_historical_credit_ceiling` raised **20000 → 60000** in both
`configs/data.yaml` and the `DataConfig` default (same integer). Sized for a
~100k cycle with resume headroom; deliberately below full quota. Full ladder
exit 0 under ceiling without `--force`.

### Quarantine / replay fix mid-run (ADR 0010)

2021 W1 `slot_close` crashed on 4 out-of-bounds book rows after the raw
archive landed. Archive-only skip would have marked the unit complete without
staging ~796 good rows. Fix: row-level split to `odds_snapshots_quarantine`;
archive presence no longer satisfies slot completion — replay parse-and-write
from archive when staged lacks that returned `event_time`. Recovery of the
crashed slot: **0 credits**.

### Live capture uninterrupted

| Checkpoint | Live `odds_snapshots` rows (season 2026) |
|---|---:|
| Pre-pull baseline (`docs/notes/data-check.md`) | 11,322 |
| During / after historical backfill | **30,804** |

Historical seasons 2021–2024 have **0** live rows. Historical pull did not
mutate live partitions.

## Preconditions (at open — retained)

| Season | games | teams | lines_historical | weeks |
|---:|---:|---:|---:|---:|
| 2021 | 887 | 670 | 4420 | 15 |
| 2022 | 896 | 672 | 5169 | 15 |
| 2023 | 910 | 672 | 4651 | 15 |
| 2024 | 920 | 679 | 5520 | 16 |
| 2025 | 934 | 681 | 5872 | 16 |

## Final locked estimate (spent)

```text
total_requests=1880
total_credits=56400
ceiling=60000
  season 2021: requests=351 credits=10530
  season 2022: requests=360 credits=10800
  season 2023: requests=355 credits=10650
  season 2024: requests=401 credits=12030
  season 2025: requests=413 credits=12390
```

Lifetime spend = lock = **56,400**. Final remaining = **43,579**.

## Decision-point schedule (frozen as spent)

| Name | Status |
|---|---|
| `tuesday_0600_et` | KEEP (spent) |
| `saturday_0600_et` | KEEP (spent; ADR 0009) |
| `slot_close` | KEEP (spent) |
| T−6h / T−1h | Out of scope |
| Hourly live cadence change | Out of scope |
| 2025 evaluative read | Out of scope (lockbox) |

## BLOCKING prerequisites for Task 16

Recorded from Task 5B acceptance / investigation. Fix before relying on
walk-forward as-of joins against this historical corpus.

1. **Week definition is an information-set bug.** `week_of` and CFBD
   `games.week` agree on **4 of 3,609** games. Feeding CFBD week into
   `week_decision_as_of` puts `as_of` after kickoff for **3,445** games.
   Recommended resolution: CFBD week canonical for labels and planning;
   `week_of` a documented calendar helper only, never compared as equal;
   stamp odds `week` from the matched game post-crosswalk.

2. **Crosswalk FCS near-duplicate failure.** North Carolina Central matched a
   mislabeled "North Carolina" listing, producing a false **−24.5** reconcile
   outlier (NCCU @ UCLA). Needs alias matching with manual quarantine, never
   silent merge.

3. **Post-kickoff rows must be excluded** from closing-line lookup and
   reconcile. Uncorrected vs filtered distributions in `docs/notes/05b.md`
   (§ Acceptance close-out). Stock reconcile defaults unchanged until Task 16.

## Out of scope (explicit)

- T−6h / T−1h decision points
- Hourly live cadence changes
- 2025 evaluation (lockbox; hygiene counts only under amended
  `docs/lockbox_access.md`)
