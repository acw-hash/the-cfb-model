# S3 — Public threshold calibration (amended post-W0)

**Date:** 2026-08-25  
**Branch:** `social-s1-s2`  
**Status:** Recommendations only — **no config written** (awaiting operator approval)  
**Authority:** `docs/social/TASKS-social.md` S3; W0-DIAG week-1 findings; ADR 0013/0014 epistemic style  

**Forbidden respected:** no Odds API, no publish, no R2, 2025 lockbox untouched
(`assert_lockbox_excluded` on seasons 2021–2024), no merge to main.

**Replay artifact:** `scripts/calibrate_social_threshold.py` →  
`data/tmp/s3_calibration/candidates_cache.parquet`, `report.json`.

---

## Method (MEASURED scope)

1. Champion week frames: `task23_fundamental_reduced_v3/full/weeks`, seasons
   **2021–2024 only** (61 season-weeks on disk: **4** week-1, **57** weeks 2+).
2. S5 provider at Tuesday `week_decision_as_of` (`n_draws=2000`, seed 42).
3. §12 `apply_bet_filters` with historical flags:
   `no_bet_on_qb_unknown=false`, `no_bet_on_stale=false`, sides only,
   `min_edge_sides=0.025` (construction floor).
4. Public bar: `edge ≥ public_min_edge_sides`, sort desc, cap
   `max_bets_per_week=10`. Grid **0.02 … 0.12** step 0.01.
5. Grade ATS from `realized_margin` vs shopped home line; stake PnL in
   displayed **u** at `unit_fraction=0.005` and the shopped American price.
6. Line CLV: `bet_side_line − close_side_line` from backtest `spread_close`.

**Week 1 is never pooled with weeks 2+ in any headline.**

---

## 1 — Grid (report only)

§12 accepted pool: **415** candidates (median edge **0.140**). Almost all
already clear every public bar ≤ 0.12, so the public threshold barely changes
the card — **weekly exposure (~0.10 / 0.015 ≈ 6)** is what pins the count.

### Weeks 2+ (57 weeks)

| thr | n | ATS hit | mean claimed p | u PnL | mean line CLV | weekly median | frac in 3–10 | frac zero |
|----:|--:|--------:|---------------:|------:|--------------:|--------------:|-------------:|----------:|
| 0.02 | 385 | 0.492 | 0.621 | −45.8 | — | 7.0 | 0.930 | 0.035 |
| 0.03 | 369 | 0.492 | 0.625 | −46.2 | — | 7.0 | 0.930 | 0.035 |
| 0.04 | 343 | 0.490 | 0.632 | −50.8 | — | 6.0 | 0.912 | 0.035 |
| 0.05 | 314 | 0.494 | 0.641 | −43.7 | +0.76 | 6.0 | 0.912 | 0.035 |
| 0.06 | 313 | 0.492 | 0.641 | −46.5 | +0.72 | 6.0 | 0.912 | 0.035 |
| 0.07 | 310 | 0.490 | 0.642 | −49.2 | +0.71 | 6.0 | 0.912 | 0.035 |
| 0.08 | 310 | 0.490 | 0.642 | −49.2 | +0.71 | 6.0 | 0.912 | 0.035 |
| 0.09 | 306 | 0.493 | 0.643 | −42.9 | +0.82 | 6.0 | 0.895 | 0.053 |
| 0.10 | 304 | 0.493 | 0.643 | −42.8 | +0.78 | 6.0 | 0.895 | 0.053 |
| 0.11 | 294 | 0.497 | 0.644 | −35.4 | +0.78 | 6.0 | 0.860 | 0.070 |
| 0.12 | 285 | 0.491 | 0.645 | −42.9 | +0.58 | 6.0 | 0.825 | 0.070 |

Weekly count histogram at thr=0.06 (weeks 2+): mostly **6** (51/57 weeks),
zeros **2**, ones **3**. Target band 3–10 is already the exposure-driven
default — the edge bar is not the dial.

### Week 1 (4 weeks)

| thr | n | ATS hit | mean claimed p | u PnL | mean line CLV | weekly median |
|----:|--:|--------:|---------------:|------:|--------------:|--------------:|
| 0.02 | 30 | 0.357 | 0.602 | −22.2 | −3.50 | 7.5 |
| 0.05 | 24 | 0.348 | 0.618 | −22.3 | −3.78 | 6.0 |
| 0.08 | 24 | 0.348 | 0.618 | −22.3 | −3.78 | 6.0 |
| 0.10 | 22 | 0.333 | 0.621 | −22.1 | −4.18 | 5.5 |
| 0.12 | 14 | 0.385 | 0.636 | −9.9 | −1.48 | 3.5 |

