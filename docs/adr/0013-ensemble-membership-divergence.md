# ADR 0013 (DRAFT): Production ensemble membership diverges from DESIGN §5.2

## Status

**Draft — STOP.** Do not wire missing members under a "wiring" label.
Accepted only after an explicit scope task estimates and implements the gap.

## Context

DESIGN §5.2 Level-0 requires LightGBM μ/quantile (margin + total), XGBoost and
CatBoost μ (margin + total), Elastic Net μ (margin + total), NGBoost
Normal(μ,σ) (margin + total), and LightGBM σ-heads. §2.6 requires Stage-1
epistemic mixture (~50 draws) and Monte Carlo bet probabilities.

Task 17 built all Level-0 member classes under `models/heads/`. Task 19 built
ensemble / distribution / MC / epistemic utilities. Task 22B's
`ProductionEnsemblePredictor` composed only a **subset** for the harness adapter
(documented in `docs/notes/22b.md` as CatBoost/NGBoost not on the predict path;
later P0 work wired MC + epistemic + σ, but not the missing tree/NGBoost
members).

## Decision (diagnostic only — no code change in 23-rerun-prep)

Record the membership table (see `docs/notes/23-rerun-prep.md` Item 4). Missing
Level-0 members mean the "full system" / A4 ensemble label is **not yet honest**
relative to §5.2 until a dedicated task wires them.

## Consequences

- Do **not** re-time the eight-run plan as "full §5.2" until members land.
- Current `backtest plan` FULL constants remain an **upper-bound model** relative
  to the wired path (and also overstate vs missing members that are still
  absent). Re-timing after membership parity is a follow-on.
- Scope estimate for the follow-on: compose XGB/CatBoost/NGBoost (+ ElasticNet
  total, LGBM quantile total) into `ProductionEnsemblePredictor.fit/predict` and
  Level-1 stacking columns — roughly a Task 22B-sized adapter session, not a
  greenfield modeling task.
