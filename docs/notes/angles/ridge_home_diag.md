# Ridge home-team diagnosis (discovery only)

Research report. **No production model, config, or feature was changed.**
Holdout 2023–2025 was not opened; `docs/notes/angles/holdout_ledger.jsonl`
does not exist.

- **headline seasons:** 2014, 2015, 2016, 2017, 2018, 2019, 2021, 2022
- **sensitivity:** 2020
- **ridge_residual** = actual margin − Ridge walk-forward `pred_margin`,
  from the named team's side (`task23_fundamental_reduced_v4`).
- **ats_residual** = CFBD close instrument, same side. Odds-API is not
  pooled in. Ridge residuals are null for 2014–2018 warmup seasons.
- Cluster-robust SE is season-week (CR1). Neutral cells use one row per
  game (designated home) so residuals are not doubled.
- **Bye** = `team_rest_days ≥ 13` and not the opener. Bye overwrites the
  last-result label in (d)/(e) so those five cells are disjoint.
  (c) uses last win/loss regardless of rest.

## (a) All non-neutral home teams, and all neutral-site games

| cell | n_ridge | ridge mean | ridge SE | ridge p | n_ats | ats mean | ats SE | ats p |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| non-neutral home | 2043 | -1.675 | 0.661 | 0.0151 | 5662 | -0.473 | 0.184 | 0.0114 |
| neutral (designated home) | 49 | 0.460 | 1.749 | 0.7952 | 171 | 2.110 | 1.029 | 0.0438 |

## (b) Same, by season

2020 is a sensitivity row and is **not** in the headline cells.
Home Ridge residual by season with n>0: 2019 -3.113, 2021 -1.171, 2022 -0.938. Not a flat series — 2019 is the most negative (-3.113), then the miss shrinks (last 2022 -0.938). Home ATS residual: 2014 -1.388, 2015 -0.746, 2016 -0.498, 2017 -0.120, 2018 -0.516, 2019 0.523, 2021 -0.438, 2022 -0.627.

| season | site | n_ridge | ridge mean | ridge SE | ridge p | n_ats | ats mean | ats SE | ats p |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2014 | home | 0 | — | — | — | 699 | -1.388 | 0.555 | 0.0253 |
| 2014 | neutral | 0 | — | — | — | 22 | 1.295 | 2.946 | 0.6717 |
| 2015 | home | 0 | — | — | — | 704 | -0.746 | 0.457 | 0.1267 |
| 2015 | neutral | 0 | — | — | — | 20 | 3.375 | 2.285 | 0.1678 |
| 2016 | home | 0 | — | — | — | 693 | -0.498 | 0.580 | 0.4058 |
| 2016 | neutral | 0 | — | — | — | 26 | 6.875 | 2.170 | 0.0089 |
| 2017 | home | 0 | — | — | — | 715 | -0.120 | 0.709 | 0.8682 |
| 2017 | neutral | 0 | — | — | — | 21 | 0.738 | 1.993 | 0.7220 |
| 2018 | home | 0 | — | — | — | 710 | -0.516 | 0.396 | 0.2146 |
| 2018 | neutral | 0 | — | — | — | 23 | -1.185 | 2.282 | 0.6161 |
| 2019 | home | 616 | -3.113 | 1.901 | 0.1255 | 714 | 0.523 | 0.507 | 0.3203 |
| 2019 | neutral | 10 | 2.669 | 3.409 | 0.4907 | 20 | 6.338 | 2.054 | 0.0273 |
| 2021 | home | 712 | -1.171 | 0.507 | 0.0368 | 712 | -0.438 | 0.399 | 0.2912 |
| 2021 | neutral | 20 | 2.700 | 1.701 | 0.1565 | 20 | -0.050 | 1.750 | 0.9780 |
| 2022 | home | 715 | -0.938 | 0.639 | 0.1661 | 715 | -0.627 | 0.462 | 0.1976 |
| 2022 | neutral | 19 | -3.061 | 2.530 | 0.2572 | 19 | -1.474 | 4.147 | 0.7305 |
| 2020 (sens.) | home | 499 | -3.039 | 0.769 | 0.0013 | 499 | -0.851 | 0.670 | 0.2237 |
| 2020 (sens.) | neutral | 9 | -3.284 | 3.643 | 0.4337 | 9 | -1.778 | 2.660 | 0.5518 |

