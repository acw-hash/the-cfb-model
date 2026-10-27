# MODEL-HFA-2 — Separate home bias from margin scale; neutral-site check

**Date:** 2026-10-08  
**Scope:** Report only. No model, calibrator, or export changes.  
**Artifacts:** `docs/notes/_artifacts/model-hfa-2/summary.json` (generator: `run_hfa2.py`)

Corpora (reported separately):

| corpus | source | non-neutral n |
|---|---|---:|
| WF v2 2019–24 | `task23_fundamental_reduced_v2/full/predictions.parquet` | 4,631 |
| 2026 graded | `results_2026.json` + staged `neutral_site` | 381 |

OLS: `actual_margin = a + b · mu_margin`. Bootstrap 4,000 resamples, seed `20261008`, 95% percentile CIs.  
FCS-opponent = either side not in season’s staged FBS `classification`.

---

## 1. Intercept vs slope

### Walkforward non-neutral

| slice | n | a [95% CI] | b [95% CI] | mean(y−μ) |
|---|---:|---|---|---:|
| all | 4,631 | **+1.62** [+0.94, +2.29] | 0.954 [0.903, 1.008] | +1.35 |
| exclude FCS-opp | 4,054 | **+0.19** [−0.47, +0.83] | **0.859** [0.800, 0.920] | −0.44 |
| FCS-opp only | 577 | **+20.1** [+17.5, +22.9] | 0.605 [0.487, 0.724] | +13.9 |
| \|μ\| < 14 | 3,440 | +0.15 [−0.53, +0.83] | **1.155** [1.056, 1.252] | +0.60 |

HFA-1’s pooled +1.35 “road bias” is almost entirely **FCS blowups**: FBS–FBS intercept is consistent with 0; slope is **compressed** (b≈0.86). Inside \|μ\|<14, slope is slightly **amplified** (b≈1.16) with a≈0.

### 2026 graded non-neutral

| slice | n | a [95% CI] | b [95% CI] | mean(y−μ) |
|---|---:|---|---|---:|
| all | 381 | **−4.48** [−7.36, −1.60] | 1.017 [0.905, 1.127] | −4.11 |
| exclude FCS-opp | 262 | **−4.29** [−7.51, −1.19] | 0.991 [0.813, 1.164] | −4.40 |
| \|μ\| < 14 | 141 | **−4.51** [−7.82, −1.37] | 0.947 [0.621, 1.260] | −4.73 |
| weeks 1–2 | 181 | +1.91 [−3.40, +7.29] | 0.881 [0.696, 1.059] | −1.23 |
| weeks 3–4 | 143 | **−9.17** [−13.28, −5.14] | 1.065 [0.898, 1.224] | −7.87 |
| weeks 5+ | 57 | −5.34 [−11.12, −0.05] | 1.154 [0.834, 1.483] | −3.85 |

### Is 2026’s −4.1 intercept or slope?

**Mainly intercept (home).**  
Identity: `mean(y−μ) = a + (b−1)·mean(μ)`.

- a = **−4.48**
- (b−1)·mean(μ) = (+0.017)·21.5 ≈ **+0.37**
- sum = −4.11 (= observed mean residual)

Slope CI includes 1 on every 2026 slice. The miss is a roughly constant **~4–9 pt home overstatement**, worst in weeks 3–4; weeks 1–2 are inconclusive (a CI crosses 0).

---

## 2. Paired bootstrap: calibrated vs Φ / raw MC

Diff = metric(cal) − metric(other). **Positive ⇒ calibrated worse.**  
2026 raw MC (`p_ml_home_raw`) is **not recoverable** from published `results_2026` or any on-disk 2026 predict parquet (would need a champion re-predict).

### Walkforward

| slice | comparison | log-loss Δ [95% CI] | Brier Δ [95% CI] |
|---|---|---|---|
| overall | cal − Φ | +0.00029 [−0.0024, +0.0030] | +0.00009 [−0.0010, +0.0011] |
| overall | cal − raw | +0.00081 [−0.0002, +0.0018] | +0.00026 [−0.0001, +0.0006] |
| \|μ\|<3 | cal − Φ | +0.0024 [−0.0011, +0.0058] | +0.0012 [−0.0005, +0.0028] |
| \|μ\|<3 | cal − raw | +0.0009 [−0.0009, +0.0026] | +0.0004 [−0.0004, +0.0013] |

All WF paired CIs include 0 → **no significant calibrator harm or help**.

### 2026 graded

| slice | comparison | log-loss Δ [95% CI] | Brier Δ [95% CI] |
|---|---|---|---|
| overall | cal − Φ | **+0.0198 [+0.0055, +0.0348]** | **+0.0047 [+0.0002, +0.0092]** |
| overall | cal − raw | unavailable | unavailable |
| \|μ\|<3 | cal − Φ | +0.0132 [−0.030, +0.059] | +0.0063 [−0.015, +0.029] |

