# MODEL-HFA-4b — Finish v4 readout + interval over-coverage

**Date:** 2026-10-08  
**Scope:** Report only. No promotion, no model/export/config changes.  
**Artifacts:** `docs/notes/_artifacts/model-hfa-4b/{summary.json,run_hfa4b.py}`  
**Inputs:** HFA-4 walkforward preds + `summary.json`; published `results_2026.json`

---

## 1. Re-scored gates

Absolute coverage gate replaced with **coverage within 1 pp of v3**.

| gate | result | detail |
|---|---|---|
| Neutral regular intercept CI includes 0 | **PASS** | a=+1.03 [−2.23, +4.32] |
| FBS–FBS home intercept CI includes 0 | **PASS** | a=−0.38 [−1.06, +0.30] |
| MAE no worse than v3 (CI overlap) | **PASS** | 14.24 vs 14.53 |
| CRPS no worse than v3 (CI overlap) | **PASS** | 9.96 vs 10.02 |
| Coverage within 1 pp of v3 | **PASS** | v3 88.10% → v4 88.62% (**+0.52 pp**) |
| Tier-share Δ vs v3 | **stated** (not a hard fail) | lean −3.9 pp; strong/clear/toss-up each ≈ +1–2 pp |

**Hard-gate bundle: PASS** under the revised coverage rule. Still **no promotion** in this task.

*(Prior absolute ±3 pp-of-nominal gate: both v3 and v4 fail at ~+8 pp — a shared conformal issue, not a v4 regression.)*

---

## 2. Missing HFA-4 items

### Item 5 — Tier shares + change rate

| tier | W1A pool | v3 WF | v4 WF | v4−v3 (pp) |
|---|---:|---:|---:|---:|
| strong_lean | 17.3 | 23.7 | 24.8 | +1.07 |
| clear_lean | 32.9 | 35.9 | 37.0 | +1.02 |
| lean | 30.8 | 27.2 | 23.4 | **−3.88** |
| toss_up | 18.9 | 13.1 | 14.9 | +1.78 |
| clear+strong | 50.3 | 59.7 | 61.7 | +2.10 |

| | v3 | v4 |
|---|---|---|
| Worst-season top-two | **66.8% (2024)** | **67.1% (2023)** |

**Tier change rate (paired game_id):** **25.8%** (1,079 / 4,176).  
Largest flows: lean→clear (245), lean→toss-up (222), clear→lean (160), clear→strong (152).

### Item 6 — Held-out 2026 weeks 1–6 (v4 vs published v3)

| slice | published v3 a [CI] | v4 a [CI] | v3 MAE | v4 MAE | v3 LL | v4 LL |
|---|---|---|---:|---:|---:|---:|
| overall | **−4.67 [−7.48, −1.79]** | **+2.56 [+0.13, +4.99]** | 15.14 | 15.12 | 0.460 | **0.413** |
| FBS–FBS home | **−4.29 [−7.48, −1.27]** | +0.65 [−1.78, +3.20] | 14.70 | **13.58** | 0.556 | **0.497** |
| FCS | −5.21 [−33.5, +21.6] | **+21.9 [+13.7, +30.3]** | 15.95 | 19.02 | 0.201 | 0.216 |
| neutral (n=6) | −5.91 [−36.4, +12.3] | −4.94 [−13.9, +0.11] | 18.31 | **6.05** | 1.40 | 0.61 |

Site graded 2026 stays under published v3. v4 held-out is frozen-mapping continuity only.

### μ moves > 1 point (v4 − v3, WF headline pairs)

| | share \|Δμ\| > 1 | mean Δμ |
|---|---:|---:|
| overall | **55.7%** | — |
| home-site | **53.1%** | **+0.57** |
| neutral | **93.1%** | **−4.67** |

Median \|Δμ\| = 1.17. Neutrals move almost everywhere (Enet intercept no longer applies off-site); home-site shifts are smaller on average but still often >1 pt.

---

## 3. Interval over-coverage (~88% vs 80% nominal)

### Source counts

