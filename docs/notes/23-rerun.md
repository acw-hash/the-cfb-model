# TASK 23-RERUN — Full Task 23 run set

**Date:** 2026-08-10  
**Status:** **STOP before any run** (Step 0 ambiguity + out-of-scope wiring).  
**Driver:** `ncaa-quant backtest` against `configs/ablations/task23_run_set.yaml`.  
**No runs executed. No tuning. No lockbox read.**

---

## Verdict

The eight-run set was **not** started. Step 0 left an unresolved artifact
ambiguity, and the production CLI still hardcodes `snapshots=None` so any run
would re-emit the GT-era / pre-5B measurement failure modes the notes chain
already documented. Fixing that requires `src/ncaa_quant/cli.py` (and possibly
config→loader wiring), which is **outside** this task's sanctioned edit set.
Diagnosis here; fix is a separate session.

**ALL prior Task 23 numbers** (original memo, wiring-proof / FIX smoke figures,
and any inert-run deltas) came from a GT-inert system and/or a path missing
roster priors and/or `snapshots=None`. They are **superseded in full** — see
the AMENDMENT appended to `docs/notes/23.md`. This memo does **not** replace
them with new numbers because no honest run completed.

---

## Step 0 — Artifact-resolution table

Verified 2026-08-10 against live paths and call sites (file references below).
Any cell marked **STOP** blocked the run set.

| Component | What production resolves today | Evidence | Status |
|---|---|---|---|
| **(a) Expected-possessions model** | **Nothing.** No backtest / `ProductionFeatureProvider` / CLI path loads any `expected_possessions` artifact. Sole on-disk GT-active refit: `data/tmp/gt_fix/expected_possessions.json` (gt-fix Step 4). No key in `configs/*.yaml` names a production location. `load_expected_possessions_artifact` callers: tests + `scripts/_gt_fix_replay.py` only. | `production_stack.py` `ProductionFeatureProvider.compute_game_features` (rating_state + market only); grep of `load_expected_possessions_artifact`; `configs/` has no possessions path | **STOP — ambiguity.** Cannot promote "to the proper configured location" because that location is unspecified. Inventing one + hardcoding `data/tmp/` into a run config are both forbidden. |
| **(b) Harness GT filter on observations** | CLI rebuilds via `build_observations_from_staged` → `apply_garbage_time` on **staged** plays (now carry `score_margin` / scores / clock). Flag rates nonzero; A5 precondition would pass. Aggregate build 2019–2024: `n_on=753288`, `n_off=903422`. | Staged plays cols; live `apply_garbage_time` / `build_observations_from_staged` (table below) — **not** a citation of gt-fix.md | **PASS (preflight only)** |
| **(c) priors_frame + A5 at construction** | `load_fitted_priors_frame_for_backtest` → `data/tmp/priors_acceptance_15/week1_priors.parquet` (shape 548×15 for seasons 2019–2024). `assert_a1_priors_precondition` **PASS**. A5: see (b). | `cli.py` `_DEFAULT_PRIORS_CACHE`; live load | **PASS** |

### (b) Harness-path GT flag rates (staged plays → `apply_garbage_time`)

| season | flag_rate | n_on | n_off | n_on < n_off |
|---:|---:|---:|---:|:---:|
| 2019 | 0.1807 | 131019 | 159915 | yes |
| 2020 | 0.1569 | 86674 | 102809 | yes |
| 2021 | 0.1691 | 131810 | 158634 | yes |
| 2022 | 0.1617 | 134395 | 160327 | yes |
| 2023 | 0.1613 | 133370 | 159011 | yes |
| 2024 | 0.1641 | 136020 | 162726 | yes |

| Component | What production resolves today | Evidence | Status |
|---|---|---|---|
| **(d) Quarantine / SUPERSEDED** | Quarantined roster `data/features/_quarantine/roster_task23_fix.parquet`: **not** opened by `ProductionFeatureProvider` or `backtest_run` (provider never reads `data/features/`). **However:** `cli._DEFAULT_FILTER_HISTORY` = `data/tmp/state_space_acceptance_14/history.parquet`, sibling of `SUPERSEDED.md` (gt-fix). Happy path returns early on `week1_priors.parquet` and does **not** read history; rebuild fallback **would** read the SUPERSEDED cache. | `cli.py` L520–523, L549–556, L616–617; `data/tmp/state_space_acceptance_14/SUPERSEDED.md` | **PASS on happy path; latent SUPERSEDED default remains.** Promoting gt_fix `state_space_history.parquet` over the SUPERSEDED default requires changing `cli.py` (out of scope) or a config key the CLI does not yet read. |
| **Odds snapshots (not in Step 0 letter list, but blocks §7.2 / bet-layer)** | Staged `data/staged/odds_snapshots/season={2021..2024}` **exist** (5B complete). CLI `backtest_run` hardcodes `snapshots=None` (`cli.py` ~L741). Walk-forward ≥2021 then nulls bet-time / close under the snapshot ladder. | `cli.py` `snapshots=None`; staged season dirs present | **STOP for honest market / CLV / A6.** Fix = load staged snapshots into `run_backtest` — **not** a sanctioned edit. |
| **Lockbox 2025** | Absent from every `task23_run_set.yaml` `test_seasons` / `continuity_seasons`. Union = {2019,2020,2021,2022,2023,2024}. | YAML parse | **PASS** |

