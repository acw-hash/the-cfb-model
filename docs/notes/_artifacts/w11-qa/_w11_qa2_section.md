## W11-QA-2 — Remaining audit sections (READ-ONLY, post W11-2)

**Date:** 2026-11-12  
**HEAD at run:** `4358b5f`  
**Command:** `uv run python -u scripts/_qa_w11_qa2.py`  
**Artifacts:** `docs/notes/_artifacts/w11-qa/qa2_report.{json,md}`, `qa2_run.out`, `qa2_partial.json`  
**Ratings (C):** production `filter_history` SHA-256 `a66cf5f9c9eede4f0bc02d41ec6c32df80166e3f03f77f127392217b4400ac28` (W11_FCSFIX).  
**Week-10 (D/F/A2b):** Nov 4 publish_history / card lines (old model). Odds credits used: **0**.

### 1. Gate table

| Check | Verdict | Evidence |
|-------|---------|----------|
| C3 | **PASS** | Top off: WVU / Georgia / Miami / ND / Wake / Boise / S.Carolina / OkSt / Arizona / Houston. Top def (higher=better): **Tennessee, Alabama, Iowa, TCU, Oklahoma, Texas, UCLA, Wake, Old Dominion, Utah** — **SEC in def top10 = 4**, bot10 = 0. Bot def: CSU / NIU / EMU / Ball St / MTSU / Akron / MoSt / WKU / UTEP / Purdue. |
| C5 | **PASS*** | NaN/inf off/def = 0; teams unchanged since w1 = 0. Largest w9→w10 abs-sum: WKU 0.234, MTSU 0.206, LSU 0.179, Alabama 0.174, Tulsa 0.173. *Innovation z mean/var w5–w10: **UNVERIFIED** (`innovations.parquet` absent). |
| A1 | **PASS** | Paths: `data/registry/artifacts/v2/{production_ensemble,possessions_artifacts}.pkl`; `data/artifacts/state_space/filter_history.parquet`. SHA: ensemble `21c51341…`, possessions `26c729b1…`, filter `a66cf5f9…`. Champion `task23_fundamental_reduced_v3` v2. Mac `data/registry` for W10-3: **UNVERIFIED** (Mac commits absent here). |
| A2b | **PASS** | Edges at posted lines via Nov4 mu/sigma: Mizzou 18.64%, Ole Miss 14.86%, LSU 13.82%, UConn 13.31%, Auburn 9.05%, TXST 8.01%, **Kentucky 8.26%** (Mac 8.3%; delta=0.037 pp < 0.5). |
| B1 | **PASS** | Games/week 1–10: 99,86,75,71,59,58,62,56,56,63. Dup game_ids 0; completed-without-points 0; neutral_site 11. Card finals: TEX 34–20 MIZZ; UGA 42–7 OM; ALA 31–13 LSU; UNC 38–21 UConn; AUB 26–23 ARK; TXST 42–21 ORST; TENN 34–6 UK. Plays scoreboard cols absent (noted). |
| B2 | **PASS** | Mean snaps w8–10: 182.7 / 179.9 / 179.1. No PBP / <100 snaps: none. Null-EPA share ~0.25 all three. Garbage drop: w8 9.1%, w9 13.2%, w10 12.2% — no outlier flag. |
| B3 | **PASS** | No team_id↔school collisions; no w10 FBS missing from filter_history. |
| B4 | **FAIL** | CFBD home/away match Odds after normalize for all 7. Posted vs staged book line: Mizzou+2.5 OK; OM+3.5 OK; UK+6.5 OK. **Mismatch:** LSU posted -6 vs DK -5.5; UConn +7.5 vs FD +6.5; Auburn -10 vs MGM -10.5; TXST +5.5 vs MGM away +6.5 (home -6.5). |
| D1 | **PASS** | Ensemble non-additive; feature parts listed, do not sum to mu. Recompute at Nov4 as_of under current FCSFIX code within ~0.4 pts of published mu. Missouri: published home mu=+4.33 → American-style **Missouri -4.3** ("ridge has Missouri -4.5" rounded). |
| D2 | **PASS** | w8–w10: vintage `W9A_REVAL`, champion v2 / `production-v0_reduced_v3` / `task23_fundamental_reduced_v3`. w11: same identity + **vintage `W11_FCSFIX`**. Only change w10→w11 = vintage_label. |
| D3 | **PASS** | All 14 card-team rating inputs: latest event ≤ week 9 postgame / bye week 8; all before kickoff; none older than week 8. Publish as_of `2026-11-04T14:03:14Z`. |
| E1 | **PASS*** | Model MAE / cov80 w5–w10: 14.08/0.86; 11.60/0.95; 12.42/0.98; 12.08/0.96; 14.15/0.93; 13.91/0.84. *Closing-consensus MAE / difference: **null** (no attachable close in harness) → close half **UNVERIFIED**. |
| E2 | **UNVERIFIED** | n_slate_rows=0 (no closing-consensus attach for ATS buckets / P4 splits). |
| E3 | **UNVERIFIED** | Ledgers w2,w4–w9 present (7 files); w10 ledger absent. Full W-L-P/CLV tabulation deferred. From prior W11-1 closes (staged): card **1-6-0**; see addendum E3 table (same-book closes). |
| E4 | **UNVERIFIED** | Edge/gap sparse in ledgers; P4 share / mean gap / edge / \|Ridge−close\| not computed. |
| F1 | **FAIL** | Mizzou: mu=4.33 sigma=16.41 line+2.5 p_cover=0.668 edge~18.6%; interval width 53.10 vs N(0,sigma) 80% width 42.06 — **no match**. Kentucky: mu=3.06 sigma=19.42 line+6.5 p_cover=0.572; width 62.66 vs 49.79 — **no match**. sigma source: `publish_history.sigma_margin`. |
| F2 | **PASS** | Gates edge>=4.5% gap<=7 QB STARTER max7. Posted-7 edge/gap all clear. Local probe qb_status_unknown; Mac QB statuses **UNVERIFIED** here. |
| G2b | **FAIL** | R2 `latest/meta.json`: week **11**, published_at `2026-11-12T15:41:33Z`, vintage `W11_FCSFIX`. Homepage (`ridgecfb.com`) "Higher is better" mentions = **0**. Regex flagged `stale` in HTML/JS (likely bundle substring; no maintenance/fixture banner). |
| G3 | **FAIL** | Claims vs NEW w10 ratings (higher=better): see list below. |

