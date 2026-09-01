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

---

## S6-W1-CARD-B — fresh odds pull + gate re-run (2026-09-01)

**Branch:** `social-s1-s2`  
**Analysis `as_of`:** `2026-09-01T20:38:58Z`  
**Publish `as_of`:** `2026-09-01T10:00:00Z` (operator, ADR 0017 Amendment 3 second primary)  
**Export / R2 / publish / merge to `main`:** OFF  
**Odds API:** exactly **one** live pull (authorized for this task)

**Forbidden respected:** no threshold writes, no QB row edits, no second Odds API
call, 2025 lockbox untouched, no Best Bets thread, no No-Bet post,
`candidates_enabled` unchanged.

**Artifacts:** `docs/notes/_artifacts/social-s6-w1-card/` (regenerated)  
**Probe:** `scripts/_s6_w1_card.py` (ordered gate view probe-only; QB worklist =
step-4 exposure survivors)

---

### Phase A — the pull

**Pre-call credit balance** (free `GET /v4/sports` probe): **100,000** remaining.

| Field | Value |
|-------|-------|
| Endpoint | `GET /v4/sports/americanfootball_ncaaf/odds` |
| Markets | `h2h`, `spreads`, `totals` |
| Region | `us` |
| Bookmakers (config) | draftkings, fanduel, betmgm, williamhill_us |
| Estimated credits | **3** (3 markets × 1 region) |
| Credits before | **100,000** |
| Credits after | **99,997** (`x-requests-last=3`) |

**Staging:** `run_odds_ingest` → raw archive → crosswalk → `odds_snapshots`
(2,068 new rows). Raw: `data/raw/odds_api/2026-09-01/20260901T203456940488Z.json`.

| Field | Value |
|-------|-------|
| Snapshot `event_time` | `2026-09-01T20:34:56.940488Z` |
| `ingested_at` | `2026-09-01T20:34:56.940488Z` |
| Games with spread (newest snap) | **56 / 91** |
| Books per covered game (min / median / max) | **2 / 4 / 4** |
| `as_of − snapshot` at analysis | **0.07 h** |
| Stale wall-clock (`event_time + 6h`) | **`2026-09-02T02:34:56Z`** |
| Stale at analysis `as_of` | **NO** |

**Finding:** pull returned **56 of 91** games — below 60; market/crosswalk
coverage gap, not ingest failure.

**Crosswalk (this pull — 146 Odds API events):**

| Status | Count |
|--------|------:|
| matched | **112** |
| unmatched | **34** |
| Match rate | **76.7%** |

Unmatched events are FCS / naming mismatches (Odds feed team strings failed to
resolve to CFBD `game_id`). Examples: `Citadel` vs Charlotte, `Houston Baptist`
vs Rice, `Albany` vs Buffalo, `Indiana State Sycamores` vs Purdue. Full list:
`analysis.json` → `crosswalk_summary.unmatched_events`.

**Games with no spread snapshot (35 — structurally uncovered on this pull):**

| game_id | matchup |
|--------:|---------|
| 401856768 | Idaho @ Utah |
| 401867866 | Murray State @ Middle Tennessee |
| 401858423 | Massachusetts @ Rutgers |
| 401856666 | Furman @ Tennessee |
| 401858435 | Indiana State @ Purdue |
| 401862700 | UT Rio Grande Valley @ UTSA |
| 401860880 | Idaho State @ Utah State |
| 401866627 | Maine @ App State |
| 401861962 | Lafayette @ UConn |
| 401866409 | UAlbany @ Buffalo |
| 401868356 | Alcorn State @ Southern Miss |
| 401856663 | Arkansas-Pine Bluff @ Missouri |
| 401856669 | Austin Peay @ Vanderbilt |
| 401868170 | Charleston Southern @ Georgia Southern |
| 401868140 | Eastern Kentucky @ Jacksonville State |
| 401862697 | Houston Christian @ Rice |
| 401856769 | Long Island University @ Kansas |
| 401870790 | Mercyhurst @ New Mexico State |
| 401866411 | Mississippi Valley State @ Sacramento State |
| 401856774 | Morgan State @ Arizona State |
| 401856771 | Nicholls @ Kansas State |
| 401856635 | North Alabama @ Arkansas |
| 401856773 | Northern Arizona @ Arizona |
| 401869129 | Northwestern State @ Louisiana Tech |
| 401860879 | Portland State @ San Diego State |
| 401868316 | SE Louisiana @ South Alabama |
| 401858431 | South Dakota State @ Northwestern |
| 401856668 | Missouri State @ Texas A&M |
| 401866410 | Tarleton State @ Bowling Green |
| 401862694 | The Citadel @ Charlotte |
| 401858211 | VMI @ Virginia Tech |
| 401864425 | West Georgia @ Kennesaw State |
| 401856659 | Youngstown State @ Kentucky |
| 401864424 | Merrimack @ Delaware |
| 401856775 | Utah Tech @ BYU |