## (c) All teams after a loss vs after a win (any venue)

| last result | n_ridge | ridge mean | ridge SE | ridge p | n_ats | ats mean | ats SE | ats p |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| after loss | 1825 | -0.289 | 0.308 | 0.3538 | 5022 | -0.357 | 0.155 | 0.0232 |
| after win | 2170 | 0.208 | 0.262 | 0.4298 | 5948 | 0.278 | 0.132 | 0.0371 |

### (d) Home teams after road loss / road win / home loss / home win / bye

| situation | n_ridge | ridge mean | ridge SE | ridge p | n_ats | ats mean | ats SE | ats p |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| road loss | 620 | -2.950 | 0.818 | 0.0009 | 1714 | -0.986 | 0.370 | 0.0090 |
| road win | 481 | -1.879 | 0.958 | 0.0569 | 1338 | 0.134 | 0.426 | 0.7543 |
| home loss | 199 | -1.379 | 1.319 | 0.3025 | 539 | -0.974 | 0.629 | 0.1246 |
| home win | 412 | -0.036 | 1.289 | 0.9778 | 1101 | -1.011 | 0.501 | 0.0461 |
| bye | 211 | -2.988 | 1.406 | 0.0409 | 564 | 0.198 | 0.668 | 0.7676 |

### (e) Away teams after the same five

| situation | n_ridge | ridge mean | ridge SE | ridge p | n_ats | ats mean | ats SE | ats p |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| road loss | 255 | 1.561 | 1.200 | 0.2016 | 712 | 0.171 | 0.588 | 0.7716 |
| road win | 168 | -0.059 | 1.383 | 0.9661 | 470 | 0.048 | 0.617 | 0.9376 |
| home loss | 478 | 2.737 | 0.826 | 0.0020 | 1348 | 0.478 | 0.398 | 0.2326 |
| home win | 805 | 1.456 | 1.138 | 0.2079 | 2208 | 0.863 | 0.290 | 0.0036 |
| bye | 238 | 2.643 | 1.223 | 0.0381 | 557 | 0.319 | 0.599 | 0.5957 |

## 2020 sensitivity (excluded from headline)

| cell | n_ridge | ridge mean | ridge SE | ridge p | n_ats | ats mean | ats SE | ats p |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| home | 499 | -3.039 | 0.769 | 0.0013 | 499 | -0.851 | 0.670 | 0.2237 |
| neutral | 9 | -3.284 | 3.643 | 0.4337 | 9 | -1.778 | 2.660 | 0.5518 |
| after loss | 429 | -0.625 | 0.512 | 0.2429 | 429 | -0.442 | 0.531 | 0.4185 |
| after win | 471 | 0.576 | 0.492 | 0.2614 | 471 | 0.156 | 0.441 | 0.7289 |

### 2020 home situations

| situation | n_ridge | ridge mean | ridge SE | ridge p | n_ats | ats mean | ats SE | ats p |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| road loss | 125 | -4.954 | 1.199 | 0.0012 | 125 | -2.338 | 1.234 | 0.0806 |
| road win | 106 | -4.372 | 1.438 | 0.0088 | 106 | -1.750 | 1.049 | 0.1174 |
| home loss | 43 | -3.289 | 3.013 | 0.2948 | 43 | -1.558 | 2.983 | 0.6103 |
| home win | 65 | -2.516 | 2.005 | 0.2302 | 65 | -1.565 | 1.977 | 0.4416 |
| bye | 97 | 0.075 | 2.019 | 0.9708 | 97 | 2.345 | 1.831 | 0.2225 |

