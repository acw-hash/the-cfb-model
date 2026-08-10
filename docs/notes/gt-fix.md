# TASK GT-FIX — Restore score/clock, activate garbage-time filter

**Date:** 2026-08-10  
**Scope:** Schema + plays normalizer + quality guard + zero-API replay from
`data/raw/cfbd/`. GT thresholds untouched.  
**Driver:** `scripts/_gt_fix_replay.py`  
**Report:** `docs/notes/_artifacts/gt_fix_report.json` (= `data/tmp/gt_fix/`)

---

## Step 0 — Column audit + WP ruling

Raw 2023 `/plays` archives (`plays_s2023_w1_regular_*.json`, n=16195) carry:

| Field | Present | Non-null |
|---|---|---|
| `offenseScore` / `defenseScore` | yes | 100% |
| `period` | yes | 100% |
| `clock` (`{minutes,seconds}`) | yes | 100% |
| `ppa` (EPA) | yes | ~75% |
| `homeWinProb` / `wp` | **no** | — |

**WP ruling:** CFBD `/plays` payloads do not ship a win-probability field.
Staged `wp` remains null (never zero-filled). Per DESIGN §4.2 fallback order,
**Connelly-from-scores is the operative GT definition** for all seasons
2014–2025. Primary WP rule stays implemented and will engage if a future
source ever populates `wp`.

---

## Step 1 — Schema + normalizer

- `PlaysSchema`: added `offense_score`, `defense_score`, `clock` (seconds
  remaining, 0–900), `score_margin` (derived offense−defense when both
  present; −100…100). `wp` unchanged (nullable).
- `normalize_plays_payload`: populates those fields from raw; out-of-range →
  null (same dirty-field policy as yards-to-goal). `score_margin` is staged so
  `apply_garbage_time` (defaults `margin_col="score_margin"`) works on staged
  frames without a features logic change.
- Quality GE suite: column set + ranges; `mostly=0.95` non-null on score/clock/
  margin (not on `wp`).
- Custom guard: `check_plays_score_clock_null_rates` (max null frac 0.05).

---

## Step 2 — Replay 2014–2025 (Δ=0)

Enriched every existing `plays` partition by `play_id` join from the latest
raw archive per `(season, week, season_type)`. Row counts unchanged:

| season | n_before | n_after | Δ | null(score_margin) | null(wp) |
|---:|---:|---:|---:|---:|---:|
| 2014 | 158315 | 158315 | 0 | 0.0000 | 1.0 |
| 2015 | 160180 | 160180 | 0 | ~0 | 1.0 |
| 2016 | 158518 | 158518 | 0 | ~0 | 1.0 |
| 2017 | 158574 | 158574 | 0 | 0.0000 | 1.0 |
| 2018 | 160512 | 160512 | 0 | 0.0001 | 1.0 |
| 2019 | 159915 | 159915 | 0 | 0.0000 | 1.0 |
| 2020 | 102809 | 102809 | 0 | ~0 | 1.0 |
| 2021 | 158634 | 158634 | 0 | 0.0001 | 1.0 |
| 2022 | 160327 | 160327 | 0 | 0.0011 | 1.0 |
| 2023 | 159011 | 159011 | 0 | 0.0002 | 1.0 |
| 2024 | 162726 | 162726 | 0 | ~0 | 1.0 |
| 2025 | 166057 | 166057 | 0 | 0.0008 | 1.0 |

**GT-incapable seasons:** none. Tiny residual nulls are unmatched play_ids /
missing raw clock-score cells — left null, never zero-filled.

---

## Step 3 — Flag verification (A5 unblocked)

| season | flag_rate | n_on | n_off | n_on < n_off |
|---:|---:|---:|---:|:---:|
| 2014 | 0.1702 | 131375 | 158315 | yes |
| 2015 | 0.1791 | 131498 | 160180 | yes |
| 2016 | 0.1784 | 130244 | 158518 | yes |
| 2017 | 0.1731 | 131128 | 158574 | yes |
| 2018 | 0.1841 | 130964 | 160512 | yes |
| 2019 | 0.1807 | 131019 | 159915 | yes |
| 2020 | 0.1569 | 86674 | 102809 | yes |
| 2021 | 0.1691 | 131810 | 158634 | yes |
| 2022 | 0.1617 | 134395 | 160327 | yes |
| 2023 | 0.1613 | 133370 | 159011 | yes |
| 2024 | 0.1641 | 136020 | 162726 | yes |
| 2025 | 0.1759 | 136849 | 166057 | yes |

