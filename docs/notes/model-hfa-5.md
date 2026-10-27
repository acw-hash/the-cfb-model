# MODEL-HFA-5 — Verify 2026 re-predict + conformal recalibration candidate

**Date:** 2026-10-08  
**Scope:** Report only. No promotion, registry, or export changes.  
**Artifacts:** `docs/notes/_artifacts/model-hfa-5/{summary.json,run_hfa5.py}`

---

## 1. Reconcile v4 home-site movement (2026 FBS–FBS home)

Paired n=262 graded FBS–FBS home games (weeks 1–6).

| quantity | value |
|---|---:|
| mean μ published v3 | **+12.82** |
| mean μ v4 held-out | **+8.21** |
| mean Δμ = μ(v4)−μ(v3) | **−4.61** |
| home-edge change (HA_v4 − HA_v3) | **+0.38** (4.95 − 4.56) |
| everything else | **−4.99** |
| mean residual v3 | **−4.40** |
| mean residual v4 | **+0.21** |
| OLS a v3 → v4 | **−4.29 → +0.48** |

Decomposition: almost none of the 2026 μ drop is the site-term redesign. Absolute home edge is similar (v3 stack-weighted intercept ≈ +4.56 on *all* games; v4 gated stack flip ≈ +4.95). The −4.6 pt shift is ratings / as_of / stack-path difference between **live publish** and **frozen WF re-predict**.

### Why intercept goes −4.3 → ~0 while WF home-site shift is +0.57

These are different comparisons:

| contrast | corpus | mean Δμ (home) |
|---|---|---:|
| HFA-4b | v4 WF vs v3 WF, 2019–24 home sites | **+0.57** |
| This section | v4 held-out WF vs *published* v3, 2026 FBS–FBS | **−4.61** |

Arithmetic on 2026 FBS–FBS:

`mean_resid_v3 (−4.399) − mean_dmu (−4.609) = +0.210 = mean_resid_v4`

Lowering μ by ~4.6 pts lifts residuals by the same amount, so OLS a moves from −4.29 to ~+0.5. The WF +0.57 is not the input to that identity.

---

## 2. Leakage audit of the 2026 re-predict

### Training window

| | v3 | v4 |
|---|---|---|
| Seasons in fit path | 2019–24 (+2020 continuity) | same + 2026 continuity (mapping frozen) |
| max `n_train_games` end-2024 | 5068 | 5068 |
| `n_train_games` at 2026 week 1 | — | **5069** (±1) |
| 2025 lockbox | excluded | excluded |
| 2026 `retrain_epoch` | — | **20 for all weeks 1–6** (freeze confirmed) |

v4 mapping window ends where v3’s did. No mid-2026 retrain.

### Five sample games (as_of vs Tuesday publish)

| week | matchup | WF as_of | publish as_of | WF ≤ pub? | epoch | n_train |
|---:|---|---|---|---|---:|---:|
| 1 | Marshall @ Penn State | 2026-09-01 10:00Z | 2026-09-01 10:00Z (tue) | yes | 20 | 5069 |
| 2 | Rice @ Notre Dame | 2026-09-08 10:00Z | 2026-09-08 10:00Z (tue) | yes | 20 | 5168 |
| 3 | WKU @ Indiana | 2026-09-15 10:00Z | 2026-09-18 10:00Z (daily; no tue primary) | yes | 20 | 5254 |
| 4 | Delaware @ Virginia | 2026-09-22 10:00Z | 2026-09-23 21:28Z (tue) | yes | 20 | 5329 |
| 5 | MTSU @ Kansas | 2026-09-29 10:00Z | 2026-10-02 12:18Z (tue) | yes | 20 | 5400 |

Inputs vs Tuesday:

- **Ratings / features:** computed at WF `as_of` (Tuesday 06:00 ET). Stage-1 updates only after completed games with `event_time < as_of`.
- **Mapping + CQR:** last fit at offseason retrain before 2026 (epoch 20); calibration seasons ≤2024. 2026 labels never enter fit.
- **Publish as_of:** sometimes later than canonical Tuesday (weeks 3–6). WF is **earlier** → less information, not leakage.

**Verdict: PASS leak-free. No re-run required.** Corrected intercept/MAE/LL = the HFA-4b held-out numbers already reported.

---

## 3. Conformal over-width

### What production CQR is calibrated on

