# S6-W1-CARD — candidate provider read for 2026 week-1 second primary

**Date:** 2026-09-01  
**Branch:** `social-s1-s2`  
**Analysis `as_of`:** `2026-09-01T20:10:44Z`  
**Publish `as_of`:** `2026-09-01T10:00:00Z` (operator, ADR 0017 Amendment 3 second primary)  
**Export / R2 / publish / Odds API / merge to `main`:** OFF  

**Forbidden respected:** no threshold writes, no QB row edits, no Odds API pull,
2025 lockbox untouched, no Best Bets thread, no No-Bet post generated.

**Artifacts:** `docs/notes/_artifacts/social-s6-w1-card/`  
**Probe:** `scripts/_s6_w1_card.py` (ordered gate view is **probe-only** — production
`apply_bet_filters` / provider filter order unchanged)

---

## Phase 0 — state (STOP after)

### 1 — The slate

**Source:** `data/webapp/publish_history/2026_w1.jsonl` (record 3,
`as_of=2026-09-01T10:00:00Z`). `latest/week_predictions.json` is not on disk.

| Field | Value |
|-------|------:|
| Games with `kickoff_utc > as_of` | **91** |
| CFBD `week` (artifact) | **1** |
| `season` | **2026** |
| Kickoff range (UTC) | **2026-09-03T22:00Z** – **2026-09-07T23:30Z** |

**ADR 0017 Amendment 3:** CFBD 2026 week 1 spans two weekends (Aug 29–30 early
slate + Labor Day weekend). The Sept 1 operator `as_of` is the **second
`tuesday_primary`** for CFBD week 1 — the **Labor Day / second-weekend** publish,
not week 2. CFBD week number remains **1**; this is not CFBD week 2.

### 2 — Odds age

| Field | Value |
|-------|------:|
| Newest staged 2026 `event_time` | `2026-08-25T16:59:11.540113Z` |
| Age at publish `as_of` (2026-09-01T10:00Z) | **161.0 h** |
| Age at analysis `as_of` (2026-09-01T20:10Z) | **171.2 h** |
| `odds_max_age_hours` | **6.0** |
| Stale at publish `as_of` | **YES** |
| Stale at analysis `as_of` | **YES** |

**Book coverage (spread, staged partitions):**

| Bucket | Count |
|--------|------:|
| Games with any spread snapshot | **57** |
| Games with **no** spread snapshot | **34** |
| Books per covered game (min / median / max) | **1 / 4 / 4** |

The Aug 25 pull does not cover the full 91-game Labor Day slate; 34 games have
`no_snapshot` at this `as_of`.

**Fresh live pull credit cost (no pull made):** **3 credits** — Odds API live
endpoint meters **markets × regions** (3 markets × 1 `us` region) per
`run_odds_ingest` / `GET /v4/sports/americanfootball_ncaaf/odds`. Historical
backfill uses the separate 30-credit envelope (`10 × markets × regions` in
`configs/data.yaml`); this task would use the **live** path only.

**STOP** — operator decides whether to spend 3 credits before any non-directional
read.

### 3 — QB coverage

Store: `data/staged/qb_status/season=2026/part.parquet` (**11 rows**, all eight
Aug 29–30 week-0 games). **No rows** for any game on this 91-game slate.

| Resolved teams per game | Games |
|-------------------------|------:|
| **0** (zero or unknown) | **91** |
| **1** | **0** |
| **2** (both non-unknown) | **0** |

Every game on this slate will reject on `qb_status_unknown` if it reaches that
gate. No QB rows written in this task.

---

## Phase 1 — per-filter verdicts (STALE INPUTS — DIRECTIONAL ONLY)

Provider: `build_candidates_from_odds`, defaults from `configs/betting.yaml`,
`n_draws=20_000`, seed 42. Ladder: **57× `odds_api_snapshot_fallback`** (Aug 25
16:59Z snap vs analysis `as_of`); **34× `null`** (no snapshot). All **91**
constructed; none accepted.

**Ordered gate view (probe script only — not production order):**

| Step | Gate group | Survivors |
|------|------------|----------:|
| start | — | **91** |
| 1 | `no_snapshot`, `stale_inputs`, `kickoff_passed`, `line_quarantined` | **0** |
| 2 | `edge_too_small`, `non_positive_ev`, `sigma_not_credible` | **0** |
| 3 | `model_market_disagree` | **0** |
| 4 | exposure caps (edge-sorted accept simulation) | **0** |
| 5 | `qb_status_unknown` | **0** |

**QB worklist (step-4 output):** **empty** — no game survives snapshot/stale
gates, so there is nothing for the operator to QB-resolve on a shortened list.
Full-slate QB lookup would be 182 team rows if staleness and odds were cleared
first.

**§12 filter incidence (independent per-filter matrix — all 91 games):**

| FilterReason | Games firing |
|--------------|-------------:|
| `qb_status_unknown` | 91 |
| `stale_inputs` | 57 |
| `no_snapshot` | 34 |
| `model_market_disagree` | 33 |
| `edge_too_small` | 39 |
| `non_positive_ev` | 39 |
| `kickoff_passed` | 0 |
| `line_quarantined` | 0 |
| `sigma_not_credible` | 0 |
| exposure caps (individual check) | 0 |

