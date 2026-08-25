# S5 — Is there per-game ATS discrimination at all? (Phase 0)

**Date:** 2026-08-25  
**Branch:** `social-s1-s2`  
**Status:** Phase 0 complete — **STOP**. No Phase 1 executed.  
**Authority:** S5 task brief; S4 `docs/notes/social-s4.md`; S3 `docs/notes/social-s3.md`.

**Forbidden respected:** no Odds API, no R2, no publish, no merge, 2025 lockbox
untouched (`assert_lockbox_excluded` on 2021–2024), no edits under `src/` /
`configs/` / `scripts/calibrate_social_threshold.py` / S4 probes, no refit, no
threshold retune, no new selection rule.

**Artifacts:** `docs/notes/_artifacts/social-s5/`  
**Probes:** `scripts/_s5_common.py`, `scripts/_s5_p0_*.py`  
**S4 rows:** `p0_graded_314.parquet` — **n=314**, keys match S3/S4 row-for-row
(`max_abs_p_win_delta=0`, `covered_mismatch=0`).

**Note on filename:** This file previously held the bet-candidate provider S5
notes (still in git history on this branch). This investigation reuses
`social-s5.md` as the next diagnostic after S4. Provider notes also remain in
`docs/notes/social-s5-w0.md` / `social-s5-w0-fix.md`.

---

## One-sentence reading (P0-2 item 7)

**AUC ~ 0.5 and BSS <= 0 → no per-game ATS discrimination exists; H4 and H5
are the same finding and no selection rule fixes it.**

Candidate `p_win` AUC on the 314 = **0.493** (95% CI [0.429, 0.556]); ML AUC on
the same fixtures = **0.725** (instrument OK). Brier skill vs base-rate on the
314 = **−0.090**.

---

## Verdict (one screen)

| Item | Label | Observed |
|---|---|---|
| Per-game ATS discrimination on the 314 | **MEASURED — none** | AUC 0.493; Spearman −0.012; BSS −0.090; bins flat |
| All-games `p_ats_home` discrimination | **MEASURED — none** | AUC 0.504; Spearman +0.008; BSS −0.151 (n=3491) |
| Instrument (ML on same 314) | **MEASURED — works** | `p_ml_home` AUC 0.725 |
| S3 +0.76 vs S4 −0.824 line CLV | **MEASURED — not a sign bug** | Same formula; different close + population |
| Odds-book line move on tickets | **MEASURED — against** | Moved n=156 mean line_units **−0.824** |
| Headline prob CLV vs vig | **MEASURED — short** | +0.0044 against **0.0238** needed (~5.4× short) |
| Accept-loop selection | **MEASURED — edge-sorted** | Overlap with top-k = **1.0** on 10 weeks |
| Line shopping | **MEASURED — net inert** | Capture **+0.0001**; prices differ 57% of time |
| `MAX_TEAM_EXPOSURE=0` | **MEASURED — not empty ids** | `len(team_ids)=2` on all 314 |

**Phase 1 branch named (not executed):** H4 and H5 collapse into one finding —
write-up / plain-language public statement only; **no replacement selection
rule**; the card does not ship. Separately: correct S3's +0.76 framing (CLV/ATS
anomaly dissolves once Odds-book movement is the instrument).

---

## P0-1 — Reconcile the line-CLV sign

**MEASURED**

### 1. Expressions (verbatim)

**S3** — `scripts/calibrate_social_threshold.py` lines **239–241**:

```python
close_side = close_h if bet_on == "home" else -close_h
line_clv = float(market_line - close_side)
```

**`line_units_clv`** — `src/ncaa_quant/betting/clv.py` lines **335–349**
(spread branch):

```python
return float(bet_line) - float(close_line)
```

### 2. Sign conventions

| Expression | Positive means | Whose side | Source |
|---|---|---|---|
| S3 `line_clv` | Bet-side line higher than close bet-side line (better number) | Ticket bet side | GradedBet comment; `_grade_row` |
| `line_units_clv` | Same: `bet_line − close_line`; "positive = we hold value" | Recommendation bet side | docstring on `line_units_clv` |