Raising the bar does **not** rescue week 1. Hit stays ~33–38%; line CLV stays
deeply negative.

---

## 2 — Calibration (the question that matters)

A rule that selects ~16% “edges” but hits ~49% is not finding edge — it is
finding **miscalibration**.

At thr=0.05:

| Slice | n decided | mean claimed p | realized hit | gap (real − claim) |
|-------|----------:|---------------:|-------------:|-------------------:|
| Week 1 | 23 | 0.618 | 0.348 | **−0.271** |
| Weeks 2+ | 314 | 0.641 | 0.494 | **−0.147** |

Weeks 2+ quantile bins (claimed p → realized): every bin but one realizes
**below** the claim; typical gap −0.10 to −0.20. Highest-p bin
(claim ~0.678) hits **0.475**.

Week 1: same pattern, worse — overall gap −0.27 on n=23 (wide CI, but
directionally catastrophic and aligned with W0 prior/cupcake story).

**Plain answer:** realized hit rates are **not** consistent with claimed
cover probabilities on the selected set. Public edge selection inherits
§12’s preference for large model–market gaps, which here track overconfident
`p_win`, not ATS skill.

---

## 3 — Kelly saturation (report only; no config change)

Among 415 §12 accepts: **81.4%** already at `max_stake_pct=0.015` (3.0u).
Accepted edge median **0.140**; uncapped quarter-Kelly median stake **0.060**
(would be 12u before the hard cap). Displayed **u** therefore carries almost
no ranking information.

| kelly_fraction | max_stake_pct | frac at cap | u median | u p10 | u p90 | distinct 0.1u bins |
|---------------:|--------------:|------------:|---------:|------:|------:|-------------------:|
| 0.25 (current) | 0.015 | 0.814 | 3.0 | 1.6 | 3.0 | 21 |
| 0.25 | 0.03 | 0.802 | 6.0 | 1.6 | 6.0 | 24 |
| 0.25 | 0.05 | 0.718 | 10.0 | 1.6 | 10.0 | 48 |
| 0.10 | 0.015 | 0.793 | 3.0 | 0.6 | 3.0 | 17 |
| **0.10** | **0.03** | **0.120** | **4.8** | **0.6** | **6.0** | **46** |
| **0.05** | **0.015** | **0.120** | **2.4** | **0.3** | **3.0** | **26** |

**Options (bankroll consequences; not applied):**

- **A — tenth-Kelly, 3% cap:** usable spread; single-bet ceiling 3% bankroll;
  max display 6u at `unit_fraction=0.005`.
- **B — twentieth-Kelly, keep 1.5% cap:** also ~12% at cap; smaller tickets;
  max still 3u.
- **C — raise cap only, keep quarter-Kelly:** still ~70–80% at the new ceiling;
  does **not** restore a spread — just larger identical tickets.

W0’s 75.3% figure was on *constructed* edges; here 81.4% is on *§12 accepts*
(the social input). Same conclusion: under current staking, **u is not a
signal**.

---

## 4 — Week-1 regime (numbers → choice)

| Option | What the grid says |
|--------|--------------------|
| **Exclude week 1** | Removes the −0.27 calibration gap / −3.8 line-CLV / ~35% hit block. Weeks 2+ median count stays **6**. |
| Higher week-1 bar (+0.03 or to 0.11) | Hit stays ~0.33–0.35; still large negative u and negative line CLV. **Does not fix.** |
| Residual / blowout guard (residual &gt; 7) on week-1 accepts | **Drops 0** — §12 `min_model_market_agreement=7` already removed those. Remaining week-1 fails are *inside* the residual bar. |

**Recommendation: exclude week 1 from public cards entirely.**  
The data do **not** say week-1 cards are fine. A higher bar or residual guard
on the accepted set is insufficient; the failure mode is prior/cupcake
miscalibration that still passes §12.

---

## 5 — Threshold recommendations (after 1–4)

**Not written to config in this task.**

### `public_min_edge_sides`

