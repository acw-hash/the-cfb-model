# TASK ATS-GRADE-DIAG — Impossible snapshot-regime ATS numbers

**Date:** 2026-08-11  
**Status:** DIAGNOSIS COMPLETE — grading-path bug confirmed. **No fix this session.**  
**Sanctioned edits:** this note, `scripts/_ats_grade_diag.py`, `tests/fixtures/ats_grade_diag_24.json`,
`tests/unit/test_ats_grade_diag_fixtures.py`. **READ-ONLY** against `data/`.

**Inputs:** `docs/notes/23-rerun-r1.md`, `docs/notes/5b-patch2.md` (side-semantics),
DESIGN §7.2 item 7 (betting-layer backtest / CLV frictions) + §2.7 CLV/line-translation,
`task23_fundamental_reduced_v1` predictions.

---

## Verdict

Snapshot ATS **39.7% (n=3577)** is not a model finding. It is a **line-resolution
bug** in `resolve_lines_for_games` → `_resolve_from_snapshots`: the resolver takes
`median(line)` over **all** spread sides. Odds rows are **side-relative**
(`outcome.point` for each team), so paired ±S lines collapse to **~0**.

That zero close is written to `spread_close` / `spread_asof` and used for ATS
grading. Separately, `p_ats_home` is computed against **CFBD** closes via
`ProductionStack._lookup_closes`. The dual path + zero close produces
anti-skilled ATS while margin MAE stays sane.

Hand-regrade with CFBD-home-side snapshot closes recovers **~50.5–51.8%** ATS
on the same `pred_margin` / stored `p_ats` — consistent with 2019 CFBD **50.7%**.

---

## STEP 1 — Hand-graded 24-row table

Selection (seeded, diverse weeks): 8×2019 CFBD closes; 8×2021–2024 snapshot
closes; 8× fundamental vs market-aware hard-pick disagreements.

Cover test (home perspective, DESIGN / `ats_home_outcomes`):
`home covers ⇔ realized_margin + home_spread > 0` (push excluded).
Model pick = `p_ats_home ≥ 0.5` (stored). Book close for 2021+ = median of Odds
rows with `side == CFBD home school` at last pre-kick snapshot (not Odds listing
home — neutrals may swap; 5b-patch2).

**Agree = 19 / 24.** All five disagreements are snapshot-regime rows where
`grader_spread_close ≈ 0` but the book CFBD-home close is a real number, flipping
the cover label.

