# S5-W0-FIX — two inert filters, then a fresh card

**Date:** 2026-08-25  
**Branch:** `social-s1-s2`  
**Export / R2 / push / publish / cron restart:** left OFF  

---

## 1 — Staleness (defect + fix)

### Quote — how `is_stale` was set (pre-fix)

```561:561:src/ncaa_quant/betting/provider.py
        is_stale = bool(row.get("is_stale", False))
```

That field is the **publish-level** stale stamp from predict/export
(`stale_ctx` / `merge_stale_onto_prediction_rows`), **not** the age of the
resolved odds snapshot. Aug 12 lines at an Aug 25 `as_of` therefore passed
`no_bet_on_stale` with zero `STALE_INPUTS` rejections.

### Fix

`is_stale = (as_of − snapshot.event_time) > odds_max_age`, where
`snapshot.event_time` is the ladder window’s latest `event_time`.

Added `betting.odds_max_age_hours` (default **6**, playbook “odds inputs
fresh < 6h”) to `BettingConfig` + `configs/betting.yaml`. Fallback-rung
resolutions older than that set `is_stale=True` → `STALE_INPUTS`.

Test: `test_thirteen_day_old_snapshot_rejects_stale` (failed on pre-fix
code; passes now).

---

## 2 — QB status (defect + fix)

### Why zero `QB_STATUS_UNKNOWN` on empty 2026 `qb_status`

Prior W0 run used `no_bet_on_qb_unknown=False`. A2 wiring then did:

```python
if betting.no_bet_on_qb_unknown:
    qb_known, qb_source = qb_status_known_for_game(...)
else:
    qb_known, qb_source = True, "check_disabled"  # ← defect
```

**Defect name:** `check_disabled` **hardcodes** `qb_status_known=True`, so a
missing staged row is reported as **known**. The §12 filter never sees
unknown when the operator follows the temporary flag-off workflow in
`docs/notes/social-s5.md`.

(`qb_status_known_for_game` itself already returned `False` for empty /
missing rows when the flag was on; the inert path was the hardcode.)

### Fix

Always call `qb_status_known_for_game`. When the flag is off, stamp
`qb_status_source="check_disabled"` but **keep** the real `qb_known` value.
The flag only gates `evaluate_filters`, never invents knownness.

Tests: `test_missing_qb_row_rejects_when_flag_on`,
`test_check_disabled_does_not_hardcode_qb_known`.

---

## 3 — Re-run the eight (Aug 12 snapshots, unchanged data)

`as_of=2026-08-25T10:00:00Z`, defaults (`no_bet_on_stale=true`,
`no_bet_on_qb_unknown=true`, `odds_max_age_hours=6`). Ladder: 8×
`odds_api_snapshot_fallback`.

| Metric | Value |
|--------|------:|
| Constructed | 8 |
| Accepted | **0** |
| Rejected | **8** |

Every game: `stale_inputs` + `qb_status_unknown` (four also
`model_market_disagree`). No accept on a 13-day-old line — fix took.

Artifact: `data/tmp/s5_w0_fix_eight_result.json`.

---

## 4 — Fresh live pull (APPROVED)

`run_odds_ingest` live path only (no historical endpoint).

| Field | Value |
|-------|------:|
| Raw | `data/raw/odds_api/2026-08-25/20260825T165911540113Z.json` |
| Rows fetched / written | 1596 / 1596 |
| Credits this call | **3** (`x-requests-last`) |
| Remaining | **43513** |
| Fresh `event_time` min=max | `2026-08-25T16:59:11.540113Z` |

Crosswalk for the eight (name maps from W0): **8 / 8 matched**
(TCU, Stanford, Virginia, UNLV, USC, Florida State, North Dakota State,
Eastern Michigan — including Bison / Hornets aliases).

---

## 5 — Real card (`as_of` = now)

`as_of ≈ 2026-08-25T17:00:21Z`. All eight on **`odds_api_snapshot`** (not
fallback). `is_stale=false`. All reject on **`qb_status_unknown`** (QB gate
until rows exist). Full table in `data/tmp/s5_w0_fix_fresh_card.json`.

