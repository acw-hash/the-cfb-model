# ADR 0014: Member-credibility contract (no constant fabrication)

## Status

Accepted (MEMBER-HEALTH-FIX).

## Context

SDMU-DIAG found two distinct ways the production ensemble emitted
constant μ under `SD(mu)=0`:

- **Mechanism A:** cold-start LightGBM produced a constant leaf; NNLS
  weighted it 1.0.
- **Mechanism B:** ElasticNet `fit` died on NaN market features;
  `contextlib.suppress` left the member unfitted; `_predict_point`
  replaced the entire member vector with the constant **2.5**; NNLS
  still assigned positive weight to that dead member.

DESIGN §5.2 Level-1 stacking is a convex combination of member μs.
Fabricating a constant in place of a failed member invents a location
that no honest member produced, poisons NNLS, and turns the D2 quality
gate into a detector of our own fallback rather than of model health.

## Decision

### Credibility contract

A Level-0 member is **CREDIBLE** only if all of the following hold:

1. **Fit completed without exception.**
2. **Estimator state is consistent with selection state** (e.g. ElasticNet
   `_model` fitted iff `_selected_features` is non-empty; a failed fit
   clears selection so stale feature lists cannot outlive the estimator).
3. **Non-degenerate on its own training window:** predictions on the
   rows used to fit that member are non-constant (population SD > 0 /
   span above a numerical epsilon). A constant cold-start stump is not
   credible.

Only credible members enter Level-1 NNLS. A member that fails any clause
is **EXCLUDED** with `member_status` recorded on the fit / manifest —
never replaced by a constant. Fabricating a constant is forbidden at
every layer (`fit`, `_predict_point`, `_member_margin_matrix`, OOF
construction, gate).

If **zero** members are credible after a fit, the predictor still
records the fit attempt but emits **null** μ predictions with an
explicit `null_reason` (e.g. `cold_start_insufficient`). Graded metrics
treat those rows as **ungradable**. That is an honest gap, not a
constant.

Fit failures are never silenced: per config they either **exclude** the
member (default) or **fail the run**. `contextlib.suppress(Exception)`
around member fits is forbidden.

### ElasticNet NaN policy (training inputs)

sklearn `ElasticNet` rejects NaN. Within each training window:

1. Drop feature columns whose null share exceeds
   `NULL_SHARE_DROP_THRESHOLD` (default **0.50**).
2. Impute remaining NaN with **training-window column medians** only
   (no zero-fill; no medians from rows outside the window — PIT).
3. Persist those medians (and the kept column set) for predict-time
   transform; predict never peeks at future rows.

`is_missing` indicators remain as features for the missingness signal.
This policy changes training inputs: **every market-aware run must
re-run** after this ADR.

### Quality-gate addenda (not widenings)

Two blind spots from SDMU-DIAG, closed without relaxing D2 thresholds:

1. **Vacuous ABSENT:** a scheduled `(season, week)` with games but
   zero prediction rows is reported as `ABSENT` with null-reason
   counts — never silently skipped as a “pass”.
2. **Partial death:** the gate fails when any positive NNLS weight rests
   on a non-credible member (SD(μ) alone is insufficient — see
   (2023, w5) in SDMU-DIAG).

Intentional null rows carrying `null_reason` are ungradable (excluded
from scored μ checks); accidental nulls without reason still fail.

## Consequences

- Mechanism A: a constant cold-start LGBM is not credible and is excluded
  from NNLS. If that leaves zero credible members, the block emits null μ
  with `cold_start_insufficient`. If another member (e.g. ElasticNet)
  remains credible, the block may still emit honest point μ from that
  member; fabricating a constant leaf is still forbidden.
- Mechanism B is repaired at the source by the NaN policy; exclusion
  remains the backstop if fit still fails.
- When σ would be block-constant, refuse it (null σ / missing probs) rather
  than flooring — and do not erase finite μ to paper over the gap.
- Published fundamental / A3 / A6 / SLOT_CLOSE tables are verified from
  stored state before any re-run; a dead or degenerate weighted member
  in those artifacts is a STOP.
- ADR 0013 (membership divergence vs full §5.2) remains a separate
  draft; this ADR does not wire missing XGB/CatBoost/NGBoost members.