| corpus | CQR (`cqr_lo/hi`) | q05/q95 only | none |
|---|---:|---:|---:|
| WF v3 headline | **4,176** | **0** | 200 |
| WF v4 headline | **4,244** | **0** | 132 |
| Published 2026 graded | **333 stamped `nominal=0.8`** | 0 without nominal | 54 null intervals |

Headline walkforward intervals are **CQR, not the q05/q95 fallback**.

### Coverage by source (bootstrap 95% CI)

| corpus / source | n | empirical | Δ vs 0.80 stamp | mean width |
|---|---:|---:|---:|---:|
| WF v3 CQR | 4,175 | 88.1% [87.1, 89.1] | **+8.1 pp** | 56.8 |
| WF v4 CQR | 4,243 | 88.6% [87.7, 89.6] | **+8.6 pp** | 57.5 |
| Published 2026 (stamped 0.8) | 333 | 82.3% [78.1, 86.2] | +2.3 pp | 61.4 |

Raw q05–q95 mean width on the same WF rows ≈ 54.3–54.4; raw q10–q90 ≈ 42.0. CQR@0.8 (inflate q10/q90) ends up **about as wide as raw 90% quantiles**, which matches ~88% hit rate.

### What sets `margin_interval_nominal`?

Predict path hard-codes CQR nominal 0.8 and stamps it:

```1457:1468:src/ncaa_quant/evaluation/production_stack.py
        if self._cqr is not None and not q_missing:
            with contextlib.suppress(Exception):
                conf = conformalize_intervals(out, self._cqr, nominal=0.8)
                out["cqr_lo"] = conf["cqr_lo"].to_numpy(dtype=float)
                out["cqr_hi"] = conf["cqr_hi"].to_numpy(dtype=float)
                out["cqr_nominal"] = 0.8
                out["cqr_is_missing"] = False
        else:
            out["cqr_lo"] = np.nan
            out["cqr_hi"] = np.nan
            out["cqr_nominal"] = np.nan
            out["cqr_is_missing"] = True
```

CQR@0.8 uses the **q10/q90** pair (not q05/q95):

```28:33:src/ncaa_quant/models/conformal.py
NOMINAL_TO_QUANTILES: Mapping[float, tuple[float, float]] = {
    0.5: (0.25, 0.75),
    0.8: (0.10, 0.90),
    0.95: (0.05, 0.95),
}
```

Export prefers CQR bounds, then falls back to raw q05/q95; nominal only follows `cqr_nominal`:

```735:737:src/ncaa_quant/webapp/export.py
    margin_lo = _optional_float(_field(row, "margin_interval_lo", "cqr_lo", "pred_margin_q05"))
    margin_hi = _optional_float(_field(row, "margin_interval_hi", "cqr_hi", "pred_margin_q95"))
    margin_nominal = _optional_float(_field(row, "margin_interval_nominal", "cqr_nominal"))
```

So the published stamp is the **configured CQR nominal (0.8)**, not a value derived from whichever lo/hi source was chosen. On the WF headline path that is consistent (source = CQR). The fallback path is a latent mislabel risk (90% band with null or stale nominal).

### Verdict

| hypothesis | on WF headline / published-with-0.8 | 
|---|---|
| Labeling mismatch (90% band stamped 0.80) | **No** for observed rows — source is CQR@0.8 |
| Conformal over-coverage | **Yes** — empirical ~88% with mean width ≈ raw q05–q95 |
| Both | Latent export fallback risk only; not the driver of the ~88% figure |

### Proposed fix (do not implement)

1. Stamp `margin_interval_nominal` from the **source actually used** (0.8 for CQR; 0.90 if falling back to q05/q95); never attach a CQR nominal to a quantile fallback.  
2. Re-tune / audit CQR score threshold + ACI online α so walkforward coverage tracks 0.80 (today’s inflation is too aggressive under drift).  
3. Optional: publish raw quantile and conformal bands as separate fields to kill silent fallback.

---

## Bottom line

Under the revised gate (**coverage within 1 pp of v3**), the hard bundle **passes**. Neutrals absorb most of the μ move (93% \|Δμ\|>1; mean −4.7 pts). 2026 held-out still looks better-calibrated on home intercept / LL under frozen v4, but this task does **not** promote. Interval over-coverage is a **conformal-width** problem shared with v3, not a v4 labeling bug on the scored path.