| game_id | side | book | line | px | snap | p_model | p_mkt | edge | EV | reasons |
|--------:|------|------|-----:|---:|------|--------:|------:|-----:|---:|---------|
| 401856766 | TCU | fanduel | −7.5 | −112 | 16:59Z | 0.695 | 0.504 | 0.190 | 0.315 | qb, disagree |
| 401858201 | Stanford | betmgm | −4.0 | −105 | 16:59Z | 0.639 | 0.489 | 0.150 | 0.248 | qb, disagree |
| 401858202 | NC State | betmgm | +5.5 | −105 | 16:59Z | 0.543 | 0.489 | 0.054 | 0.061 | qb |
| 401862693 | UNLV | draftkings | −4.0 | −110 | 16:59Z | 0.637 | 0.500 | 0.137 | 0.215 | qb |
| 401864494 | San José State | betmgm | +38.5 | −110 | 16:59Z | 0.824 | 0.500 | 0.324 | 0.573 | qb, disagree |
| 401864570 | New Mexico State | betmgm | +31.5 | −110 | 16:59Z | 0.828 | 0.500 | 0.328 | 0.581 | qb, disagree |
| 401864577 | North Dakota State | betmgm | −7.0 | −105 | 16:59Z | 0.671 | 0.489 | 0.182 | 0.310 | qb, disagree |
| 401866408 | Eastern Michigan | draftkings | −10.0 | −108 | 16:59Z | 0.548 | 0.496 | 0.053 | 0.056 | qb |

### Sixteen teams + exact `set_qb_status` invocations

Replace `--status starter` with `starter|backup|unknown` by hand.

```text
uv run ncaa-quant roster set-qb --game 401856766 --team "North Carolina" --status starter
uv run ncaa-quant roster set-qb --game 401856766 --team "TCU" --status starter
uv run ncaa-quant roster set-qb --game 401858201 --team "Hawai'i" --status starter
uv run ncaa-quant roster set-qb --game 401858201 --team "Stanford" --status starter
uv run ncaa-quant roster set-qb --game 401858202 --team "NC State" --status starter
uv run ncaa-quant roster set-qb --game 401858202 --team "Virginia" --status starter
uv run ncaa-quant roster set-qb --game 401862693 --team "Memphis" --status starter
uv run ncaa-quant roster set-qb --game 401862693 --team "UNLV" --status starter
uv run ncaa-quant roster set-qb --game 401864494 --team "San José State" --status starter
uv run ncaa-quant roster set-qb --game 401864494 --team "USC" --status starter
uv run ncaa-quant roster set-qb --game 401864570 --team "New Mexico State" --status starter
uv run ncaa-quant roster set-qb --game 401864570 --team "Florida State" --status starter
uv run ncaa-quant roster set-qb --game 401864577 --team "Jacksonville State" --status starter
uv run ncaa-quant roster set-qb --game 401864577 --team "North Dakota State" --status starter
uv run ncaa-quant roster set-qb --game 401866408 --team "Sacramento State" --status starter
uv run ncaa-quant roster set-qb --game 401866408 --team "Eastern Michigan" --status starter
```

Team ids: NC 153 / TCU 2628; Hawaiʻi 62 / Stanford 24; NC State 152 /
Virginia 258; Memphis 235 / UNLV 2439; SJSU 23 / USC 30; NMSU 166 / FSU 52;
Jax St 55 / NDSU 2449; Sac St 16 / EMU 2199.

---

## 6 — Separate finding (report only; not fixed)

Live odds capture **is** scheduled: Prefect deployment
`ingest_odds/ingest_odds` on `pipeline.odds_ingest_cron`
(`0 0,4,8,12,16,20 * * *` UTC) — see `docs/notes/04.md` / `04a.md`.

**What stopped it:** the Prefect API/serve process is down
(`http://127.0.0.1:4200/api/health` unreachable this session). Raw archives
have a **312.6 h** gap from `2026-08-12T16:00:10Z` → first Aug 25 file
(`2026-08-25T16:39:00Z`, then this task’s `16:59:11Z` pull). Cron was not
restarted here.

---

## Verification

`make test`: **992 passed**, coverage 80.52%.

---

