# Task 5B-VERIFY — Historical odds backfill validation

**Date:** 2026-08-10  
**Scope:** Read-only against `data/` (no API spend, no model/feature code).  
**Probe:** `uv run python scripts/_verify_5b.py`  
**Seasons pulled:** 2021–2025. Evaluative metrics for **2021–2024** only; 2025 is lockbox hygiene (`docs/lockbox_access.md`).

---

## Acceptance paste

### 1. Coverage table — zero unexplained gaps

Progress markers (= credit-spend / unit-complete truth) vs planned units from
`plan_historical_units`. Silent-gap scan: every completed unit’s request times
have staged `odds_snapshots` rows at the archive’s returned `event_time` **or**
an `_empty_slots` marker. `_empty_slots` count on disk: **0**.

| season | decision_point | expected | completed markers | marker % | silent_gap_units | lockbox |
|---:|---|---:|---:|---:|---:|:---:|
| 2021 | tuesday_0600_et | 15 | 15 | 100.0 | 0 | |
| 2021 | saturday_0600_et | 15 | 15 | 100.0 | 0 | |
| 2021 | slot_close | 15 | 15 | 100.0 | 0 | |
| 2022 | tuesday_0600_et | 15 | 15 | 100.0 | 0 | |
| 2022 | saturday_0600_et | 15 | 15 | 100.0 | 0 | |
| 2022 | slot_close | 15 | 15 | 100.0 | 0 | |
| 2023 | tuesday_0600_et | 15 | 15 | 100.0 | 0 | |
| 2023 | saturday_0600_et | 15 | 15 | 100.0 | 0 | |
| 2023 | slot_close | 15 | 15 | 100.0 | 0 | |
| 2024 | tuesday_0600_et | 16 | 16 | 100.0 | 0 | |
| 2024 | saturday_0600_et | 16 | 16 | 100.0 | 0 | |
| 2024 | slot_close | 16 | 16 | 100.0 | 0 | |
| 2025 | tuesday_0600_et | 16 | 16 | 100.0 | 0 | Y |
| 2025 | saturday_0600_et | 16 | 16 | 100.0 | 0 | Y |
| 2025 | slot_close | 16 | 16 | 100.0 | 0 | Y |

**Silent gaps: none.** No re-fetch plan.

Stock `coverage_report` (2021–2024; week-join uses `odds.week` from
`week_of(kickoff)` vs CFBD `games.week`) still shows the known undercount on
2021 `tuesday_0600_et` and `slot_close` (**14/15**). Markers are 15/15 — same
`week_of` vs CFBD week mismatch documented in `docs/notes/05b.md` (not a spend
gap).

**Quarantine sidecar** (`odds_snapshots_quarantine`): **434** rows.

| season | n |
|---:|---:|
| 2021 | 72 |
| 2022 | 56 |
| 2023 | 128 |
| 2024 | 74 |
| 2025 | 104 (hygiene only) |
| **total** | **434** |

| quarantine_reason | n | book (top) | n | market | n |
|---|---:|---|---:|---|---:|
| total_out_of_bounds | 324 | fanduel | 162 | total | 324 |
| spread_out_of_bounds | 110 | draftkings | 118 | spread | 110 |
| | | betmgm | 108 | | |
| | | williamhill_us | 46 | | |

Nonzero quarantine is expected (ADR 0010 / line-sanity split). Reasons are
fully explained: book garbage outside `|spread|<70` or totals ∈ `[20,100]`.

---

### 2. Timestamp discipline (20-row real-data sample)

Sampled 20 historical archives across 2021–2024 (seed=42, ≥5/season). For each:
parsed envelope `timestamp` == filename returned stamp; staged rows exist at that
returned `event_time` (never at the requested `date` when they differ).

