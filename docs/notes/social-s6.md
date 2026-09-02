# S6 — Consolidated finding, S3 correction, public statement draft

**Date:** 2026-08-25  
**Branch:** `social-s1-s2`  
**Status:** Phase 1 complete — documentation only. Nothing posted, published, or merged.  
**Authority:** S3 `docs/notes/social-s3.md`; S4 `docs/notes/social-s4.md`; S5
`docs/notes/social-s5.md`; Phase 0 report (operator-approved 2026-08-25).

**Forbidden respected:** no Odds API, no R2, no publish, no merge, 2025 lockbox
untouched in S3–S5 (`assert_lockbox_excluded` on 2021–2024), no edits under
`src/` or `configs/`, no re-run of S3/S4/S5 analyses.

**Artifacts:** `docs/notes/_artifacts/social-s6/statement_draft.md`

---

## Finding (one paragraph)

Across three read-only investigations (S3 calibration replay, S4 ATS gap
diagnosis, S5 discrimination test), the same population of **314** accepted
side tickets (2021–2024, weeks 2+, thr=0.05) shows **sub-coin-flip**
realization (**49.4%** MEASURED, S3/S4/S5) against overconfident claimed cover
probs (**64.1%** mean, gap **−0.147** MEASURED, S4). S5 shows **no per-game ATS
discrimination** to select on: candidate `p_win` AUC **0.493** [0.429, 0.556],
BSS **−0.090**, top bin **0.508** vs **0.524** break-even (all MEASURED, S5).
The accept loop already picks the highest claimed edges (top-*k* overlap **1.0**,
MEASURED, S5) — selection was optimal and still produced nothing. **H4 and H5
collapse to one finding:** there is no ATS signal in the ticket claims, and no
selection rule fixes it. This confirms and specifies the site’s existing **NOT
CURRENTLY FIT TO BET** verdict; it does not reverse it.

Three **separate** background facts (do not merge):

1. **Pooled margin encompassing (D6):** joint b2 **0.211**, 95% CI **[0.090,
   0.332]**, `edge_declared=true` (MEASURED, D6) — stack μ carries **material
   pooled incremental information** beyond the closing line in the margin
   regression. This is **not** the same estimand as per-game ATS cover
   discrimination on selected tickets.
2. **Season stability (D6 stop rule):** pre-registered stop fired on the
   **stability branch** only — **2** seasons with reliable positive b2 (2021,
   2025), need **≥3** (MEASURED, D6). `ci_entirely_below_plus_0.05=false`. The
   rule says stop pursuing a **fundamental-model betting edge**; it does **not**
   say μ is uninformative in the pooled regression.
3. **Selected-ticket ATS (S5):** no discrimination (AUC ≈ 0.5, BSS ≤ 0) on the
   314 — **no betting card** from this selection path (MEASURED, S5).

---

## Pre-registered stop rule (verbatim)

From `src/ncaa_quant/evaluation/d6_eval.py` lines 58–62 (`D5_STOP_RULE_VERBATIM`):

> if after the powered sample the joint b2 CI lies entirely below +0.05 (no
> material positive weight on stack μ) or fewer than 3 seasons show reliable
> positive b2, stop pursuing a fundamental-model betting edge and treat the
> market-aware / residual stack as the betting workhorse.

**Thresholds** (`configs/eval/encompassing.yaml`): `substantial_b2=0.10`,
`stability_min_seasons_positive=3`, `min_games_per_season=400`.

**D6 run verdict** (`docs/notes/D6.md`, `d6_results.json`): **missed**
(stability branch). b2=**0.211**, CI **[0.090, 0.332]**, reliable-positive
seasons=**2** (2021, 2025).

---

## D6 and the 2025 lockbox — asymmetry vs S3–S5

| Question | Answer |
|---|---|
| Did D6 n=5083 include 2025? | **Yes.** 2025 contributes **934** games (MEASURED, D6 coverage table). |
| Does stability count 2 depend on 2025? | **Yes.** Reliable-positive seasons listed: **2021**, **2025** (MEASURED, `d6_results.json` `stability.seasons_with_reliable_positive_b2`). Without 2025 the count would be **1**, not 2. |
| Did S3–S5 include 2025? | **No.** Seasons **2021–2024** only; `assert_lockbox_excluded` (MEASURED, S3/S4/S5 notes). |
| What authorized removing 2025 from the config template? | **Not an ADR.** `configs/eval/encompassing.yaml` comment dated **2026-08-07** cites **DESIGN §7.2 item 9** (lockbox) and states the D6 run that included 2025 is archived history, not a template. `docs/lockbox_access.md` records lockbox rules; D7’s 2025 read is logged there. |
| Label | **UNRESOLVED** — whether citing D6 pooled b2 alongside S3–S5 ticket results without re-running D6 on `[2019, 2021–2024]` leaves a season-composition mismatch. Do not treat D6 and S3–S5 as one homogeneous sample. |

**Do not say** stack μ carries no information beyond the close. **Do say** pooled
incremental information exists but failed the pre-registered **stability** gate,
and selected tickets still show **no ATS discrimination**.

**Market-relative forecast note (D6, separate from card finding):** stack MAE
**13.76** vs market MAE **12.15** (MEASURED, D6); optimal combination w=**0.15**,
ΔMAE 95% CI **[−0.127, 0.040]** covers 0 (MEASURED, D6). “Credible” point
machinery on `/results` is an **absolute** claim (MAE/CRPS curve, in-season
learning) — **not** “better than the market.”