### 3. Populations — 2×2 means

|  | all 314 (CFBD `spread_close`) | Odds moved-line n=156 |
|---|---:|---:|
| S3 expression | **+0.760** | **−0.824** |
| `line_units_clv` | **+0.760** | **−0.824** |

Expressions are **algebraically identical** on a fixed bet-side pair. The S3
(+0.76) vs S4 (−0.824) disagreement is **population + close source**, not sign:
CFBD close on all 314 vs Odds same-book `slot_close` on moved rows only.

### 4. Synthetic positive control

| Case | S3 | `line_units_clv` | Expect |
|---|---:|---:|---:|
| home toward (+1) | +1.0 | +1.0 | +1.0 |
| home against (−1) | −1.0 | −1.0 | −1.0 |
| away toward (+1) | +1.0 | +1.0 | +1.0 |
| away against (−1) | −1.0 | −1.0 | −1.0 |

**All eight match. No STOP.**

### 5. Hand-check six real rows (CFBD close)

| game_id | bet | market_line | close_home | S3 | `line_units` | hand | class |
|---|---|---:|---:|---:|---:|---:|---|
| 401282626 | home | … | … | +0.5 | +0.5 | +0.5 | toward |
| 401309912 | home | … | … | −0.5 | −0.5 | −0.5 | against |
| 401309612 | home | … | … | 0.0 | 0.0 | 0.0 | unmoved |
| 401524057 | away | … | … | +0.25 | +0.25 | +0.25 | toward |
| 401426370 | away | … | … | −0.5 | −0.5 | −0.5 | against |
| 401282187 | away | … | … | 0.0 | 0.0 | 0.0 | unmoved |

Full numbers in `p0_1_clv.json` → `hand_checks`. All agree.

### 6. Which figure is correct for the tickets?

**MEASURED.** For ticket CLV, the Odds same-book figures are the ones that
matter: moved-line mean **−0.824** points, and same_line probability CLV
**+0.0044** (S4). S3's **+0.76 vs CFBD close** is a different instrument.

**Plain statement:** against the book that priced these tickets, the market
moved ~0.8 points *against* the bet on moved lines. A **49.4%** realization is
the ordinary consequence of buying a stale number. There is no
"positive line CLV + sub-coin-flip" anomaly left to explain — that framing
was an artifact of reading CFBD close as if it were the bet's book.

---

## P0-2 — Discrimination (decides the card)

**MEASURED**

**SHOULD hold if there is anything to select on:** realized cover rises
monotonically with claim, and the top bin sits above −110 break-even
(**0.5238**).

### 1. Five equal-count bins on the 314 (`p_win`)

| bin | n | mean claim | realized | Wilson 95% | unit PnL |
|---:|---:|---:|---:|---|---:|
| 1 | 62 | 0.607 | 0.516 | [0.394, 0.636] | +0.9 |
| 2 | 63 | 0.630 | 0.429 | [0.314, 0.551] | −32.1 |
| 3 | 63 | 0.642 | 0.587 | [0.464, 0.700] | +24.6 |
| 4 | 63 | 0.652 | 0.429 | [0.314, 0.551] | −33.1 |
| 5 | 63 | 0.672 | 0.508 | [0.388, 0.627] | −4.0 |

Binomial SE ≈ **0.063**/bin. Top vs bottom Wilson CIs **overlap**. Rates are
**not** monotonic. Top bin **0.508 < 0.524**. Pattern **not distinguishable
from flat**.

### 2–3. AUC and Spearman on the 314

| Metric | Point | 95% CI |
|---|---:|---|
| AUC (`p_win` vs cover) | **0.493** | [0.429, 0.556] |
| Spearman ρ | **−0.012** | [−0.124, 0.098] |

### 4. All walk-forward `p_ats_home` vs home cover (n=3491)

