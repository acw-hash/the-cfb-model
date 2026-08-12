# TASK WEEK-ALIGN-FIX — CFBD-week ↔ decision-point calendar

**Date:** 2026-08-11  
**Scope:** Week/decision-point mapping (`walkforward.WeekDecisionCalendar`),
`market_lines` consumption of corrected as_of, ablation config + tests.  
**Forbidden (honored):** no tuning; no lockbox; no guard-band widening.

Artifacts: `docs/notes/_artifacts/week_align_fix/`.

---

## STEP 1 — Mapping by construction

For each `(season, CFBD week)`, take the **modal America/New_York Monday** among
that week's kickoffs, then resolve `tuesday_0600_et` / `saturday_0600_et` via
`resolve_decision_point` + `zoneinfo` (AUDIT-6 DST). No Labor-Day arithmetic.

| check | result |
|---|---|
| 2021–2024 games | 3613 |
| tuesday strictly before kickoff | **3586** |
| violations (named Week-0 exceptions) | **27** (target 0 among non-exceptions) |

All 27 exceptions are Week-1 / Week-0 Friday–Saturday early games whose kickoff
falls before that CFBD week's modal Tuesday; each resolves to **`slot_close`**
(kick−5min). Listed in
`docs/notes/_artifacts/week_align_fix/step1_mapping.json`.

DST fixtures (kickoff-calendar path): Sat/Tue 06:00 ET across 2024 Nov
fall-back shift UTC by +1h — `tests/unit/test_week_align_fix.py` + existing
`test_timeutils` AUDIT-6 cases.

---

## STEP 2 — Ladder fallback distribution

Hard per-game kickoff guard from MKT-ASOF-FIX retained. After calendar fix,
`slot_close` is rare:

| season | tuesday | saturday | slot_close | null | pct slot_close |
|---:|---:|---:|---:|---:|---:|
| 2021 | 882 | 0 | 5 | 0 | 0.56% |
| 2022 | 885 | 0 | 11 | 0 | 1.23% |
| 2023 | 903 | 0 | 7 | 0 | 0.77% |
| 2024 | 916 | 0 | 4 | 0 | 0.44% |
| **2021–2024** | **3586** | **0** | **27** | **0** | **0.75%** |

Well under a few percent — proceed (no STOP).

---

## STEP 3 — Re-run `market_aware_reduced_v2` @ Tuesday decision

- Config: `task23_market_aware_full_reduced_v2_tue`
- Run id: `task23_market_aware_reduced_v2_tue`
- Label: `week-align-fix;FEATURE_TIME=TUESDAY_DECISION;ensemble_scope=REDUCED_PER_ADR_0013`
- Wall clock: ~2885 s (~48.1 min)
- **Guard disposition: INSIDE_BAND_PUBLISHED** (snapshot ATS 51.42% ∈ [47.46%, 52.54%]; 2019 ∈ [44.5%, 55.5%])
- Predictions: `data/backtests/task23_market_aware_reduced_v2_tue/full/predictions.parquet`

### FEATURE_TIME=TUESDAY_DECISION (honest table)

| Regime | ATS | n | LL (model) | MAE margin | CRPS margin | 95% bootstrap CI | band |
|---|---:|---:|---:|---:|---:|---|---|
| cfbd_2019 | 45.63% | 743 | 0.835 | 14.59 | 10.55 | [42.9%, 48.6%] | [44.5%, 55.5%] |
| snapshots_2021_2024 | **51.42%** | 3491 | 0.812 | 14.32 | 10.26 | [49.3%, 53.6%] | [47.46%, 52.54%] |

### Deltas vs kick−5min run (FEATURE_TIME=SLOT_CLOSE_KICK_MINUS_5M)

| Regime | Δ ATS (pp) | Δ MAE | Δ CRPS | kick ATS | tue ATS | kick MAE | tue MAE |
|---|---:|---:|---:|---:|---:|---:|---:|
| cfbd_2019 | −1.88 | −0.52 | −0.53 | 47.51% | 45.63% | 15.11 | 14.59 |
| snapshots_2021_2024 | **−0.83** | **+2.60** | +1.76 | 52.25% | 51.42% | 11.72 | 14.32 |

### Reading (same vintage / scope; feature time labeled)

Snapshot **margin MAE regresses toward fundamental_v2 / A3** (11.72 → 14.32 vs
fund/A3 14.21): most of the kick−5min MAE “skill” was near-close information.
Snapshot **ATS also softens** (52.25% → 51.42%, −0.83 pp vs A3 52.22%). That
quantifies how much of 52.25% / 11.72 was near-close ceiling rather than
Tuesday decision-time edge. Residual market-aware vs A3 at Tuesday
(−0.80 pp ATS, +0.11 MAE) is small — no clear decision-time market edge on this
reduced-v2 stack.

---

## STEP 4 — Relabel (do not bury)

- Kick−5min table in `docs/notes/mkt-asof-fix.md` kept, headed
  **FEATURE_TIME=SLOT_CLOSE_KICK_MINUS_5M** (near-close ceiling; not comparable
  to Tuesday-decision; CLV degenerate by construction).
- This run: **FEATURE_TIME=TUESDAY_DECISION**.
- Future comparisons must state feature time alongside vintage and scope.

---

## Built / decisions

- `WeekDecisionCalendar` / `decision_points_from_kickoffs` in
  `walkforward.py`; harness builds calendar from games; Labor-Day path remains
  fallback for synthetic fixtures without a schedule.
- `resolve_lines_for_games` also builds the calendar from the games batch so
  feature as-of tracks CFBD weeks even if a caller still passes Labor-Day
  `week_decision_as_of`.
- `market_lines.feature_as_of_for_game` consumes corrected `week_as_of` (+ optional
  calendar saturday); no Labor-Day week math.
- Ambiguity: Week-0 / early Week-1 games before modal Tuesday → `slot_close`
  (named exceptions). Bowl games mislabeled as CFBD week 1 do not move the modal
  Monday (late-August slate dominates).

## Acceptance

- [x] Step 1: 0 non-exception violations; 27 Week-0 named exceptions → slot_close
- [x] Step 2 fallback distribution (0.75% slot_close)
- [x] Step 3 re-run table + deltas + **INSIDE_BAND_PUBLISHED**
- [x] Relabeled headers in `mkt-asof-fix.md`
- [x] DST fixtures passing
- [x] `make lint typecheck test`
