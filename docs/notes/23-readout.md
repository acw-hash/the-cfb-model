# TASK 23-READOUT — Decision memo (corrected v2)

**Date:** 2026-08-11  
**Status:** DECISION — documentation only.  
**ensemble_scope:** `REDUCED_PER_ADR_0013` (not §5.2-complete; ADR 0013).  
**Vintages in force:** **REGRADED_V2** (fundamental, A1, A2, A4, A5 — fixed
closes on stored μ/σ) and **RERUN_V2** (A3, A6 published; market-aware refused
by ATS plausibility guard).  
**Contamination:** all v1 snapshot-regime ATS / ATS log-loss / A2 ATS deltas /
market-aware snapshot ATS / A3 “market features hurt” / A6 **36.5%** in
`docs/notes/23-rerun-r1.md` are **CONTAMINATED_v1** — cite only inside an
explicit contamination reference. MAE, CRPS, weekly MAE curve, A2 MAE (+1.60),
OU@close, and SU from that memo remain valid.

Sources: DESIGN §1.6 / §13 / §16; `23-rerun-r1.md` (CONTAMINATION notice);
`ats-grade-diag.md`; `ats-grade-fix.md` + RERUN_V2 appendix;
`docs/adr/0013-ensemble-membership-divergence.md`;
`docs/notes/_artifacts/ats_grade_fix/regrade_summary.json`;
`docs/notes/_artifacts/ats_grade_fix/rerun_v2_summary.json`.

---

## 1. THESIS (A2) — two clauses, not one

**Clause A — the rating engine learns in-season (point prediction).**  
Freezing Stage-1 after Week 1 (A2) vs continual updates (fundamental),
**REGRADED_V2 / REDUCED**, all-season basis (n=4375):

| Metric | Continual (fund) | A2 frozen | Δ |
|---|---:|---:|---:|
| MAE margin | 14.85 | 16.45 | **+1.60** |
| CRPS margin | 10.68 | 11.87 | **+1.20** |

Weekly MAE curve (fundamental, REDUCED, untouched): Week 4 → 14.98, Week 10 →
13.55 (Δ −1.43). Continual Stage-1 updates are a real MAE/CRPS gain.

**Clause B — how much of that learning the close already prices (sides).**  
ATS deltas, **REGRADED_V2 / REDUCED**, regimes never pooled:

| Regime | Fund ATS | A2 ATS | Δ (pp) | n |
|---|---:|---:|---:|---:|
| CFBD 2019 | 51.3% | 46.9% | **−4.3** | 743 |
| Snapshots 2021–24 | 50.7% | 50.4% | **−0.3** | 3496 |

The MAE/CRPS learning is **not** the same object as an ATS edge vs the close.
On the multi-season snapshot ladder the ATS gap shrinks to noise (−0.3 pp). The
2019 −4.3 pp read is a **small-n caveat** (n=743, single CFBD-close regime) and
must not be pooled with snapshots or treated as the headline market result.

*(CONTAMINATED_v1 reported snapshot ATS Δ ≈ −3.4 pp; that number is invalid.)*

---

## 2. MARKET (probabilistic)

**Log-loss vs fair-coin market baseline 0.693** (ATS @ −110/−110 → fair 0.5),
post-fix, **REDUCED**:

| Run | Vintage | 2019 LL | Snapshots LL |
|---|---|---:|---:|
| fundamental | REGRADED_V2 | 0.946 | 0.924 |
| A1 | REGRADED_V2 | 0.968 | 0.924 |
| A2 | REGRADED_V2 | 1.041 | 0.933 |
| A4 | REGRADED_V2 | 0.883 | 0.901 |
| A5 | REGRADED_V2 | 0.923 | 0.913 |
| A3 market-off | RERUN_V2 | 0.950 | 0.820 |
| A6 cfbd features | RERUN_V2 | — | 0.865 |

The miss is now **cleanly measured on correct labels** and is **universal**
across published runs: model ATS log-loss ≈ **0.82–1.04** (REGRADED_V2 core
band **0.88–1.04**; A3 snapshot RERUN_V2 extends the low end to 0.820), all ≫
0.693.

**Suspect:** the **derived-probability path** (margin distribution →
`p_ats_home`), distinct from the closed P0-1 calibration/history path. Hard-pick
ATS straddles ~50% while probabilities remain badly scored — that points at
cover-probability construction / calibration of Φ((μ+S)/σ) (and MC equivalents),
not at “the whole stack is broken.”

**CRPS vs de-vigged market baseline:** **NOT COMPUTED.**  
`regrade_summary.json` / `rerun_v2_summary.json` / the reduced metrics summary
emit model CRPS only; no de-vigged closing-line CRPS was written for this
REDUCED REGRADED_V2 / RERUN_V2 suite. Do not backfill from CONTAMINATED_v1 or
pre-rerun Task 23 market-CRPS figures.

