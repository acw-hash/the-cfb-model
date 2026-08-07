# AUDIT-5 — Evaluation-integrity spec amendments

**Date:** 2026-08-06
**Scope:** Documentation only (no code changes). Touched: `docs/DESIGN.md`,
`docs/TASKS.md`, `docs/lockbox_access.md` (new).

## Changes summary

| Item | Where | Action |
|---|---|---|
| Lockbox season **2025** | §7.2 item 9; §7.2 item 1; §6; Tasks 18, 23; §15 | New; quarantine = **2024** |
| Promotion multiplicity | §8 item 7; Task 22; §1.6 | Bonferroni α₀/k; ledger; paper-trade confirmatory |
| Leakage suite | §14; Task 16; §15 item 16 | Delete shifted-label; add within-week permutation + planted prophecy |
| Conformal | §2.6; Task 19; §15 item 19 | Approximate coverage; ACI production; split-CQR initializer |
| Key numbers | §2.3; Task 19 item 6 | Conditional on μ_M; bucket validation |
| Data size | §0.3; §9.7 (+ §4.1, §5.1 GP for consistency) | ~10k games; ~60 ≈ 0.6% |

---

## 1. New §7.2 lockbox text

```
9. **Lockbox season (2025).** Season **2025** — the most recent completed FBS
   season at the time of writing (Aug 2026) — is a **lockbox**. It is excluded
   from **all** development, HPO, ablation, and promotion evaluations. It may be
   read **at most once per calendar year** for a confirmatory report only. Every
   lockbox read is logged in `docs/lockbox_access.md` (date, reader, purpose,
   git SHA of the code/config used, and a one-line summary of what was reported).
   The Task 18 quarantine-tiebreak season (**2024**) is a **different** season
   from the lockbox and must remain so if either designation is ever revised.
```

Walk-forward primary harness seasons become `{2019, 2021, 2022, 2023, 2024}`.
Task 23: `2019-2024`; A6 on `2021-2024`. Access log stub: `docs/lockbox_access.md`.

---

## 2. Replaced passages — before / after

### §0.3 data regime (excerpt)

**BEFORE:**
```
… not for forcing transformers onto 20,000 rows.
```

**AFTER:**
```
Across 2014–2025 that is roughly **~10,000 FBS games**, not 20k. …
… not for forcing transformers onto ~10,000 rows.
```

### §1.6 success criteria — confirmatory instrument

**BEFORE:** (ended at Process bullet; no confirmatory instrument)

**AFTER:**
```
- **Confirmatory instrument:** live forward performance via paper-trade
  (§16 item 2) is the confirmatory check on these success criteria —
  backtest/promotion gates are necessary but not sufficient; a full (or half)
  season of paper-traded CLV is what confirms the criteria under live
  information flow.
```

### §2.3 option (B) key-number kernel

**BEFORE:**
```
… an empirical margin-distribution kernel learned from historical residuals
reallocates probability mass to exact margins (3, 7, 10, 14…). Key numbers
matter less in CFB than NFL (more variance, higher totals) but are not
negligible; the empirical kernel handles this without hand-tuning.
```

**AFTER:**
```
… an empirical margin-distribution kernel learned **conditional on the
predicted margin** — at minimum by buckets of `μ_M`; preferably a smooth model
of `P(M = k | μ_M, σ_M)` — reallocates probability mass to exact margins
(3, 7, 10, 14…). An unconditional residual kernel is rejected: key-number mass
depends on where the continuous predictive sits (a −3 favorite is not a −20
favorite). … **Validation:** empirical exact-margin frequencies by
predicted-spread bucket must be compared to kernel output (same buckets);
material divergence is a misspecification alarm, not a silent acceptance.
```

### §2.6 prediction intervals / conformal

**BEFORE:**
```
- **Prediction intervals:** primary intervals from the parametric predictive
  distribution; **split conformal prediction** (Vovk; Romano et al.'s CQR
  variant on the quantile heads) wrapped on top as a distribution-free
  guarantee layer, using the trailing 2 seasons as calibration set. Report
  both; alert if they diverge materially (a symptom of misspecification).
```

**AFTER:**
```
- **Prediction intervals:** primary intervals from the parametric predictive
  distribution; a **conformal layer** (Vovk; Romano et al.'s CQR variant on
  the quantile heads) wrapped on top. Split-conformal coverage is guaranteed
  only under exchangeability; season-over-season drift in CFB violates that
  assumption, so the layer provides **approximate** coverage and is monitored
  weekly (empirical coverage vs nominal at 50/80/95%). **Production variant:
  Adaptive Conformal Inference (ACI)** — online α adjustment that tracks
  realized coverage under non-exchangeability. **Initializer:**
  trailing-2-season split conformal / CQR sets the starting conformity scores
  and α before ACI takes over. Report parametric and conformal intervals;
  alert if they diverge materially (a symptom of misspecification) or if
  weekly conformal coverage drifts outside tolerance.
```

### §7.2 item 1 season set

**BEFORE:**
```
For each test season `Y` in {2019, 2021, 2022, 2023, 2024, 2025}: …
```

**AFTER:**
```
For each test season `Y` in {2019, 2021, 2022, 2023, 2024}: …
**Season 2025 is excluded from this harness** — it is the lockbox (§7.2 item 9).
```

### §8 item 7 promotion

