# Task 5B-PATCH-2 — home_away_swap residual resolution

**Date:** 2026-08-10  
**Scope:** `src/ncaa_quant/ingestion/odds_api.py` (unordered-pair fallback),
throwaway `scripts/_patch_5b_crosswalk.py`, tests, this note.  
**API spend:** **0** (replay from `data/raw/odds_api_historical/` only).  
**Sanctioned edits:** same as 5B-PATCH (unchanged).

---

## Step 0 — Side-semantics audit

**Verdict: NAME-BASED — proceed with swap-tolerant match.**

Code path in `normalize_odds_payload`:

```python
name = str(outcome.get("name", ""))
if schema_market == "total":
    side = name.strip().casefold()          # over / under
else:
    side = normalize_team_name(name, team_map)  # team NAME
```

Spread / h2h `side` is the outcome's team **name**, never derived from whether
that team sits in `event["home_team"]` vs `event["away_team"]`. Those fields
only feed `game_key` / context columns. Matching a home↔away listing swap
therefore does **not** flip spread signs.

The forbidden failure mode (position-derived side + swap match ⇒ sign flip)
does not apply. No STOP.

---

## Step 1 — Matcher + verify + replay

### Matcher change

`match_odds_events_to_cfbd` still requires ordered home/away first. On miss,
it retries the unordered (swapped) pair inside the **same** ±36h kickoff
tolerance (`KICKOFF_MATCH_TOLERANCE` unchanged). Successes set
`swap_detected=True` on the matcher DataFrame.

`OddsCfbdGameCrosswalkSchema` is strict and was **not** sanctioned for a new
column. `write_odds_cfbd_crosswalk` drops `swap_detected` before validate /
persist. Swaps remain reconstructible: Odds `home_team`/`away_team` vs CFBD
schedule for the matched `game_id`.

### Neutral-site breakdown of the 23 patch-1 residuals

All **23 / 23** resolve in staged CFBD `games` with `neutral_site=True`.
**Non-neutral swapped matches: 0** (none suspicious).

| season | n | all neutral? |
|---:|---:|:---:|
| 2021 | 18 | yes (bowls) |
| 2022 | 2 | yes (kickoff neutrals) |
| 2023 | 2 | yes (Army–Navy + bowl) |
| 2024 | 1 | yes (USC–LSU neutral) |

### Replay (2021–2024 eval; 2025 hygiene)

- Archives replayed: **1880**
- Rows written: **1,647,144**
- Quarantine rebuilt: **434**
- Previously-matched events re-keyed: **0**

| season | events | matched before | matched after | match % after | FBS–FBS um before | FBS–FBS um after |
|---:|---:|---:|---:|---:|---:|---:|
| 2021 | 1208 | 854 | 873 | 72.3% | 18 | **0** |
| 2022 | 1236 | 837 | 839 | 67.9% | 2 | **0** |
| 2023 | 925 | 846 | 848 | 91.7% | 2 | **0** |
| 2024 | 960 | 837 | 838 | 87.3% | 1 | **0** |

FBS–FBS unmatched residuals after: **none** (table empty).

Historical `odds_snapshots` row-count delta: **0** every season (incl. 2025).

All 23 patch-1 labels post-status=`matched` (Odds listing flipped vs CFBD
designated home; `game_id` filled).

### Spread-sign spot-check (5 games, hand)

Compared Odds API median line for **CFBD home team name** as `side` vs CFBD
`lines_historical` closes. Signs agree; App State shows CFBD-internal book
disagreement (Odds aligns with Bovada).

| season | game_id | CFBD home | Odds side line (med) | CFBD close spreads | sign |
|---:|---:|---|---:|---|:---:|
| 2021 | 401331165 | Coastal Carolina | −10.5 | Bovada −11; WH/cons/tr −12..−12.5 | OK |
| 2021 | 401331166 | App State | −2.75 | Bovada **−3**; WH/cons/tr **+1** | OK vs Bovada |
| 2021 | 401331170 | Oregon State | −7.0 | −7.0 / −6.5 / −6.5 / −7.0 | OK |
| 2021 | 401331171 | Marshall | +4.0 | +4.0 / +3.5 / +4.0 / +4.0 | OK |
| 2024 | 401628334 | LSU | −4.5 | Bovada/DK −4.0; ESPN Bet −3.5 | OK |

No evidence of systemic sign flip from the swap match.

### Regression

`4195` previously-matched events retained the same `game_key` (0 re-keyed).

---

## Built

- `match_odds_events_to_cfbd` — unordered-pair fallback + `swap_detected`
- `write_odds_cfbd_crosswalk` — drops matcher-only `swap_detected` for strict schema
- `scripts/_patch_5b_crosswalk.py` — Step 0 print, neutral audit, replay, spot-check
- Tests: swap match, ordered preference, write drops flag, tolerance not widened

## Ambiguities

1. Persisting `swap_detected` needs `OddsCfbdGameCrosswalkSchema` (not on the
   5B-PATCH sanctioned list). Flag is matcher/test-only; reconstructible from
   Odds vs CFBD home/away after write.
2. CFBD close books can disagree on neutral-site home designation (App State
   bowl); spot-check uses book-level comparison, not a single median.

## `make lint typecheck test`

```text
uv run ruff check src tests
All checks passed!
uv run ruff format --check src tests
162 files already formatted
uv run mypy
Success: no issues found in 103 source files
uv run pytest -m "not live"
699 passed, 1 deselected, 27 warnings in 245.69s
Required test coverage of 80% reached. Total coverage: 80.78%
```
