# AUDIT-4 — Statistical spec repairs (priors, calibration, variance)

**Date:** 2026-08-06
**Scope:** Documentation only (no code changes).

## Problem

Three statistical defects in the production spec:

1. **Prior fitting circularity.** §9.6 / Task 15 fit prior weights by regressing
   next-season *early* ratings on prior components. Early ratings are
   prior-dominated → the regression recovers the assumed weights.
2. **Calibration breaks consistency.** §5.2 Level 2 / Task 19 fit separate
   isotonic maps per market, violating the §2.2 internal-consistency guarantee
   (`P(win) == P(cover at 0)` after independent maps). SP+ wording in §3.1
   conflicted with §9.6 (prior anchor vs absent from the blend).
3. **Variance pipeline underspecified.** σ-head target, epistemic
   double-counting, winsorized Kalman \(R\) consistency, and Level-1 stacking
   geometry (NNLS+renorm vs simplex QP; intercept) were scattered or wrong.

## Changes

### `docs/DESIGN.md`

| Section | Action |
|---|---|
| **§0.4** | Diagram: calibration labeled PIT / distributional |
| **§2.6** | Distributional PIT recalibration; per-market diagrams diagnostic-only; fundamental targets market-free |
| **§3.1** | SP+ Value cell → benchmark always; prior blend market-aware only (ADR 0003) |
| **§5.2** | Simplex QP Level 1 (no intercept); Predictive variance block (a)(b)(c); Level 2 PIT maps; stack instances cite ADR 0003 |
| **§9.5** | \(R_{\mathrm{eff}} = R \cdot (|z|/2.5)^2\) on clipped innovations |
| **§9.6** | Diffuse-init late-season (≥8) weight fit; Weeks 1–4 likelihood preferred upgrade; OOS vs games/diffuse late ratings; SP+ w7 market-aware only |
| **§9.7** | Calibration row → PIT maps |
| **§15 items 14–19** | Aligned acceptance language with new tests |

### `docs/TASKS.md`

| Task | Action |
|---|---|
| **14** | \(R_{\mathrm{eff}}\) deliverable; CLIPPED-UPDATE VARIANCE test |
| **15** | Diffuse-late / likelihood fitting; OOS vs games; CIRCULARITY DEMONSTRATION test; SP+ market-aware only |
| **17** | σ-head × \(\sqrt{\pi/2}\), net of member-mean; SIGMA UNBIASEDNESS test |
| **19** | Simplex QP; distributional PIT calibration; post-calibration consistency test; fundamental ATS@close diagnostic-only |

### `docs/adr/0003-sp-plus-prior-scope.md`

SP+ in §9.6 prior blend for **market-aware stack only**; nowhere in fundamental
priors or fundamental Stage-2 features; always an external benchmark.

## Decisions recorded (spec was silent / required a pick)

1. **σ-head target:** absolute residuals of the stacked mean × \(\sqrt{\pi/2}\)
   (Normal MAD scaling). Squared-residual / σ² targeting rejected for v1.
2. **SP+:** market-aware prior blend only (ADR 0003), not "nowhere."
3. **No-intercept stacking:** convex combination only; bias → Level-2 PIT maps.
4. **Preferred prior upgrade:** Weeks 1–4 one-step-ahead predictive likelihood
   is stated as the preferred replacement for diffuse-late regression when the
   likelihood plumbing exists; baseline method for Task 15 remains diffuse-late
   OLS/regression.

## Ambiguities left (smallest reasonable choice)

- Thin-data PIT fallback: "parametric (e.g. Beta / Platt-on-PIT)" — exact family
  left to Task 19.
- Diffuse \(P_0\) scale for the fitting run: "wide, nearly flat" — numeric
  multiple of production prior variance left to Task 15 config.
- Whether market-aware Stage-2 *features* also include in-season SP+ is **not**
  required by ADR 0003; only the prior-blend term is decided here.

## Verification — rewritten paragraphs in full

### §9.6 weight-fitting paragraph

```
**Weight fitting (no circularity):** Do **not** fit weights by regressing
next-season *early* (prior-initialized) posterior ratings on the prior
components — early ratings are prior-dominated, so that regression recovers the
*assumed* blend weights rather than the true generative relationship
(circular). Instead, weights are fit by regressing each season's **late-season
(≥8 games played) posterior ratings from a diffuse-initialization filter run**
(wide, nearly flat preseason P₀; no informative prior blend) on the preseason
predictors, over 2015–2024. The diffuse-run late ratings are dominated by
within-season observations, so the regression recovers how well the preseason
covariates predict eventual team strength — not how the prior was constructed.
**Preferred upgrade** (replace the late-rating regression when the likelihood
plumbing is ready): maximize Weeks 1–4 one-step-ahead predictive likelihood of
the *game observations* with respect to the prior weights (and optionally prior
variance hyperparameters), still using time-ordered seasons only. Out-of-sample
acceptance must score fitted priors against **realized game observations** or
against diffuse-run late ratings — never against prior-initialized early
posteriors.
```

### §5.2 Level 2 text

```
**Level 2:** Distributional recalibration per §2.6 — one monotone PIT map on the
OOF margin predictive CDF and one on total — so every derived market probability
recalibrates coherently; then bivariate assembly with estimated ρ; key-number
kernel; Monte Carlo to bet probabilities. Per-market reliability diagrams (ML,
ATS@close, OU@close) are diagnostics only. Fundamental-stack calibration targets
are market-free (distribution / moneyline PIT); ATS@close reliability is never a
fitting target for the fundamental stack.
```

### §5.2 Predictive variance paragraph

```
**Predictive variance (specified exactly once here):**
(a) The σ-head is trained on **absolute residuals of the Level-1 stacked mean**,
|y − μ_stack|, on OOF rows, then multiplied by √(π/2) to yield an unbiased σ
estimate under a Normal residual assumption (E[|Z|]=√(2/π) for
Z∼N(0,σ²)). Squared-residual / σ² targeting is rejected for v1 (heavier outlier
sensitivity on CFB margins).
(b) Total predictive variance decomposes as
σ²_pred = σ²_aleatoric + σ²_members + σ²_Stage-1,
where σ²_aleatoric is the squared σ-head output, σ²_members = Var_k(μ_k) is
disagreement across Level-0 members (weighted by stacking weights), and
σ²_Stage-1 is the variance of the mapping-layer mean across Stage-1 posterior
draws (§2.6 epistemic mixture). The σ-head is fit on residuals that **exclude**
the epistemic components already counted: specifically on OOF residuals of the
stacked mean against the realized outcome **net of the member-mean** — i.e.
labels are |y − μ_stack| where μ_stack is the Level-1 combination of member
means at fixed Stage-1 point estimates (no posterior draws in the σ-head
training labels). Member-disagreement and Stage-1 draw variance are added
afterward; they must not be double-counted inside the σ-head target.
(c) After Level-1 / variance assembly, conformal-check as before (CQR on
quantile heads).
```

## Verification checklist

- [x] Rewritten §9.6 fitting paragraph
- [x] New Level-2 text
- [x] §5.2 variance paragraph (a)(b)(c) + simplex Level 1
- [x] §9.5 \(R_{\mathrm{eff}}\)
- [x] §2.6 / §3.1 reconciled; ADR 0003
- [x] Tasks 14 / 15 / 17 / 19 required tests updated
- [x] No code edited
