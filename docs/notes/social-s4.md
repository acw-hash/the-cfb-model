# S4 — ATS calibration gap: Phase 0 discrimination (read-only)

**Date:** 2026-08-25  
**Branch:** `social-s1-s2`  
**Status:** Phase 0 complete — **STOP**. No Phase 1 executed.  
**Authority:** S4 task brief; S3 `docs/notes/social-s3.md`; ADR 0013/0014 labels.

**Forbidden respected:** no Odds API, no R2, no publish, no merge, 2025 lockbox
untouched (`assert_lockbox_excluded` on 2021–2024), no edits under `src/` /
`configs/` / `scripts/calibrate_social_threshold.py`, no refit, no threshold
retune.

**Artifacts:** `docs/notes/_artifacts/social-s4/`  
**Probes:** `scripts/_s4_phase0.py`, `scripts/_s4_p0_clv_settle.py`

**S3 reference (unchanged n):** thr=0.05, weeks 2+, **n=314**, claim 0.641,
realized 0.494, gap **−0.147**, line CLV +0.76.

---

## Verdict (one screen)

| Hypothesis | Status | Evidence |
|---|---|---|
| **H1** grading orientation / pushes | **ELIMINATED** | Hand fixtures agree; inverted hit **0.506** ≈ 1−0.494, not ≈0.64; **0** pushes in the 314 |
| **H1b/H3** claim priced at wrong line | **ELIMINATED** | S3 claim is provider `p_win` at shopped line; recompute gap **−0.147** identical |
| **H2** σ too small | **ELIMINATED** | μ±1.28σ coverage **0.810** vs 0.80 (near-nominal / slight over); top-bin claim 0.678 is not extreme |
| **H3** MC cover path broken in general | **ELIMINATED** (as general defect) | All-games `p_ats_home` gap **+0.0009**; raw MC@close also ~flat |
| **H4** selection | **SURVIVES (primary)** | All-games ATS calibrated; candidate `p_win` gap **−0.147**; residuals cluster under the 7.0 ceiling |
| **H5** genuine ATS overconfidence | **NOT ASSUMED** | Ruled out as a *general* ATS defect by all-games calibration; residual overconfidence is on the **selected ticket** |

**Phase 1 branch selected:** **H4** — all-games ATS fine, candidates not.  
**One-line reason:** §12 + public edge bar select soft Tuesday tickets whose claimed cover probs do not convert; the margin distribution is honest at the close.

---

## P0-1 — Grader orientation and pushes (H1)

**MEASURED**

### 1. Grading expression (verbatim)

File `scripts/calibrate_social_threshold.py` lines **211–220**:

```python
adj = rm + home_line
if abs(adj) < 1e-12:
    pushed = True
    covered = None
elif bet_on == "home":
    covered = adj > 0.0
elif bet_on == "away":
    covered = adj < 0.0
```

### 2. Sign conventions (from producers)

| Input | Observed convention | Source |
|---|---|---|
| `realized_margin` | **home_points − away_points** | Walk-forward week parquet; identity vs scores max abs err **0** on sampled week |
| Shopped line used in grader | `market_line_home` — **home-relative** (home lays negative), same as `spread_cover_probs` | `betting/provider.py` `details["market_line_home"] = best["home_line"]` |
| `market_line` (CLV / display) | **bet-side-relative** (home: `home_line`; away: `−home_line`) — matches `betting.clv.spread_cover_prob` | `provider.py` 765–772 |
| `BetCandidate.side` | **No `side` field.** Orientation is `details["bet_on"] ∈ {home,away}`; `BetCandidate.market == "side"` | `betting/filters.py`, `provider.py` |

### 3. Hand-graded fixtures (n=314)

All available rows: **hand agrees with grader**. Exact push: **NOT_AVAILABLE_IN_314** (n_pushes=0).

| Label | game_id | Teams (A@H) | Score | rm | bet | line / home_line | grader | hand |
|---|---|---|---|---:|---|---|---|---|
| home fav covers | 401282773 | Washington@Michigan | 10–31 | +21 | home | −6 / −6 | cover | cover |
| home fav fails | 401282065 | South Carolina@East Carolina | 20–17 | −3 | home | −2 / −2 | fail | fail |
| away dog covers | 401282193 | WKU@Army | 35–38 | +3 | away | +6.5 / −6.5 | cover | cover |
| away dog fails | 401282187 | North Texas@SMU | 12–35 | +23 | away | +22.5 / −22.5 | fail | fail |
| exact push | — | — | — | — | — | — | **NOT AVAILABLE** | — |
| line near 3/7 | 401300999 | Temple@Akron | 45–24 | −21 | home | +6.5 / +6.5 | fail | fail |
| neutral site | 401331163 | Pitt@Wake Forest | 45–21 | −24 | home | +2.5 / +2.5 | fail | fail |
| CFBD≠Odds home | 401645383 | Navy@Army (CFBD); Odds home=Navy | 31–13 | −18 | away | +6.5 / −6.5 | cover | cover |