## S5-W0-CARD — preview on 16:59Z snapshot (2026-08-25)

**Export / R2 / push / publish / Odds API / cron:** left OFF.  
**as_of:** ~`2026-08-25T17:20Z` against staged snap `2026-08-25T16:59:11.540113Z`.

### 1 — Staged `qb_status` as-of now

`data/staged/qb_status` **does not exist**. `store.read("qb_status", season=2026)` → **0 rows**.

All sixteen teams: **NO_ROW** (no status, no event_time). None flagged
`AFTER_AS_OF` (no rows to be late). Nothing added, edited, or inferred.

| game_id | team | team_id | status | event_time | flag |
|--------:|------|--------:|--------|------------|------|
| 401856766 | North Carolina | 153 | — | — | NO_ROW |
| 401856766 | TCU | 2628 | — | — | NO_ROW |
| 401858201 | Hawai'i | 62 | — | — | NO_ROW |
| 401858201 | Stanford | 24 | — | — | NO_ROW |
| 401858202 | NC State | 152 | — | — | NO_ROW |
| 401858202 | Virginia | 258 | — | — | NO_ROW |
| 401862693 | Memphis | 235 | — | — | NO_ROW |
| 401862693 | UNLV | 2439 | — | — | NO_ROW |
| 401864494 | San José State | 23 | — | — | NO_ROW |
| 401864494 | USC | 30 | — | — | NO_ROW |
| 401864570 | New Mexico State | 166 | — | — | NO_ROW |
| 401864570 | Florida State | 52 | — | — | NO_ROW |
| 401864577 | Jacksonville State | 55 | — | — | NO_ROW |
| 401864577 | North Dakota State | 2449 | — | — | NO_ROW |
| 401866408 | Sacramento State | 16 | — | — | NO_ROW |
| 401866408 | Eastern Michigan | 2199 | — | — | NO_ROW |

### 2 — Provider table (defaults; QB gate fires)

Ladder label is `odds_api_snapshot_fallback` because `as_of` is ~20 min past
the 16:59Z snap (post-2022 tolerance 5 min); window is still that snap.
`is_stale=false` (age ≪ 6h). All eight: `qb_status_known=false`.

| game_id | side | book | line | px | event_time | rung | p_model | p_mkt | edge | EV | stake | u | reasons |
|--------:|------|------|-----:|---:|------------|------|--------:|------:|-----:|---:|------:|--:|---------|
| 401856766 | TCU | fanduel | −7.5 | −112 | 16:59Z | fallback | 0.695 | 0.504 | 0.190 | 0.315 | 0.015 | 3.0 | qb, disagree |
| 401858201 | Stanford | betmgm | −4.0 | −105 | 16:59Z | fallback | 0.639 | 0.489 | 0.150 | 0.248 | 0.015 | 3.0 | qb, disagree |
| 401858202 | NC State | betmgm | +5.5 | −105 | 16:59Z | fallback | 0.543 | 0.489 | 0.054 | 0.061 | 0.015 | 3.0 | qb |
| 401862693 | UNLV | draftkings | −4.0 | −110 | 16:59Z | fallback | 0.637 | 0.500 | 0.137 | 0.215 | 0.015 | 3.0 | qb |
| 401864494 | San José State | betmgm | +38.5 | −110 | 16:59Z | fallback | 0.824 | 0.500 | 0.324 | 0.573 | 0.015 | 3.0 | qb, disagree |
| 401864570 | New Mexico State | betmgm | +31.5 | −110 | 16:59Z | fallback | 0.828 | 0.500 | 0.328 | 0.581 | 0.015 | 3.0 | qb, disagree |
| 401864577 | North Dakota State | betmgm | −7.0 | −105 | 16:59Z | fallback | 0.671 | 0.489 | 0.182 | 0.310 | 0.015 | 3.0 | qb, disagree |
| 401866408 | Eastern Michigan | draftkings | −10.0 | −108 | 16:59Z | fallback | 0.548 | 0.496 | 0.053 | 0.056 | 0.015 | 3.0 | qb |

**Private accept: 0 / 8.**

#### Prior Aug-12 `model_market_disagree` four — verdict on current lines