Market-aware full **RERUN_V2** did not publish (AtsPlausibilityError at
snapshot ATS 52.71% > band [47.46%, 52.54%], n=3491) — no graded market-aware
log-loss table.

---

## 3. SIDES / TOTALS

### ATS vs close (REDUCED; regimes never pooled)

| Run | Vintage | Regime | ATS | n | 95% bootstrap CI | 95% naive CI |
|---|---|---|---:|---:|---|---|
| fundamental | REGRADED_V2 | 2019 | 51.3% | 743 | [48.3%, 54.3%] | [47.7%, 54.9%] |
| fundamental | REGRADED_V2 | snapshots | 50.7% | 3496 | [48.7%, 52.7%] | [49.0%, 52.3%] |
| A1 | REGRADED_V2 | 2019 | 50.2% | 743 | [47.9%, 52.9%] | [46.6%, 53.8%] |
| A1 | REGRADED_V2 | snapshots | 50.5% | 3496 | [48.7%, 52.3%] | [48.8%, 52.1%] |
| A2 | REGRADED_V2 | 2019 | 46.9% | 743 | [44.2%, 49.4%] | [43.4%, 50.6%] |
| A2 | REGRADED_V2 | snapshots | 50.4% | 3496 | [49.1%, 51.5%] | [48.7%, 52.1%] |
| A4 | REGRADED_V2 | 2019 | 51.1% | 743 | [46.2%, 55.5%] | [47.5%, 54.7%] |
| A4 | REGRADED_V2 | snapshots | 50.7% | 3496 | [48.3%, 53.5%] | [49.1%, 52.4%] |
| A5 | REGRADED_V2 | 2019 | 49.9% | 743 | [45.4%, 54.4%] | [46.3%, 53.5%] |
| A5 | REGRADED_V2 | snapshots | 50.6% | 3496 | [48.2%, 53.1%] | [49.0%, 52.3%] |
| A3 | RERUN_V2 | 2019 | 50.7% | 743 | [48.0%, 53.7%] | [47.1%, 54.3%] |
| A3 | RERUN_V2 | snapshots | 52.2% | 3491 | [50.3%, 54.2%] | [50.6%, 53.9%] |
| A6 | RERUN_V2 | snapshots | 51.9% | 3369 | [50.9%, 53.0%] | [50.2%, 53.6%] |

**Does any ATS CI exclude 50%?**  
- **Fundamental / A1 / A4 / A5 REGRADED_V2:** no — both regimes’ bootstrap CIs
  include 50%.  
- **A2 2019 REGRADED_V2:** bootstrap [44.2%, 49.4%] excludes 50% **low** (frozen
  ratings; expected worse).  
- **A3 / A6 RERUN_V2 snapshots:** bootstrap CIs exclude 50% **high** ([50.3%,
  54.2%] and [50.9%, 53.0%]). Neither clears a clean §1.6 ≥51.5% claim once
  market-aware remains unpublished and the reduced stack is not §5.2-complete.

### OU vs close (REDUCED; untouched by ATS-grade bug)

From `23-rerun-r1.md` / `metrics_summary.json` (fundamental):

| Regime | OU | n | 95% bootstrap CI | 95% naive CI |
|---|---:|---:|---|---|
| CFBD 2019 | 50.9% | 747 | [46.6%, 55.4%] | [47.3%, 54.5%] |
| Snapshots 2021–24 | 52.3% | 3136 | [49.7%, 54.8%] | [50.6%, 54.1%] |

**Possessions-null caveat (verbatim):** Per prep ambiguity: **no `is_missing`
indicator column** in provider output; values are **NaN** (LightGBM-native),
**never zero-filled**. Drives staged for **2023 only** → structural 100% null
for `expected_possessions` except partial 2023 week≥5 after first PIT retrain.
OU is therefore measured without the §4.5 key totals feature on almost all
rows; Weeks 1–4 OU accuracy is partly on a totals feature that is absent for
almost all rows.

---

## 4. MEASUREMENT GAPS — only remaining §1.6 instruments unmeasurable

| Instrument | Status | Successor |
|---|---|---|
| **CLV** (primary §1.6) | **NOT COMPUTED** — runner has no `bets.parquet` / settle path | **CLV/bets runner seam** |
| **Possessions / honest OU** | Structurally null outside 2023 (drives never backfilled) | **Drives backfill 2014–2025** (CFBD `/drives`, season_week grain; order-of-hundreds of GETs vs Tier 1 ~5k/mo — small; **do not run from this memo**) |

These are the **only** §1.6 instruments still unmeasurable after the ATS-grade
fix. Everything else below is measured (or scored as miss) on correct
labels under REDUCED scope.