**STOP check:** no hand/grader disagreement → continue.

### 4. Inversion positive control

| | hit rate |
|---|---:|
| S3 grader | **0.494** |
| Sign-flipped grader | **0.506** |

**Interpretation (MEASURED):** flipped ≈ 0.506 = 1 − 0.494. A pure sign flip does **not** explain a −0.147 gap. H1 survives only via pushes/partial mis-orientation — and pushes are zero (next).

### 5. Pushes

| Quantity | Value |
|---|---:|
| Exact pushes in 314 | **0** |
| Grader treatment | `covered=None`, `u_pnl=0`, excluded from hit denominator |
| Hit rates: excl / half / loss | **0.494 / 0.494 / 0.494** |

`p_win` is `two_way_side_prob` (push-conditional). Grading excludes pushes. **No push-conditional vs push-inclusive mismatch** on this set (no pushes to mismatch).

### 6. Neutral sites

**n=12** among the 314. Home-anchor agreement CFBD vs Odds:

| game_id | CFBD home | Odds home | agrees |
|---|---|---|---|
| 401331163 | Wake Forest | Wake Forest | yes |
| 401287953 | Oklahoma State | Oklahoma State | yes |
| 401331160 | Northern Illinois | Northern Illinois | yes |
| 401331159 | Utah | Utah | yes |
| 401404141 | Army | Army | yes |
| 401437035 | USC | USC | yes |
| 401404145 | Army | Army | yes |
| 401539475 | Washington | Washington | yes |
| 401539479 | Texas | Texas | yes |
| 401641041 | Sam Houston | Sam Houston | yes |
| 401628373 | Texas A&M | Texas A&M | yes |
| **401645383** | **Army** | **Navy** | **no** |

CFBD≠Odds home count in 314: **1** (`401645383`).

---

## P0-2 — Does the claim price the graded ticket? (H1b/H3)

**MEASURED**

### 1. Claim column

**`p_win`** on the S3 cache / `GradedBet` — from `BetCandidate.p_win` =
`two_way_side_prob(spread_cover_probs(..., home_line, side=bet_on))` at the
**shopped** book line.

Cite: `scripts/calibrate_social_threshold.py` cache write ~L164; producer
`src/ncaa_quant/betting/provider.py` ~L692–714.

**Not** stored walk-forward `p_ats_home`.

### 2. Shopped − CFBD close (diagnostic only)

Claim is not `p_ats_home`, but Δ is reported because ADR 0015 / ticket tension is live:

| Stat | Value |
|---|---:|
| n with both | 314 |
| share Δ ≠ 0 | 0.904 |
| mean \|Δ\| | **8.99** |
| max \|Δ\| | 35.0 |
| share straddle ±3 or ±7 | 0.401 |

Shopped ≈ `spread_asof` (mean \|shopped−asof\|=0.16). Large Δ is **Tuesday→close movement**, not a convention bug.

### 3. Point-in-time

**MEASURED — claim does not embed post-Tuesday close.**  
Provider snapshot ladder requires `event_time ≤ as_of` (Tuesday `week_decision_as_of`).  
CFBD close + `ats_close` calibrator are on the `p_ats_home` path only.  
**PIT STOP does not fire.**

### 4. Recompute at shopped line (no retune)

| | claim | realized | gap |
|---|---:|---:|---:|
| S3 `p_win` | 0.641 | 0.494 | **−0.147** |
| Recomputed MC@shopped | 0.641 | 0.494 | **−0.147** |
| mean \|p_win − recompute\| | 0.0 | | |

**This is the separator:** wrong-line claim pricing is **eliminated**. Gap survives on the correctly priced ticket → not H1b/H3-as-stated.

---

## P0-3 — Probability CLV (§2.7)

### Feasibility (MEASURED)

Same-book two-way **closing** quote in staged `odds_snapshots` at the bet book:

