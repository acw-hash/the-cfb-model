# S7-XWALK — recover 34 unmatched Odds API events

**Date:** 2026-09-01  
**Branch:** `social-s1-s2`  
**Raw archive:** `data/raw/odds_api/2026-09-01/20260901T203456940488Z.json`  
**Odds API / R2 / publish / merge to `main`:** OFF  
**2025 lockbox:** untouched  

**Artifacts:** `docs/notes/_artifacts/social-s7-xwalk/`  
**Scripts:** `scripts/_s7_xwalk_phase0.py` (read-only diagnostic), `scripts/_s7_xwalk.py` (replay + gate probe)

---

## Phase 0 — read-only (STOP after)

### 1 — The matcher

**Implementation:** `src/ncaa_quant/ingestion/odds_api.py`

| Piece | Location |
|-------|----------|
| Team normalization | `ncaa_quant.ingestion.teams.normalize_team_name` |
| Event extraction | `extract_odds_events` |
| CFBD schedule load | `load_cfbd_schedule` |
| Match logic | `match_odds_events_to_cfbd` |
| Ingest orchestration | `_enrich_frame_via_crosswalk` |
| Persist crosswalk | `write_odds_cfbd_crosswalk` |
| Freeze `game_id` into snapshots | `normalize_odds_payload(..., event_game_ids=resolve_event_game_ids(crosswalk))` |

**Call site (live ingest):**

```1751:1760:src/ncaa_quant/ingestion/odds_api.py
        with ParquetStore(staged_dir) as store:
            frame = _enrich_frame_via_crosswalk(
                store,
                body,
                names,
                captured_at=captured,
                ingested_at=ingested,
                snapshot_source="live",
                decision_point=None,
                event_time=captured,
            )
```

**Matching strategy (ladder — no fuzzy matching):**

1. **Alias table** — `configs/team_names.yaml` → `odds_api:` map; keys matched case-insensitively after whitespace collapse (`load_team_name_map` / `normalize_team_name`).
2. **Mascot suffix strip** — deterministic regex in `teams._MASCOT_SUFFIX_RE` for unmapped FBS-style names (e.g. `Michigan Wolverines` → `Michigan`).
3. **Exact pair + kickoff** — `match_odds_events_to_cfbd` requires normalized `home_team`/`away_team` to match CFBD schedule within **±36 h** (`KICKOFF_MATCH_TOLERANCE`).
4. **Swap fallback** — if ordered pair misses, try home↔away swapped within the same window (`swap_detected=True`).
5. **Prior `odds_event_id` reuse** — postpone resilience; same event id keeps prior `game_id`.
6. **Ambiguity → quarantine** — multiple schedule hits within tolerance → `match_status='quarantined'`, `game_id` null (never guessed).

**Alias table:** `configs/team_names.yaml` — YAML `odds_api:` dict, **152 rows** before this task (FBS-focused + prior patches). Maintained manually; replay preflight uses `preview_crosswalk_game_key_regression` to refuse re-keys.

---

### 2 — The 34, in full

Source: raw archive replayed against **pre-patch** team map. Full machine-readable list: `docs/notes/_artifacts/social-s7-xwalk/phase0_unmatched.json`.