---

## 5. §1.6 SCORECARD (REDUCED)

| Criterion | Result | Number vs target | Vintage |
|---|---|---|---|
| Mean same-book CLV > 0, 95% CI excludes 0, n≥300 | **UNMEASURABLE** | NOT COMPUTED (no bets/settle path) | — |
| Fundamental ATS ≥ 51.5% | **MISSED** | Snapshots **50.7%** [48.7%, 52.7%] (n=3496); 2019 **51.3%** [48.3%, 54.3%] (n=743) — neither regime’s CI clears 51.5% as a clean pass | REGRADED_V2 |
| Fundamental OU ≥ 51.5% | **MISSED / uninterpretable** | Snapshots **52.3%** [49.7%, 54.8%] (CI includes 51.5%); 2019 **50.9%** — possessions structurally null outside partial 2023 | REDUCED (OU untouched) |
| Brier / log-loss ≤ market baseline | **MISSED** | ATS LL **0.82–1.04** vs market **0.693** (universal) | REGRADED_V2 + RERUN_V2 |
| Calibration slope ∈ [0.9, 1.1] | **UNMEASURABLE this session** | Not re-scored on the corrected suite | — |
| Process: zero leakage / pipeline | **Carried** | Prep wiring + 22B audits; not re-litigated | — |
| Full §5.2 ensemble | **MISSED by definition** | REDUCED_PER_ADR_0013 | ADR 0013 |

No hyperparameters tuned against these numbers. Lockbox 2025 excluded.

---

## 6. FINDINGS LEDGER

### 6a. A4 — single LGBM beats the reduced NNLS stack

**Evidence (REGRADED_V2 / REDUCED, all-season unless noted):**

| Metric | Reduced stack (fund) | Single LGBM (A4) | Increment (stack − single) |
|---|---:|---:|---:|
| MAE margin | 14.85 | 14.15 | **+0.70** (stack worse) |
| CRPS margin | 10.68 | 10.14 | **+0.54** (stack worse) |
| ATS LL 2019 / snap | 0.946 / 0.924 | 0.883 / 0.901 | stack worse |
| Snapshot ATS | 50.7% | 50.7% | 0.0 pp (v1 +3.8 pp was grading artifact) |

Total-side A4 remains stub-vs-stub (measures nothing about §5.2 ensembling).

**Disposition:** The membership build (ADR 0013, already pre-registered) is the
test of whether full §5.2 composition fixes this; until then the reduced stack
adds **negative value** vs single LGBM on MAE, CRPS, and log-loss. Promote only
through the Task 22 gate vs the reduced incumbent.

### 6b. A1 — fitted priors slightly worse than league-mean

**Evidence (REGRADED_V2 / REDUCED, all-season):** league-mean priors (A1) MAE
14.80 / CRPS 10.63 vs fitted fundamental 14.85 / 10.68 (Δ ≈ −0.04 / −0.05 —
fitted slightly worse). Snapshot ATS 50.5% vs 50.7%.

**Disposition:** Deficit list; **candidate research sprint** (pre-registration
draft only — see §7).

### 6c. A5 — GT filter off improves MAE/CRPS

**Evidence (REGRADED_V2 / REDUCED, all-season):** GT-off MAE 14.39 / CRPS 10.35
vs fund 14.85 / 10.68 (Δ ≈ −0.46 / −0.33). Snapshot ATS 50.6% vs 50.7%.

**Disposition:** Spec-vs-performance tension; does **NOT** license flipping the
filter off in production. Candidate pre-registered sprint (e.g. GT-filtered
features for ratings, GT-inclusive for the margin head) — draft only.

### 6d. A3 and A6 (v2) — market features after correct features + fixed ladder

**What they measure now:**

- **A3 RERUN_V2:** market features off, graded on fixed snapshot ladder —
  snapshot ATS **52.2%** [50.3%, 54.2%] (n=3491), LL 0.820; vs fundamental
  REGRADED_V2 snapshot ATS **50.7%** → **+1.5 pp**.
- **A6 RERUN_V2:** CFBD open/close as **features only**, graded on the **same**
  fixed snapshot ladder — snapshot ATS **51.9%** [50.9%, 53.0%] (n=3369),
  MAE 14.94. CONTAMINATED_v1 **36.5%** was grading contamination, not “CFBD
  features destroy ATS.”
- **Market-aware RERUN_V2:** unpublished (guard trip at 52.71%).

**Disposition of CONTAMINATED_v1 “market features hurt margin”:** **died.**
The v1 A3 +6.9 pp ATS vs market-aware story was a dual contamination (buggy
closes + buggy `mkt_spread≈0` features). On correct labels, A3 sits above
fundamental; unpublished market-aware 52.71% would reverse the A3-vs-aware
sign if published. MAE comparison vs market-aware still needs a published
table (blocked by guard).