| Season | n | same-book two-way close |
|---:|---:|---:|
| 2021 | 78 | 78 |
| 2022 | 79 | 79 |
| 2023 | 77 | 77 |
| 2024 | 80 | 80 |
| **Total** | **314** | **314** |

S3 cache originally lacked `RecommendationRecord` fields
(`bet_other_american`, `bet_line_source_row_id`, consensus pair, close ids).
Those were **reconstructed from staged snapshots** (`tuesday_0600_et` +
`slot_close`) in `scripts/_s4_p0_clv_settle.py` — no Odds API.

### Settlement (MEASURED — unpooled)

| Block | n | mean | %positive | 95% CI |
|---|---:|---:|---:|---|
| **Headline** `same_book` + probability-valued | **158** | **+0.0044** | 44.9% | [0.0017, 0.0072] |
| `fallback_consensus` | 0 | — | — | — |
| `line_units` (not probability) | 156 | **−0.824** | 46.8% | [−1.641, −0.007] |

Method mix: `same_line` 158, `line_units` 156 (moved lines without alt/model translation).

### `mean_line_shopping_capture` (MEASURED)

**+0.0001** (n=314).  
**ADR 0006:** `implied(best@bet) − implied(consensus@bet)`; **negative** would mean
bought cheaper than consensus (not a skill credit). Near-zero here.

**Do not read S3 +0.76 line CLV as §2.7.** Headline probability CLV is ~0.

---

## P0-4 — ATS-only or distributional? (H2 vs H3 vs site)

**MEASURED** — 2×2 built (`p_ml_home`, `p_ats_home` / candidate `p_win` present).

### Reliability 2×2

| Cell | n | claim | realized | gap | Brier | log-loss |
|---|---:|---:|---:|---:|---:|---:|
| `p_ml_home` × all WF | 3612 | 0.630 | 0.626 | **−0.004** | 0.187 | 0.550 |
| `p_ats_home` × all WF | 3491 | 0.492 | 0.493 | **+0.001** | 0.288 | 0.800 |
| `p_ml_home` × 314 | 314 | 0.602 | 0.599 | **−0.003** | 0.206 | 0.599 |
| candidate claim (`p_win`) × 314 | 314 | 0.641 | 0.494 | **−0.147** | 0.272 | 0.740 |

**Reading (stated):** all-games ATS calibrated, candidates-only ATS claim not → **H4**.  
ML calibrated on both slices → not a general distributional collapse (against H2).

*Instrument note (MEASURED addendum):* on the same 314, home-ATS vs **close** with
`p_ats_home` has gap **−0.017** — still near-flat. The −0.147 is specific to
**bet-side `p_win` at the shopped Tuesday line**, i.e. the selected ticket.

### PIT / interval coverage

| Nominal | kσ | empirical (all scored) | Δ |
|---|---:|---:|---:|
| 50% | 0.6745 | 0.512 | +0.012 |
| 80% | 1.2816 | **0.810** | +0.010 |
| 95% | 1.960 | 0.954 | +0.004 |

z mean/std on all: see artifact `p0_4_reliability.json`.

### H2 vs W9-CQR tension

**MEASURED.** Coverage is **near-nominal / slight over** (σ not too small).  
W9-CQR’s over-coverage also points σ **large**, opposite of H2.  
Top-bin claim ~0.678 is not an extreme probability.  
**H2 does not survive.** Comparability to W9-CQR: same family (Gaussian μ±kσ on walk-forward margins) — not UNRESOLVED.

---

## P0-5 — Selection (H4) and silent filters

**MEASURED**

### Residual deciles (all walk-forward, home-ATS @ close)

**Should hold if skill:** gap flat or improving as `|model−market|` grows.

**Observed:** gap-vs-decile correlation **+0.178** (weak; not a clean skill gradient). Gaps oscillate around 0 without monotonic improvement.

### Accepted-314 residual vs §12 ceiling (7.0)

| min | p25 | median | p75 | p90 | max | share ≥ 6.0 |
|---:|---:|---:|---:|---:|---:|---:|
| 1.91 | 5.30 | **6.06** | 6.50 | 6.81 | 6.99 | **0.522** |

**Band:** accepts cluster in the top point of the `[min_edge, 7)` window — just under
`min_model_market_agreement: 7.0`. Not moved (finding only).

### FilterReason histogram (S3-equivalent replay, zeros included)

Settings: `no_bet_on_qb_unknown=false`, `no_bet_on_stale=false`, `min_edge_sides=0.025`
(same as S3).