| dimension | production behavior |
|---|---|
| Seasons | Trailing `min(2, n)` distinct seasons in the OOF frame at each retrain |
| Weeks | All weeks in those seasons (no week filter) |
| FCS | **Included** |
| Quantile inputs at **fit** | **Synthetic** `μ ± z_q·MAD` placeholders (`_fit_cqr_layer`) — **not** the LightGBM quantile head |
| Applied at **predict** | Threshold added to **real** `q10`/`q90` |
| Nominal | 0.80 → pair (0.10, 0.90) |

Implied production threshold from widths ≈ **+7.75 pts** per side.  
Candidate thresholds using real quantiles ≈ **+1.0 to +1.3 pts**.

### Residual SD (v4 headline)

| set | n | resid SD |
|---|---:|---:|
| Calib 2023–24 all | 1,829 | 17.14 |
| Calib 2023–24 FBS–FBS home | 1,452 | 16.72 |
| Calib 2023–24 FBS–FBS home weeks 1–6 | 599 | 16.99 |
| Test headline all | 4,265 | 18.09 |
| Test FBS–FBS home | 3,495 | 17.61 |

Calib vs test SD is close; the dominant bug is **MAD-placeholder calibration vs real-quantile application**, not FCS mix alone.

### Candidate: recalibrate on matching distribution @ 0.80 (real q10/q90)

| candidate | n_calib | thr | test | coverage | mean width | within 80±2pp |
|---|---:|---:|---|---:|---:|---|
| trailing_2 all → headline | 1,830 | 1.03 | all | 77.4% | 44.1 | no |
| trailing_2 FBS–FBS home → FBS test | 1,453 | 1.10 | FBS | **78.5%** | 43.5 | **yes** |
| trailing_2 FBS–FBS home w1–6 → same | 600 | 1.29 | FBS w1–6 | **79.0%** | 44.4 | **yes** |
| FBS w1–6 thr → full headline | 600 | 1.29 | all | **78.2%** | 44.6 | **yes** |
| *production CQR (same tests)* | — | ~7.75 | — | **~88–89%** | **~57** | no |

**CRPS:** unchanged by CQR recal (μ/σ unchanged). v4 headline CRPS remains **~9.96** (HFA-4). Interval recal only moves coverage/width.

**Best candidate for the package:** `trailing_2_fbs_fbs_home_w1_6` → **79.0%** coverage, width **44.4** (vs production 89.1% / 57.5 on the same FBS early-season slice).

### Export fallback guard (propose only)

Fail (or null the interval) in `webapp/export.py` when lo/hi resolve from `pred_margin_q05`/`q95` while `margin_interval_nominal` is not `0.90` (especially if it still says `0.80` from `cqr_nominal`). Do not implement here.

---

## 4. Release package (draft only) — `reduced_v4 + CQR recal`

### Gate table

| gate | status |
|---|---|
| Neutral regular intercept CI ∋ 0 | PASS |
| FBS–FBS home intercept CI ∋ 0 | PASS |
| MAE no worse than v3 | PASS |
| CRPS no worse than v3 | PASS |
| Coverage within 1 pp of v3 (prod CQR) | PASS |
| Coverage 80% ±2 pp after CQR recal | **PASS** (candidate `fbs_fbs_home_w1_6`) |
| Promotion / registry / export | **not done** |

### Tier-share change (v4 vs v3 WF, pp)

strong +1.07 · clear +1.02 · lean **−3.88** · toss-up +1.78 · **25.8%** of games change tier.

### About-page changelog (3 sentences)

We replaced a constant home-field intercept that also applied on neutral sites with an explicit home-site term in the mapping layer (reduced_v4). Walk-forward accuracy is unchanged to slightly better, and neutral-site forecasts no longer carry a phantom home edge. Separately, we are recalibrating margin intervals so the stated 80% bands match realized coverage more closely (today they are too wide).

---

## Bottom line

1. 2026 intercept move is explained by μ dropping **4.6 pts** vs publish, not by the WF +0.57 home-site shift.  
2. Re-predict is **leak-free**; no corrected re-run.  
3. Over-coverage is largely **CQR fit/apply mismatch** (MAD placeholders → real quantiles); real-quantile recal on FBS early weeks hits **~79%** at ~44 pt width.  
4. Draft package is ready; **still no promotion**.
