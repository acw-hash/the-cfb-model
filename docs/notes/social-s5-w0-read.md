# S5-W0-READ — complete eight-game read for Aug 29–30

**Date:** 2026-08-25  
**Branch:** `social-s1-s2`  
**as_of (analysis run):** `2026-08-25T23:37:41Z`  
**Export / R2 / publish / Odds API / merge to main:** OFF  

**Forbidden respected:** no threshold writes, no QB row edits, no Odds API,
2025 lockbox untouched, no Best Bets thread, no No-Bet post generated.

**Artifacts:** `docs/notes/_artifacts/social-s5-w0-read/analysis.json`,
`replies_w1.md`  
**Probe:** `scripts/_s5_w0_read.py`

---

## Phase 0 — state (corrected counts)

### 1 — Eight games

**Source:** `data/webapp/publish_history/2026_w1.jsonl` (record 1,
`as_of=2026-08-25T10:00:00Z`, games `published_at=2026-08-25T13:51:33Z`).
`latest/week_predictions.json` is not on disk.

| game_id | matchup | kickoff UTC | μ_margin | σ_margin | σ_cred | μ_total | tier | p_fav |
|--------:|---------|-------------|----------:|----------:|:------:|--------:|------|------:|
| 401856766 | North Carolina @ TCU | 2026-08-29T16:00Z | 17.620 | 19.822 | True | 56.005 | strong_lean | 0.864 |
| 401864494 | San José State @ USC | 2026-08-29T19:00Z | 19.854 | 20.257 | True | 56.843 | strong_lean | 0.882 |
| 401858202 | NC State @ Virginia | 2026-08-29T19:30Z | 2.981 | 23.641 | True | 52.098 | lean | 0.631 |
| 401864577 | Jacksonville State @ NDSU | 2026-08-29T21:30Z | 14.513 | 17.807 | True | 53.830 | strong_lean | 0.853 |
| 401866408 | Sacramento State @ Eastern Mich. | 2026-08-29T22:30Z | 12.387 | 20.993 | True | 55.437 | clear_lean | 0.797 |
| 401858201 | Hawai'i @ Stanford | 2026-08-29T23:00Z | 11.151 | 21.297 | True | 55.370 | clear_lean | 0.770 |
| 401864570 | New Mexico State @ Florida State | 2026-08-29T23:00Z | 15.488 | 16.998 | True | 53.093 | strong_lean | 0.873 |
| 401862693 | Memphis @ UNLV | 2026-08-30T02:00Z | 10.896 | 20.078 | True | 57.352 | clear_lean | 0.775 |

**Count: 8.**

### 2 — Snapshot age

| Field | Value |
|-------|------:|
| Newest 2026 `event_time` | `2026-08-25T16:59:11.540113Z` |
| Age at analysis `as_of` | **6.64 h** |
| `odds_max_age_hours` | **6.0** |
| Book coverage (eight games) | **4 books each**, max `event_time` = 16:59Z on all eight |

**Staleness at analysis `as_of`: YES** — every game would reject on
`STALE_INPUTS`. Phase 1 table is labeled **STALE INPUTS — DIRECTIONAL ONLY**.

### 3 — QB status (sixteen teams)

Store: `data/staged/qb_status/season=2026/part.parquet` (11 rows).

**Corrected summary:**

| Bucket | Count | game_ids |
|--------|------:|----------|
| **Fully resolved** (both teams non-unknown) | **2** | 401858202, 401858201 |
| **QB-blocked** | **6** | 401856766, 401864494, 401864577, 401866408, 401864570, 401862693 |

Breakdown of the six blocked:

- **Three games, both teams NO_ROW:** 401856766, 401864494, 401864570
- **Three games, at least one explicit `unknown`:** 401864577 (Jax State),
  401866408 (Sac State), 401862693 (UNLV latest row)

*(A game can sit in only one bucket; the old prose that summed “four NO_ROW +
two unknown” to five was wrong — three NO_ROW *pairs* plus three partial rows
= six blocked games.)*