| Metric | Point | 95% CI |
|---|---:|---|
| AUC | **0.504** | (boot in artifact) |
| Spearman ρ | **+0.008** | (boot in artifact) |

### 5. Brier Skill Score vs base-rate constant

| Slice | n | Brier model | Brier baseline | BSS |
|---|---:|---:|---:|---:|
| `p_ats_home` all-games | 3491 | 0.288 | 0.250 | **−0.151** |
| `p_ml_home` all-games | 3612 | 0.187 | 0.234 | **+0.201** |
| `p_ml_home` on 314 | 314 | 0.206 | 0.240 | **+0.143** |
| candidate `p_win` on 314 | 314 | 0.272 | 0.250 | **−0.090** |

Negative BSS = worse than a constant equal to the base rate.

### 6. Instrument control

| | AUC on the 314 |
|---|---:|
| ATS claim (`p_win` vs bet cover) | **0.493** |
| ML (`p_ml_home` vs home win) | **0.725** [0.665, 0.779] |

ATS near 0.5, ML well above → **measurement works; ATS result is real.**
No STOP on instrument.

### 7. Reading

**AUC ~ 0.5 and BSS <= 0 → no per-game ATS discrimination exists; H4 and H5
are the same finding and no selection rule fixes it.**

---

## P0-3 — Is the headline CLV block a non-random half?

**MEASURED**

| Half | n | claim | realized | gap | unit PnL |
|---|---:|---:|---:|---:|---:|
| `same_line` (headline) | 158 | 0.639 | 0.500 | **−0.139** | −15.2 |
| moved (`line_units`) | 156 | 0.643 | 0.487 | **−0.156** | −28.6 |

Halves are similar on calibration gap; the moved half loses more bankroll.
Headline probability CLV describes only the **158** unmoved rows.

**Vig line:** +0.0044 against **0.0238** needed to clear −110 from a 0.50
close — short by factor **~5.4×**. The −43.7u needs no bug: the headline edge
is an order of magnitude too small to overcome the juice.

---

## P0-4 — Does the rule select, or merely qualify?

**MEASURED**

### Iteration order

Call site: `scripts/calibrate_social_threshold.py:140` →
`apply_bet_filters(...)`.  
Implementation `src/ncaa_quant/pipelines/predict.py:590`:

```python
ordered = sorted(candidates, key=lambda c: float(c.edge), reverse=True)
```

**Yes — sorted by edge descending before filtering.** Public select also sorts
(`calibrate_social_threshold.py:269`).

### Every replayed week (55 weeks with tickets in the 314)

Weekly exposure bound before public qualifiers exhausted on **51/55** weeks.
Typical shape: ~20–35 public qualifiers, ~6 accepts, exposure rejects the rest.

### Overlap test (10 weeks × 4 seasons)

| season | week | k | overlap | qualifiers | exposure bound |
|---:|---:|---:|---:|---:|---|
| 2021 | 3 | 6 | 6 | 23 | yes |
| 2021 | 8 | 6 | 6 | 32 | yes |
| 2021 | 12 | 6 | 6 | 28 | yes |
| 2022 | 4 | 6 | 6 | 24 | yes |
| 2022 | 10 | 6 | 6 | 35 | yes |
| 2023 | 5 | 6 | 6 | 22 | yes |
| 2023 | 11 | 6 | 6 | 33 | yes |
| 2024 | 3 | 6 | 6 | 20 | yes |
| 2024 | 7 | 6 | 6 | 29 | yes |
| 2024 | 12 | 6 | 6 | 31 | yes |

Mean overlap fraction = **1.0**. The 314 are the top-k-by-edge among
qualifiers, not an arbitrary arrival-order sample. H4 is not "barely selects"
— it selects the highest claimed edges, and those edges do not discriminate
covers (P0-2).

---

## P0-5 — Is shopping inert?

**MEASURED**

**SHOULD (ADR 0006):** `line_shopping_capture` systematically **negative**;
best price often differs from consensus.