| # | away (Odds API) | home (Odds API) | commence (UTC) | intended `game_id` | CFBD matchup |
|--:|-----------------|-----------------|----------------|-------------------:|--------------|
| 1 | Albany | Buffalo Bulls | 2026-09-03T23:00Z | 401866409 | UAlbany @ Buffalo |
| 2 | Merrimack Warriors | Delaware Blue Hens | 2026-09-03T23:00Z | 401864424 | Merrimack @ Delaware |
| 3 | West Georgia Wolves | Kennesaw State Owls | 2026-09-03T23:00Z | 401864425 | West Georgia @ Kennesaw State |
| 4 | Arkansas Pine Bluff Golden Lions | Missouri Tigers | 2026-09-04T00:00Z | 401856663 | Arkansas-Pine Bluff @ Missouri |
| 5 | Idaho Vandals | Utah Utes | 2026-09-04T01:00Z | 401856768 | Idaho @ Utah |
| 6 | Indiana State Sycamores | Purdue Boilermakers | 2026-09-04T23:00Z | 401858435 | Indiana State @ Purdue |
| 7 | LIU Sharks | Kansas Jayhawks | 2026-09-05T00:00Z | 401856769 | Long Island University @ Kansas |
| 8 | Tarleton State Texans | Bowling Green Falcons | 2026-09-05T16:00Z | 401866410 | Tarleton State @ Bowling Green |
| 9 | Lafayette Leopards | UConn Huskies | 2026-09-05T16:00Z | 401861962 | Lafayette @ UConn |
| 10 | Youngstown St Penguins | Kentucky Wildcats | 2026-09-05T17:00Z | 401856659 | Youngstown State @ Kentucky |
| 11 | Maine Black Bears | Appalachian State Mountaineers | 2026-09-05T19:30Z | 401866627 | Maine @ App State |
| 12 | Citadel Bulldogs | Charlotte 49ers | 2026-09-05T19:30Z | 401862694 | The Citadel @ Charlotte |
| 13 | Furman Paladins | Tennessee Volunteers | 2026-09-05T19:30Z | 401856666 | Furman @ Tennessee |
| 14 | UT Rio Grande Valley Vaqueros | UTSA Roadrunners | 2026-09-05T19:30Z | 401862700 | UT Rio Grande Valley @ UTSA |
| 15 | North Alabama Lions | Arkansas Razorbacks | 2026-09-05T20:15Z | 401856635 | North Alabama @ Arkansas |
| 16 | Alcorn State Braves | Southern Mississippi Golden Eagles | 2026-09-05T21:00Z | 401868356 | Alcorn State @ Southern Miss |
| 17 | Southeastern Louisiana Lions | South Alabama Jaguars | 2026-09-05T23:00Z | 401868316 | SE Louisiana @ South Alabama |
| 18 | Missouri State Bears | Texas A&M Aggies | 2026-09-05T23:00Z | 401856668 | Missouri State @ Texas A&M |
| 19 | Murray State Racers | Middle Tennessee Blue Raiders | 2026-09-05T23:00Z | 401867866 | Murray State @ Middle Tennessee |
| 20 | Nicholls State Colonels | Kansas State Wildcats | 2026-09-05T23:00Z | 401856771 | Nicholls @ Kansas State |
| 21 | Austin Peay Governors | Vanderbilt Commodores | 2026-09-05T23:00Z | 401856669 | Austin Peay @ Vanderbilt |
| 22 | Houston Baptist Huskies | Rice Owls | 2026-09-05T23:00Z | 401862697 | Houston Christian @ Rice |
| 23 | Eastern Kentucky Colonels | Jacksonville State Gamecocks | 2026-09-05T23:00Z | 401868140 | Eastern Kentucky @ Jacksonville State |
| 24 | Charleston Southern Buccaneers | Georgia Southern Eagles | 2026-09-05T23:00Z | 401868170 | Charleston Southern @ Georgia Southern |
| 25 | Idaho State Bengals | Utah State Aggies | 2026-09-05T23:00Z | 401860880 | Idaho State @ Utah State |
| 26 | Utah Tech Trailblazers | BYU Cougars | 2026-09-05T23:30Z | 401856775 | Utah Tech @ BYU |
| 27 | Northwestern State Demons | Louisiana Tech Bulldogs | 2026-09-05T23:30Z | 401869129 | Northwestern State @ Louisiana Tech |
| 28 | VMI Keydets | Virginia Tech Hokies | 2026-09-05T23:30Z | 401858211 | VMI @ Virginia Tech |
| 29 | South Dakota State Jackrabbits | Northwestern Wildcats | 2026-09-06T00:00Z | 401858431 | South Dakota State @ Northwestern |
| 30 | Mercyhurst Lakers | New Mexico State Aggies | 2026-09-06T01:00Z | 401870790 | Mercyhurst @ New Mexico State |
| 31 | Northern Arizona Lumberjacks | Arizona Wildcats | 2026-09-06T01:30Z | 401856773 | Northern Arizona @ Arizona |
| 32 | Portland State Vikings | San Diego State Aztecs | 2026-09-06T01:30Z | 401860879 | Portland State @ San Diego State |
| 33 | Morgan State Bears | Arizona State Sun Devils | 2026-09-06T02:00Z | 401856774 | Morgan State @ Arizona State |
| 34 | Mississippi Valley State Delta Devils | Sacramento State Hornets | 2026-09-06T02:00Z | 401866411 | Mississippi Valley State @ Sacramento State |

