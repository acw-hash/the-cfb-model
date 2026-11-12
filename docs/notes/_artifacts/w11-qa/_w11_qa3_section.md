## W11-QA-3 — Pricing calibration + week-11 card probe

**Date:** 2026-11-12  
**HEAD before:** `b277a8b`  
**Export gate:** `export False` (verified).  
**Commands:** `uv run python -u scripts/_qa_w11_qa3_calib.py`; then (PASS) odds stage + `uv run python -u scripts/_s6_w11_card.py`.  
**Artifacts:** `docs/notes/_artifacts/w11-qa/qa3_part_a.{json,md,out}`; `docs/notes/_artifacts/social-s6-w11-card/`.

### Reclassify — B4 (notes only)

B4 (posted w10 lines vs Nov-4 staged snapshot) is **(iii) process**, not a pricing defect. Mac Sat Nov 7 W10-4 fresh check matched LSU DK -6, UConn FD +7.5, Auburn MGM -10, TXST MGM +5.5 at the card books. Only the Nov-4 snapshot is on this machine; Mac commits not pushed.

---

### Part A — pricing sigma vs realized error (CARD GATE)

| Check | Verdict | Evidence |
|-------|---------|----------|
| A1 | **PASS** | Per-game `sigma_margin` on prediction/publish row (LightGBMSigmaHead). Card path: `build_candidates_from_odds` reads `sigma_margin` (`provider.py:575-576`), samples joint draws (`:665-677`), `P(cover)=two_way_side_prob(spread_cover_probs(...))` (`:692-698`). Card uses `n_draws=20_000` (`_s6_w10_card.py:778`). Week-11 FBS–FBS n=61: median **17.70**, IQR **1.67** (p25 16.91, p75 18.57). |
| A2 | **PASS** | Graded 2026 w2–w10 FBS–FBS with published pre-kickoff mu: **n=504**. SD(actual−mu)=**16.49**; robust SD=1.4826×MAD=**16.29**. By week SD ~14.5–18.5. By \|mu\|: 0–7 n=174 SD=16.54; 7–14 n=175 SD=15.51; 14+ n=155 SD=17.51. |
| A3 | **PASS** | sigma_price = median sigma on graded sample = **19.23**. R_plain=19.23/16.49=**1.166**; R_robust=**1.181**. Using w11 median: R=**1.074**. Rule: FAIL if R_plain < 0.90 → **PASS**. |
| A4 | **PASS** (info) | n=441 priced at latest pre-kickoff regulated-book line (MC 20k). Buckets: 50–55 n=115 pred=0.529 real=0.513; 55–60 n=94 pred=0.576 real=0.457; 60–65 n=98 pred=0.624 real=0.500; 65+ n=134 pred=0.739 real=0.493. |
| A5 | **UNVERIFIED** | Skipped: CFBD `/lines` is season_week grain; w5–w10 would be 6 calls (>5 credit cap). `lines_historical` has no 2026 rows here. |
| **CARD_GATE** | **PASS** | R_plain >= 0.90 → continue to Part B. |

---

### Part B — Week-11 card probe (NO PUBLISH, NO QB)

**Decisions (fixed):** Sat Nov 14 ET only; HFA NONE; stake 0.014; max 7; weekly cap 10%; gates edge>=4.5% and \|Ridge−market\|<=7; regulated books only (espnbet display "theScore Bet").

#### B1 — Odds pull

- `uv run python -u docs/notes/_artifacts/social-s6-w11-card/_w11_allbooks_stage.py`
- markets=spreads, regions=us,us2, american; Saturday week-11 events only.
- Credits used: **2** (remaining 342). Hard-stop 10 OK.
- Raw: `odds_allbooks_20261112T162342Z.json`. Staged **1708** rows with per-book `last_update` as `event_time`.
- Drops (stale>6h / one-sided): **0**. Books dropped: none.

#### B2 — Card script

- Copied `scripts/_s6_w10_card.py` → `scripts/_s6_w11_card.py`.
- Diff: artifact dir `social-s6-w11-card`; publish_history `2026_w11.jsonl`; HFA_MODE `"NONE"`; SITE_URL `https://www.ridgecfb.com`; Saturday Nov 14 ET filter; replies_w11.md.
- Production path: `build_candidates_from_odds` → `BetCandidate`, seed 42+game_id.

#### B3 — Top 25 non-QB-gate candidates (by edge)