| game_id | team | status | event_time |
|--------:|------|--------|------------|
| 401856766 | North Carolina | NO_ROW | — |
| 401856766 | TCU | NO_ROW | — |
| 401864494 | San José State | NO_ROW | — |
| 401864494 | USC | NO_ROW | — |
| 401858202 | NC State | starter | 2026-08-25T23:06:37Z |
| 401858202 | Virginia | starter | 2026-08-25T22:58:49Z |
| 401864577 | Jacksonville State | **unknown** | 2026-08-25T23:11:26Z |
| 401864577 | North Dakota State | starter | 2026-08-25T23:11:35Z |
| 401866408 | Sacramento State | **unknown** | 2026-08-25T23:11:12Z |
| 401866408 | Eastern Michigan | starter | 2026-08-25T23:11:19Z |
| 401858201 | Hawai'i | starter | 2026-08-25T23:13:25Z |
| 401858201 | Stanford | starter | 2026-08-25T23:13:33Z |
| 401864570 | New Mexico State | NO_ROW | — |
| 401864570 | Florida State | NO_ROW | — |
| 401862693 | Memphis | starter | 2026-08-25T23:07:31Z |
| 401862693 | UNLV | **unknown** | 2026-08-25T23:11:05Z |

### UNLV 23:08Z row (report only)

The partition holds **two** UNLV rows for game 401862693:

| event_time | status | source_version | ingested_at |
|------------|--------|----------------|-------------|
| 2026-08-25T23:08:36.334644Z | starter | manual_v1 | 2026-08-25T23:08:36.334644Z |
| 2026-08-25T23:11:05.418853Z | unknown | manual_v1 | 2026-08-25T23:11:05.418853Z |

**`run_id`:** the `qb_status` schema has **no `run_id` column** — only
`event_time`, `ingested_at`, `source_version`, `game_id`, `team_id`, `season`,
`status`.

**Write paths:** production code can append to `qb_status` only through
`set_qb_status()` in `features/builders/roster.py`, invoked today exclusively
by `ncaa-quant roster set-qb` (`cli.py`). The depth-chart scrape stub raises
`NotImplementedError`. Unit tests under `tests/unit/test_roster.py` write
isolated fixture partitions, not `data/staged`.

The 23:08 row matches CLI defaults exactly (`source_version=manual_v1`,
`event_time == ingested_at`). Memphis on the same game was written at 23:07:31 —
consistent with a prior `set-qb --status starter` for UNLV ~67 seconds before
the operator's 23:11 `unknown` overwrite. **Not deleted or edited in this
task.**

---

## Phase 1 — per-filter verdicts (STALE INPUTS — DIRECTIONAL ONLY)

Provider: `build_candidates_from_odds`, defaults from `configs/betting.yaml`,
`n_draws=20_000`, seed 42. Ladder: **8× `odds_api_snapshot_fallback`**
(16:59Z snap vs 23:37Z `as_of`). All eight constructed; none accepted.

**Near-residual flag:** `|residual − 7.0| ≤ 1.0` — a Thursday line move could
flip `model_market_disagree`.

### Summary table

| game_id | side | book | line | px | rung | model line | μ | σ | p_mod | p_mkt | edge | EV | residual | stale | age h | QB? | src | stake | u | max? | reasons firing |
|--------:|------|------|-----:|---:|------|------------|--:|--:|------:|------:|-----:|---:|---------:|:-----:|------:|:----:|:----|------:|--:|:----:|----------------|
| 401856766 | TCU | fanduel | −7.5 | −112 | fallback | −17.62 | 17.62 | 19.82 | 0.695 | 0.504 | 0.190 | 0.315 | **10.12** | yes | 6.64 | no | unchecked | 0.015 | 3.0 | yes | stale, qb, disagree |
| 401864494 | SJSU | betmgm | +38.5 | −110 | fallback | +19.85 | 19.85 | 20.26 | 0.824 | 0.500 | 0.324 | 0.573 | **18.65** | yes | 6.64 | no | unchecked | 0.015 | 3.0 | yes | stale, qb, disagree |
| 401858202 | NC State | betmgm | +5.5 | −105 | fallback | +2.98 | 2.98 | 23.64 | 0.543 | 0.489 | 0.054 | 0.061 | 2.52 | yes | 6.64 | **yes** | staged_asof | 0.015 | 3.0 | yes | **stale only** |
| 401864577 | NDSU | betmgm | −7.0 | −105 | fallback | −14.51 | 14.51 | 17.81 | 0.671 | 0.489 | 0.182 | 0.310 | **7.51** ⚠ | yes | 6.64 | no | unchecked | 0.015 | 3.0 | yes | stale, qb, disagree |
| 401866408 | EMU | draftkings | −10.0 | −108 | fallback | −12.39 | 12.39 | 20.99 | 0.548 | 0.496 | 0.053 | 0.056 | 2.39 | yes | 6.64 | no | unchecked | 0.015 | 3.0 | yes | stale, qb |
| 401858201 | Stanford | betmgm | −4.0 | −105 | fallback | −11.15 | 11.15 | 21.30 | 0.639 | 0.489 | 0.150 | 0.248 | **7.15** ⚠ | yes | 6.64 | **yes** | staged_asof | 0.015 | 3.0 | yes | stale, disagree |
| 401864570 | NMSU | betmgm | +31.5 | −110 | fallback | +15.49 | 15.49 | 17.00 | 0.828 | 0.500 | 0.328 | 0.581 | **16.01** | yes | 6.64 | no | unchecked | 0.015 | 3.0 | yes | stale, qb, disagree |
| 401862693 | UNLV | draftkings | −4.0 | −110 | fallback | −10.90 | 10.90 | 20.08 | 0.637 | 0.500 | 0.137 | 0.215 | **6.90** ⚠ | yes | 6.64 | no | unchecked | 0.015 | 3.0 | yes | stale, qb |