### 2020 away situations

| situation | n_ridge | ridge mean | ridge SE | ridge p | n_ats | ats mean | ats SE | ats p |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| road loss | 56 | 3.444 | 2.721 | 0.2278 | 56 | 1.906 | 2.363 | 0.4343 |
| road win | 41 | 4.712 | 1.873 | 0.0271 | 41 | 1.476 | 2.090 | 0.4937 |
| home loss | 105 | 3.585 | 1.454 | 0.0283 | 105 | 0.769 | 1.432 | 0.6004 |
| home win | 143 | 1.940 | 1.483 | 0.2133 | 143 | 0.010 | 1.429 | 0.9943 |
| bye | 99 | 3.190 | 1.565 | 0.0623 | 99 | 0.371 | 1.193 | 0.7607 |

## STEP 2 — Which explanation the data support

Two candidate explanations (both can be true):

1. **Ridge's home-field advantage is too large.** (a) is negative
   across the board and roughly the same in every cell of (d).
2. **Ridge's ratings drop too slowly after losses.** (c) is negative
   after losses for home *and* away teams, and (d)/(e) after-loss
   cells are much worse than after-win cells.

- **(1) HFA too large:** supported. Non-neutral home ridge residual
  mean = -1.675 (ATS -0.473).
  Neutral-site ridge mean = 0.460.
  Range of (d) home situation ridge means = 2.952
  (roughly uniform vs |home mean|? no).
- **(2) Sticky ratings after losses:** not supported. All-team after-loss
  ridge mean = -0.289; after-win =
  0.208; difference (loss − win) =
  -0.498 (negative ⇒ losers underperform
  Ridge more than winners). Home after-loss vs after-win =
  -2.164 vs -0.957.
  Away after-loss vs after-win = 2.149 vs
  0.698.

**Primarily (1), with a home-side road-loss / bye overlay; not (2).**

Non-neutral homes miss Ridge by -1.675 pts vs the market ATS
residual -0.473; Ridge overstates home by 1.202 pts
relative to the close. Neutrals are 0.460 (not negative), so
leftover intercept-HFA on neutral sites is not the story. (d) is
**not** flat: home-after-win is near 0, home-after-road-loss and
home-after-bye are about −3. That is an HFA × situation
interaction, not a constant site-term miss.

(2) fails the away-side test: after-loss away residual 2.149
is *better* than after-win 0.698. If ratings stayed too high
after losses, both home and away after-loss cells would be negative.
All-team (c) loss−win is only -0.498 pts (home after-loss
-2.164 vs after-win -0.957). Magnitude: the home bias
(-1.675) is about 3× the after-loss gap (-0.498).

## STEP 3 — Where Ridge's HFA is set, vs the close

Read-only. No mapping-layer or Stage-1 parameter was changed.

### Config / artifact

- **Walk-forward predictions used here:**
  `data/backtests/task23_fundamental_reduced_v4/full/predictions.parquet`
  (`configs/angles.yaml` `sources.ridge_predictions`). Home-perspective
  `pred_margin`; flipped to the named team's side for `ridge_residual`.
- **Mapping-layer HFA (v4, as documented in `docs/notes/model-hfa-4.md`):**
  gated `is_home_site` = 0 if `neutral_site` else 1. Stacked home edge
  **+4.95 pts** (LGBM +4.62, unpenalized ElasticNet +5.46, NNLS weights
  0.612 / 0.388) from the final mapping fit before 2026. It is a **site
  term, not a per-team term**. Retrain weeks 5 and 10 mean the fitted
  coefficient can move slightly across folds; it is not a per-season
  schedule in config.
- **That wiring is not in the current source tree.**
  `WalkForwardConfig` has no `explicit_home_site` field;
  `ElasticNetMuHead` has no `unpenalized_features`;
  `configs/ablations/task23_fundamental_full_reduced_v4.yaml` is absent.
  The v4 **prediction artifact** still carries the gated HFA. This
  diagnosis reads the artifact; it does not re-fit.
