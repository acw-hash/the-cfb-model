# AUDIT-3 — State-space spec amendments

**Date:** 2026-08-06
**Scope:** Documentation only (no code changes).

## Problem

Three defects in the Stage-1 / feature spec:

1. **Joint state ambiguous.** §9.2 read as per-team states with "full covariance,"
   which could mean per-team blocks only. Epistemic sampling in §2.6 did not require
   joint draws. The §5.1 claim that the Kalman layer propagates schedule information
   "optimally" was unjustified without a joint filter.
2. **Identifiability.** Measurement `off − def` identifies only differences; a
   global level shift is unobservable and collinear with season scoring-environment
   drift. No constraint was specified.
3. **Era / environment covariates missing.** §4.5 had no explicit rule-era feature
   for the 2023 clock change; scoring-environment posterior was not listed as a
   Stage-2 input; Task 21 slice analysis had no totals-by-era cut.

## Changes

### `docs/DESIGN.md`

| Section | Action |
|---|---|
| **§2.6** | Epistemic draws sample the two game teams' rating blocks from the **joint** posterior (preserve cross-team covariance) |
| **§4.5** | Rating features: scoring-environment posterior mean; Situational: `pre_2023_clock` / `post_2023_clock` with 2023 clock-rule hypothesis |
| **§5.1** | Footnote `[^joint-kalman]`: "optimally" holds only because the filter is joint |
| **§9.2** | Rewrote as **joint league state** (~540-dim v1), full cross-team `P`, one joint update per game |
| **§9.3** | Measurement includes `scoring_env`; **constraint projection** to league-mean-zero off/def after every update; `scoring_env` carries the level |
| **§13 / §15.14** | Aligned GNN / Task-14 prompt language with joint + invariance |

### `docs/TASKS.md`

| Task | Action |
|---|---|
| **11** | Situational builders include rule-era categorical |
| **14** | Joint state + projection in deliverables; **IDENTIFIABILITY / INVARIANCE** test |
| **21** | Slice analysis: totals bias per rule era |

## Ambiguities left by the spec (smallest choice recorded)

- **`hfa_team_deviation` layout:** still "small, heavily shrunk" league-level language;
  whether it is one scalar or a per-team deviation block inside the joint vector is
  left to Task 14 — either way it sits inside the same joint `(x, P)`.
- **Projection vs Joseph form:** mean-centering operator `M` on off/def index sets
  with `P ← (I−M)P(I−M)ᵀ` is the mandated method; Joseph-stabilized variants are
  allowed if numerically helpful so long as the mean constraint is exact.
- **Rule-era cut date:** seasons with kickoff in calendar 2023+ are `post_2023_clock`
  (the rule took effect for the 2023 season); bowl games of the 2022 season remain
  `pre_2023_clock`.

## Verification checklist

- [x] Amended §9.2 / §9.3 text
- [x] Task 14 invariance test description
- [x] Grep §4.5 for era feature (`pre_2023_clock` / `post_2023_clock`)
- [x] No code edited
