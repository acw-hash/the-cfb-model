# ADR 0011: Task 23-FIX P0-1 is satisfied by PIT + CQR (not per-market isotonic)

## Status

Accepted

## Context

`docs/task-23-fix.md` P0-1 asked whether `models/calibrate.py` (isotonic per
derived market) and `models/conformal.py` (CQR) are on the production predict
path, and if absent, to wire them. That wording predates the AUDIT-4 amendment
to DESIGN §2.6 / §5.2 / §9.6.

AUDIT-4 replaced per-market probability maps with **distributional
recalibration**: one monotone map on the PIT values of the OOF margin
predictive CDF and one on total (`models/pit_calibration.py`), so every derived
market (ML, ATS at any line, OU at any line) recalibrates coherently and
preserves the §2.2 internal-consistency guarantee. Per-market isotonic /
reliability / Cox slope-intercept from `models/calibrate.py` remain
**diagnostics only** — never a fitting target. CQR from `models/conformal.py`
stays on the path as the conformal layer.

Literal reading of the pre-AUDIT-4 P0-1 sentence would push re-wiring
`calibrate.py` isotonic into production — which would *violate* the amended
spec.

## Decision

1. P0-1 is **satisfied** by the production call path
   `ProductionEnsemblePredictor.fit` → `_fit_cqr_layer` (`fit_cqr`) →
   `_fit_calibration_from_oof` (`fit_pit_recalibrator` / `gate_pit_recalibrator`)
   and `predict` → `conformalize_intervals` → `_apply_calibrator` (margin/total
   PIT maps). No further wiring is required for closure of this item.
2. Wiring `models/calibrate.py` per-market isotonic into the production stack to
   satisfy the stale P0-1 wording is **explicitly forbidden**.
3. Acceptance artifacts for P0-1 are evaluated against the PIT path: Cox
   slope/intercept before/after on derived market probs (diagnostic), reliability
   diagram, and PIT histogram on a held-out season — under
   `docs/notes/_artifacts/task23_fix/`.

## Consequences

- Code audits that mark P0-1 "PARTIAL" solely because `calibrate.py` is absent
  from production imports are outdated relative to AUDIT-4; treat that absence as
  correct.
- Future agents must not "fix" P0-1 by restoring per-market maps.
- Diagnostics that use `calibrate.py` Cox / reliability helpers remain valid and
  encouraged; they do not re-enter the fit path.