- **Stage-1 EPA HFA** (`configs/angles.yaml` `sources.hfa_epa_per_play`:
  **0.025 EPA/play**, constant, non-neutral only) is used only for H14
  luck's expected EPA margin. It is **not** added on top of
  `pred_margin`. Filter history does not persist HFA state.

### Empirical home margin over the close (discovery headline)

Non-neutral home teams, CFBD close. Positive `mean_ats_residual` means
homes outperformed the close (the close understated HFA).

| n | mean actual home margin | mean close expected (`−spread`) | mean ATS residual | n_ridge | mean Ridge `pred_margin` | mean Ridge residual |
|---:|---:|---:|---:|---:|---:|---:|
| 5662 | 4.186 | 4.660 | -0.473 | 2043 | 5.848 | -1.675 |

By season (home non-neutral):

| season | n | actual | close expected | ATS residual | n_ridge | Ridge pred | Ridge residual |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2014 | 699 | 3.878 | 5.266 | -1.388 | 0 | — | — |
| 2015 | 704 | 4.089 | 4.835 | -0.746 | 0 | — | — |
| 2016 | 693 | 4.241 | 4.739 | -0.498 | 0 | — | — |
| 2017 | 715 | 4.431 | 4.551 | -0.120 | 0 | — | — |
| 2018 | 710 | 4.369 | 4.885 | -0.516 | 0 | — | — |
| 2019 | 714 | 4.597 | 4.074 | 0.523 | 616 | 7.826 | -3.113 |
| 2021 | 712 | 3.678 | 4.116 | -0.438 | 712 | 4.849 | -1.171 |
| 2022 | 715 | 4.200 | 4.827 | -0.627 | 715 | 5.138 | -0.938 |
| 2020 (sens.) | 499 | 2.132 | 2.983 | -0.851 | 499 | 5.171 | -3.039 |

### What the failing HFA4 tests assert, and whether they relate

`tests/unit/test_model_hfa4.py` (pre-existing failures; not touched):

1. `test_explicit_home_site_feature_gated` — `WalkForwardConfig(
   explicit_home_site=True)` should emit `is_home_site` = 1 on
   non-neutral, 0 on neutral, and omit the column when the flag is
   off. **Fails** because `WalkForwardConfig` has no such field
   (`AttributeError`).
2. `test_enet_unpenalized_home_site_not_shrunk` — ElasticNet with
   `force_include_features` / `unpenalized_features = ('is_home_site',)`
   should recover a planted +3.5 home coefficient under a heavy
   penalty. **Fails** because `ElasticNetMuHead` does not take
   those constructor args.
3. `test_v4_config_wiring` — `walkforward_config_from_mapping`
   should pass `explicit_home_site`, `elasticnet_fit_intercept`,
   and `freeze_mapping_from_season` through. **Fails**; those keys
   are not on `WalkForwardConfig`.

HFA4 was the attempt to **move** intercept HFA onto a gated site
term (~+4.95 stacked) so neutrals are not given home edge. The
walk-forward readout in `docs/notes/model-hfa-4.md` mixed 2019–2024
(including holdout) and is **not** recomputed here. Relation to
this diagnosis: the v4 artifact we score **is** that gated-HFA
model. If (a) is negative on homes, the +4.95 site term (or the
ratings it sits on) is too large on discovery; if after-loss cells
are worse on both sides, the problem is Stage-1 stickiness, which
HFA4's site term was never designed to fix. The failing unit tests
mean the current checkout cannot *re-run* v4 from source; they do
not change the residuals in the committed parquet.

## Guardrails

- Discovery 2014–2022 only; 2020 sensitivity; **2023–2025 unread.**
- H22r / H25 `retired_unopened`; holdout ledger empty.

