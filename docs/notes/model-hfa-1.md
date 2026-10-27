# MODEL-HFA-1 — Home lean in `p_win_home` (report only)

**Date:** 2026-10-08  
**Scope:** Diagnostics only. No changes to model, calibrator, or export.  
**Artifacts:** `docs/notes/_artifacts/model-hfa-1/summary.json`

## Data

| corpus | source | n used |
|---|---|---:|
| Walkforward 2019–2024 | `data/backtests/task23_fundamental_reduced_v2/full/predictions.parquet` | 4,943 (1 unscored dropped) |
| 2026 graded | `data/social/artifacts_cache/latest/results_2026.json` (`grade_status=graded`) | 387 |
| `neutral_site` | join on `game_id` from `data/staged/games` (2019–2026) | — |

Column map (walkforward): `mu = pred_margin`, `sigma = sigma_m`, `actual_margin = realized_margin`, `p_cal = p_ml_home`, `p_raw = p_ml_home_raw`, `p_phi = Φ(μ/σ)`.  
2026 published results expose `p_win_home` (= calibrated) and μ/σ; **`p_ml_home_raw` is unavailable** without a model re-run.

Bootstrap: 4,000 resamples, seed `20261008`, 95% percentile CIs.

---

## 1. Margin residual `actual_margin − μ`

Positive ⇒ home scored more than μ predicted ⇒ μ leaned toward the road.

### Walkforward (`task23_fundamental_reduced_v2`)

| slice | n | mean | 95% CI | median | 95% CI |
|---|---:|---:|---|---:|---|
| non-neutral | 4,631 | **+1.35** | [+0.79, +1.89] | **+0.92** | [+0.40, +1.52] |
| neutral | 312 | **−7.33** | [−9.47, −5.30] | **−7.05** | [−9.72, −5.65] |

Non-neutral by season (mean residual): 2019 +3.35; 2020 −1.88; 2021 +0.25; 2022 +1.07; 2023 −0.01; 2024 +4.34. Pooled positive signal is real but not every season.

### 2026 graded

| slice | n | mean | 95% CI | median | 95% CI |
|---|---:|---:|---|---:|---|
| non-neutral | 381 | **−4.11** | [−5.98, −2.30] | **−3.85** | [−6.22, −1.16] |
| neutral | 6 | **−18.3** | [−29.9, −8.3] | **−17.4** | [−33.1, −4.4] |

2026 (partial season) flips sign vs the walkforward pool: homes are **under**-performing μ.

Neutral cells are sparse / selected (bowls, neutrals); large negative residuals are consistent with HFA still in μ when the site is neutral, or with “home” designation on neutrals — flagged for a separate look, not used as the main verdict.

---

## 2. Log-loss and Brier (home win = `actual_margin > 0`; no ties in these sets)

### Walkforward overall (n=4,943)

| predictor | log-loss | Brier |
|---|---:|---:|
| `p_ml_home` (calibrated / published) | 0.5778 | 0.1964 |
| `Φ(μ/σ)` | 0.5775 | 0.1963 |
| uncalibrated MC `p_ml_home_raw` | **0.5770** | **0.1961** |

### Walkforward `|μ| < 3` (n=1,129)

| predictor | log-loss | Brier |
|---|---:|---:|
| calibrated | 0.6901 | 0.2485 |
| `Φ(μ/σ)` | **0.6877** | **0.2473** |
| raw MC | 0.6892 | 0.2481 |

### 2026 graded overall (n=387) — raw MC N/A

| predictor | log-loss | Brier |
|---|---:|---:|
| `p_win_home` | 0.4598 | 0.1457 |
| `Φ(μ/σ)` | **0.4400** | **0.1410** |

### 2026 `|μ| < 3` (n=31)

| predictor | log-loss | Brier |
|---|---:|---:|
| `p_win_home` | 0.6993 | 0.2527 |
| `Φ(μ/σ)` | **0.6861** | **0.2465** |

**Read:** On the long walkforward, the three predictors are nearly tied; raw MC is microscopically best and calibrated is never better than Φ on these metrics. On 2026 graded, calibrated is clearly worse than Φ.

Mean probability gaps (walkforward non-neutral):

| slice | mean(`p_cal − p_phi`) | mean(`y − p_cal`) | mean(`y − p_raw`) | mean(`y − p_phi`) |
|---|---:|---:|---:|---:|
| home favored by μ (n=3,401) | +0.027 | +0.008 | +0.003 | +0.035 |
| away favored by μ (n=1,230) | −0.028 | +0.028 | +0.023 | +0.001 |