| Reason | count |
|---|---:|
| EDGE_TOO_SMALL | 322 |
| STALE_INPUTS | **0** |
| QB_STATUS_UNKNOWN | **0** |
| MODEL_MARKET_DISAGREE | 858 |
| MAX_BETS_PER_WEEK | **0** |
| MAX_WEEKLY_EXPOSURE | 1349 |
| MAX_TEAM_EXPOSURE | **0** |
| NON_POSITIVE_EV | 300 |

**STOP finding — zero rows by name:**

- `STALE_INPUTS` = 0 — S3 disabled `no_bet_on_stale` (by design for historical replay).
- `QB_STATUS_UNKNOWN` = 0 — S3 disabled `no_bet_on_qb_unknown` (same class as prior `check_disabled` defect: gate never armed).
- `MAX_BETS_PER_WEEK` = 0 — weekly exposure binds first (~6 bets before the 10 cap).
- **`MAX_TEAM_EXPOSURE` = 0 across four seasons** — silent-filter signature; never rejected anyone.

### Exposure call path

**SHOULD:** `evaluate_filters` receive live `weekly_exposure_so_far`,
`team_exposure_so_far`, `proposed_stake_fraction`.

**OBSERVED:** S3 calls `apply_bet_filters(cands, betting_config=betting)` at
`calibrate_social_threshold.py:140`. Implementation
`pipelines/predict.py:613–619` **does** pass populated exposure kwargs (not
defaults). `recommended_stake` is deliberately pre-clamp with exposure 0.0; caps
fire inside `evaluate_filters`. Weekly exposure **did** fire (1349).

---

## P0-6 — Live-site conviction tiers

**MEASURED** — from `p_ml_home` → `p_favored` / raw §2.2 tiers (ADR 0015 kept this path).

| Tier | n | mean claimed `p_favored` | realized fav win | gap |
|---|---:|---:|---:|---:|
| toss_up | 446 | 0.525 | 0.558 | +0.033 |
| lean | 969 | 0.641 | 0.589 | **−0.052** |
| clear_lean | 1302 | 0.773 | 0.724 | **−0.049** |
| strong_lean | 895 | 0.916 | 0.913 | −0.003 |

Material (lean+) overall gap: **−0.037**.

**Live-site conviction tiers miscalibrated?**  
**MEASURED — mild yes on lean/clear_lean (~5pp overconfidence); strong_lean calibrated.**  
Not the −0.147 ATS-candidate failure mode. Operator number before Thursday: lean/clear
claims run ~5 points hot; strong_lean is honest on 2021–2024 walk-forward.

---

## Epistemic ledger

### MEASURED

- Grader orientation, inversion, pushes, neutrals, one home-anchor disagree.
- Claim = shopped-line `p_win`; recompute gap −0.147 unchanged.
- §2.7 probability CLV (staged reconstruct): headline +0.0044 on n=158.
- 2×2 reliability; PIT/interval coverage; H2 rejected.
- FilterReason histogram including zeros; residual clustering under 7.0.
- Conviction-tier calibration from `p_ml_home`.

### NOT MEASURED

- 2025 lockbox; forward weeks; totals market.
- Probability CLV with **exact** S3 `week_decision_as_of` ladder (used named
  `tuesday_0600_et` slot as staged proxy — note in artifact).
- Live QB/stale gates (disabled in S3 replay by design).

### UNRESOLVED

- Whether a different §12 residual / shopping rule could yield honest selected
  `p_win` without retuning the model (out of scope for Phase 0; H4 finding).
- Why `MAX_TEAM_EXPOSURE` never fires (team_ids / stake path) — named finding,
  not diagnosed further this session.

---

## Phase 1 branch (do not execute)

| Phase 0 finding | Phase 1 |
|---|---|
| **H4 — all-games ATS fine, candidates not** | **This is a finding, not a fix:** no betting product under this selection rule. Write-up only. Do not invent a replacement rule tonight. |

Eliminated branches (H1 fix grader / H1b src line-fix / H3 simulate audit / H5 retune) are not selected.

---

## Acceptance

| Item | Result |
|---|---|
| Phase 0 only | Yes |
| n=314 preserved | Yes |
| 2025 lockbox | Untouched |
| No src/configs/S3-script edits | Yes |
| Artifacts + probes | `docs/notes/_artifacts/social-s4/`, `scripts/_s4_*.py` |
| `make lint typecheck test` | **green** — ruff ok; mypy 129 files; **999 passed**, 1 deselected |