⚠ = within 1.0 pt of `min_model_market_agreement` (7.0).

**Filters that pass on every constructed row:** `edge_too_small` (all edges ≥
0.025), `non_positive_ev`, `sigma_not_credible`, `kickoff_passed`, `no_snapshot`,
`line_quarantined`, `max_bets_per_week`, `max_weekly_exposure`, `max_team_exposure`.

### Per-game prose

**401856766 — North Carolina @ TCU.** The model makes TCU a 17.6-point home
favorite (strong lean, p_fav 0.86); the market lays only −7.5 at FanDuel. That
10.1-point residual drives a 19% edge and max stake, but the line is 6.6 hours
old, both QBs are unset, and the residual blows past the 7-point agreement bar.
Not a bet on any gate.

**401864494 — San José State @ USC.** Largest disagreement on the slate: model
USC −19.9 vs market −38.5 (+38.5 dog), residual 18.65, edge 32%. Preseason
priors vs a cupcake spread. Stale, no QB rows, and `model_market_disagree` all
fire. Directional read only.

**401858202 — NC State @ Virginia.** The tightest game: model Virginia by 3.0
(lean, wide σ 23.6); market has NC State +5.5. Residual 2.52 — agreement passes.
Both QBs now `starter`. **Only `STALE_INPUTS` blocks** at this `as_of`; if a
fresh pull landed before kickoff, this is the one marginal construct (edge 5.4%,
EV +0.06).

**401864577 — Jacksonville State @ NDSU.** Model NDSU −14.5 vs market −7.0;
residual **7.51** (⚠ flip zone). Jax State is explicit `unknown`. Stale + QB +
disagree.

**401866408 — Sacramento State @ EMU.** Model EMU −12.4 vs market −10.0;
residual 2.39, edge 5.3% at the public bar. Sac State `unknown`. Stale + QB.

**401858201 — Hawai'i @ Stanford.** Model Stanford −11.2 vs market −4.0;
residual **7.15** (⚠). Both QBs known. Stale + disagree — would stay off even
with fresh odds unless the market moves ~3 points toward the model.

**401864570 — NMSU @ Florida State.** Model FSU −15.5 vs market −31.5 (+31.5
dog); residual 16.01, edge 33%. Same preseason-spread story as SJSU/USC. No QB
rows. Stale + QB + disagree.

**401862693 — Memphis @ UNLV.** Model UNLV −10.9 vs market −4.0; residual
**6.90** (⚠). Memphis starter; UNLV latest row `unknown` (23:11Z supersedes
23:08Z starter). Stale + QB. Agreement would pass if UNLV were confirmed.

---

## Phase 2 — public overlay (reported honestly)

Edge-only “clear public bar” counts **ignore** stale, QB, and residual gates —
they answer “what would the unvalidated default select on shape alone?”

### 1 — Public bar clears