Overall 2026: calibrated is **significantly worse** than Φ. Toss-up slice too small for a clean call.

---

## 3. Neutral sites (WF)

| season_type | n | mean residual | median residual |
|---|---:|---:|---:|
| regular | 125 | **−4.41** | −5.89 |
| postseason | 187 | **−9.29** | −8.91 |

Designated “home” on neutrals underperforms μ by a lot; bowls/playoffs are worse than regular-season neutrals (rivalry / conference championship sites).

### Scoring-time code that drops HFA on neutrals

Module docstring (`state_space.py`):

```27:33:src/ncaa_quant/ratings/state_space.py
For a home/away game (neutral drops HFA)::

    epa_h = off_h − def_a + hfa_global + hfa_dev_h + scoring_env + ε
    ...
    margin = margin_scale · (off_h − def_a − off_a + def_h + hfa) + ε_m
```

Measurement update (HFA columns only when not neutral):

```811:813:src/ncaa_quant/ratings/state_space.py
        if not neutral_site:
            h[2 * d] = 1.0
            h[2 * d + 1] = 1.0
```

```864:866:src/ncaa_quant/ratings/state_space.py
        if not neutral_site:
            h[2 * d] = cfg.margin_scale
            h[2 * d + 1] = cfg.margin_scale
```

Same pattern in `league_state.py` (~L400–402, ~L478–480). So **filter scoring** drops HFA on neutrals. The large negative residuals are therefore not a sign-flip bug; they mean **published μ still expects too much from the designated home** on neutrals (mapping-layer / feature HFA path is the suspect for a later task—not audited as a code change here).

### Spot-check (10 neutrals): orientation OK

All 10 have `actual_margin == home_points − away_points`. Examples:

| game | type | matchup (away @ home) | score | actual | μ |
|---|---|---|---|---:|---:|
| 401520352 | regular | Georgia @ Florida | 43–20 | −23 | −8.2 |
| 401405059 | regular | Nebraska @ Northwestern | 28–31 | +3 | +6.9 |
| 401415623 | regular | Cincinnati @ Miami (OH) | 38–17 | −21 | +9.8 |
| 401282641 | regular | North Carolina @ Georgia Tech | 22–45 | +23 | −0.7 |
| 401256144 | post | Louisiana @ UTSA | 31–24 | −7 | −6.0 |
| 401677105 | post | Duke @ Ole Miss | 20–52 | +32 | +9.2 |
| 401551758 | post | Virginia Tech @ Tulane | 41–20 | −21 | +18.4 |
| 401331219 | post | Western Michigan @ Nevada | 52–24 | −28 | +22.3 |
| 401551469 | post | Western Kentucky @ Old Dominion | 38–35 | −3 | +10.0 |
| 401677103 | post | South Carolina @ Illinois | 17–21 | +4 | +12.9 |

Orientation is consistent. Several postseason rows show μ strongly favoring the designated home while the “away” side won big — consistent with the −9 pt mean residual, not a flipped coordinate.

---

## 4. Verdict (plain language)

| finding | what is biased | size | implied fix (future task) |
|---|---|---|---|
| WF FBS–FBS | **μ scale compressed** (b≈0.86); intercept ≈ 0 | ~14% shrink of μ | Recalibrate margin scale / shrink on FBS–FBS; do **not** add a home intercept from the FCS-inflated pool |
| WF FCS-opp | μ **understates** home blowouts | residual +14 pts; a≈+20 | Separate FCS handling / prior; exclude from HFA diagnostics |
| 2026 graded | **μ intercept / home overconfidence** | a≈−4.5 pts; weeks 3–4 a≈−9 | Early-season HFA or prior too strong; watch as season fills — not a slope bug |
| WF calibrator | no material bias vs Φ or raw | Δ log-loss / Brier CIs cover 0 | Leave calibrator alone on long-run evidence |
| 2026 calibrator | calibrated **worse** than Φ | +0.020 log-loss [+0.006, +0.035] | Symptom of home-overconfident μ pushed further by favorite-sharpening; fix μ first, re-check cal |
| Neutrals | designated-home μ too optimistic | RS −4.4; post −9.3 | Confirm mapping-time HFA is zeroed on `neutral_site`; scoring-time already drops HFA; not an orientation bug |

**Bottom line:**  
- Long-run: HFA-1’s “μ biased toward the road” was **FCS contamination**; FBS–FBS looks like **scale shrink**, not a missing home intercept. Calibrator is fine.  
- 2026: the −4.1 is **almost pure intercept** (home too strong), not slope; calibrator currently hurts vs Φ.  
- Neutrals: orientation correct; HFA is dropped at **scoring** time; residual still says μ overrates the listed home — investigate prediction/mapping path next, still report-only here.