| bucket | season | week | game_id | matchup | score | book home close | pred_μ | pick | hand cover | hand hit | grader spread | grader cover | grader hit | agree |
|---|---:|---:|---:|---|---|---:|---:|:---:|:---:|:---:|---:|:---:|:---:|:---:|
| cfbd_2019 | 2019 | 2 | 401112192 | Rutgers @ Iowa | 0-30 | −17.75 (cfbd) | −21.59 | AWAY | HOME | MISS | −17.75 (cfbd_close) | HOME | MISS | YES |
| cfbd_2019 | 2019 | 9 | 401112110 | Oklahoma @ Kansas State | 41-48 | +23.50 (cfbd) | −8.97 | HOME | HOME | HIT | +23.50 (cfbd_close) | HOME | HIT | YES |
| cfbd_2019 | 2019 | 10 | 401112165 | Northwestern @ Indiana | 3-34 | −8.50 (cfbd) | +13.61 | HOME | HOME | HIT | −8.50 (cfbd_close) | HOME | HIT | YES |
| cfbd_2019 | 2019 | 14 | 401121982 | UL Monroe @ Louisiana | 30-31 | −20.00 (cfbd) | +18.03 | AWAY | AWAY | HIT | −20.00 (cfbd_close) | AWAY | HIT | YES |
| cfbd_2019 | 2019 | 3 | 401114256 | New Hampshire @ FIU | 17-30 | −13.50 (cfbd) | −7.08 | AWAY | AWAY | HIT | −13.50 (cfbd_close) | AWAY | HIT | YES |
| cfbd_2019 | 2019 | 2 | 401112445 | Ohio @ Pittsburgh | 10-20 | −4.00 (cfbd) | −39.15 | AWAY | HOME | MISS | −4.00 (cfbd_close) | HOME | MISS | YES |
| cfbd_2019 | 2019 | 11 | 401114160 | USC @ Arizona State | 31-26 | +4.00 (cfbd) | +8.23 | HOME | AWAY | MISS | +4.00 (cfbd_close) | AWAY | MISS | YES |
| cfbd_2019 | 2019 | 14 | 401112134 | West Virginia @ TCU | 20-17 | −13.50 (cfbd) | +13.75 | HOME | AWAY | MISS | −13.50 (cfbd_close) | AWAY | MISS | YES |
| snapshot_closes | 2021 | 1 | 401331219 | Western Michigan @ Nevada | 52-24 | **+7.00** (odds cfbd-home) | +22.33 | HOME | AWAY | MISS | **0.00** (odds fallback) | AWAY | MISS | YES |
| snapshot_closes | 2021 | 8 | 401309625 | Texas State @ Georgia State | 16-28 | **−7.50** | +11.11 | AWAY | HOME | MISS | **0.00** | HOME | MISS | YES |
| snapshot_closes | 2022 | 1 | 401426330 | Virginia Tech @ Old Dominion | 17-20 | **+7.50** | −2.41 | HOME | HOME | HIT | **0.00** | HOME | HIT | YES |
| snapshot_closes | 2022 | 8 | 401426590 | Rice @ Louisiana Tech | 42-41 | **+1.50** | −2.07 | HOME | HOME | HIT | **0.00** | AWAY | MISS | **NO** |
| snapshot_closes | 2022 | 15 | 401404145 | Navy @ Army | 17-20 | **+2.50** | +2.22 | HOME | HOME | HIT | **0.00** | HOME | HIT | YES |
| snapshot_closes | 2023 | 1 | 401551741 | Arkansas State @ NIU | 19-21 | **−7.50** | +29.87 | HOME | AWAY | MISS | **0.00** | HOME | HIT | **NO** |
| snapshot_closes | 2021 | 1 | 401331241 | LSU @ Kansas State | 20-42 | **−10.00** | +10.78 | AWAY | HOME | MISS | **0.00** | HOME | MISS | YES |
| snapshot_closes | 2021 | 8 | 401287928 | Kansas State @ Texas Tech | 25-24 | **0.00** (pick'em) | +4.18 | HOME | AWAY | MISS | **0.00** | AWAY | MISS | YES |
| fund_mkt_disagree | 2021 | 1 | 401281943 | Rice @ Arkansas | 17-38 | **−19.50** | +24.49 | HOME | HOME | HIT | **0.00** | HOME | HIT | YES |
| fund_mkt_disagree | 2021 | 1 | 401281944 | Akron @ Auburn | 10-60 | **−53.50** | +41.47 | HOME | AWAY | MISS | **0.00** | HOME | HIT | **NO** |
| fund_mkt_disagree | 2022 | 1 | 401403859 | Miami (OH) @ Kentucky | 13-37 | **−23.50** | +16.29 | HOME | HOME | HIT | **0.00** | HOME | HIT | YES |
| fund_mkt_disagree | 2022 | 1 | 401403861 | Memphis @ Miss State | 23-49 | **−27.50** | +29.09 | HOME | AWAY | MISS | **0.00** | HOME | HIT | **NO** |
| fund_mkt_disagree | 2023 | 1 | 401520148 | Nebraska @ Minnesota | 10-13 | **−3.50** | +13.13 | HOME | AWAY | MISS | **0.00** | HOME | HIT | **NO** |
| fund_mkt_disagree | 2023 | 1 | 401520149 | MTSU @ Alabama | 7-56 | **−44.50** | +38.50 | HOME | HOME | HIT | **0.00** | HOME | HIT | YES |
| fund_mkt_disagree | 2024 | 1 | 401636387 | Jackson State @ UL Monroe | 14-30 | **−5.00** | +5.09 | HOME | HOME | HIT | **0.00** | HOME | HIT | YES |
| fund_mkt_disagree | 2024 | 1 | 401677086 | NIU @ Fresno State | 28-20 | **+2.50** | −1.81 | HOME | AWAY | MISS | **0.00** | AWAY | MISS | YES |

Canonical fixture: `tests/fixtures/ats_grade_diag_24.json`.

**Disagreement mechanism (canonical):** Auburn −53.5, won by 50 → **did not cover**
the book number (hand AWAY). Grader at 0 treats “home won” as cover (HOME) →
stored `p_ats` (priced at CFBD −53.5) and hand agree the favorite failed to
cover; grader marks the opposite.

---

## STEP 2 — Convention trace

| Hop | Location | Convention | Home-perspective conversion? |
|---|---|---|---|
| 1. Odds API `outcome.point` | The Odds API payload | **Side-relative**: favorite team name gets negative point (e.g. WMU −7, Nevada +7). | Never — point is attached to `outcome.name`, not to event home/away. |
| 2. `normalize_odds_payload` | `src/ncaa_quant/ingestion/odds_api.py` `normalize_odds_payload` | `line = float(outcome["point"])`; `side = normalize_team_name(name)` for spreads (5b-patch2 NAME-BASED). Event `home_team`/`away_team` are context only. | **No conversion.** Side-relative line stored as-is. |
| 3. `odds_snapshots.line` | staged parquet / `OddsSnapshotsSchema` | One row per (book, market, side, line, …). Both sides present. | Still side-relative. |
| 4. Line ladder (as-of + close) | `walkforward.resolve_lines_for_games` → `_resolve_from_snapshots` L588–596 | `spreads = window.loc[market=="spread","line"]; spread = median(spreads)` — **no `side` filter**. | **Missing.** Should select `side == CFBD home`, then median. Contrast: `odds_api._snap_home_spread` (reconcile) **does** filter home side. |
| 5. CFBD branch | `_resolve_from_cfbd` / CFBD `lines_historical.spread` | CFBD provider spread is **already home-perspective** (negative ⇒ home favored). | N/A — native home convention. |
| 6. ATS grader | `metrics.ats_home_outcomes` / `ablation_basis_metrics` | `home covers ⇔ margin + spread_close > 0`. Docstring: home spread. | Assumes hop 4 already produced home-perspective. |
| 7. MC cover probs | `distribution.simulate.spread_cover_probs` | Same: `adj = margin_draw + spread` (home-centric). | Assumes caller passes home spread. |
| 8. `p_ats_home` assembly | `production_stack.ProductionStack` predict path: `_lookup_closes` then `spread_cover_probs` | **`_lookup_closes` reads CFBD closes only** (home-perspective) — **not** the snapshot ladder. | CFBD path is home-native; **diverges from `spread_close` on the prediction row** for 2021+. |
| 9. Market features | `ProductionStack._resolve_market_lines` → same `resolve_lines_for_games(..., closing=False)` when `market_feature_source=snapshots` | Same buggy all-sides median → `mkt_spread ≈ 0`. | Missing (same bug). A6 (`cfbd_open_close`) uses `_resolve_cfbd_only_line` → home-native CFBD for **features only**. |

**Where home-perspective conversion happens:** it **never does** on the snapshot
ladder. Reconcile already knew the rule (`_snap_home_spread`); the walk-forward
resolver does not call it.

**CLV / line translation (§2.7):** probability CLV requires same-book close
translated to the **ticket line**. That path is orthogonal to ATS@close accuracy,
but any close lookup that reuses `_resolve_from_snapshots` inherits the same
all-sides median bug. Task 23-RERUN-R1 did not compute CLV (`bets.parquet` absent).

---

## STEP 3 — Regime contrast (why 2019 ~50% and snapshots ~40%)

### Empirical split (fundamental REDUCED, headline)

| Slice | ATS | n | `spread_close` abs median | Notes |
|---|---:|---:|---:|---|
| 2019 CFBD | **50.7%** | 743 | 10.5 | Both `p_ats` and grader use CFBD home spreads — consistent. |
| 2021–2024 all | **39.7%** | 3577 | **0.0** | ~86% of rows have `|spread_close| < 0.5`. |
| Snapshot near-zero | **36.7%** | 3096 | 0.0 | Grader ≈ moneyline outcome; `p_ats` still priced at CFBD lines. |
| `cfbd_close_eval` only | **58.7%** | 482 | real | Snapshot miss → CFBD eval fill; instruments align → skill shows. |
| Corrected CFBD-home Odds close + `pred_margin` edge | **50.5%** | 3502 | 12.5 | Same predictions, fixed closes. |
| Corrected closes + stored `p_ats` | **51.8%** | 3497 | 12.5 | `p_ats` was already @ CFBD; close enough to Odds home lines. |

SU accuracy (`p_ml`) on the same years is **66–72%**. Using `pred_margin` sign
against stored near-zero closes also yields ~68%. Stored `p_ats` against those
zeros yields **36.7%** because **`p_ats` ⟂ `pred_margin`** on that slice
(corr ≈ 0.02): probabilities were computed at large CFBD spreads, then graded
as if the line were pick'em.

### Mechanical asymmetry (exactly this bug)

1. Snapshot ladder writes `spread_close ≈ 0` (all-sides median).
2. Grader: home “covers” iff home **wins**.
3. `p_ats_home` uses CFBD home spread S (often −20, −40, …): picks the dog whenever
   μ does not clear |S|.
4. Big favorites usually **win** but often **fail to cover**. Model correctly
   fades the cover → grader marks a miss because the win counts as a cover at 0.
5. 2019 never hits step 1 (CFBD-only ladder) → no asymmetry → ~50%.

### A6 grading ladder — ambiguity resolved

Memo said “CFBD open/close vs snapshots.” Config sets
`market_feature_source: cfbd_open_close`, which switches **feature** construction
only (`_resolve_market_lines`).

**A6 `spread_close` sources (2021–2024):** still snapshot ladder —
`odds_api_snapshot_fallback` 2635, `odds_api_snapshot` 374, `cfbd_close_eval` 460;
median spread **0.0**; **86%** near zero; ATS **36.5%**.

So **36.5% is still graded on buggy snapshot closes**, not on CFBD closes. It
does **not** mean “ATS vs CFBD close = 36.5%.” It means “market-aware μ trained
with CFBD market features, graded with the broken snapshot close.”

---

## STEP 4 — Blast radius

### Contaminated (hypothesis holds)

| Metric / artifact | Why | Regrade vs RE-RUN |
|---|---|---|
| Snapshot-regime **ATS %** (fundamental + all ablations) | Graded on `spread_close≈0` | **Regrade** outcomes with home-side closes helps hard-pick from `pred_margin`; clean `p_ats@close` needs probs at the **same** line → **RE-RUN** MC market step (or offline recompute from stored μ/σ). |
| **ATS log-loss** | Same dual-path | **RE-RUN** (or recompute `p_ats` at corrected close). |
| **A6 ATS 36.5%** | Features CFBD-OK; grading still snapshot-buggy | **Regrade** vs CFBD close for an A6-feature-consistent number; vs corrected Odds close after resolver fix. Full coherence → light re-run of market probs. |
| **A2 ATS** component (38.1% / 41.6%) | Same grader | Same as fundamental ATS. |
| Market-aware **ATS 32.7%** | Grading bug **and** `mkt_spread≈0` features (`spread_asof` 100% near-zero 2021–2024) | **RE-RUN required** — bug entered **features**. |
| Market features in market-aware / non-A6 stacks | `mkt_spread` from buggy ladder | **RE-RUN**. |
| A3 (market off) ATS | Features OK; grading contaminated | Regrade / recompute `p_ats` like fundamental. |
| A1/A4/A5 snapshot ATS | Same as fundamental grading | Regrade / recompute. |
| Any future **CLV** using this close resolver | Same median bug | Fix resolver then settle; not computed in R1. |
| ATS calibration / reliability vs stored `spread_close` | Labels wrong | Rebuild after fix. |

### Untouched

| Metric | Why safe |
|---|---|
| **MAE margin / total** | Score residuals only; no spread. |
| **CRPS margin / total** | Distributional vs scores. |
| **Weekly MAE curve** (W10−W4) | MAE only. |
| **A2 MAE** (+1.60) | MAE only. |
| **OU vs close** (≈52%) | Totals: both sides share the same `line` number; all-sides median of totals is not a sign-collapse. Spot-check: total path not implicated in the ATS collapse. |
| **SU / `p_ml` accuracy** | No spread in the label. |
| Stage-1 ratings / filter history | No lines. |

---

## STEP 5 — Permanent guard spec (design only)

**Name:** `assert_ats_vs_close_plausible` (pipeline gate; sibling of 22B-FIX
`chance_band_mae`).

**Null:** under a fair close and no skill, hard ATS accuracy is Binomial(n, ½).

**Band (derived from n, not a round number):**

\[
\mathrm{SE} = \sqrt{0.5 \cdot 0.5 / n}, \quad
\mathrm{band} = 0.5 \pm z \cdot \mathrm{SE}
\]

Recommend **z = 3** for a hard pipeline fail (same spirit as two-sided chance
bands that refuse to publish impossible numbers). Example: n=3577 →
SE≈0.00836 → band **[47.5%, 52.5%]**. Observed 39.7% is ~12 SE below ½ — must
fail the run.

**Scope:** every backtest / ablation summary that emits ATS vs close, **per
line-source regime** (never pool 2019 CFBD with 2021+ snapshots). Apply to
headline ATS accuracy; optionally to ATS log-loss vs a ½ baseline with an
analogous band.

**On failure:** raise a **pipeline error** (do not publish the rate as a model
finding). Message must cite n, observed rate, band, and `line_source` mix
(including `% |spread_close|<0.5` as a diagnostic hint for this specific bug).

**Non-goals:** this guard does **not** assert skill ≥ 51.5% (§1.6). It only
forbids publishing rates that are impossible under the coin-flip null at the
chosen z. A true 48% season can pass; a 40% on n>3000 cannot.

**Prerequisite for the guard to be meaningful:** `spread_close` must be
home-perspective. Until the resolver fix lands, the guard would correctly
fail every snapshot-regime run — which is the desired behavior.

---

## Ambiguities / follow-ups (out of scope)

1. After fixing `_resolve_from_snapshots`, unify `p_ats` closes with
   `spread_close` (today `_lookup_closes` is CFBD-only even when grading is
   snapshot — instrument mismatch even with correct medians).
2. Totals median across over/under rows should be audited the same way (likely
   OK because over/under share the number).
3. Fix implementation is a **separate task** — not done here.

---

## Artifacts

- `scripts/_ats_grade_diag.py` — read-only diagnosis driver
- `docs/notes/_artifacts/ats_grade_diag/summary.json` — machine summary
- `tests/fixtures/ats_grade_diag_24.json` + `tests/unit/test_ats_grade_diag_fixtures.py`