`p_cal − p_phi` flips sign with the favorite: this is **favorite sharpening**, not a one-sided home additive. Overall `mean(p_cal − p_phi) ≈ +0.013` mostly because home-favored games dominate the slate.

2026: `mean(y − p_cal) = −0.085`, `mean(y − p_phi) = −0.050` — both overstate home wins; calibrated more so.

---

## 3. Reliability of `p_win_home` / `p_ml_home` (deciles)

Gap = empirical home-win rate − mean predicted `p`.

### Walkforward — home favored by μ (n=3,648)

| decile (p) | n | p_mean | y_rate | gap |
|---|---:|---:|---:|---:|
| (0.43, 0.55] | 365 | 0.517 | 0.507 | −0.010 |
| (0.55, 0.60] | 365 | 0.572 | 0.532 | −0.041 |
| (0.60, 0.64] | 365 | 0.615 | 0.619 | +0.005 |
| (0.64, 0.68] | 364 | 0.661 | 0.659 | −0.001 |
| (0.68, 0.72] | 365 | 0.699 | 0.688 | −0.011 |
| (0.72, 0.76] | 365 | 0.737 | 0.732 | −0.005 |
| (0.76, 0.80] | 364 | 0.780 | 0.808 | +0.028 |
| (0.80, 0.85] | 365 | 0.826 | 0.811 | −0.015 |
| (0.85, 0.91] | 365 | 0.877 | 0.863 | −0.014 |
| (0.91, 1.00] | 365 | 0.953 | 0.948 | −0.005 |

Mostly well calibrated; mild overconfidence in a couple of mid bins.

### Walkforward — away favored by μ (n=1,295)

| decile (p home) | n | p_mean | y_rate | gap |
|---|---:|---:|---:|---:|
| (0.00, 0.14] | 130 | 0.091 | 0.231 | **+0.140** |
| (0.14, 0.20] | 129 | 0.175 | 0.326 | **+0.151** |
| (0.20, 0.26] | 130 | 0.229 | 0.185 | −0.044 |
| (0.26, 0.31] | 129 | 0.285 | 0.341 | +0.056 |
| (0.31, 0.35] | 130 | 0.328 | 0.400 | +0.072 |
| (0.35, 0.39] | 129 | 0.367 | 0.395 | +0.029 |
| (0.39, 0.42] | 129 | 0.404 | 0.380 | −0.025 |
| (0.42, 0.45] | 130 | 0.435 | 0.408 | −0.027 |
| (0.45, 0.48] | 129 | 0.463 | 0.481 | +0.018 |
| (0.48, 0.54] | 130 | 0.496 | 0.408 | −0.088 |

Strong home underdogs (lowest p bins) win **much** more often than predicted — consistent with μ/p understating home when the road team is favored.

### 2026 — home favored by μ (n=350)

Every decile but the top shows **negative** gap (p too high), e.g. mid bins −0.11 to −0.19. Small-n away-favored table (n=37) is noisy; gaps also mostly negative on home-win rate.

---

## 4. Verdict

**On the 2019–2024 walkforward: μ is biased toward the road; the calibrator is not adding a home lean the outcomes fail to support.**

Evidence:

1. Non-neutral residual mean **+1.35** pts with CI excluding 0 — homes beat μ.
2. Probabilistic scores: raw MC ≥ Φ ≥ calibrated (ties within noise overall); calibrator does **not** improve home-win accuracy vs Φ or raw.
3. `p_cal − Φ` is **favorite sharpening** (±2.7pp on the favored side), not a pure home additive. Overall positive mean is compositional (more home favorites).
4. Reliability pain is concentrated when μ favors the **away** team and home is a big underdog — homes win more than `p` says — i.e. residual **home** strength in outcomes vs the stack, not extra unsupported home juice from calibration.

**On 2026 graded so far: the opposite pattern — μ and especially calibrated `p_win_home` look home-overconfident** (residual −4.1 pts; `mean(y−p) = −8.5pp`; Φ beats calibrated on log-loss/Brier). Treat as early-season / incomplete-n evidence, not a refutation of the walkforward.

**Bottom line for the SOC-1c question:** Long-run OOS says fix **μ / HFA in the margin model** (road-biased μ), not “turn down a home-leaning ML calibrator.” The calibrator’s main effect vs Φ is sharpening favorites; on walkforward that does not buy accuracy. 2026’s home-overconfidence is a live monitoring flag for a separate model task once more of the season grades — still report-only here.