All 34 confidently identified. Row 2 (Merrimack @ Delaware) failed only because **both** sides lacked aliases (`Delaware Blue Hens`, `Merrimack Warriors`); CFBD game `401864424` is on the schedule at the same kickoff.

**Not in the 34:** Massachusetts @ Rutgers (`401858423`) — crosswalk matched; no spread lines on this pull (market gap).

---

### 3 — Why each failed (classification counts)

| Class | Count | Examples |
|-------|------:|---------|
| **mascot_appended** | 28 | `Indiana State Sycamores`, `LIU Sharks`, `Furman Paladins` — FCS mascots outside `_MASCOT_SUFFIX_RE` |
| **stale_school_name** | 2 | `Albany` (CFBD: UAlbany), `Houston Baptist` (renamed Houston Christian 2022) |
| **article_present/absent** | 1 | `Citadel` (CFBD: The Citadel) |
| **abbreviation** | 2 | `LIU Sharks`, `Youngstown St Penguins` |
| **punctuation** | 1 | `Arkansas Pine Bluff` vs CFBD `Arkansas-Pine Bluff` |
| **genuinely_absent** | 0 | — |
| **unknown** | 0 | — |

Root cause: **alias-table gap for FCS / renamed schools**, not missing Odds API markets. Same defect class as the prior Bison / Hornets failure (Task 5B-PATCH).

---

### 4 — Blast radius on historical data

| Question | Answer |
|----------|--------|
| Ingest-time or read-time? | **Ingest-time.** `game_id` is resolved in `_enrich_frame_via_crosswalk` and **frozen** into `odds_snapshots` at write. `build_candidates_from_odds` joins on `snapshots.game_id` — no read-time crosswalk. |
| Would historical archive replay add lines? | **Yes.** `replay_historical_from_archives` wipes and rebuilds historical partitions. Staged crosswalk shows **1,120 unmatched events** (2021–2026); alias fixes would attach **~233 unique `game_id`s** that currently have no matched spread rows. |
| Impact on S3/S5 **314-ticket** population? | **0 tickets gain or change a line** (exact-pair simulation with proposed aliases against `p0_graded_314.parquet`). `preview_crosswalk_game_key_regression` → **0 re-keys** on prior matched events. |
| Invalidates 314 comparability? | **No** — frozen ticket population unaffected. Broader backtest pools *would* change if historical archives were replayed without operator sign-off. |

**Operator decision:** Phase 1 proceeds on **tonight's archive only** (`replay_live_from_archive`). Historical replay deferred.

---

### 5 — False-match risk

| Risk | Present? | Mitigation |
|------|----------|------------|
| Fuzzy string match attaching wrong team | **No** — matching is exact normalized string + kickoff window only. |
| Ambiguous kickoff window | **Partial** — multiple hits → `quarantined` (0 quarantined rows in staged data). |
| Home/away swap attaching wrong game | **Low** — swap only when ordered pair misses; requires both teams + kickoff hit. Prior 5B-PATCH audited swap residuals. |
| Wrong `game_id` on matched row | **Possible in theory** if alias maps two schools to same canonical name — **no such alias added**. |
| Detection today | Audit only — `swap_detected` computed but **not persisted**; no ingest guard existed pre-S7. |
| Detection post-S7 | **`assert_crosswalk_teams_match_schedule`** — raises `CrosswalkTeamMismatchError` at ingest if matched teams ∉ CFBD game (swap allowed). Logged metric: `odds_crosswalk_match_rate`. |

