# Task 5B-PATCH — Odds↔CFBD name map repair + archive replay

**Date:** 2026-08-10  
**Scope:** `configs/team_names.yaml`, archive replay entrypoint in
`src/ncaa_quant/ingestion/odds_api.py`, throwaway
`scripts/_patch_5b_crosswalk.py`, tests, this note.  
**API spend:** **0** (replay from `data/raw/odds_api_historical/` only).

---

## Step 0 — Scope verdict

**RE-NORMALIZATION FROM ARCHIVE REQUIRED** (not crosswalk-only).

Evidence (pre-patch staged data):

| season | unmatched events | unmatched game_keys present in odds_snapshots | hist rows |
|---:|---:|---:|---:|
| 2021 | 387 | 321 / 386 | 265826 |
| 2022 | 433 | 413 / 433 | 288168 |
| 2023 | 125 | 125 / 125 | 313862 |
| 2024 | 169 | 167 / 169 | 374686 |

Unmatched events already carried staged `odds_snapshots` rows under wrong /
non-CFBD `game_key`s (`Appalachian State`, `UMass`, `Southern Mississippi`,
`Sam Houston State`, …) with null `game_id`. Crosswalk-only rematch would
update `odds_cfbd_game_crosswalk` but leave snapshot `game_key` / team /
`side` mis-keyed.

**Replay path used:** wipe historical `odds_snapshots` (keep live) + wipe
season crosswalk + wipe ingest quarantine for 2021–2025, then
`replay_historical_from_archives` over all 1,880 planned units’ on-disk
archives. Missing archive → refuse (would imply API spend).

---

## Step 1 — Name map

### Aliases added / direction fixes

Distinct unmatched **FBS-side** Odds names that were not CFBD `teams.school`
(complete set from crosswalk frequency × FBS classification):

| Odds string | Target (CFBD `teams.school`) |
|---|---|
| `Appalachian State` (+ Mountaineers form) | `App State` |
| `UMass` / `UMASS` / `UMass Minutemen` / `UMASS Minutemen` | `Massachusetts` |
| `Southern Mississippi` (+ Golden Eagles forms) | `Southern Miss` |
| `Sam Houston State` (+ Bearkats form) | `Sam Houston` |

Direction bug fixed: `Southern Miss Golden Eagles` and `UMass Minutemen*`
previously targeted non-CFBD strings (`Southern Mississippi`, `UMass`).

### Full-map target-resolution test

`test_team_name_map_targets_resolve_against_staged_cfbd` iterates every
`odds_api` map target and asserts membership in the union of staged CFBD
`teams.school`. Passing under this patch.

### Sam Houston FCS gate

`Sam Houston State` → `Sam Houston` only supplies the canonical school string.
Matcher still requires CFBD schedule pair + ±36h kickoff (`match_odds_events_to_cfbd`).
Unit test `test_sam_houston_state_match_requires_schedule_presence` covers
empty-schedule → unmatched. Post-replay: Sam Houston is `fcs` in 2021–2022;
FBS–FBS residuals involving Sam Houston = **0** (no false FBS–FBS matches).

---

## Step 2–3 — Replay + regression

- Archives replayed: **1880**
- Rows written (post-wipe): **1,647,144**
- Quarantine rebuilt: **434**
- Prior matched events: **3989**
- Previously-matched events re-keyed: **0** (preflight + post check)

### Crosswalk before vs after (2021–2024)

| season | events | matched before | match % before | matched after | match % after | FBS–FBS um before | FBS–FBS um after |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 2021 | 1208 | 821 | 68.0% | 854 | 70.7% | 51 | 18 |
| 2022 | 1236 | 803 | 65.0% | 837 | 67.7% | 35 | 2 |
| 2023 | 925 | 800 | 86.5% | 846 | 91.5% | 47 | 2 |
| 2024 | 960 | 791 | 82.4% | 837 | 87.2% | 46 | 1 |

2025 replayed for hygiene; match rates unreported (`docs/lockbox_access.md`).

### FBS–FBS residuals (all `home_away_swap`)

Odds lists home/away flipped vs CFBD; kickoff within ±36h. Not a name-map
miss; matcher still requires ordered home/away (tolerance not widened).

**2021 (18):** Northern Illinois@Coastal Carolina; Western Kentucky@App State;
Utah State@Oregon State; Louisiana@Marshall; Old Dominion@Tulsa; Kent
State@Wyoming; UTSA@San Diego State; Georgia State@Ball State; Air
Force@Louisville; Mississippi State@Texas Tech; Clemson@Iowa State; North
Carolina@South Carolina; Tennessee@Purdue; Wisconsin@Arizona State; Wake
Forest@Rutgers; Penn State@Arkansas; Iowa@Kentucky; North Texas@Miami (OH)
(dh=3.50).

**2022 (2):** LSU@Florida State; Clemson@Georgia Tech.

**2023 (2):** Navy@Army; North Carolina@West Virginia.

**2024 (1):** USC@LSU.

Name-map-caused FBS–FBS misses (App State / Southern Miss / Massachusetts /
Sam Houston) → **0**.

### Historical `odds_snapshots` row-count delta

| season | before | after | delta |
|---:|---:|---:|---:|
| 2021 | 265826 | 265826 | 0 |
| 2022 | 288168 | 288168 | 0 |
| 2023 | 313862 | 313862 | 0 |
| 2024 | 374686 | 374686 | 0 |
| 2025 | 404602 | 404602 | 0 (hygiene) |

Wipe+replay from the same archives yields identical row counts. Previously
unmatched games **gain correct `game_key` + filled `game_id` in place** (no
net row invent); delta is entirely explained by re-key/resolve rather than
new archive content.

---

## Built

- `configs/team_names.yaml` — FBS bare-name aliases + direction fixes
- `src/ncaa_quant/ingestion/odds_api.py` —
  `preview_crosswalk_game_key_regression`, `replay_historical_from_archives`
- `scripts/_patch_5b_crosswalk.py` — throwaway driver
- Tests: bare aliases, full-map CFBD target resolution, Sam Houston schedule
  gate, regression preview, zero-API replay

## Ambiguities

1. Ordered home/away mismatch (bowl / neutral sites) remains the residual FBS–FBS
   class; fixing it would be a matcher change outside this name-map patch.
2. Row-count “gaining rows” in the prompt is satisfied as in-place re-key with
   Δ=0 under wipe+replay identity with the archive.

## `make lint typecheck test`

```text
uv run ruff check src tests
All checks passed!
uv run ruff format --check src tests
162 files already formatted
uv run mypy
Success: no issues found in 103 source files
uv run pytest -m "not live"
696 passed, 1 deselected, 27 warnings in 239.11s
Required test coverage of 80% reached. Total coverage: 80.77%
```
