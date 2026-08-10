# TASK 23-RERUN-PREP — Close wiring gaps for an honest eight-run set

**Date:** 2026-08-10  
**Status:** Items 1–3 wired; Item 4 STOP (membership ADR draft). No eight-run
execution.

---

## Item 1a — Scope ruling (expected_possessions)

**Ruling: YES — specified production totals feature.**

References:

- DESIGN §4.5 Tempo/possession features: *“expected possessions for this
  matchup (regression on both teams' pace + pass rates — **the key totals
  feature**: totals ≈ possessions × points/possession)”*
- Task 11: *“Expected possessions model per §4.5… This is the key totals
  feature. Fit on historical games, stored as a fitted artifact, applied
  point-in-time.”*
- Task 17 feature-signature contract records whatever the production provider
  emits into mapping-layer heads; it does not exclude `expected_possessions`.

ADR: `docs/adr/0012-expected-possessions-production-pit.md`.

---

## Item 1b–d — PIT fit, consumption, live artifact, leakage

Built:

- `src/ncaa_quant/features/possessions.py` — PIT fit entrypoint
  (`fit_expected_possessions_at_retrain`), staged training builder, live loader.
- `ProductionFeatureProvider` emits `expected_possessions`; refits via
  `fit_possessions_at_retrain` keyed by `(season, week)`.
- `WalkForwardHarness._retrain` calls that fit at each retrain gate (never loads
  the live/global JSON).
- Live path: `configs/artifacts.yaml` →
  `artifacts.expected_possessions_live` =
  `data/artifacts/expected_possessions/live.json` (promoted gt_fix Step 4
  refit). Comment in YAML + `README.txt`: live only; backtest fits its own.

Tests: `tests/unit/test_23_rerun_prep.py` — PIT bound assertion; information-set
+ prophecy audit over the new feature.

---

## Item 2 — Snapshots into the CLI

`backtest_run` loads staged `odds_snapshots` for the run's seasons via
`load_staged_odds_snapshots`, which:

1. Calls `assert_lockbox_excluded` on the requested season list.
2. Asserts loaded rows contain no season 2025.

Passes the frame into `build_production_stack` / `run_backtest` (no more
`snapshots=None`).

Tests: lockbox season in the request raises; 2021–2024 load returns non-None
with the expected season set.

---

## Item 3 — Retire SUPERSEDED filter-history default

`_DEFAULT_FILTER_HISTORY` removed. Rebuild fallback uses
`resolve_filter_history_path()` → `configs/artifacts.yaml`
`artifacts.filter_history` =
`data/artifacts/state_space/filter_history.parquet` (promoted GT-active
history). Any path containing `state_space_acceptance_14` or `data/tmp/` raises.

Grep: `state_space_acceptance_14` appears in `cli.py` only as rejection
markers, not as a default path.

---

## Item 4 — Ensemble membership audit (diagnose only)

| §5.2 / §2.6 member or path | In `ProductionEnsemblePredictor` today? | Symbol refs |
|---|---|---|
| LightGBM μ_M | yes | `margin_head: LightGBMMuHead` |
| LightGBM μ_T | yes | `total_head: LightGBMMuHead` |
| LightGBM quantile margin | yes | `quantile_margin_head` |
| LightGBM quantile total | **no** | — |
| XGBoost μ_M / μ_T | **no** | heads exist (`xgboost_mu.py`); not composed |
| CatBoost μ_M / μ_T | **no** | heads exist (`catboost_mu.py`); not composed |
| Elastic Net μ_M | yes | `enet_margin` |
| Elastic Net μ_T | **no** | — |
| NGBoost margin/total | **no** | heads exist (`ngboost_dist.py`); not composed |
| LightGBM σ_M / σ_T | yes | `sigma_margin_head`, `sigma_total_head` |
| Level-1 NNLS stack (margin) | yes (LGBM+ENet) | `_set_weights` / `fit_nnls_stack` |
| Level-1 stack (total) | stub single-LGBM | `single_lgbm_stack` for total |
| MC draw path | yes | `sample_joint` in `predict` (`n_mc_draws`) |
| Epistemic draw path | yes | `_epistemic_mix` / `mix_epistemic_predictions` |

**STOP:** specified Level-0 diversity members are **missing**. The “full system”
label is not yet honest. ADR draft:
`docs/adr/0013-ensemble-membership-divergence.md`. Deferred composition traces
to Task 22B adapter scope (`docs/notes/22b.md`); do not add members in this
session.

Plan-basis: keep treating FULL week/retrain constants as an **upper-bound
model**; do not re-time as §5.2-complete until membership lands.

---

## Ambiguities recorded

- Pace inputs for possessions come from the Task 11 training-frame construction
  (season-to-date plays/game + pass rate), not from Kalman pace state alone.
- Offseason `(season, 0)` fit is empty when the training frame has no prior
  season; first finite `expected_possessions` appear after the first in-season
  retrain with prior weeks.

---

## `make lint typecheck test`

```text
uv run ruff check src tests          → All checks passed!
uv run ruff format --check src tests → 167 files already formatted
uv run mypy                          → Success: no issues found in 104 source files
uv run pytest -m "not live"          → 722 passed, 1 deselected; coverage 80.33%
```
