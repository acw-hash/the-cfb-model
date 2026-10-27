# MODEL-HFA-6 — Feature drift check + out-of-sample CQR + export guard

**Date:** 2026-10-08  
**Artifacts:** `docs/notes/_artifacts/model-hfa-6/{summary.json,run_hfa6.py}`  
**Code change (only):** `src/ncaa_quant/webapp/export.py` margin-interval source guard + tests in `tests/unit/test_webapp_w9d.py`.

---

## A. Why the non-HFA channel moves −5 in 2026 (report only)

### Reduced Stage-2 features + scaling

Champion signature (16 cols; v4 adds `is_home_site` when `explicit_home_site: true`):

`home_off_epa`, `away_off_epa`, `off_epa_diff`, `rating_diff_off_epa`, `home_def_epa`, `away_def_epa`, `def_epa_diff`, `rating_diff_def_epa`, `home_st_value`, `away_st_value`, `st_value_diff`, `home_pace`, `away_pace`, `pace_diff`, `rating_uncertainty`, `expected_possessions`

| head | centering / scaling |
|---|---|
| **ElasticNet** | training-window median impute → `StandardScaler` on **penalized** cols → sklearn `ElasticNet`. Unpenalized cols (v4 `is_home_site`) stay raw via residual OLS. |
| **LightGBM** | **no** scaler; raw frame into `lgb.Dataset`. |

```271:283:src/ncaa_quant/models/heads/elasticnet.py
                scaler = StandardScaler()
                x_scaled = scaler.fit_transform(x_pen)
                # Unpenalized site term carries the home mean; do not also fit an
                # intercept on the penalized block (would double-count).
                use_intercept = bool(self.fit_intercept) and not unpen
                model = ElasticNet(
                    alpha=self.alpha,
                    l1_ratio=self.l1_ratio,
                    max_iter=self.max_iter,
                    random_state=self.seed,
                    fit_intercept=use_intercept,
                )
                model.fit(x_scaled, y_arr, sample_weight=w)
```

```93:104:src/ncaa_quant/models/heads/margin.py
        dataset = lgb.Dataset(
            x,
            label=y,
            weight=sample_weight,
            feature_name=list(x.columns),
            free_raw_data=False,
        )
        self._booster = lgb.train(
            params,
            dataset,
            num_boost_round=self.train.n_estimators,
        )
```

Champion ENet selected (12): diffs + home/away EPA levels + pace + `rating_uncertainty` (drops ST / `expected_possessions` via null/top-k).

### SMD: 2026 FBS–FBS home vs WF train FBS–FBS home

n_2026=262 (ratings-reconstructed), n_train=3,460 (champion feature bank ∩ v3 headline).

| feature | mean 2026 | SD 2026 | mean train | SD train | SMD | \|SMD\|>0.25 |
|---|---:|---:|---:|---:|---:|:---:|
| **rating_uncertainty** | 0.225 | 0.037 | 0.172 | 0.029 | **+1.60** | **yes** |
| away_off_epa | −0.010 | — | +0.010 | — | −0.17 | no |
| home_off_epa | +0.009 | — | +0.020 | — | −0.09 | no |
| off_epa_diff / rating_diff_off_epa | +0.019 | — | +0.009 | — | +0.06 | no |
| (all other Stage-2 cols) | — | — | — | — | \|SMD\|≤0.07 | no |

Only **`rating_uncertainty`** flags.

### Decompose v4 − v3 Δμ by feature (coefΔ × x)

On 2026 FBS–FBS home: mean Δμ = **−4.61**; HA change = **+0.38**; non-HFA = **−4.99**.

Paired diagnostic ENet refits on the **same** WF FBS–FBS train rows (v3-style intercept vs v4-style `is_home_site` unpenalized):

| contributor | stack-weighted (w_enet·Δβ·x̄) |
|---|---:|
| **is_home_site** | **+1.34** |
| rating_uncertainty | ~0 (β unchanged) |
| rating_diff_def_epa | ~0 |
| constant-term change (centering / unpen intercept) | **−1.34** |

Top 3 by \|coefΔ·x\|: **`is_home_site`**, `rating_uncertainty`, `rating_diff_def_epa`. Net of constant: **≈0** — v3→v4 ENet is a home-edge reparameterization on home sites, not a −5 pt non-HFA shift.