Aug-12 disagree set: TCU, San José State, North Dakota State, New Mexico State.

| game | Aug-12 | Today residual | Today disagree? | Verdict change? |
|------|--------|---------------:|:---------------:|:----------------|
| TCU | disagree | 10.12 | yes (>7) | **No** — still disagree |
| San José State | disagree | 18.65 | yes | **No** — still disagree |
| North Dakota State | disagree | 7.51 | yes | **No** — still disagree |
| New Mexico State | disagree | 16.01 | yes | **No** — still disagree |

None of the four flipped. (Separately, Stanford is now also over the residual
bar at 7.15 — was an Aug-12 accept — but QB already rejects everything.)

### 3 — Public bar (`public_min_edge_sides=0.045`)

| Bucket | Count | Games |
|--------|------:|-------|
| Clear public bar | **0** | — |
| Between private and public | **0** | — |
| Private accept | **0** | — |

### 4 — Generate

No private accepts → No-Bet path via `ridge_social.py`.

Artifacts: `data/tmp/s5_w0_card/social/thread_w1.md`, `replies_w1.md`.

#### `thread_w1.md` (full)

```
--- POST 1/1 (241 chars) ---
Week 1: the model found ZERO bets that clear our bar.

Yes, really. The market priced this slate tight.

Most accounts would post 10 picks anyway. We'd rather be flat than wrong on purpose. Forecasts for every game: https://ridge.example.com
```

#### Reply bank (all eight)

Every game lands on the forecast-only branch with primary plain reason
`qb_status_unknown` (“QB situation is unclear…”). Full text in
`data/tmp/s5_w0_card/social/replies_w1.md`.

### 5 — Sanity check (report only; not fixed)

| Check | Result |
|-------|--------|
| Post over 280 chars | **None** (No-Bet post 241) |
| Total rendered with a sign (`+48.5`) | **N/A** — side-only card; no totals in thread. Reply-bank margin intervals use signed `fmt_line` (expected for margins). |
| Duplicated why-line across two bets | **N/A** — no bet posts |
| Season-record placeholder as `0-0, +0.00 avg CLV` | **Not rendered** — No-Bet template omits the hook record/CLV line. (With bets + `record.json` `{season_record:"0-0", season_clv:0.0}` the hook would show `0-0, +0.00 avg CLV`.) |

---

## S5-W0-DIAG — why the edges are this large + No-Bet copy fix

**Date:** 2026-08-25  
**Branch:** `social-s1-s2`  
**Scope:** read-only diagnostics except `render.py` (+ CLI/tests wiring).  
**Forbidden left alone:** thresholds, Odds API, publish, R2, provider math, merge to main.

Artifact: `data/tmp/s5_w0_diag_report.json` (from `data/tmp/s5_w0_diag.py`).

### 1 — Three-game decomposition

Card lines from S5-W0-CARD (`s5_w0_card_result.json`). Cover path = production MC
(`sample_joint` + `spread_cover_probs` + `two_way_side_prob`), empty kernel,
`n_draws=20_000`. Ratings = latest **2025 weekly** filter-history state (no 2026
updates: **0 scored 2026 games** → week-1 inputs are **preseason priors /
carry-forward**, not updated from 2026 results).

#### New Mexico State @ Florida State (`401864570`)

| Field | Value |
|-------|------:|
| μ_margin (home) | **15.488** |
| σ_margin | **16.998** |
| σ_margin_credible | **True** |
| Shopped | NMSU **+31.5** @ BetMGM −110 (home line −31.5) |
| Model implied | FSU **−15.49** / NMSU **+15.49** |
| \|model − market\| (home) | **16.01** |
| Card p_model / p_mkt / edge | 0.828 / 0.500 / **0.328** |

Rating inputs (EOY 2025 weekly):

| Team | off_epa | def_epa | pace | sd_off |
|------|--------:|--------:|-----:|-------:|
| NMSU (166) | −0.155 | −0.063 | −0.092 | 0.081 |
| FSU (52) | +0.023 | +0.006 | −0.176 | 0.083 |

Both FBS 2023–2026. Each has **3 FCS opponents** in 2023–2025 history (typical
week-1 cupcake), not FCS themselves.