| # | requested (UTC) | envelope returned | gap_s | staged@returned | ok |
|---:|---|---|---:|---:|:---:|
| 1 | 2021-12-23T23:55:00Z | 23:55:00Z | 0 | 638 | Y |
| 2 | 2021-09-12T02:10:00Z | 02:05:00Z | 300 | 230 | Y |
| 3 | 2021-09-03T21:55:00Z | 21:55:00Z | 0 | 1000 | Y |
| 4 | 2021-10-09T21:55:00Z | 21:55:00Z | 0 | 740 | Y |
| 5 | 2021-10-02T23:55:00Z | 23:55:00Z | 0 | 476 | Y |
| 6 | 2022-09-30T23:25:00Z | 23:20:40Z | 260 | 1284 | Y |
| 7 | 2022-09-16T23:55:00Z | 23:55:00Z | 0 | 1132 | Y |
| 8 | 2022-09-10T16:55:00Z | 16:55:00Z | 0 | 1222 | Y |
| 9 | 2022-12-27T23:40:00Z | 23:35:39Z | 261 | 418 | Y |
| 10 | 2022-11-19T23:55:00Z | 23:50:38Z | 262 | 398 | Y |
| 11 | 2023-09-09T14:55:00Z | 14:50:42Z | 258 | 1294 | Y |
| 12 | 2023-11-25T23:55:00Z | 23:50:38Z | 262 | 530 | Y |
| 13 | 2023-11-01T22:55:00Z | 22:50:43Z | 257 | 1384 | Y |
| 14 | 2023-09-01T22:55:00Z | 22:50:42Z | 258 | 1114 | Y |
| 15 | 2023-09-01T22:25:00Z | 22:20:42Z | 258 | 1116 | Y |
| 16 | 2024-09-07T17:55:00Z | 17:50:38Z | 262 | 1494 | Y |
| 17 | 2024-09-26T23:25:00Z | 23:20:39Z | 261 | 1316 | Y |
| 18 | 2024-09-28T19:55:00Z | 19:50:38Z | 262 | 906 | Y |
| 19 | 2024-11-09T20:55:00Z | 20:50:38Z | 262 | 814 | Y |
| 20 | 2024-11-23T16:55:00Z | 16:50:39Z | 261 | 1230 | Y |

- **Sample max (requested − returned):** **300 s**
- **All 1,456 archives 2021–2024:** max gap **540 s**, median **261 s**
  (the two known 540 s post-Sept-2022 exceptions from `05b.md`; not corrected)

---

### 3. `n_books_available` by season (2021–2024)

Per distinct `(event_time, game_key)` historical snapshot. **Null count: 0** on
every historical row.

| season | n_snapshot_events | min | median | max | mean |
|---:|---:|---:|---:|---:|---:|
| 2021 | 15933 | 1 | 3.0 | 4 | 2.938 |
| 2022 | 16072 | 1 | 3.0 | 4 | 3.124 |
| 2023 | 16514 | 1 | 4.0 | 4 | 3.444 |
| 2024 | 21726 | 1 | 4.0 | 4 | 3.188 |

**Pattern confirmed (§3.4):** earlier seasons have materially fewer books
(2021 median 3 → 2024 median 4; mean 2.938 → 3.188). Peak mean is 2023 (3.444);
2024 adds more thin-coverage events so mean dips while median stays 4. Cap
remains 4 books in the configured region set. Caveat every cross-season “best
price” / dispersion figure with this covariate — never pool into one headline.

---

### 4. Reconciliation: CFBD close vs `slot_close` (uncorrected)

`diff = Odds API slot_close − CFBD close`. Tolerance =
`CFBD_SLOT_CLOSE_TOLERANCE = 1.5` (`quality/validators.py`). **No offset
applied.**

| season | market | n | mean | median | p95 \|diff\| | share \|diff\| > 1.5 |
|---:|---|---:|---:|---:|---:|---:|
| 2021 | spread | 729 | −0.071 | 0.0 | 2.0 | 6.04% |
| 2021 | total | 729 | −0.006 | 0.0 | 1.5 | 3.70% |
| 2022 | spread | 755 | +0.067 | 0.0 | 2.0 | 5.96% |
| 2022 | total | 755 | −0.019 | 0.0 | 2.0 | 7.02% |
| 2023 | spread | 770 | −0.090 | 0.0 | 3.0 | 13.90% |
| 2023 | total | 670 | −0.057 | 0.0 | 3.0 | 14.18% |
| 2024 | spread | 778 | −0.080 | 0.0 | 4.0 | 17.61% |
| 2024 | total | 776 | −0.080 | 0.0 | 3.0 | 13.14% |

Overall (2021–2024): spread n=3032 mean=−0.044 median=0; total n=2930
mean=−0.040 median=0. **Median bias ≈ 0** — not a systematic offset to
“correct.” Tail share beyond 1.5 pts rises in later seasons (soft-book /
post-kickoff / FCS@FBS / thin-book instrument disagreement). Data-quality
finding per ADR 0002 / §2.7; write-up only.

---

### 5. Crosswalk match rate (2021–2024)

Unique `odds_event_id` rows in `odds_cfbd_game_crosswalk`.

| season | events | matched | match % | unmatched | unmatched reasons |
|---:|---:|---:|---:|---:|---|
| 2021 | 1208 | 821 | 68.0% | 387 | name_normalization_miss 326; no_cfbd_pair_within_tol 61 |
| 2022 | 1236 | 803 | 65.0% | 433 | name_normalization_miss 413; no_cfbd_pair_within_tol 20 |
| 2023 | 925 | 800 | 86.5% | 125 | name_normalization_miss 119; no_cfbd_pair_within_tol 6 |
| 2024 | 960 | 791 | 82.4% | 169 | name_normalization_miss 147; no_cfbd_pair_within_tol 22 |

No `kickoff_outside_36h` or `ambiguous_window` rows observed in this pull’s
crosswalk status field (unmatched are status=`unmatched`).