**BEFORE:**
```
7. **Registry & promotion** (`registry/`): MLflow model registry; candidate
   promoted to `champion` only if it beats the incumbent on the pre-registered
   metric set (CRPS + log-loss + CLV-backtest) on the same walk-forward seasons
   with a paired block-bootstrap test at p < 0.10, *and* passes calibration and
   leakage gates. Otherwise archived with the comparison report. One-command
   rollback to any prior champion.
```

**AFTER:**
```
7. **Registry & promotion** (`registry/`): … lockbox excluded — §7.2 item 9 …
   significance threshold is **Bonferroni-adjusted for promotion multiplicity**
   … Exact rule: append-only **promotion-attempt ledger**; within each calendar
   year, k = attempts that year including current, α₀ = 0.10; significant iff
   p < α₀/k. Comparison report **must** print k, α₀, and α₀/k. …
   Live forward paper-trade (§16 item 2; named in §1.6) remains the confirmatory
   instrument for success criteria after any promotion.
```

### §9.7 weekly-retrain justification

**BEFORE:**
```
Weekly retraining rejected: ~60 games add <1% to a 20k-game training set; …
```

**AFTER:**
```
Weekly retraining rejected: ~60 games add ~0.6% (<1%) to a ~10k-game training
set; …
```

Recompute: 60 / 10_000 = 0.6%. Statement kept (still <1%).

### §14 leakage suite

**BEFORE:**
```
**leakage suite** (pit_audit random-row recomputation; shifted-label test —
models must score ≈ chance predicting *past* games from future features;
feature-timestamp static analysis)
```

**AFTER:**
```
**leakage suite** (pit_audit random-row recomputation; **within-week label
permutation** — models trained on labels permuted within week must score ≈
chance out-of-sample; **planted prophecy** — a deliberately future-leaking
feature is added in a test fixture and both pit_audit and the information-set
audit must catch it; feature-timestamp static analysis)
… The former "shifted-label" null … is **deleted**: strength persistence makes
future features legitimately predictive of past games, so that null is wrong.
```

### Task 16 tests

**BEFORE:**
```
- SHIFTED-LABEL TEST per §14: a model given future features to predict PAST
  games must score approximately at chance. Build the hook for this now.
```

**AFTER:**
```
- WITHIN-WEEK LABEL PERMUTATION TEST per §14: …
- PLANTED PROPHECY TEST per §14: …
```

### Task 18 quarantine

**BEFORE:**
```
… compare the top-5 configs on a season never used in the study; …
```

**AFTER:**
```
… compare the top-5 configs on season **2024** (never used in the study;
distinct from lockbox **2025** per §7.2 item 9); …
```

### Task 19 conformal + key numbers

**BEFORE:**
```
4. Conformal layer: split conformal / CQR on the quantile heads using trailing 2
   seasons as the calibration set. Report empirical coverage vs nominal at
   50/80/95%.
…
6. distribution/key_numbers.py: empirical discretization kernel per §2.3
   reallocating probability mass to exact margins learned from historical
   residuals. Do NOT hand-tune key-number bumps.
```

**AFTER:**
```
4. Conformal layer per §2.6: ACI production variant; trailing-2-season
   split-CQR initializer; approximate coverage under drift; do not claim
   distribution-free guarantee.
…
6. … kernel CONDITIONAL on predicted margin (buckets of mu_M min; smooth
   P(M=k|mu_M,sigma_M) preferred). Validation: empirical exact-margin
   frequencies by predicted-spread bucket vs kernel output.
```

### Task 22 promotion

**BEFORE:**
```
… paired block-bootstrap test at p < 0.10, AND passes calibration and leakage
gates.
```

**AFTER:**
```
… Bonferroni-adjusted … k including current, α₀ = 0.10, significant iff
p < α₀/k; report prints k and α₀/k; paper-trade confirmatory per §1.6 / §16.2.
```

### Task 23 seasons

**BEFORE:**
```
Full walk-forward backtest 2019-2025 …
Run it on 2021-2025 only …
```

**AFTER:**
```
Full walk-forward backtest 2019-2024 (lockbox 2025 excluded) …
Run it on 2021-2024 only …
```

---

## 3. Grep verification: `20,000` / `20k`

| Location | Status |
|---|---|
| §0.3 game-count / "20,000 rows" | **Corrected** → ~10,000 |
| §9.7 "20k-game" | **Corrected** → ~10k-game; ~0.6% |
| §4.1 "20k-row dataset" | **Corrected** → ~10k-row (consistency) |
| §5.1 GP "20k×150" | **Corrected** → ~10k×150 (consistency) |
| §3.2 Odds API `20K plan … 20,000 credits/mo` | **Unchanged** (credits, not games) |
| TASKS.md Task 5B `20,000/month` quota | **Unchanged** (credits) |

No remaining game-count `20k` / `20,000` claims in DESIGN.md.

---

## 4. Decisions / ambiguities

1. **Lockbox = 2025** as most recent completed season at writing (Aug 2026).
2. **Quarantine = 2024** (explicitly ≠ lockbox).
3. **Bonferroni:** α_adj = α₀ / k with α₀ = 0.10 and k including the current
   attempt within the calendar year.
4. ACI step-size / learning rate for online α left to Task 19 implementation
   (spec silent); initializer is trailing-2-season split-CQR.
5. Conditional kernel: bucketed μ_M is the minimum; smooth P(M=k|μ_M,σ_M) is
   preferred — Task 19 may ship buckets first if the smooth model is deferred,
   but must still run the bucket-frequency validation.