---

## Hypotheses eliminated (and what eliminated each)

| Hypothesis | Status | Eliminated by |
|---|---|---|
| H1 grading orientation / pushes | **ELIMINATED** | S4 P0-1: hand fixtures agree; 0 pushes; inversion ≈ 1−hit |
| H1b/H3 wrong-line claim pricing | **ELIMINATED** | S4 P0-2: recompute at shopped line, gap unchanged **−0.147** |
| H2 σ too small | **ELIMINATED** | S4 P0-4: μ±1.28σ coverage **0.810** vs 0.80 nominal |
| H3 general MC cover path broken | **ELIMINATED** | S4 P0-4: all-games `p_ats_home` gap **+0.001** |
| H4 mis-ordered / suboptimal selection | **ELIMINATED** | S5 P0-4: edge-sorted accept loop, overlap **1.0** |
| H5 per-game ATS discrimination exists | **ELIMINATED** | S5 P0-2: AUC **0.493**, BSS **−0.090**, flat bins |
| S3 “positive CLV + sub-coin-flip” anomaly | **CORRECTED** | S5 P0-1: same formula; CFBD all-314 **+0.76** vs Odds moved **−0.824** |
| H4 vs H5 as separate card fixes | **COLLAPSED** | S5: one finding — nothing to select on |

---

## Epistemic ledger

### MEASURED (with source)

| Quantity | Value | Source | Label |
|---|---:|---|---|
| Selected tickets n | 314 | S3/S4/S5 | MEASURED |
| ATS hit (thr=0.05, wk 2+) | 0.494 | S3 grid | MEASURED |
| Mean claimed `p_win` | 0.641 | S3/S4 | MEASURED |
| Calibration gap | −0.147 | S4 P0-2 | MEASURED |
| Candidate `p_win` AUC | 0.493 [0.429, 0.556] | S5 P0-2 | MEASURED |
| Candidate BSS | −0.090 | S5 P0-2 | MEASURED |
| Top bin realized | 0.508 vs 0.524 BE | S5 P0-2 | MEASURED |
| All-games `p_ats_home` AUC | 0.504 | S5 P0-2 | MEASURED |
| `p_ml_home` AUC (instrument) | 0.725 | S5 P0-2 | MEASURED |
| Accept overlap | 1.0 | S5 P0-4 | MEASURED |
| Odds moved-line mean CLV | −0.824 | S4/S5 | MEASURED |
| Headline prob CLV (same_line n=158) | +0.0044 | S4 P0-3 | MEASURED |
| `line_shopping_capture` mean | +0.0001 | S4/S5 | MEASURED |
| Conviction tier gaps (lean / clear / strong) | −0.052 / −0.049 / −0.003 | S4 P0-6 | MEASURED |
| D6 joint b2 | 0.211 [0.090, 0.332] | D6 | MEASURED |
| D6 stack vs market MAE | 13.76 vs 12.15 | D6 | MEASURED |
| Site ATS snapshots | 48.9% [47.5%, 50.5%] | 23-reval / track_record | MEASURED |

### NOT MEASURED

- Market-relative **forecast skill** as a single promoted claim (CRPS vs
  de-vigged market: **NOT COMPUTED**, 23-readout §2).
- 2025 lockbox in S3–S5 replay; 2026 live; totals public bar; live QB/stale
  gates (disabled in S3 replay).
- Forward performance of any kind.
- Exact live @RidgeCFB **pinned post verbatim** (see below).

### UNRESOLVED

- **D6 vs S3–S5 season composition** (2025 in D6, excluded in S3–S5) — see
  table above.
- **Pinned thread verbatim** — not archived in repo; live fetch blocked (403).
  Operator task brief (S6 spec) states a weekly Best Bets commitment first due
  **Tuesday 2026-09-01**; internal cadence in `docs/social/RIDGE_X_PLAYBOOK.md`
  §2 is not a substitute for the live pinned post text.
- **`line_shopping_capture` vs ADR 0006** — net **+0.0001**, not systematically
  negative (S4/S5); expectation mismatch, not closed.
- **3.0u stake-cap concentration 81.4%** — named in S4, out of scope, not
  diagnosed further.

---

## Open findings (S4/S5 named, not closed)

1. **`line_shopping_capture`** nets **+0.0001** (MEASURED, S4/S5) against ADR
   0006’s expectation of systematically negative capture when best price differs
   from consensus (**57%** of rows differ). Net shopping value is inert, not
   negative.
2. **3.0u stake-cap concentration at 81.4%** — still out of scope; not closed
   this session.

---

## S3 correction

Appended to `docs/notes/social-s3.md` (original text unchanged).

---

## Public statement

Draft only: `docs/notes/_artifacts/social-s6/statement_draft.md` with trace
table. **Not posted.**

---

## ADR decision

**No new ADR.** This task consolidates measured findings already recorded in
S3–S5 notes and D6; it appends an S3 correction and drafts a statement. No
code, schema, or pre-registered rule threshold changed.

---

## Acceptance

| Item | Result |
|---|---|
| Phase 0 amendments applied | Yes |
| social-s6.md | Yes |
| S3 correction appended | Yes |
| statement_draft.md + trace | Yes |
| No src/config edits | Yes |
| `make lint typecheck test` | **green** — ruff ok; mypy 129 files; **999 passed**, 1 deselected |
