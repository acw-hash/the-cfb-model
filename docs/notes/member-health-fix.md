# TASK MEMBER-HEALTH-FIX — Member-credibility contract (ADR 0014)

**Date:** 2026-08-12  
**Scope:** Ensemble credibility + ElasticNet NaN policy + quality-gate addenda.  
**Forbidden (honored):** no member hyperparameter / threshold tuning; no constant
fabrication; no exception suppression around member fits; no gate widening.

Artifacts: `docs/notes/_artifacts/member_health_fix/`.  
ADR: `docs/adr/0014-member-credibility-contract.md`.

---

## Principle (ADR 0014)

A Level-0 member is **CREDIBLE** only if (a) fit completed without exception,
(b) estimator state is consistent with selection state, and (c) output is
non-degenerate on its own training window. Only credible members enter NNLS.
Failures are **EXCLUDED** with `member_status` recorded — never replaced by a
constant. Zero credible members → null μ + `null_reason`; graded metrics treat
those rows as ungradable.

---

## Items 1–4 — Code

| Item | Change |
|------|--------|
| 1 | Removed `suppress(Exception)` around member fits; removed block-wide 2.5 fill in `_predict_point`; ENet clears selection on fit failure |
| 2 | ENet: drop cols with null share > 0.50; impute remaining NaN with **training-window medians only** (PIT test); no zero-fill |
| 3 | Degeneracy check excludes constant cold-start LGBM from NNLS |
| 4 | Gate: ABSENT for scheduled weeks with zero rows; fail if any positive NNLS weight rests on a non-credible member |

Tests: `tests/unit/test_member_health_fix.py` (mechanisms A/B, gate blind spots, PIT impute, μ preserved when σ refused).

**Training-input change:** every market-aware run must re-run under this ADR.

---

## Item 5 — Published runs' member health (stored state)

Script: `scripts/_member_health.py` →
`docs/notes/_artifacts/member_health_fix/item5_summary.json`,
`item5_member_health.csv`.

| Run | Folds | Clean? | Dead/degenerate weighted? |
|-----|------:|:------:|:-------------------------:|
| fundamental_v2 | 18 | yes | no |
| A3_v2 | 18 | yes | no |
| A6_v2 | 12 | yes | no |
| SLOT_CLOSE | 18 | yes | no |

**Verdict:** `any_stop=false`. No published run carried a dead or degenerate
weighted member. No blast-radius STOP.

---

## Item 6 — Tuesday market-aware re-run under the contract

Config: `task23_market_aware_full_reduced_v2_tue`  
Label: `member-health-fix;FEATURE_TIME=TUESDAY_DECISION;ensemble_scope=REDUCED_PER_ADR_0013;ADR_0014`  
Out: `data/backtests/task23_market_aware_reduced_v2_tue/full/` (n=4944)  
MLflow: `851a6408fd3248a394a351a026672648`  
Both gates published (D2 quality + ATS plausibility; D4 distributional after
null_reason skip + constant-σ refusal).

Report: `docs/notes/_artifacts/member_health_fix/item6_report.json`.

### Corrected ATS table + guard dispositions

| Slice | ATS | n | LL | MAE | 95% CI | Disposition |
|-------|----:|--:|---:|----:|--------|-------------|
| 2019 (CFBD eval lines; null snapshot `mkt_*`) | 49.19% | 553 | 0.796 | 17.78 | [44.8%, 53.2%] | **INSIDE_BAND_PUBLISHED** (44.5–55.5%) |
| 2021–2024 snapshots | 50.27% | 3491 | 0.771 | 13.46 | [48.6%, 51.9%] | **INSIDE_BAND_PUBLISHED** (47.46–52.54%) |

### Focus blocks — member_status + NNLS

**2019 w1:** ABSENT (scheduled games, zero prediction rows) — gate reports ABSENT, not a silent pass.

**2019 w2–4 (Mechanism A repaired):**

| week | n | finite μ | SD(μ) | w_lgbm | w_enet | lgbm credible | enet credible | null rows |
|-----:|--:|--------:|------:|-------:|-------:|:-------------:|:-------------:|----------:|
| 2 | 74 | 41 | 25.16 | 0.0 | 1.0 | no (degenerate) | yes | 33 |
| 3 | 68 | 34 | 24.73 | 0.0 | 1.0 | no (degenerate) | yes | 34 |
| 4 | 58 | 35 | 26.98 | 0.0 | 1.0 | no (degenerate) | yes | 23 |