**p_model at +31.5 (away), step-by-step:**

1. Draw \(M \sim \mathcal{N}(15.488,\,16.998^2)\) (home margin).
2. Home line \(L = -31.5\). Away covers when \(M + L < 0\) i.e. \(M < 31.5\).
3. MC: \(p_{\text{away cover}} \approx 0.828\), \(p_{\text{push}} \approx 0\) →
   `two_way_side_prob` ≈ **0.828**.
4. Two-way −110/−110 → \(p_{\text{mkt}} = 0.5\); edge = 0.828 − 0.5 = **0.328**.

Intuition: market prices a **31.5-pt** FSU blowout; model’s prior only has FSU
~**15.5** better than NMSU (FSU’s 2025 state is near league-mean). Covering +31.5
is easy under that μ.

#### San José State @ USC (`401864494`)

| Field | Value |
|-------|------:|
| μ_margin | **19.854** |
| σ_margin | **20.257** |
| σ_credible | **True** |
| Shopped | SJSU **+38.5** @ BetMGM −110 |
| Model implied | USC **−19.85** / SJSU **+19.85** |
| Residual (home) | **18.65** |
| Card p / edge | 0.824 / **0.324** |

| Team | off_epa | def_epa | pace | FCS hist |
|------|--------:|--------:|-----:|---------:|
| SJSU (23) | −0.155 | −0.076 | −0.014 | 3 FCS opps |
| USC (30) | +0.098 | +0.023 | −0.102 | **0** |

Same story: market −38.5 vs model −19.9 → huge ATS prob on the dog.

#### Memphis @ UNLV (`401862693`)

| Field | Value |
|-------|------:|
| μ_margin | **10.896** |
| σ_margin | **20.078** |
| σ_credible | **True** |
| Shopped | UNLV **−4.0** @ DK −110 |
| Model implied | UNLV **−10.90** |
| Residual | **6.90** (< 7.0 disagree bar) |
| Card p / edge | 0.637 / **0.137** |

| Team | off_epa | def_epa | pace | FCS hist |
|------|--------:|--------:|-----:|---------:|
| Memphis (235) | +0.047 | −0.077 | +0.029 | 3 |
| UNLV (2439) | +0.183 | −0.083 | −0.213 | 3 |

UNLV’s prior is much stronger than Memphis; market only lays −4. Cover path:
\(P(M + (-4) > 0)\) with \(M\sim\mathcal{N}(10.90,\,20.08^2)\) ≈ **0.637**.

#### Verdict (plain)

**Not a cover-prob / de-vig defect.** Arithmetic matches the production MC path
and the card. These edges are the model **genuinely disagreeing with the market
on a preseason slate** whose ratings are still 2025-end priors. Large cupcake
spreads (+31.5 / +38.5) amplify any μ shortfall into 30%+ ATS edges. Separate
data smell on the *other* early games: Sac State + NDSU are labeled **fbs** in
2026 teams after years as **fcs** (not these three, but same slate).

### 2 — Backtest candidate-edge distribution (2021–2024)

Provider replay on champion week parquets
(`task23_fundamental_reduced_v3`), `n_draws=2000`, constructed (unblocked)
edges only. `no_bet_on_qb_unknown=false` so construction isn’t QB-starved.

| Slice | n | median | mean | p90 | frac ≥0.15 | frac ≥0.30 |
|-------|--:|-------:|-----:|----:|-----------:|-----------:|
| Overall | 2928 | 0.108 | 0.123 | 0.243 | 0.334 | 0.040 |
| **Week 1** | 177 | **0.159** | **0.168** | **0.319** | **0.525** | **0.141** |
| Weeks 2+ | 2751 | 0.107 | 0.120 | 0.237 | 0.321 | 0.033 |

Week-1 median is **~48% higher** than weeks 2+ (0.159 / 0.107); share of
edges ≥0.30 is **~4.2×** weeks 2+ (14.1% vs 3.3%). That is the number that
decides whether a week-1 card is ever postable without a preseason guard:
**week-1 edges are systematically larger**, not a one-off 2026 glitch.

### 3 — Stake concentration