(34 crosswalk-unmatched + Massachusetts @ Rutgers has only Aug-25 fallback snap →
`stale_inputs` + `no_snapshot` on newest ladder rung.)

---

### Phase B — gate re-run (probe ordering)

Provider: `build_candidates_from_odds`, defaults from `configs/betting.yaml`,
`n_draws=20_000`, seed 42.

| Step | Gate group | Survivors |
|------|------------|----------:|
| start | — | **91** |
| 1 | `no_snapshot`, `stale_inputs`, `kickoff_passed`, `line_quarantined` | **56** |
| 2 | `edge_too_small`, `non_positive_ev`, `sigma_not_credible` | **53** |
| 3 | `model_market_disagree` | **21** |
| 4 | exposure caps (edge-sorted accept simulation) | **8** |
| 5 | `qb_status_unknown` | **0** |

**QB worklist (step-4 output — 8 games):**

| game_id | away | home |
|--------:|------|------|
| 401856636 | Baylor | Auburn |
| 401858209 | Tulane | Duke |
| 401858434 | Marshall | Penn State |
| 401866623 | North Carolina A&T | Georgia State |
| 401864496 | Duquesne | Air Force |
| 401864499 | Fordham | North Dakota State |
| 401858422 | Eastern Illinois | Minnesota |
| 401864498 | Central Michigan | New Mexico |

**§12 filter incidence (independent per-filter matrix — all 91):**

| FilterReason | Games firing |
|--------------|-------------:|
| `qb_status_unknown` | 91 |
| `model_market_disagree` | 33 |
| `no_snapshot` | 34 |
| `edge_too_small` | 37 |
| `non_positive_ev` | 37 |
| `stale_inputs` | 1 |
| `kickoff_passed` | 0 |
| `line_quarantined` | 0 |
| `sigma_not_credible` | 0 |
| exposure caps (individual check) | 0 |

Full matrix: `phase1_matrix.md`, `analysis.json` → `betting_rows`.

---

### Phase C — overlay and forecast

**Public `public_min_edge_sides` survivors:**

| Bar | Edge-only | All §12 filters clear |
|-----|----------:|----------------------:|
| Config default **0.045** | **49 / 91** | **0** |
| S3 recommendation **0.05** (not written) | **48 / 91** | **0** |

**S5 discrimination overlay** (verbatim on every edge-only survivor):

| Quantity | Value |
|----------|-------|
| Candidate `p_win` AUC | **0.493** [0.43, 0.56] |
| Top bin realized vs break-even | **0.508** vs **0.524** |
| Accept-loop top-*k* overlap | **1.0** |
| Probability CLV vs required | **+0.0044** vs **+0.0238** |

**Edge distribution (all 91):**

| Stat | Value |
|------|------:|
| min / max | 0.000 / 0.435 |
| ≥ 0.15 regime | **33** games |

**FCS opponent games (48 on slate):**

| Stat | Value |
|------|------:|
| min / max edge | 0.000 / 0.394 |
| ≥ 0.15 regime | **6** games |
| Top FCS-edge | Rhode Island @ Temple **0.394** |

Large-edge tail is **not** concentrated in FCS-only matchups: **33** slate-wide
vs **6** FCS-opponent games in the ≥0.15 band.

**Verdict:** **NOT MEASURED** — eight games survive exposure simulation but all
reject on `qb_status_unknown`; S5 overlay (AUC 0.493) applies to any hypothetical
survivor.

**Forecast:** all **91** games — `analysis.json` → `forecast_rows`.  
**Reply bank:** `replies_w1.md` with `site_url=https://the-cfb-model.vercel.app`.  
No Best Bets thread.

---

### Verification

`make lint typecheck test` — see commit (betting-language ratchet pin updated
for regenerated card artifacts).

### Ambiguities

1. **56/91 Odds API coverage** is a market + crosswalk naming gap, not stale data.
2. **QB worklist = step 4** (exposure survivors), not step 5 — operator QB effort
   is 16 team rows across 8 games if gates were cleared through exposure.
3. **Massachusetts @ Rutgers** retains Aug-25 fallback only (`stale_inputs`).