Fold 0 `member_status`: LGBM `degenerate_constant_on_train` (train_sd≈0);
ENet credible (train_sd≈3.14); NNLS weight **100% ENet**.

These weeks emit **honest point μ** from ENet where finite; σ is refused when
block-constant (no fabricated floor) → `p_ats` missing → games do not enter
ATS n. Per-row `|enet μ| > 80` (or nonfinite) → null μ with
`null_reason=no_credible_members` (90 rows total, all in 2019 w2–4) — never a
constant fill.

**Zero-credible / `cold_start_insufficient` blocks on this Tuesday re-run: 0.**
Item 3's hypothetical (entire block null when no member is credible) did not
fire: ENet remained credible. The honest cold-start gap is LGBM exclusion +
missing σ/probs + 90 per-row OOD nulls — not a fabricated constant leaf.

**2023 w1–10 (Mechanism B repaired):**

| week | n | SD(μ) | w_lgbm | w_enet | both credible |
|-----:|--:|------:|-------:|-------:|:-------------:|
| 1 | 136 | 15.23 | 0.505 | 0.495 | yes |
| 2 | 85 | 15.77 | 0.505 | 0.495 | yes |
| 3 | 75 | 14.55 | 0.505 | 0.495 | yes |
| 4 | 67 | 12.14 | 0.505 | 0.495 | yes |
| 5 | 59 | 11.66 | 0.653 | 0.347 | yes |
| 6 | 51 | 9.57 | 0.653 | 0.347 | yes |
| 7 | 55 | 10.53 | 0.653 | 0.347 | yes |
| 8 | 54 | 10.92 | 0.653 | 0.347 | yes |
| 9 | 54 | 10.71 | 0.653 | 0.347 | yes |
| 10 | 65 | 10.85 | 0.773 | 0.227 | yes |

ENet fits through NaN market features via the ADR policy; no 2.5 fill; no
weight on a dead member. Gate: `n_zero_sd_blocks=0`,
`noncredible_weight_blocks=[]`.

### Ungradable

| reason | n rows |
|--------|-------:|
| `no_credible_members` (per-row ENet OOD in 2019 w2–4) | 90 |
| `cold_start_insufficient` | 0 |

ABSENT blocks: `[(2019, 1)]`.

### 2019 equivalence (MKT-2019-FIX Step 4, owed)

Snapshot features keep 2019 `mkt_*` null + `is_missing`. Graded 2019 ATS under
the contract is **49.19% (n=553)** — inside the published band.

Overlap on the 553 games with finite aware `p_ats` vs A3_v2 / fundamental_v2
(identical on this overlap):

| | aware | A3/fund | Δ pp | side agree |
|--|------:|--------:|-----:|----------:|
| ATS | 49.19% | 52.08% | −2.89 | 88.8% |

n is below A3's 743 because early-2019 weeks lack `p_ats` (σ refused) and 90
rows are intentional null μ. Not a silent CFBD-feature contamination path.

### Aware vs A3 (re-stated)

| Slice | aware ATS (n) | A3 ATS (n) | Δ pp |
|-------|--------------:|-----------:|-----:|
| 2019 | 49.19% (553) | 50.74% (743) | −1.55 (n mismatch) |
| 2021–2024 | 50.27% (3491) | 52.22% (3491) | −1.95 |

---

## Decisions / ambiguities

1. **Constant σ refused, finite μ kept.** After refusing a block-constant σ
   floor (D4 / ADR 0014), predict emits honest point μ with null σ/probs rather
   than nulling the whole block. Fabricating σ is forbidden; erasing μ is also
   forbidden.
2. **Predict must not mutate fit-time `_null_reason`.** A local reason is used
   so one bad week cannot poison later weeks until the next retrain.
3. **Per-row OOD vs block cold-start.** `|enet|>80` nulls share the
   `no_credible_members` reason string in this publish; the block still has a
   credible ENet member. A dedicated `member_prediction_nonfinite` reason is
   left as a follow-up label clarification (not a threshold change).
4. **Item 5 clean ⇒ no re-run of published fundamental/A3/A6/SLOT_CLOSE** solely
   for member health; only market-aware Tuesday was re-run (training inputs
   changed by ENet NaN policy).

---

## Acceptance

- [x] ADR 0014
- [x] Failing-then-passing tests for Mechanisms A/B, gate ABSENT + partial death, PIT impute
- [x] Item 5 table (clean)
- [x] Item 6 tables + both gates
- [x] `make lint typecheck test`
- [x] Commit