Current staged matched rows (2024–2025): **0 team mismatches** on audit against CFBD schedule.

---

## Phase 1 — fix + replay (tonight's archive only)

### 1 — Resolution path

- **37 alias entries** appended to `configs/team_names.yaml` under `# S7-XWALK` (no fuzzy matching).
- **`assert_crosswalk_teams_match_schedule`** in `odds_api.py` — fails ingest on team mismatch.
- **`_log_crosswalk_match_rate`** — structured log on every ingest/replay.
- **`replay_live_from_archive`** — zero-API re-crosswalk of one live JSON archive.

### 2 — Per-alias verification evidence

Each alias verified: normalized name + opponent + kickoff ±36 h → unique CFBD `game_id` on 2026 week-1 schedule.

| Alias key | Canonical | `game_id` | Evidence (CFBD matchup @ kickoff UTC) |
|-----------|-----------|----------:|---------------------------------------|
| Albany | UAlbany | 401866409 | UAlbany @ Buffalo 2026-09-03T23:00Z |
| Merrimack Warriors | Merrimack | 401864424 | Merrimack @ Delaware 2026-09-03T23:00Z |
| Delaware Blue Hens | Delaware | 401864424 | same |
| West Georgia Wolves | West Georgia | 401864425 | West Georgia @ Kennesaw State 2026-09-03T23:00Z |
| Arkansas Pine Bluff Golden Lions | Arkansas-Pine Bluff | 401856663 | Arkansas-Pine Bluff @ Missouri 2026-09-04T00:00Z |
| Idaho Vandals | Idaho | 401856768 | Idaho @ Utah 2026-09-04T01:00Z |
| Indiana State Sycamores | Indiana State | 401858435 | Indiana State @ Purdue 2026-09-04T23:00Z |
| LIU Sharks | Long Island University | 401856769 | LIU @ Kansas 2026-09-05T00:00Z |
| Tarleton State Texans | Tarleton State | 401866410 | Tarleton State @ Bowling Green 2026-09-05T16:00Z |
| Lafayette Leopards | Lafayette | 401861962 | Lafayette @ UConn 2026-09-05T16:00Z |
| Youngstown St Penguins | Youngstown State | 401856659 | Youngstown State @ Kentucky 2026-09-05T17:00Z |
| Maine Black Bears | Maine | 401866627 | Maine @ App State 2026-09-05T19:30Z |
| Citadel / Citadel Bulldogs | The Citadel | 401862694 | The Citadel @ Charlotte 2026-09-05T19:30Z |
| Furman Paladins | Furman | 401856666 | Furman @ Tennessee 2026-09-05T19:30Z |
| UT Rio Grande Valley Vaqueros | UT Rio Grande Valley | 401862700 | UTRGV @ UTSA 2026-09-05T19:30Z |
| North Alabama Lions | North Alabama | 401856635 | North Alabama @ Arkansas 2026-09-05T20:15Z |
| Alcorn State Braves | Alcorn State | 401868356 | Alcorn State @ Southern Miss 2026-09-05T21:00Z |
| Southeastern Louisiana Lions | SE Louisiana | 401868316 | SE Louisiana @ South Alabama 2026-09-05T23:00Z |
| Missouri State Bears | Missouri State | 401856668 | Missouri State @ Texas A&M 2026-09-05T23:00Z |
| Murray State Racers | Murray State | 401867866 | Murray State @ Middle Tennessee 2026-09-05T23:00Z |
| Nicholls State Colonels | Nicholls | 401856771 | Nicholls @ Kansas State 2026-09-05T23:00Z |
| Austin Peay Governors | Austin Peay | 401856669 | Austin Peay @ Vanderbilt 2026-09-05T23:00Z |
| Houston Baptist / Houston Baptist Huskies | Houston Christian | 401862697 | Houston Christian @ Rice 2026-09-05T23:00Z |
| Eastern Kentucky Colonels | Eastern Kentucky | 401868140 | Eastern Kentucky @ Jacksonville State 2026-09-05T23:00Z |
| Charleston Southern Buccaneers | Charleston Southern | 401868170 | Charleston Southern @ Georgia Southern 2026-09-05T23:00Z |
| Idaho State Bengals | Idaho State | 401860880 | Idaho State @ Utah State 2026-09-05T23:00Z |
| Utah Tech Trailblazers | Utah Tech | 401856775 | Utah Tech @ BYU 2026-09-05T23:30Z |
| Northwestern State Demons | Northwestern State | 401869129 | Northwestern State @ Louisiana Tech 2026-09-05T23:30Z |
| VMI Keydets | VMI | 401858211 | VMI @ Virginia Tech 2026-09-05T23:30Z |
| South Dakota State Jackrabbits | South Dakota State | 401858431 | South Dakota State @ Northwestern 2026-09-06T00:00Z |
| Mercyhurst Lakers | Mercyhurst | 401870790 | Mercyhurst @ New Mexico State 2026-09-06T01:00Z |
| Northern Arizona Lumberjacks | Northern Arizona | 401856773 | Northern Arizona @ Arizona 2026-09-06T01:30Z |
| Portland State Vikings | Portland State | 401860879 | Portland State @ San Diego State 2026-09-06T01:30Z |
| Morgan State Bears | Morgan State | 401856774 | Morgan State @ Arizona State 2026-09-06T02:00Z |
| Mississippi Valley State Delta Devils | Mississippi Valley State | 401866411 | MVSU @ Sacramento State 2026-09-06T02:00Z |

