# ADR 0012: Expected possessions is a production totals feature; PIT fit in backtest

## Status

Accepted

## Context

DESIGN §4.5 lists under Tempo/possession features:

> **expected possessions for this matchup** (regression on both teams' pace +
> pass rates — the key totals feature: totals ≈ possessions × points/possession)

Task 11 requires the same regression, "stored as a fitted artifact, applied
point-in-time." Task 17's feature-signature contract records whatever columns
the production provider emits into mapping-layer heads; it does not exclude
`expected_possessions` from the production set.

`docs/notes/23-rerun.md` Step 0 found no production resolve path: the GT-active
refit lived only under `data/tmp/gt_fix/`, unused by `ProductionFeatureProvider`.

## Decision

1. **`expected_possessions` is an in-scope production feature input** for the
   totals path (and available to margin heads via the shared feature frame).
2. **Walk-forward** refits the regression at each retrain gate on strictly-prior
   games' GT-filtered pace inputs. The gt_fix / live globally-fitted JSON is
   **never** loaded inside the walk-forward (would leak 2023 fit into 2019–2022).
3. **Live inference** uses `configs/artifacts.yaml` →
   `artifacts.expected_possessions_live` →
   `data/artifacts/expected_possessions/live.json` (promoted gt_fix refit).
   That path is live-only; backtests fit their own.

## Consequences

- `ProductionFeatureProvider` emits `expected_possessions`; information-set /
  prophecy audits cover it with other `feat__*` columns.
- Plan-basis wall-clock for Task 23 may rise slightly from the per-retrain fit;
  no ensemble-member work is implied by this ADR.