Fixed-coef feature-drift channel (champion β × Δx̄): uncertainty **+2.15** (wrong sign for −5), then `away_off_epa` +0.20, `off_epa_diff` +0.12.

Reconstruction check (champion stack on `team_ratings_2026` feats): pub μ **12.82** · recon **10.94** · v4 **8.21**. Publish sits ~1.9 above recon; v4 ~2.7 below — the −4.6 gap is path/as_of, not mapping coefs.

### Home/away levels vs diffs; league-wide level shift

Features enter as **both** side levels and diffs (`{dim}_diff` + `rating_diff_{off,def}_epa` aliases). Construction in `ProductionFeatureProvider.compute_game_features`.

A league-wide +c on all teams' `off_epa`: diffs cancel; residual ENet effect is `(β_home_off + β_away_off)·c`. Champion raw sum ≈ **−4.65** → stack-weighted ≈ **−2.9·c** — moves μ **toward away**, not home.

### Verdict

**Not** “mean input drift explains the −5.” EPA/pace/diffs are exchangeable; the one flagged SMD (uncertainty) would **raise** μ under fixed coefs. v4 HA redesign is ~+0.4 and nets out in the coefΔ decomposition. Prefer upstream **publish as_of / feature-path** audit over reverting reduced_v4. Do **not** treat v4 as the cause of the 2026 v3 home bias.

---

## B. CQR candidate — real q10/q90, out of sample (no promotion)

**Fix (candidate only):** fit the conformal threshold on the same real `pred_margin_q10`/`q90` used at predict time (not MAD placeholders in `_fit_cqr_layer`).

### Leave-one-season-out (2019, 2021–24)

Calibrate on the other seasons; test on holdout. Aggregate mean coverage / width:

| slice | coverage | mean width | within 80±2pp |
|---|---:|---:|:---:|
| all | **79.6%** | 46.4 | **yes** |
| FBS–FBS home | **80.6%** | 45.7 | **yes** |
| FCS | 73.0% | 50.9 | no |
| neutral | 76.6% | 46.9 | no |
| weeks 1–4 | 75.4% | 48.3 | no |
| weeks 5+ | **81.3%** | 45.6 | **yes** |

Per-holdout thresholds ≈ 1.72–2.69 (vs production implied ~7.75). Early-season / FCS slices miss the ±2pp band under a single global threshold.

### 2026 weeks 1–6 pure holdout (calib ≤2025 = 2019+2021–24 WF)

Threshold **2.14**.

| slice | candidate cov / width | production CQR cov / width |
|---|---|---|
| all | 74.9% / 47.0 | 84.6% / 55.8 |
| FBS–FBS home | **78.9%** / 45.8 | 87.2% / 54.6 |
| FCS | 65.5% / 49.4 | 78.2% / 58.3 |
| neutral (n=6) | 83.3% / 49.0 | 100% / 57.8 |
| weeks 1–4 | 74.9% / 47.4 | 85.5% / 56.2 |
| weeks 5+ | 74.6% / 44.5 | 79.7% / 53.3 |

FBS–FBS home hits 80±2pp; overall / FCS / early weeks undershoot. Still **better-calibrated width** than production. **Not promoted.**

---

## C. Export fallback guard (implemented)

`resolve_margin_interval_bounds` + `assert_margin_interval_source_ok` in `webapp/export.py`, called from `build_game_prediction` before the coherence gate:

- Finite lo/hi from **q05/q95** fallback → publish **fails** unless `margin_interval_nominal == 0.90`.
- **Unknown** source (mismatched lo/hi key families) → fail.
- Null band → ok. CQR / explicit sources unchanged.

Tests: `test_webapp_w9d.py` (`test_q05_q95_fallback_*`, `test_unknown_interval_source_fails_publish`, …).

---

## Bottom line

1. Non-HFA −5 is **not** explained by Stage-2 mean drift or v4 coef change; uncertainty is the only SMD flag and has the wrong sign.  
2. Real-q CQR LOSO hits ~80% on all/FBS/late weeks; FCS and weeks 1–4 need more work; 2026 FBS home OK, overall thin. Candidate only.  
3. Export now **hard-fails** mislabeled q05/q95 fallbacks.
