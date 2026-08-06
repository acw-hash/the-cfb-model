# Task 22B — Pre-flight audit (Deliverable 0)

Verified against source on 2026-08-05. Earlier notes files were not trusted.

## Audit table

| Item | Actual state | Gap vs expected for Task 23 |
|---|---|---|
| **FeatureProvider / RatingEngine / Predictor** | Protocols in `evaluation/walkforward.py` (`FeatureProvider` L199, `RatingEngine` L219, `Predictor` L177). Implementations in `src/`: **toy only** — `LeagueAverageMarginPredictor` (L235), `RunningMarginRatingEngine` (L295). No production adapters. Real model heads live under `models/heads/` (`LightGBMMuHead`, etc.) but are not harness adapters. | Need `evaluation/production_stack.py` composing real features + ratings + ensemble. |
| **`ncaa_quant.cli.backtest`** | `cli.py` L425–428: `backtest()` raises `NotImplementedError`. Typer group exists but has no `run` / `plan` subcommands. | Implement `backtest_runner.py` + CLI. |
| **Ablation controls on `WalkForwardConfig`** | Only `market_features_available: bool = True` (L151). No A1/A2/A4/A5/A6 fields. | Add six switches + manifest recording. |
| **Priors → `run_filter` (Task 15 item 3)** | `priors.py` exports `gaussian_state_from_priors` / `build_preseason_states` / `efficiency_prior_lookup`. `run_filter` (`state_space.py` L927) still calls `apply_season_regression` at season boundaries (L994–995) with **no** prior-injection parameter. Soft league-mean regression only. | Auto-inject priors into `run_filter` + RatingEngine. |
| **Retrain features (Task 17)** | Harness `_retrain(pd.DataFrame(), …)` at L779, L801–802, L822–824. `BasePredictor._resolve_feature_frame` / `_feature_bank` in `models/heads/base.py` reconstructs X from predict-time bank when features empty. | Harness must pass real as-of feature frames. Bank removal from `base.py` is **outside** sanctioned-edit list — see STOP note. |
| **MLflow at train/eval call sites (Task 22 item 1)** | `registry/tracking.py` provides `TrackingSession` / `log_training_run` / `log_evaluation_run`. **No callers** in `evaluation/` or walk-forward paths. HPO optionally logs trials (`models/hpo.py`). `promote()` takes hand-passed `MetricComparisonInput` sequences (`promote.py` L337). | Wire tracking into backtest runner; add promote-from-logged-runs path. |
| **Line-source fallback ladder** | **Present** in `resolve_lines_for_games` (`walkforward.py` L381+). Snapshot seasons ≥2021: snapshot → nearest earlier (tolerance) → null; CFBD excluded from that ladder. Pre-2021: CFBD open/close. Prediction rows carry `line_source_asof` / `n_books_asof` / close analogs (PREDICTION_COLUMNS L77–82). | Deliverable 7: no new ladder work; keep discipline under ablation A6. |
| **Materialized seasons** | `data/staged/`: **season=2023 only** for CFBD tables (games, plays, lines_historical, …). `data/staged/odds_snapshots/`: **season=2026** only (live). `data/features/`: empty (`.gitkeep` only). Week-1 prior parquet: `data/predictions/week1_priors_2023_2024.parquet`. | Smoke = 2023 wiring proof. Full 2019–2025 + A6 snapshots require ingest / Task 5B. |

## Additional gaps (scope check)

| Gap | In sanctioned list? | Action |
|---|---|---|
| No dedicated market-feature builder (`features/builders/market*.py` absent; registry has no market cards) | Builders: flag plumbing only — **cannot** add new market logic there. | Compose market columns in `production_stack.py` from existing line resolution (`resolve_lines_for_games` + CFBD/snapshot frames). Document. |
| `BasePredictor` feature-bank fallback | `models/heads/base.py` **not** sanctioned | Harness will stop passing empty frames. Bank code left inert; full deletion needs a follow-up sanctioned edit. **Report, do not expand scope.** |
| Promotion from MLflow run artifacts | `registry/` is sanctioned | Add resolver that loads comparison metrics from logged evaluation runs. |
| Insert Task 22B into `docs/TASKS.md` | Not listed, but required by task header | Update TASKS.md as instructed by the task prompt itself. |

## Difference vs deferred notes

Reality matches notes/15, 16, 17, 22 on the deferred seams. One upside vs notes: line-source ladder **was** implemented in Task 16 (notes/16 item 4) — Deliverable 7 is largely verification, not greenfield.