**CFBD FBS–FBS games with no matched Odds event (FINDING):**

| season | n |
|---:|---:|
| 2021 | 51 |
| 2022 | 35 |
| 2023 | 47 |
| 2024 | 46 |

Dominant cause: **name-map gaps** between Odds API bare names and CFBD
`teams.school`, not FCS-vs-FCS (those are expected §1.5 non-goals among the
unmatched *events*). Highest-frequency Odds names among unmatched events:

| Odds name (unmatched event side) | ~count/season | CFBD school |
|---|---:|---|
| `Appalachian State` | 12–14 | `App State` |
| `UMass` | 11–12 | `Massachusetts` |
| `Southern Mississippi` | 12–13 | `Southern Miss` |
| `Sam Houston State` | 1–13 | `Sam Houston` (FCS until 2023; FBS 2023+) |
| FCS nicknames (`… Seawolves`, `… Braves`, …) | many | often absent / FCS-only |

`configs/team_names.yaml` maps mascot forms (e.g. `App State Mountaineers` →
`App State`) but **not** the bare Odds strings above. Also:
`Southern Miss Golden Eagles` → `Southern Mississippi` points at a non-CFBD
school string — alias direction bug / FINDING (do not fix in this verify task).

**Unmatched FCS-vs-FCS / FCS@FBS nickname events:** expected; not a backfill
failure. **Unmatched FBS–FBS via alias miss:** finding for a later name-map
patch (not a re-fetch).

---

### 6. Credit spend reconciliation (closes 23-FIX P2-8)

No live API call this session. Figures from the authorized pull log
(`docs/notes/05b.md` / `05b-backfill-run.log`) plus a local re-estimate.

| Item | Value |
|---|---:|
| Pre-pull `--estimate` (locked) | **56,400** credits / 1,880 requests |
| Current local re-estimate | **56,400** / 1,880 (**exact match**) |
| Actual lifetime historical spend | **56,400** (= 660 prior/probe + 55,740 resume) |
| Actual lifetime requests | **1,880** (= 56,400 / 30) |
| Remaining before probe | 99,988 |
| Remaining after full pull | 43,579 |
| Remaining delta | 56,409 |
| Historical ceiling | 60,000 |
| Live reserve | 50 |
| Reserve intact after pull? | **yes** (43,579 ≫ 50) |

**Estimate vs historical actual:** agree **exactly** (0% diff). Estimator is
trustworthy for future pulls at the current decision-point schedule.

**Remaining-delta +9 vs historical 56,400 — diagnosis:** projected remaining
after historical-only spend was `99,988 − 56,400 = 43,588`; observed final
remaining is `43,579` (**9 credits**). That is **not** estimator error. Live
cost is `markets × regions = 3` credits/call → **exactly 3 live snapshot
calls** during/after the historical window account for the 9. Historical path
still spent the locked 56,400; do not treat the 9 as backfill overspend.

---

### 7. Dedupe against live rows

| Metric | Value |
|---|---:|
| Live rows (all seasons; all in 2026) | 35,676 |
| Historical rows | 1,647,150 |
| Overlapping `(game_key, captured_at_minute)` moments | **0** |
| Rows deduped away at overlaps | **0** |
| Exact dedupe-key duplicate rows in staged table | **0** |

No live↔historical moment overlap exists today (live capture is 2026-only;
historical is 2021–2025). Dedupe path is exercised in unit tests; on real data
there is nothing to collapse. **PASS.**

---

### 8. `make lint typecheck test`

```text
uv run ruff check src tests
All checks passed!
uv run ruff format --check src tests
162 files already formatted
uv run mypy
Success: no issues found in 103 source files
uv run pytest -m "not live"
691 passed, 1 deselected, 27 warnings in 247.07s
Required test coverage of 80% reached. Total coverage: 80.81%
```

---

## Decisions / ambiguities

1. **Lockbox 2025:** progress markers, silent-gap hygiene, and quarantine
   counts included; coverage %, `n_books`, reconcile, and crosswalk rates
   withheld per `docs/lockbox_access.md`.
2. **Silent-gap definition:** archive returned `event_time` + `decision_point`
   present in season staged rows (week-agnostic) OR `_empty_slots` marker.
   Stock `coverage_report` week join is **not** the silent-gap oracle.
3. **Reconcile tolerance:** documented as `1.5` points in
   `CFBD_SLOT_CLOSE_TOLERANCE`; share beyond that is the finding metric.
4. **No data correction** of CFBD↔snapshot divergence, name-map gaps, or the
   540 s as-of exceptions — findings only.
5. **P2-8 loop closed:** pre-pull estimate == actual historical spend ==
   current re-estimate at 56,400; remaining +9 diagnosed as live, not
   estimator drift.

## Built this session

- `scripts/_verify_5b.py` — throwaway read-only probe
- `docs/notes/5b-verify.md` — this deliverable