| rank | game_id | kickoff ET | side | book | line | price | Ridge | gap | edge | noon |
|-----:|--------:|------------|------|------|-----:|------:|------:|----:|-----:|:----:|
| 1 | 401864537 | Sat 11:00 PM ET | Hawai'i | espnbet | 6.5 | +100 | -0.10 | 6.60 | 0.1726 | |
| 2 | 401871100 | Sat 7:00 PM ET | South Alabama | hardrockbet | -3.5 | +110 | -8.73 | 5.23 | 0.1536 | |
| 3 | 401869928 | Sat 6:00 PM ET | Marshall | hardrockbet | 1.5 | -105 | -4.71 | 6.21 | 0.1534 | |
| 4 | 401858520 | Sat 12:00 PM ET | Maryland | fanduel | 7.5 | +100 | 1.82 | 5.68 | 0.1435 | Y |
| 5 | 401856861 | Sat 5:30 PM ET | West Virginia | betrivers | -3.0 | +100 | -8.15 | 5.15 | 0.1391 | |
| 6 | 401856887 | Sat 4:15 PM ET | Oklahoma | betmgm | 1.0 | -112 | -5.51 | 6.51 | 0.1357 | |
| 7 | 401856862 | Sat 2:00 PM ET | TCU | betrivers | -10.5 | -109 | -16.24 | 5.74 | 0.1350 | |
| 8 | 401856863 | Sat 9:30 PM ET | Oklahoma State | draftkings | -3.5 | -105 | -9.24 | 5.74 | 0.1335 | |
| 9 | 401862826 | Sat 3:30 PM ET | Tulsa | betrivers | -5.5 | -110 | -12.47 | 6.97 | 0.1327 | |
| 10 | 401856858 | Sat 3:30 PM ET | Baylor | hardrockbet | 7.5 | -110 | 1.35 | 6.15 | 0.1300 | |
| 11 | 401862829 | Sat 1:00 PM ET | Temple | hardrockbet | -2.5 | -105 | -7.08 | 4.58 | 0.1276 | |
| 12 | 401871061 | Sat 3:30 PM ET | Liberty | fanduel | -21.5 | +100 | -26.40 | 4.90 | 0.1260 | |
| 13 | 401862827 | Sat 9:00 PM ET | North Texas | betrivers | -2.5 | -109 | -7.53 | 5.03 | 0.1217 | |
| 14 | 401856864 | Sat 7:30 PM ET | Arizona | espnbet | 2.5 | +100 | -1.49 | 3.99 | 0.1194 | |
| 15 | 401864539 | Sat 7:30 PM ET | New Mexico | espnbet | -14.5 | +100 | -18.50 | 4.00 | 0.1159 | |
| 16 | 401856859 | Sat 12:00 PM ET | Iowa State | fanduel | -3.5 | +110 | -6.00 | 2.50 | 0.1127 | Y |
| 17 | 401870752 | Sat 3:30 PM ET | Western Kentucky | draftkings | 7.5 | -112 | 2.27 | 5.23 | 0.1110 | |
| 18 | 401860915 | Sat 9:30 PM ET | Utah State | betrivers | -3.0 | -107 | -7.44 | 4.44 | 0.1094 | |
| 19 | 401856884 | Sat 6:15 PM ET | Kentucky | espnbet | -3.5 | +100 | -7.72 | 4.22 | 0.1080 | |
| 20 | 401871099 | Sat 5:00 PM ET | Louisiana Tech | espnbet | -17.5 | -105 | -21.86 | 4.36 | 0.1075 | |
| 21 | 401856886 | Sat 12:00 PM ET | Auburn | hardrockbet | -2.5 | -110 | -7.59 | 5.09 | 0.1071 | Y |
| 22 | 401858295 | Sat 3:30 PM ET | Virginia | fanduel | -10.5 | +100 | -14.34 | 3.84 | 0.1065 | |
| 23 | 401870796 | Sat 3:00 PM ET | Missouri State | espnbet | -1.5 | +100 | -4.62 | 3.12 | 0.1016 | |
| 24 | 401860914 | Sat 6:00 PM ET | Boise State | draftkings | -14.5 | -110 | -19.07 | 4.57 | 0.0949 | |
| 25 | 401871060 | Sat 3:00 PM ET | Delaware | ballybet | -4.0 | -113 | -8.60 | 4.60 | 0.0929 | |

Slate: 56 Saturday games; pre-QB survivors: 36. Noon-flagged in top 25: Maryland, Iowa State, Auburn.

#### B4 — QB-check list (top 20)

```
401864537 | North Dakota State @ Hawai'i (Sat 11:00 PM ET)
401871100 | Troy @ South Alabama (Sat 7:00 PM ET)
401869928 | App State @ Marshall (Sat 6:00 PM ET)
401858520 | Wisconsin @ Maryland (Sat 12:00 PM ET)
401856861 | Kansas @ West Virginia (Sat 5:30 PM ET)
401856887 | Ole Miss @ Oklahoma (Sat 4:15 PM ET)
401856862 | Kansas State @ TCU (Sat 2:00 PM ET)
401856863 | Texas Tech @ Oklahoma State (Sat 9:30 PM ET)
401862826 | Florida Atlantic @ Tulsa (Sat 3:30 PM ET)
401856858 | Baylor @ BYU (Sat 3:30 PM ET)
401862829 | UAB @ Temple (Sat 1:00 PM ET)
401871061 | Middle Tennessee @ Liberty (Sat 3:30 PM ET)
401862827 | North Texas @ UTSA (Sat 9:00 PM ET)
401856864 | Utah @ Arizona (Sat 7:30 PM ET)
401864539 | UNLV @ New Mexico (Sat 7:30 PM ET)
401856859 | Cincinnati @ Iowa State (Sat 12:00 PM ET)
401870752 | Jacksonville State @ Western Kentucky (Sat 3:30 PM ET)
401860915 | Utah State @ San Diego State (Sat 9:30 PM ET)
401856884 | Florida @ Kentucky (Sat 6:15 PM ET)
401871099 | Southern Miss @ Louisiana Tech (Sat 5:00 PM ET)
```