### `n_books_available` at Tuesday (by season)

| season | n | min | median | max | share &lt; 2 |
|---:|---:|---:|---:|---:|---:|
| 2021 | 78 | 2 | 4 | 4 | 0.000 |
| 2022 | 79 | 2 | 4 | 4 | 0.000 |
| 2023 | 77 | 1 | 4 | 4 | 0.013 |
| 2024 | 80 | 1 | 4 | 4 | 0.013 |
| **all** | 314 | 1 | 4 | 4 | **0.006** |

### Best vs consensus

| Quantity | Value |
|---|---:|
| Share best-side price ≠ consensus-side price | **0.567** |
| Mean `line_shopping_capture` | **+0.0001** (matches S4) |

**Observed vs should:** books are available (median 4), and the captured price
differs from consensus more than half the time — but net capture is ~0, not
systematically negative. Provider selects by **max edge** across book×side,
not by cheapest price on a fixed ticket. Net shopping value is inert in the
ADR 0006 sense even though raw prices often differ. Same *class* as a silent
mechanism: the skill channel shopping is supposed to open is not producing
negative capture.

---

## P0-6 — `MAX_TEAM_EXPOSURE = 0`

**MEASURED**

Of the 314 accepted candidates, **0** have `len(team_ids)==0`
(distribution: **{2: 314}**). `provider.py` does populate `team_ids` from
`(home_id, away_id)`; `evaluate_filters` does loop `for tid in
candidate.team_ids`. An empty tuple would make the filter structurally dead,
but that is **not** what we have here. Zero `MAX_TEAM_EXPOSURE` rejects are
therefore not from empty ids — weekly exposure binds first on most weeks
(P0-4), and same-team collisions at 0.015 stake vs 0.05 team cap are rare
inside a six-bet week.

---

## Epistemic ledger

### MEASURED

- Line-CLV algebra identical; S3 +0.76 vs S4 −0.824 is close/population.
- Odds-book moved lines average −0.82 against the ticket.
- No ATS discrimination on the 314 or all-games; ML instrument works.
- Headline CLV half vs moved half; vig shortfall 5.4×.
- Accept loop edge-sorted; overlap 1.0; exposure binds.
- Shopping capture ~0 despite multi-book availability.
- `team_ids` length 2 on all 314.

### NOT MEASURED

- 2025 lockbox; forward weeks; totals.
- Exact S3 `week_decision_as_of` vs named `tuesday_0600_et` slot (same staged
  proxy note as S4).

### UNRESOLVED

- None that block the Phase 0 reading. Residual question of *why* all-games
  ATS Brier is worse than constant while remaining calibrated in the mean is
  outside this card decision (discrimination, not calibration).

---

## Phase 1 branch (name only — do not execute)

| Phase 0 finding | Phase 1 |
|---|---|
| **No discrimination (AUC ≈ 0.5, BSS ≤ 0)** | H4 and H5 are one finding. Deliverable is the write-up and a plain-language public statement. **No replacement selection rule.** The card does not ship. |
| S3's +0.76 was the wrong instrument for ticket CLV | Correct `docs/notes/social-s3.md`: the CLV/ATS anomaly framing dissolves; restate what S3 actually found (sub-coin-flip selected tickets with overconfident claims; CFBD line CLV is not book CLV). |

Discrimination-exists / misordered-selection branch is **not** selected
(overlap = 1.0 and AUC ≈ 0.5).

---

## Acceptance

| Item | Result |
|---|---|
| Phase 0 only | Yes |
| n=314 preserved, matches S4 | Yes |
| 2025 lockbox | Untouched |
| No src/configs/S3/S4 edits | Yes |
| Artifacts + probes | `docs/notes/_artifacts/social-s5/`, `scripts/_s5_*.py` |
| `make lint typecheck test` | **green** — ruff ok; mypy 129 files; **999 passed**, 1 deselected (language-ratchet pin re-measured after S4 probe locals + this note) |