**Recommend 0.05** (status quo default is 0.045 — either is fine as a
*honesty floor*).

Why not optimize on ATS/u:

- Hit rate and u P&amp;L are **flat** across 0.02–0.12 on weeks 2+.
- The bar does not create the 3–10 count band; **exposure does**.
- Choosing 0.05 states “we only post gaps the private stack already treated
  as material” without pretending the dial finds edge.

### `public_min_edge_totals`

**NOT MEASURED** this session (sides-only provider run). Leave **0.055** until
a totals calibration exists.

### Week 1

**Exclude from the public card** (No-Bet or forecast-only week). Implement in
S4 / select path when approved — not in this task’s config write.

### Staking (optional follow-on; not a social.yaml field today)

If displayed u should mean something: prefer **kelly_fraction=0.10 with
max_stake_pct=0.03** (option A) or **kelly_fraction=0.05 with 0.015** (option B).
Requires a conscious `BettingConfig` change outside S3.

---

## Epistemic ledger (ADR 0013/0014 style)

### MEASURED

- §12-accepted candidate counts and edge distribution (2021–2024 replay).
- Public-bar weekly counts, ATS hit, u PnL, line-unit CLV — **week 1 vs
  weeks 2+ separately**.
- Claimed `p_win` vs realized cover on the selected set (calibration bins).
- Stake-cap saturation under current Kelly settings; counterfactual stake
  spreads for alternate kelly/cap pairs (arithmetic only).

### NOT MEASURED

- Same-book **probability** CLV (DESIGN §2.7 primary skill metric).
- 2025 lockbox; 2026 live; forward guarantee of any kind.
- Totals market public bar.
- Live QB / stale gates (disabled for this historical replay).
- Full 2019–2020 weeks (not present in this champion week dump; 61 weeks only).
- Whether a *different* §12 residual / preseason guard would repair
  calibration upstream of the public bar.

### UNRESOLVED

- Whether fixing week-1 / prior regime **upstream** (ratings / FCS / residual)
  would make selected `p_win` honest enough that a public edge bar could
  again mean “edge.” Until then, the public bar is a **card-size / honesty
  floor**, not a skill filter.

---

## Acceptance checklist

| Item | Result |
|------|--------|
| 2025 lockbox untouched | Yes — seasons tuple (2021–2024) + `assert_lockbox_excluded` |
| No config write | Yes — recommendations in this note only |
| Week-1 split on every headline | Yes |
| Script + report on disk | `scripts/calibrate_social_threshold.py`, `data/tmp/s3_calibration/` |
| Operator approval before thresholds land in config | **Required next step** |

---

## CORRECTION (S6 — 2026-08-25)

**Supersedes interpretation only.** Grid numbers and method above are unchanged.

### What S3 said

At thr=0.05 on weeks 2+, **n=314**, ATS hit **49.4%**, mean claimed `p_win`
**0.641**, mean **line CLV +0.76** (CFBD `spread_close` on all 314). S3 framed
this as positive line CLV alongside sub-coin-flip ATS — a tension that drove
calibration hypothesis #1 across S4 and S5.

### What is now known (S4/S5)

| Population | Close source | n | Mean line CLV |
|---|---|---:|---:|
| All 314 (S3) | CFBD `spread_close` | 314 | **+0.760** |
| Moved rows (S4/S5) | Odds same-book `slot_close` | 156 | **−0.824** |

Same formula (`bet_side_line − close_side_line`; algebraically identical to
`line_units_clv`). The sign flip is **population + close instrument**, not a
sign bug (S5 P0-1: synthetic and hand-check rows match).

For **these tickets**, the Odds-book figure is the one that matters: on rows
where the line moved, the market moved roughly **0.8 points against** the bet.
Headline probability CLV on unmoved rows is **+0.0044** (n=158, S4 P0-3) —
order of magnitude below vig.

### Interpretation correction

The "positive CLV alongside sub-coin-flip ATS" framing **does not survive**. A
stale number bought at Tuesday prices realizing **49.4%** is an ordinary
outcome, not an anomaly. S5 additionally shows **no per-game ATS discrimination**
(AUC **0.493**, selection overlap **1.0**) — there is nothing to select on, not
a mis-ordered filter.

**Authority:** `docs/notes/social-s5.md` P0-1/P0-2; `docs/notes/social-s4.md`
P0-3; S6 consolidated note.