| Bar | Games clearing (edge only) | game_ids |
|-----|---------------------------|----------|
| `public_min_edge_sides = 0.045` (config default, unvalidated) | **8 / 8** | all |
| S3 recommendation `0.05` (not written) | **8 / 8** | all |

Smallest edges: Eastern Michigan (−10.0 line, **0.053**) and NC State (+5.5,
**0.054**) — both above 0.05 but not “near bar” in the 0.04–0.05 band.

### 2 — S3 week-1 calibration label (any game clearing either bar)

Every game that clears the public edge bar carries the same historical label —
**not a recommendation:**

| S3 week-1 slice (thr=0.05) | Claimed p | Realized ATS | Line CLV | n |
|----------------------------|----------:|-------------:|---------:|--:|
| Week 1 only | **0.618** | **0.348** | **−3.78** | **23** |

(S3 pooled weeks 2+ at 0.05: claimed 0.641 / realized 0.494 / −43.7u — not
attached to these tickets; week-1 split only per task.)

### 3 — Edge distribution

| Stat | Value |
|------|------:|
| min / max edge | 0.053 / 0.328 |
| Sorted | 0.053, 0.054, 0.137, 0.150, 0.182, 0.190, 0.324, 0.328 |
| Near public bar (0.04–0.05) | **0** games |
| Small but postable (0.05–0.15) | **3** — 401866408, 401858202, 401862693 |
| S3 miscalibration regime (≥ 0.15) | **5** — 401856766, 401864494, 401864577, 401858201, 401864570 |

Large residuals (≥ 7.0): five games. Three within 1.0 pt of the bar could flip
on a Thursday move: **401864577 (7.51), 401858201 (7.15), 401862693 (6.90)**.

### 4 — Verdict

**NOT MEASURED** — no public card; load-bearing gate is **`STALE_INPUTS`**
(all eight at this `as_of`). If staleness were waived without a fresh pull, six
games remain QB-blocked and five also fail `model_market_disagree`; only
401858202 survives all non-stale gates today, and S3 week-1 calibration
(0.348 ATS on n=23) argues against posting it anyway.

---

## Phase 3 — forecast-only read

All eight: `sigma_margin_credible=True`, coherence gate **not** suppressing
intervals (`coherence_suppressed=false`).

| game_id | matchup | μ_margin | σ_margin | 80% interval | tier | p_fav |
|--------:|---------|----------:|----------:|--------------|------|------:|
| 401856766 | NC @ TCU | +17.6 | 19.8 | [−10.6, +45.7] | strong_lean | 0.864 |
| 401864494 | SJSU @ USC | +19.9 | 20.3 | [−7.1, +48.3] | strong_lean | 0.882 |
| 401858202 | NC State @ Virginia | +3.0 | 23.6 | [−30.9, +30.9] | lean | 0.631 |
| 401864577 | Jax State @ NDSU | +14.5 | 17.8 | [−19.2, +45.1] | strong_lean | 0.853 |
| 401866408 | Sac State @ EMU | +12.4 | 21.0 | [−21.2, +45.6] | clear_lean | 0.797 |
| 401858201 | Hawai'i @ Stanford | +11.2 | 21.3 | [−18.4, +40.8] | clear_lean | 0.770 |
| 401864570 | NMSU @ FSU | +15.5 | 17.0 | [−7.6, +43.5] | strong_lean | 0.873 |
| 401862693 | Memphis @ UNLV | +10.9 | 20.1 | [−13.4, +38.4] | clear_lean | 0.775 |

μ_margin sign: positive = home favored. Intervals are wide on every game —
week-1 σ reflects preseason uncertainty, not a broken export.

### Reply bank

Generated at `docs/notes/_artifacts/social-s5-w0-read/replies_w1.md`.
Primary `FilterReason` per game follows `render.py` (first firing reason):
**all eight → `stale_inputs`** at this `as_of`. No `ridge_social.py` thread
generator run; posting decision left to operator.

---

## Verification

`make lint typecheck test` — see commit.

---

## Ambiguities

1. **Ladder rung `fallback` vs `odds_api_snapshot`:** 16:59Z snap is >5 min
   before 23:37Z `as_of` (post-2022 tolerance), so rung labels fallback even
   though the batch is the Aug-25 live pull — not Aug-12 history.
2. **Fresh pull required for a card:** staleness is binding now; operator must
   approve Odds API credits for a post-6h snap before any non-directional read.