All eight W0-CARD candidates: `stake_fraction=0.015`, **3.0u**, capped by
`max_stake_pct`.

Saturation under quarter-Kelly at −110: full Kelly ≥ 0.06 ⇒
\(p^\* \approx 0.552\) ⇒ edge vs 0.50 ≈ **0.052**. So any ~5%+ edge at −110
already hits the 1.5% / 3u cap.

Backtest constructed candidates: **2206 / 2928 (75.3%)** hit the stake cap.
Saturating-edge median ≈ 0.139 (floor ≈ 0.052).

### 4 — No-Bet template fix (code)

**Defect:** `render_thread` hard-coded “ZERO bets… market priced this slate
tight” — a *why* claim the renderer cannot know.

**Fix:** `dominant_rejection_reason(rejected)` (mode of each row’s primary
`FilterReason`) → `render_no_bet_post` mid-copy per reason. QB unknown, stale,
no-edge, disagree, etc. read differently. Fallback never invents a market
claim. `ridge_social.py` passes `rejected` into `render_thread`.

Golden / parametrized tests in `test_social_render.py` for
`qb_status_unknown`, `stale_inputs`, `edge_too_small`, `non_positive_ev`,
`model_market_disagree`, `no_snapshot`, plus primary-reason dominance.

Playbook T6 still shows the old “priced tight” wording — stale relative to
code; not edited in this task (render-only scope).

### 5 — `min_model_market_agreement = 7.0` as FCS/preseason guard (report only)

**Partially doing that job, badly.**

- It *does* catch the worst μ–line residuals (NMSU 16pt, SJSU 18.6pt →
  `model_market_disagree`).
- It does **not** encode FCS / preseason structure. UNLV’s 6.9pt residual
  clears 7.0 while still posting a 13.7% edge and max stake. Week-1 edges are
  fat even after ordinary FBS–FBS priors.
- 2026 **FCS→FBS mislabels** (Sac State, NDSU) bypass any classification
  logic that trusts the current season’s `teams.classification`.

**Principled version (not implemented here):**

1. **Preseason / week-1 regime:** separate residual and/or min-edge bars
   (or a hard max public edge) until each team has ≥N FBS observations in
   the current season.
2. **FCS / classification gate:** refuse or pool when either side is
   non-FBS on a multi-season class history (not the 2026 label alone);
   keep `FCS_TIER` pooling honest.
3. **Keep `min_model_market_agreement`** as a residual sanity check, not as
   a stand-in for (1)–(2).

---

## Verification (this diag)

`make test`: **998 passed**, 1 deselected; language-ratchet pin held at
405/290/86 after rewording the No-Bet mid away from a `\bplay\b` hit.

---

## S5-QB-VERIFY — one row round-trips (2026-08-25)

**Date:** 2026-08-25  
**Branch:** `social-s1-s2`  
**Scope:** read-only. No writes, Odds API, publish, or config changes.

Hand-written row (via `ncaa-quant roster set-qb`):

| field | value |
|-------|-------|
| game_id | 401858202 |
| team_id | 258 (Virginia) |
| status | starter |
| event_time | 2026-08-25T22:58:49.130903Z |

### 1 — Where did it land

**CLI write call site** — `ParquetStore(cfg.paths.staged_dir)` →
`set_qb_status` → `store.write_partition("qb_status", …, {"season": season},
mode="append")`:

```474:484:src/ncaa_quant/cli.py
    cfg = load_config()
    with ParquetStore(cfg.paths.staged_dir) as store:
        games = store.read("games", filters={"game_id": game})
        if games.empty:
            typer.echo(f"game_id {game} not found in staged games")
            raise typer.Exit(code=2)
        season = int(games.iloc[0]["season"])
        teams = store.read("teams", filters={"season": season})
        try:
            team_id = resolve_team_id(team, teams, season=season)
            row = set_qb_status(store, game_id=game, team_id=team_id, status=status)
```

```398:403:src/ncaa_quant/features/builders/roster.py
    store.write_partition(
        QB_STATUS_TABLE,
        row,
        {"season": season},
        mode="append",
    )
```

