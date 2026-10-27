# MODEL-HFA-3 — Where does the extra home edge enter?

**Date:** 2026-10-08  
**Scope:** Report only. No model, calibrator, config, or export changes.  
**Artifacts:** `docs/notes/_artifacts/model-hfa-3/{summary.json,hfa_weeks.json,run_hfa3.py}`

---

## 1. Model identity by 2026 publish (weeks 1–6)

Every publish in `data/webapp/publish_history/2026_w{1..6}.jsonl` carries the same identity:

| field | value |
|---|---|
| `champion_version` | **2** |
| `model_version` | **`production-v0_reduced_v3`** |
| `run_id` | **`task23_fundamental_reduced_v3`** |
| `vintage_label` | `W9A_REVAL` |

Including the last week-2 publish (`2026-09-10T13:34:17Z` daily_refresh) and the week-3 publish (`2026-09-18T14:22:50Z` daily_refresh): **nothing changed** in model/champion/run_id.

**Confirmed:** production is **reduced_v3** (registry champion v2). HFA-1/HFA-2 walkforward diagnostics used **`task23_fundamental_reduced_v2`** / `production-v0_reduced_v2` (registry archived v1). The week 2→3 residual jump is **not** a champion swap.

---

## 2. Decompose `mu_margin` (Stage-1 HFA vs Stage-2 home vs rest)

### Architecture fact (critical)

Champion **reduced** feature signature (`artifacts/v2/feature_signature.json`) is ratings-only:

`home_off_epa`, `away_off_epa`, diffs, pace, `rating_uncertainty`, `expected_possessions` — **no `hfa_global`, no `hfa_dev`, no `neutral_site`.**

Stage-1 HFA is used in the **filter measurement** (`state_space.py`) when updating ratings, but it is **not** a predict-time input to the reduced mapping layer. Home edge in μ must therefore come from Stage-2 learning on home-labeled margins.

### Stage-2 smoking gun — ElasticNet intercept

Champion v3 (`production_ensemble.pkl`):

| piece | value |
|---|---:|
| ElasticNet `intercept_` | **+7.405 pts** |
| NNLS stack weights (LGBM, Enet) | 0.384, **0.616** |
| Stack intercept | 0.0 |
| **Enet intercept × stack weight** | **+4.563 pts** |

Same structure on archived v2 champion: intercept +7.645 × weight 0.548 ≈ **+4.19 pts**.

Code path (sklearn default `fit_intercept=True`):

```176:182:src/ncaa_quant/models/heads/elasticnet.py
            model = ElasticNet(
                alpha=self.alpha,
                l1_ratio=self.l1_ratio,
                max_iter=self.max_iter,
                random_state=self.seed,
            )
            model.fit(x_scaled, y, sample_weight=sample_weight)
```

### Swap-implied home advantage `HA = ½(μ + μ_swap)`

| sample | n home / neut | mean HA home | mean HA neut | Enet intercept contrib |
|---|---:|---:|---:|---:|
| Oracle week-5 `predict_features` (v3) | 56 / — | **3.60** | — | 4.56 |
| 2024 WF games in v2 feature bank | 20 / 0 | **5.74** | — | 4.19 |
| 2026 graded (ratings-rebuilt features)* | 20 / 6 | **4.02** | **4.91** | 4.56 |

\*2026 features rebuilt from published `team_ratings_2026` (st=0, possessions=nan) — approximate for attribution; MAE vs published μ is large, so treat HA as order-of-magnitude. The **intercept term does not depend on that rebuild**.

**Stage-1 column in the tables:** `margin_scale × hfa_prior_mean = 80 × 0.025 = 2.0` pts (0 on neutrals) — the prior if HFA were applied at predict time. It is **not** currently added to μ under reduced features.

**Mean home contribution summary (production v3):**

| bucket | FBS–FBS home (est.) | Neutral (est.) |
|---|---:|---:|
| Stage-1 HFA in μ | **0** (not a feature) | **0** |
| Stage-1 HFA prior if wired | ~2.0 | 0 |
| Stage-2 Enet intercept (stack-weighted) | **~4.6** | **~4.6** (same — no neutral gate) |
| Stage-2 total HA (swap) | ~3.6–5.7 | ~4.9 (approx) |

---

## 3. `hfa_global` by 2026 week (table)

`hfa_history` is **not** persisted in production `filter_history.parquet`. Diagnostic: 2026-only `run_filter` from staged advanced_box (starts from prior; no pre-2026 history — within-season path only).

| week | n games | hfa_global last (EPA) | ×80 → pts | mean EPA in week |
|---:|---:|---:|---:|---:|
| 1 | 99 | 0.0575 | 4.60 | 0.0438 |
| 2 | 86 | 0.0759 | 6.08 | 0.0651 |
| 3 | 75 | 0.0710 | 5.68 | 0.0749 |
| 4 | 71 | 0.0566 | 4.53 | 0.0606 |
| 5 | 59 | 0.0583 | 4.66 | 0.0587 |

**Week 2→3:** hfa_global **fell** slightly (−0.0049 EPA ≈ **−0.39 pts**). No upward jump.  
**`hfa_dev`:** not logged in `hfa_history` (only `hfa_global` + `scoring_env` per game) — cannot tabulate mean `hfa_dev` from persisted outputs.

---

## 4. 2024 reduced_v2 comparison

On 20 non-neutral 2024 games present in the v2 champion feature bank: swap HA **+5.74**, Enet intercept contrib **+4.19**. Same mechanism as v3; slightly smaller stack weight on Enet.

---

## 5. Verdict

**What is biased:** Stage-2 **ElasticNet home intercept** in the reduced mapping layer (`models/heads/elasticnet.py` → sklearn `ElasticNet.fit`), stacked at weight ~0.62 into published `mu_margin`. Not a week-2→3 champion change; not a Stage-1 `hfa_global` spike.

**Size:**
- FBS–FBS home: **~4.6 pts** from the intercept alone (swap HA ~3.6–5.7 depending on sample).
- Neutrals: **same ~4.6 pts** because `neutral_site` is absent from reduced features — designated home still gets the intercept (matches HFA-2’s large negative neutral residuals).

**Stage-1 role:** HFA is correctly dropped on neutrals at **scoring** time, but that state is **not** the channel that writes home edge into reduced μ. Wiring Stage-1 HFA into predict without removing the Enet intercept would risk double-counting.

**Proposed fix (do not implement here):**
1. Fit margin ElasticNet with **`fit_intercept=False`** (or subtract/store intercept and gate it), **and/or**
2. Add **`neutral_site`** (and optionally explicit Stage-1 `hfa_points`) to the reduced feature set so neutrals do not inherit home intercept; if adding Stage-1 HFA at predict, zero it when `neutral_site` and remove the learned intercept.
3. Walkforward re-run: `task23_fundamental_reduced_v4` (same REDUCED_PER_ADR_0013 scope, 2019–2024), grade FBS–FBS residual intercept `a` and neutral residual, then promote only if gates pass vs incumbent v3.

**Not the fix:** recalibrating ML probabilities alone (HFA-1/2 already showed calibrator is secondary).