### 6e. Fundamental / A4 identical line-backed ATS — verified coincidence

Both fundamental and A4 **REGRADED_V2** report line-backed ATS **2153 / 4239
(50.79%)**. This is a **verified coincidence**, not a bug:

| Run | 2019 hits / n | Snapshot hits / n | Total |
|---|---|---|---|
| fundamental | 381 / 743 | 1772 / 3496 | **2153 / 4239** |
| A4 | 380 / 743 | 1773 / 3496 | **2153 / 4239** |

Different regime splits; same aggregate. Pre-empt the false bug report.

---

## 7. VERDICT AND SEQUENCE

### Verdict (one recommendation)

**NOT CURRENTLY FIT TO BET.**

Sharper statement: point-prediction machinery is **credible** (weekly MAE curve
passes, MAE/CRPS sane, A2 Clause A confirms in-season learning on the rating
engine) but **no edge vs the close is demonstrated** (ATS straddles ~50% on
fundamental REGRADED_V2; log-loss loses universally to 0.693; CLV unmeasurable)
and **two §1.6 instruments remain unmeasurable** (CLV; honest OU via
possessions).

The v2 trio does **not** overturn this: A3/A6 snapshot ATS ~52% with CIs that
exclude 50% high is interesting under REDUCED scope, but market-aware failed to
publish, log-loss still loses (0.82–0.87 on those runs), and §1.6 primary CLV is
absent. Do not treat ~52% hard-pick ATS as a license to bet.

### Successor sequence (dependency order)

1. **CLV/bets runner seam** — wire `bets.parquet` + settle path into the
   backtest runner so same-book CLV can be measured. Unblocks the primary §1.6
   instrument.
2. **Drives backfill 2014–2025** — stage CFBD `/drives` outside 2023 so
   `expected_possessions` is not structurally null. Unblocks honest OU.
   Quota: small CFBD spend (season_week `/drives`; order-of-hundreds of GETs
   vs Tier 1 ~5k/mo headroom) — **flag only; do not run from this memo.**
3. **Membership build per ADR 0013** — compose missing §5.2 members into
   `ProductionEnsemblePredictor`; test finding 6a through the Task 22 promotion
   gate vs the reduced incumbent.
4. **Plan-estimator recalibration** — replace the ~90 s/run fantasy with the
   **eleven measured wall clocks** (eight REDUCED_v1 + three RERUN_V2 actuals in
   `23-rerun-r1.md` / `v2_wall_clocks.json`).
5. **Determinism re-run demonstration** — single hash of fundamental
   predictions was recorded; acceptance requires a **second independent run**
   with byte-identical tables, not a one-shot hash.
6. **Candidate research sprints (DRAFTS only — no code, no runs):**
   - **6b priors:** hypothesis — fitted preseason priors beat league-mean on
     all-season MAE/CRPS by a pre-registered ε; metric MAE+CRPS; threshold
     pre-registered; seasons 2019+2021–2024 lockbox-excluded.
   - **6c GT dual-path:** hypothesis — GT-filtered Stage-1 + GT-inclusive
     margin head beats both pure-on and pure-off on MAE/CRPS without harming
     ATS LL; metric MAE/CRPS + ATS LL; threshold pre-registered; same seasons.
   - **Derived-prob calibration:** hypothesis — recalibrating
     margin-distribution → `p_ats_home` (or replacing Φ-path with MC@close)
     drives ATS log-loss to ≤ market 0.693 + ε on held-out seasons; metric ATS
     LL vs 0.693; threshold ≤ market; seasons snapshot 2021–2024 (+ 2019
     separately). Pre-registration only.

### What gates Task 24 and the webapp

| Product intent | Gates |
|---|---|
| **Predictions-with-uncertainty** (forecasting / reporting) | **Nothing on this list gates Task 24.** Prefect flows and a prediction UI can proceed; advertise forecast quality honestly, not betting edge. |
| **Betting recommendations** | Gated by **CLV seam + membership build + the pre-registered sprints that address log-loss / priors / GT** (and drives backfill before trusting OU recommendations). Paper-trade (§16) remains the confirmatory instrument after those land. |

Plan-estimator recalibration and determinism re-run are **optional hygiene**
for either product; they do not gate Task 24.

---

## Standing labels (carry forward)

- Every number above is **REDUCED_PER_ADR_0013** unless marked otherwise.
- Never mix unlabeled CONTAMINATED_v1 snapshot ATS with REGRADED_V2 / RERUN_V2.
- Regimes never pooled; lockbox 2025 excluded; no lockbox read.
- No hyperparameter, threshold, or feature “quick fix” proposed.