**On-disk path:** `data/staged/qb_status/season=2026/part.parquet`
(`PathsConfig.staged_dir` = `data/staged`; `qb_status` is a reference table
partitioned by `season` only).

**Provider as-of read call site** — same store root, same table name:

```543:546:src/ncaa_quant/betting/provider.py
    try:
        qb_frame = store.read("qb_status", filters={"season": int(season)})
    except Exception:  # noqa: BLE001
        qb_frame = pd.DataFrame()
```

**Same store?** **Yes.** Both paths use `ParquetStore(load_config().paths.staged_dir)`
→ `data/staged`, table `qb_status`, season hive partition.

### 2 — Read back the way the provider does

`as_of ≈ 2026-08-25T23:00:52Z` (UTC now). Game 401858202: Virginia home
(`team_id=258`), NC State away (`team_id=152`).

Per-team resolution mirrors `qb_status_known_for_game._team_ok` (filter
`game_id`, `event_time <= as_of`, latest row per `team_id`, known iff
`status != "unknown"`):

| team | team_id | qb_status_known | status | source |
|------|--------:|:---------------:|--------|--------|
| Virginia | 258 | **True** | starter | staged_asof |
| NC State | 152 | **False** | — | NO_ROW |

Game-level (provider API):

```565:571:src/ncaa_quant/betting/provider.py
        qb_known, qb_source = qb_status_known_for_game(
            qb_frame,
            game_id=gid_int,
            home_team_id=home_id,
            away_team_id=away_id,
            as_of=as_of_utc,
        )
```

→ `qb_status_known=False`, `qb_status_source=unchecked` (both teams must
pass; NC State has no row).

**Verdict:** Virginia **round-trips**. Not wrong store, not join-key mismatch,
not as-of ordering, not stale partition. The single row is visible at
`data/staged/qb_status/season=2026/part.parquet` (n=1 after write).

### 3 — Join key

**CLI path:** `--team "Virginia"` → `resolve_team_id` matches
`teams.school` case-insensitively (or accepts a numeric string) → writes
**integer `team_id`** into `qb_status`:

```438:455:src/ncaa_quant/features/builders/roster.py
def resolve_team_id(
    team: str,
    teams: pd.DataFrame,
    *,
    season: int | None = None,
) -> int:
    """Resolve ``team`` as numeric id or school name within ``teams``."""
    stripped = team.strip()
    if stripped.isdigit():
        return int(stripped)
    frame = teams
    if season is not None and "season" in frame.columns:
        frame = frame.loc[frame["season"] == season]
    matches = frame.loc[frame["school"].astype(str).str.casefold() == stripped.casefold()]
    if matches.empty:
        msg = f"team {team!r} not found in staged teams"
        raise ValueError(msg)
    return int(matches.iloc[0]["team_id"])
```

**Provider path:** `_game_teams` loads `home_team_id` / `away_team_id` from
staged `games` (not from the `--team` string). `qb_status_known_for_game`
then filters `qb_frame["team_id"].astype(int) == tid`:

```316:321:src/ncaa_quant/betting/provider.py
    def _team_ok(tid: int) -> bool:
        sub = work.loc[work["team_id"].astype(int) == int(tid)]
        if sub.empty:
            return False
        latest = sub.sort_values("event_time").iloc[-1]
        return str(latest["status"]).casefold() != "unknown"
```

**Plain answer:** the provider does **not** join on team name. Name → id
happens only at CLI write time via `resolve_team_id`; the staged row and
provider lookup both key on **`(game_id, team_id)`**.

### 4 — Odds snapshot age

| field | value |
|-------|------:|
| `as_of` | 2026-08-25T23:00:52.617988Z |
| newest 2026 `odds_snapshots.event_time` | 2026-08-25T16:59:11.540113Z |
| age | **6.03 h** |
| `odds_max_age_hours` | **6.0** |
| game 401858202 snap age | 6.03 h (same batch) |

**Staleness right now:** **yes** — `(as_of − snapshot.event_time) > 6h` by
~1.7 minutes. A provider run at this instant would set `is_stale=True` →
`STALE_INPUTS` (in addition to game-level `QB_STATUS_UNKNOWN` because NC
State has no row).