Flag rates sit in the same order as the Task 8 / gt-diag raw probe (~16.6%);
not tuned. Fallback frac ≈ 1.0 (WP still null).

**Blowout fixtures (gt-diag 10 games):** all `garbage_time=True`.  
**Close-game controls (10× final margin=1, Q4 last play):** all `False`.

---

## Step 4 — Downstream rematerialization

Artifacts under `data/tmp/gt_fix/` (feature hive was empty aside from
quarantine; builders ran on corrected staged plays):

| Artifact | Path |
|---|---|
| Stage-1 play observations (GT on) | `stage1_observations_from_plays.parquet` |
| Task 10 efficiency play-game obs | `efficiency_play_game_obs.parquet` |
| Task 11 expected-possessions model | `expected_possessions.json` (**refit** on GT-filtered 2023 week holdout — specified fit, not threshold tuning) |
| Task 14 filter history / innovations | `state_space_history.parquet`, `state_space_innovations.parquet` |

### Expected-possessions MAE (2023 week holdout ≤10 / ≥11)

| | MAE | n_train | n_test |
|---|---:|---:|---:|
| Before (GT-inert staged) | 2.791 | 536 | 204 |
| After (GT-active) | 2.799 | 536 | 204 |
| notes/11 baseline (raw-era) | 2.778 | — | — |

### Filter health (play-path observations, 2014–2025)

| | mean_z | var_z | n | misspecified |
|---|---:|---:|---:|:---:|
| Before (GT inert) | 0.0082 | 0.618 | 37621 | no |
| After (GT active) | −0.0156 | 0.970 | 37621 | no |

`var_z` moves toward the §9.5 target of ~1 under GT filtering.

**Superseded (not deleted):** `data/tmp/state_space_acceptance_14/SUPERSEDED.md`
marks the Task 14 advanced-box acceptance cache as superseded by the play-level
GT-filtered outputs above. `summary.json` retained.

---

## Step 5 — Regression guard

`check_plays_score_clock_null_rates` fails ingest/quality when
`offense_score` / `defense_score` / `clock` / `score_margin` null fraction
> 0.05 (or column absent). Wired into `quality/runner.py` for `plays`.

Seeded score-less fixture test:
`test_plays_score_clock_null_rate_guard_trips_on_scoreless` — PASS.

---

## Debt attribution

**Which task dropped the columns:** Task 5 CFBD ingest
(`normalize_plays_payload` / `PlaysSchema`) archived raw scores+clock but did
not stage them — documented in `docs/notes/08.md` as a prior-task gap and
confirmed WORLD A in `docs/notes/gt-diag.md`.

**Artifacts superseded by this fix:**

- Staged `plays` partitions 2014–2025 (columns widened in place; Δ=0 rows)
- Implicit Stage-1 observations previously built from GT-inert plays
- Task 11 expected-possessions fit (regenerated → `data/tmp/gt_fix/expected_possessions.json`)
- Task 14 acceptance cache `data/tmp/state_space_acceptance_14/` (marked
  SUPERSEDED; advanced-box path, not deleted)
- A5 precondition status: was NOT RUN (`n_on == n_off`); now `n_on < n_off`
  every season

---

## Acceptance paste

1. **Step 0:** raw has offenseScore/defenseScore/period/clock/ppa; no WP →
   Connelly-from-scores operative.
2. **Δ=0** table above; null(score_margin) ≪ 0.05 all seasons; null(wp)=1.0.
3. **Flag rates** ~0.16–0.18; blowouts True; controls False; `n_on < n_off`
   everywhere; no GT-incapable seasons.
4. **MAE** 2.791 → 2.799; **filter health** var_z 0.618 → 0.970.
5. **Quality guard** trips on score-less fixture (unit test).
6. **Debt line:** Task 5 dropped columns; superseded artifacts listed above.
7. **`make lint typecheck test`:** pass (ruff + mypy strict; 716 passed, 1 deselected; coverage 80.47%).