### Step 0 STOP reasons (plain)

1. **Expected-possessions resolve path is undefined** in production config and
   unused by the backtest provider — promoting the gt_fix JSON requires a
   judgment call about destination and wiring. Spec says stop on ambiguity.
2. **`snapshots=None`** means the run set cannot meet the task's own bet-layer /
   regime / A6 measurement contract even if (1) were waived. Wiring is outside
   sanctioned files.

No config promotion was performed (would be a false claim of resolution).

---

## Step 1 — Plan (under budget; not executed)

```text
uv run ncaa-quant backtest plan --config task23_run_set
```

| Run | week_units | retrain_points | est_sec | ~min | seasons |
|---|---:|---:|---:|---:|---|
| fundamental_full | 92 | 18 | 89.8 | 1.5 | 2019–2024 (+2020 cont.) |
| market_aware_full | 92 | 18 | 89.8 | 1.5 | same |
| A1_priors_off | 92 | 18 | 89.8 | 1.5 | same |
| A2_rating_updates_frozen | 92 | 18 | 89.8 | 1.5 | same |
| A3_market_features_off | 92 | 18 | 89.8 | 1.5 | same |
| A4_single_lgbm | 92 | 18 | 89.8 | 1.5 | same |
| A5_garbage_time_filter_off | 92 | 18 | 89.8 | 1.5 | same |
| A6_cfbd_open_close | 61 | 12 | 59.6 | 1.0 | 2021–2024 only |

**Total estimated wall clock:** **688.1 s (~11.5 min, ~0.19 h)** — under the
§1.4 8-hour budget. **Not a budget STOP.**

**Measurement basis** (from plan text): week_unit = `WalkForwardHarness.run`
staged 2023 (910 games / 15 weeks) wired=0.1413 s/week; add-ons microbench
CatBoost+NGBoost predict + 100k-draw MC (~65 games) + 50 epistemic LGBM
predicts → full=0.7528 s/week; retrain_full=1.1404 s; hardware=local Windows
workstation. Plan also notes CatBoost/NGBoost/MC/epistemic are **not** in the
`production_stack` predict loop today — the FULL constants are an upper-bound
model relative to the wired path.

---

## Step 2 — Runs

**NOT RUN** (Step 0 STOP). Order that would have been used:
fundamental → market-aware → A1 → A2 → A3 → A4 → A5 → A6.

---

## Step 3 — Bet layer

**NOT RUN.** Would have been impossible to measure honestly under
`snapshots=None` even without the Step 0 stop (2021–2024 snapshot lines never
enter the harness).

---

## Acceptance metrics (all NOT COMPUTED)

| Required deliverable | Result |
|---|---|
| Step 0 resolution table | **Above** |
| ATS / OU vs close with CIs, per regime | **NOT COMPUTED** — no run |
| CRPS / log-loss vs de-vigged market | **NOT COMPUTED** — no run |
| A2 delta per basis (`report_a2_components_by_basis`) | **NOT COMPUTED** — no run |
| Mean CLV with CI (guard-passing) | **NOT COMPUTED** — no run; would also be blocked by `snapshots=None` |
| A5 delta (first real measurement) | **NOT COMPUTED** — no run (preflight only: A5 precondition would pass) |
| A6 delta | **NOT COMPUTED** — no run |
| Weekly error curve (Week 10 ? Week 4) | **NOT COMPUTED** — no run |
| Determinism (byte-identical tables) | **NOT COMPUTED** — no run |
| Every missed §1.6 criterion | **N/A for this session** — no new measurement; prior §1.6 miss list in `23.md` remains historical and superseded, not re-litigated with new numbers |

Standing caveats that would apply to any future honest run (restate for the
next session):

- Per-season CFBD-vs-snapshot divergence tail — `docs/notes/5b-verify.md` §4 —
  beside every vs-close metric.
- `n_books` gradient (5b-verify §3) beside every cross-book price number.
- Crosswalk completion tables (5b-verify / 5b-patch / 5b-patch2) as coverage.
- Regimes never pooled: 2019 CFBD-only vs 2021–2024 snapshot-backed; 2020
  continuity-only (§7.2 item 5); lockbox 2025 out.

---

## Separate-session fix list (not done here)

1. **Define and wire** the production expected-possessions artifact path
   (config key + loader used by whatever component is supposed to consume the
   GT-active refit), then copy `data/tmp/gt_fix/expected_possessions.json` to
   that location — not a tmp path in a run YAML.
2. **CLI:** load staged `odds_snapshots` for the run's seasons (excluding
   lockbox 2025 from evaluation) and pass them into `run_backtest` instead of
   `snapshots=None`.
3. **Optional hygiene:** retarget `_DEFAULT_FILTER_HISTORY` away from the
   SUPERSEDED Task 14 cache (e.g. to `data/tmp/gt_fix/state_space_history.parquet`
   via a config-promoted non-tmp path), so rebuild fallback cannot read
   SUPERSEDED.

---

## `make lint typecheck test`

```text
uv run ruff check src tests          → All checks passed!
uv run ruff format --check src tests → 165 files already formatted
uv run mypy                          → Success: no issues found in 103 source files
uv run pytest -m "not live"          → 716 passed, 1 deselected, 27 warnings
```

Notes-only change set (no `src/` edits).