### G3 claim list (only)

| Claim | Verdict | Correct under higher=better |
|-------|---------|------------------------------|
| Tennessee's defense 137th of 138, allowing +0.26 EPA/play | **wrong** | Tennessee **1st of 138**, def_epa **+0.269** |
| Texas's D slipped +0.13 → +0.21 EPA/play allowed | **wrong** | w9→w10 def_epa **0.214 → 0.206** (claim's "allowed" framing ≠ def_epa) |
| Auburn's defense +0.20 → +0.09 allowed | **wrong** | w9→w10 def_epa **0.092 → 0.055** |
| UConn's defense negative EPA every week | **correct** | def_epa negative in **10/10** weeks |

### 2. Defects

**(i) Changes forecasts / edges**
- None newly found that rewrite Nov-4 mu/edges: A2b Kentucky parity holds; D1 recompute within ~0.4 pts under FCSFIX code (expected drift vs old pin).
- **B4** posted card lines ≠ staged book lines for LSU/UConn/Auburn/TXST — week-10 edges were computed at **posted** lines (A2b), not at the mismatched staged snapshot lines. Touches: LSU -6 vs -5.5; UConn +7.5 vs +6.5; Auburn -10 vs -10.5; TXST +5.5 vs +6.5.

**(ii) Display / copy only**
- **G3** week-10 tweet/rating-rank claims invert defense ranking language under higher=better (Tennessee claimed 137th; true rank **1**). File context: social blurbs (Mac artifacts absent); measured on production ratings.
- **G2b** homepage HTML lacks visible "Higher is better" defense label (chart component still has copy in `RatingTrajectoryChart.tsx` per earlier C2 — not SSR'd on home).
- **F1** published interval width ≠ 1.28*sigma*2 from `sigma_margin` — intervals from quantile/CQR fields (`export.py` ~735–776 / margin_interval_*), not sigma-derived; copy implying sigma↔interval width is misleading.

**(iii) Process / ops**
- **Broken EPA-gap check** (carry-forward): `docs/notes/_artifacts/w11-1/audit.py:397-402` — `if 10 in rating_weeks: HAS-WEEK10-RATING` treats publish-week synthetic point as folded week-10 game. Same pattern W9-1/W10-1. Labels only; week-10 forecasts were not on stale ratings (addendum C4).
- Closing-consensus attach missing → E1 close MAE / E2 ATS buckets **UNVERIFIED**.
- `innovations.parquet` absent → C5 innovation z **UNVERIFIED**.
- Mac W10-3 registry / sat-card commits still absent → A1 Mac registry **UNVERIFIED**; F2 QB STARTER on-machine **UNVERIFIED**.
- w10 `posted_calls` ledger absent locally (card graded from task lines + W11-1 closes).

### Numbers-only reminder (E)
No edge conclusions from card n=7 or incomplete slate ATS. E3 card closes previously: **1-6-0**.