### 3 — Guard

`assert_crosswalk_teams_match_schedule` called in `_enrich_frame_via_crosswalk` before crosswalk write. Tests: `test_assert_crosswalk_teams_match_schedule_*` in `tests/unit/test_odds_api.py`.

### 4 — Replay results (tonight's archive only)

| Metric | Before (S6-W1-CARD-B) | After (S7 replay) |
|--------|----------------------:|------------------:|
| Events in pull | 146 | 146 |
| Crosswalk matched | 112 | **146** |
| Match rate | 76.7% | **100%** |
| Spread coverage (91-game slate) | 56 | **90** |
| No spread snapshot | 35 | **1** (`401858423` Massachusetts @ Rutgers — market gap) |

Replay: `replay_live_from_archive` → 522 new snapshot rows staged (deduped against prior null-`game_id` rows with old `game_key`s).

### 5 — S6-W1-CARD-B Phase B gates (probe ordering, `as_of=2026-09-01T20:38:58Z`)

| Step | Before | After |
|------|-------:|------:|
| start | 91 | 91 |
| 1 — snapshot/stale/kickoff/quarantine | 56 | **90** |
| 2 — edge/EV/σ | 53 | **84** |
| 3 — model_market_disagree | 21 | **28** |
| 4 — exposure caps | 8 | **8** |
| 5 — qb_status_unknown | 0 | 0 |

**QB worklist (step 4 survivors) — after:**

| game_id | matchup |
|--------:|---------|
| 401869129 | Northwestern State @ Louisiana Tech |
| 401856636 | Baylor @ Auburn |
| 401860879 | Portland State @ San Diego State |
| 401858209 | Tulane @ Duke |
| 401858434 | Marshall @ Penn State |
| 401866623 | North Carolina A&T @ Georgia State |
| 401858422 | Eastern Illinois @ Minnesota |
| 401864498 | Central Michigan @ New Mexico |

(Swapped in: Northwestern State @ Louisiana Tech, Portland State @ San Diego State. Swapped out: Duquesne @ Air Force, Fordham @ North Dakota State.)

No reply bank regenerated in this task.

---

## Verification

`make lint typecheck test` — green (1020 passed).

---

## Ambiguities

1. **Massachusetts @ Rutgers** remains the sole no-spread game — Odds API did not list it; not a crosswalk defect.
2. **Historical replay** (~233 games, 0/314 ticket impact) deferred pending explicit operator approval.
3. **Prior null-`game_id` rows** with old `game_key`s remain in `odds_snapshots`; coverage queries filter on `game_id` so reporting is correct without partition surgery.