Full per-game matrix (game_id, matchup, side, book, line, price, μ, σ, p_mod,
p_mkt, edge, EV, residual, stake, units, all firing reasons):
`docs/notes/_artifacts/social-s6-w1-card/phase1_matrix.md` and
`analysis.json` → `betting_rows`.

**Top edges on stale snapshot games (directional only — not bets):**

| game_id | matchup | edge | residual | reasons (subset) |
|--------:|---------|-----:|---------:|------------------|
| 401862698 | Rhode Island @ Temple | 0.437 | 32.0 | stale, qb, disagree |
| 401858428 | Western Michigan @ Michigan | 0.431 | 31.2 | stale, qb, disagree |
| 401856665 | Kent State @ South Carolina | 0.377 | 28.8 | stale, qb, disagree |
| 401856634 | East Carolina @ Alabama | 0.373 | 28.3 | stale, qb, disagree |
| 401864497 | UNLV @ Hawai'i | 0.361 | 27.1 | stale, qb, disagree |

Edges in the ≥0.15 regime (S3 miscalibration band): **31 / 91** games. Max edge
**0.437** — far above any calibrated public bar, consistent with preseason μ vs
stale Aug-25 lines rather than measured skill.

**Survivors:** none — no table of accepted candidates.

---

## Phase 2 — honest overlay

### 1 — Public `public_min_edge_sides` survivors

| Bar | Edge-only (ignores stale/QB/residual) | All §12 filters clear |
|-----|--------------------------------------:|----------------------:|
| Config default **0.045** | **48 / 91** | **0** |
| S3 recommendation **0.05** (not written) | **48 / 91** | **0** |

Nine constructed games have edge in (0, 0.045); none in the 0.04–0.05 near-bar
band.

### 2 — S5 discrimination overlay (verbatim on every edge-only survivor)

Any game clearing the public edge bar still carries:

| Quantity | Value |
|----------|-------|
| Candidate `p_win` AUC | **0.493** [0.43, 0.56] |
| Top bin realized vs break-even | **0.508** vs **0.524** |
| Accept-loop top-*k* overlap | **1.0** |
| Probability CLV vs required | **+0.0044** vs **+0.0238** |

Clearing filters or the public edge bar does **not** confer information — S5
measured no per-game ATS discrimination on the 314-ticket population.

### 3 — Edge distribution

| Stat | Value |
|------|------:|
| min / max edge | 0.000 / 0.437 |
| Near public bar (0.04–0.05) | **0** games |
| Small postable band (0.05–0.15) | **17** games |
| S3 miscalibration regime (≥ 0.15) | **31** games |

### 4 — Verdict

**NOT MEASURED** — load-bearing gates (`STALE_INPUTS` on 57 games, `NO_SNAPSHOT`
on 34, `QB_STATUS_UNKNOWN` on all 91) block the slate before discrimination
matters; the 48 edge-only public-bar clears are draws from a ranking with AUC
0.493 and no measured ATS information.

---

## Phase 3 — forecast-only read

All **91** games: `sigma_margin_credible=True`, coherence gate **not**
suppressing intervals (`coherence_suppressed=false`).

Full forecast table (`μ_margin`, `σ_margin`, 80% interval whole points,
`conviction_tier`, `p_favored`, coherence flag):
`analysis.json` → `forecast_rows`.

**Sample (first kickoff bucket):**

| game_id | matchup | μ | σ | 80% interval | tier | p_fav |
|--------:|---------|--:|--:|--------------|------|------:|
| 401858423 | Massachusetts @ Rutgers | +41.5 | 20.0 | [−2, +70] | strong_lean | 0.901 |
| 401866409 | UAlbany @ Buffalo | +31.9 | 18.0 | [−27, +41] | strong_lean | 0.882 |
| 401858204 | Akron @ Wake Forest | +28.0 | 21.8 | [+0, +56] | strong_lean | 0.864 |

μ_margin sign: positive = home favored. Wide intervals reflect week-1 σ, not a
broken export.

### Reply bank

Regenerated with S5-W0-REPLIES multi-reason renderer at
`docs/notes/_artifacts/social-s6-w1-card/replies_w1.md`.
`site_url=https://the-cfb-model.vercel.app` (not `ridge.example.com`).
No Best Bets thread generated.

---

## Verification

`make lint typecheck test` — see commit.

---

## Ambiguities

1. **Ordered gate view is probe-only** — production `evaluate_filters` order
   unchanged; step 4 exposure simulation uses edge-sorted accept loop matching
   S5 P0-4 convention.
2. **QB worklist empty** because step 1 eliminates the full slate; operator QB
   effort is undefined until fresh odds land.
3. **Fresh pull required** before any non-directional card read — staleness
   binding at 161–171 h vs 6 h max age.
4. **34 games without staged spread** may still appear on Odds API live pull
   even after a 3-credit refresh.
