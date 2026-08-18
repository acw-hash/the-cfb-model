# W9-I — 2026 ingest and rating-state readiness

**Date:** 2026-08-18  
**Status:** Complete  
**Authority:** `docs/notes/webapp-w9-0.md` (`7c31100`); `docs/notes/webapp-w9v.md`
§4.4; `docs/notes/webapp-w9m.md`; `docs/notes/webapp-w9push.md` (`7d7fea5`);
lockbox rule in `docs/notes/23-readout.md`; ADR 0013 / 0014.

**This task ends at a local dry predict.** No R2 write, no publish, no
revalidation POST, no fit/retrain/promotion, no lockbox-guard edit, no Prefect
change, no site/CI/`push.py` edit. 2025 was read only as Kalman observations
and hygiene counts — never as labels, metrics, ATS, or odds snapshots.

Artifacts: `docs/notes/_artifacts/webapp-w9i/`.

---

## Headline

**Schedule, teams, venues, and entering-2026 rating state are on disk.** A
local isolated dry predict of 2026 week 1 produced 99 finite-μ/σ rows on the
serialized v2 champion, with 2025-filtered Kalman ratings (137/137 overlapping
teams moved vs end-of-2024). Isolation hashes of the real hysteresis files and
idempotency ledger were unchanged. **A production Tuesday publish still cannot
emit that artifact:** the wired `predict_fn` loads champion week parquet (no
2026 file → `FileNotFoundError`), and W9-0 found no `predict_publish`
deployment.

Neither lockbox failure mode is the current dry path. Failure mode 1 (guard
raises on live 2026) is not what `LockboxSeasonError` does — that raise is
`season == 2025` only. Failure mode 2 (silent end-of-2024 ratings) is **not**
live: `filter_history.parquet` carries 3,386 season-2025 rows, and the
entering-2026 vs EOY-2024 `off_epa` comparison is 0 identical / 137 moved.

---

## STOP AND REPORT

| # | Condition | Result |
|---|-----------|--------|
| 1 | Phase 0 before ingest/config | **Reported first.** Phase 0 inventory is `phase0_inventory.json` (inspected_at `2026-08-18T11:44:33Z`), before `end_season` bump and ingest. |
| 2 | Lockbox blocks 2026 rating state | **Did not trip.** `LockboxSeasonError` is evaluation-scoped (`season == 2025` prediction-frame load). Kalman `initialize_season` has no lockbox import. |
| 3 | 2025 postgame incomplete/absent | **Did not trip.** 934/934 completed, 0 missing scores, weeks 1–16, last `event_time` 2026-01-20. |
| 4 | 2026 week 1 absent / bad ids | **Did not trip as absent or bad ids.** 99 games, 0 ids failing `^[0-9]{6,12}$`. Count is lighter than 2024 week 1 (146) / 2025 week 1 (142) — CFBD as of 2026-08-18; incremental ingest may add games. |
| 5 | Ratings entering 2026 identical to EOY-2024 | **Did not trip.** `n_identical=0` `n_moved=137`. |
| 6 | Dry predict nulls / refusals / alien tiers | **Did not trip.** 0 null μ, 0 σ-refused, μ/σ inside the v3 week-1 envelope. 73/99 `strong_lean` is a cupcake-heavy current slate (FCS openers), not a null block. |
| 7 | Producing 2026 rating state requires a fit | **Did not trip.** Task 14 already filtered 2014–2025 into `filter_history.parquet`. No mapping-layer fit in this task. Live `initialize_season(2026)` was **not** used; it hung here (see §4). |

Forbidden actions **not taken:** no lockbox edit; no 2025 metrics/grading/odds;
no R2 put; no registry/promotion; no real hysteresis/ledger write; no Prefect
create/apply; no site/`push.py`/CI edit; no new ingest pipeline (existing
`run_cfbd_backfill` + a DESIGN §8 clamp in the existing games normalizer).

---

## 1. Phase 0.1 — Can the rating engine advance through 2025?

Reported from source **before** `data.end_season` changed and before ingest
(`phase0_inspect.py` → `phase0_inventory.json`).

### 1.1 Every `LockboxSeasonError` site

**One class, one raise.** The name is not used anywhere else.

```46:47:src/ncaa_quant/pipelines/predict.py
class LockboxSeasonError(ValueError):
    """predict_fn refused a lockbox season (2025 is never evaluated)."""
```

```129:146:src/ncaa_quant/pipelines/predict.py
def load_production_prediction_rows(season: int, week: int) -> list[dict[str, Any]]:
    """Return production walkforward rows for ``(season, week)`` plus stamp aliases.
    ...
    Season 2025 is refused. The parquet is 2024-only for the W9-P oracle.
    """
    if season == LOCKBOX_SEASON:
        msg = (
            f"season {LOCKBOX_SEASON} is lockbox; predict_fn refuses it "
            "(producing predictions for 2025 is not permitted)"
        )
        raise LockboxSeasonError(msg)
    path = production_week_predictions_path(season, week)
    if not path.is_file():
        msg = f"champion week predictions missing: {path}"
        raise FileNotFoundError(msg)
```

**What it guards:** loading **stored champion prediction frames** for season
2025. Not Kalman, not staged games, not `initialize_season`, not 2026.

A 2026 `predict_publish` that uses the **default** `predict_fn` therefore does
not hit `LockboxSeasonError`. It hits `FileNotFoundError` on
`data/backtests/task23_fundamental_reduced_v2/full/weeks/season=2026_week=1.parquet`
— loud, recoverable, **failure mode 1-shaped but not the lockbox**.

### 1.2 Related guards that are not `LockboxSeasonError`

| Guard | Site | What it protects |
|-------|------|------------------|
| `LockboxViolation` / `assert_lockbox_excluded` | `WalkForwardConfig.validate_ablations` (`walkforward.py`) | backtest **replay-season selection** (test/warmup/continuity). Listing 2025 raises. |
| same | `cli.py` `load_staged_odds_snapshots` | **staged-odds loading** — requested seasons must exclude 2025; loaded rows asserted lockbox-free. |
| same | `cli.py` `backtest run` | replay seasons of the named YAML. |
| same | `registry/champion_serialize.py` | W9-M serialize replay list. |
| `W9AStop` | `registry/w9a_revalidate.py` | YAML seasons containing 2025; **fit games** containing 2025 (`"lockbox 2025 present in loaded games"`); predictions.parquet `N_2025`. |
| `GradeExportError` / `assert_live_season` | `webapp/grade.py` | **grading** seasons `< 2026`. |
| frontend throw | `webapp/site/src/lib/artifacts/loader.ts` `loadResultsSeason` | loading `results_2025.json`. |

```28:35:src/ncaa_quant/webapp/grade.py
def assert_live_season(season: int) -> None:
    """Refuse grading for lockbox / pre-live seasons (2025 and earlier)."""
    if season < LIVE_PUBLISH_MIN_SEASON:
        msg = (
            f"grade export refused for season {season}: live publish begins "
            f"{LIVE_PUBLISH_MIN_SEASON}+ (lockbox guard)"
        )
        raise GradeExportError(msg)
```

```80:83:webapp/site/src/lib/artifacts/loader.ts
export async function loadResultsSeason<T>(season: number): Promise<T | null> {
  if (season === 2025) {
    throw new Error("Season 2025 is lockbox — results are never loaded or graded");
  }
```

None of these fire because 2025 games are present in the observations frame
with a 2026 `as_of`. That is state propagation, not evaluation. **Putting 2025
in `WalkForwardConfig` replay/test/warmup would raise** — the v3 YAML used
here lists `[2019, 2020, 2021, 2022, 2023, 2024]` only.

### 1.3 `initialize_season(2026)` / forward Kalman vs the lockbox

`src/ncaa_quant/ratings/` has **zero** lockbox imports. The production wrapper:

```173:199:src/ncaa_quant/evaluation/production_stack.py
    def initialize_season(self, season: int, as_of: datetime) -> None:
        assert_tz_aware(as_of)
        self._current_season = int(season)
        ...
        hist = obs.loc[obs["event_time"] < to_utc(as_of)]
        if hist.empty:
            return
        ...
        result = run_filter(
            hist,
            config=self.ss_config,
            fbs_team_ids=self.fbs_team_ids,
            preseason_states=preseason,
            record_weekly=True,
        )
        self._ingest_history(result.history)
```

If 2025 rows are in `self.observations` and `as_of` is the 2026 week-1 Tuesday
(`2026-09-01T10:00:00Z`), those rows have `event_time` in 2025-08-23 …
2026-01-20, all `< as_of`, and **are consumed**. No lockbox check.

**This task did not call `initialize_season`.** An attempt with advanced-box
observations hung (>10 min wall / >> Task 14’s 2.7 s for the full 2014–2025
filter). Week-1 ratings were taken from the already-written
`filter_history.parquet` snapshot (Task 14). That is not a mapping-layer fit.

### 1.4 2025 postgame completeness (hygiene; no metrics)

From `phase0_inventory.json` `games.2025`:

| | |
|--|--|
| rows | **934** |
| weeks | 1–16 (all present) |
| season_type | regular 888, postseason 46 |
| completed | **934 true / 0 false** |
| missing scores | **0** |
| `event_time` | 2025-08-23 19:30Z → **2026-01-20 04:00Z** |

n_by_week: 1=142, 2=83, 3=70, 4=62, 5=53, 6=51, 7=56, 8=60, 9=53, 10=52, 11=52,
12=59, 13=64, 14=67, 15=9, 16=1.

Observations (not labels): advanced_box 3302 rows / 16 weeks; plays 166057 /
16 weeks; **drives absent** (same as 2024). Teams 681, venues 844.

The season finished in the staged lake (CFP/bowl through 2026-01-20, all
scored).

### 1.5 Rating state on disk

| | |
|--|--|
| path | `data/artifacts/state_space/filter_history.parquet` |
| mtime | **2026-08-10T19:10:49Z** |
| rows | 37,869 |
| seasons | 2014–2025 |
| n_2025 | **3,386** (through 2026-01-20) |
| producer | Task 14 full filter (`docs/notes/14.md`): advanced-box EPA, `FILTER_WALL_CLOCK_SEC=2.718`, `history_rows=37870` |

Not a champion walk-forward byproduct. The v3 / v2 backtests never loaded this
file for evaluation; their replay seasons stop at 2024.

Phase 0 EOY-2025 vs EOY-2024 `off_epa` (136 overlapping teams in that script’s
cut): `n_identical=0`, median |Δ| ≈ 0.083. Dry-predict snapshot (last postgame
row per team, `event_time < as_of`) later measured **137** overlapping teams,
still **0 identical**.

**Usable production entering-2026 state:** yes, as the Task 14 Kalman history.
Not a mapping fit. Live `run_filter` replay was not re-proven cheap on this
machine.

### 1.6 Failure modes — current state

1. **Guard raises on first live 2026 run.** Not the lockbox. Default
   `predict_fn` → `FileNotFoundError` on missing 2026 week parquet. Loud.
2. **Guard silent, observations lack 2025 → end-of-2024 ratings.** The
   **dangerous path exists if someone copies the backtest load path** (`cli.py`
   `backtest run` loads only `replay_seasons` = 2019–2024). It is **not** the
   current dry path: `filter_history` 2025 rows are present and the sanity
   check moved every overlapping team.

**Neither failure mode is the dry-predict state.** The path that uses Task 14
history + the serialized champion works and 2025 observations are available
for state.

---

## 2. Phase 0.2 — What else a 2026 week-1 predict needs

Inventory **before ingest** (2026 schedule absent at Phase 0; present after).

| Need | Pre-ingest | After this task |
|------|------------|-----------------|
| 2026 schedule | **Absent** | **Present** — 888 games, week 1 = 99 |
| 2026 teams | **Absent** | **Present** — 684 |
| 2026 venues | **Absent** | **Present** — 852 |
| Rating state entering 2026 | **Present** — `filter_history` 2025 rows | unchanged (not rewritten) |
| Serialized v2 champion | **Present** — `data/registry/artifacts/v2/production_ensemble.pkl` 8,772,060 bytes, mtime 2026-08-17T20:41:49Z; `run_id=task23_fundamental_reduced_v3`, registry version 2 / export identity `champion_version: 3` | used, not modified |
| CQR / σ / PIT calibrators | **Inside the pickle** (`calibrator_paths: []`) | used, not modified |
| Conformal/interval | CQR columns emitted on dry predict (`cqr_lo`/`cqr_hi`) | same |
| Conviction hysteresis | Present on disk but **2024 fixture keys**; first 2026 publish should treat as empty | dry run redirected to `_artifacts/webapp-w9i/dry_state/` |
| Idempotency ledger | Present | untouched |
| Mapping features at Tuesday decision, no 2026 games played | Ratings + `expected_possessions` | `expected_possessions` **99/99 NaN** (no `possessions_by_game` for 2026 ids; drives still absent). v3 week 1 2021–2024: 545 rows, all finite μ/σ, `null_reason` None — LightGBM/ElasticNet median-impute NaN. Structural, not a week-1-only surprise. |

**Week-1 information set:** no current-season games. Ratings are end-of-2025
posteriors (plus 0.0 default for teams never in `filter_history`). That is a
different information set from mid-season backtest weeks, but it **is** the
set the v3 week-1 slices used (2020–2024). 2019 week 1 is absent from that
frame (`n_2019_w1=0`).

`data.end_season` gates **only** `run_cfbd_incremental`’s
`min(end_season, season_of(now))` clamp (`cfbd.py` ~2425). Explicit
`--seasons` backfill is not clamped. YAML + `DataConfig` default were 2025
at Phase 0.

Workstation `webapp.export_enabled` may be true in `.env`; dry predict set
`NCAA_QUANT_WEBAPP__EXPORT_ENABLED=false` and
`run_isolated_week_export` forces `export_enabled=False`.

---

## 3. Config: `data.end_season` 2025 → 2026

```diff
--- a/configs/data.yaml
@@ -2,7 +2,9 @@ data:
   start_season: 2014
   # PLACEHOLDER — bump each offseason; not named as a fixed value in the spec.
-  end_season: 2025
+  # Live season is 2026 (W9-I). This constant clamps run_cfbd_incremental
+  # via min(end_season, season_of(now)); explicit --seasons backfill is not clamped.
+  end_season: 2026
```

```diff
--- a/src/ncaa_quant/config.py
@@ -41,7 +41,7 @@ class DataConfig(BaseModel):
     start_season: int = 2014
-    end_season: int = 2025
+    end_season: int = 2026
```

Nothing else in `src/` reads `end_season` except the incremental ingest clamp.
Walk-forward YAML seasons, lockbox, grading `LIVE_PUBLISH_MIN_SEASON=2026`,
and `predict_publish_flow` default week are independent.

---

## 4. Ingest 2026 schedule and teams

**Flow (existing):** `uv run ncaa-quant ingest cfbd --seasons 2026 --endpoints teams,venues,games`
→ `run_cfbd_backfill` (not incremental). No new ingest pipeline.

**CFBD endpoints:**

| Wrapper | HTTP |
|---------|------|
| `fetch_teams(2026)` | `GET /teams?year=2026` (no classification filter) |
| `fetch_venues()` | `GET /venues` |
| `fetch_games(2026, season_type=regular\|postseason)` | `GET /games?year=2026&seasonType=…&classification=fbs` |

### First run — games failed Pandera

Teams **684** and venues **852** wrote. Games failed
`event_time_le_ingested_at`: unplayed rows had `event_time` = kickoff+duration
(future) > `ingested_at`. Log: `ingest.log`.

**Existing-normalizer fix** (not a new ingest): in `normalize_games_payload`,
if `not completed` and `event_time > ingested`, set `event_time = ingested`,
`estimated=True`. Kickoff stays on `start_date`. DESIGN §8. Completed-game
path unchanged. Test:
`tests/unit/test_cfbd.py::test_normalize_unplayed_future_game_event_time_not_after_ingest`.

### Retry — games wrote

```text
{"endpoint": "teams", "season": 2026, "table": "teams", "event": "cfbd_partition_skipped", ...}
{"endpoint": "venues", "season": 2026, "table": "venues", "event": "cfbd_partition_skipped", ...}
{"endpoint": "games", "season": 2026, "week": 1, "rows": 99, "event": "cfbd_partition_written", ...}
{"endpoint": "games", "season": 2026, "week": 2, "rows": 86, ...}
{"endpoint": "games", "season": 2026, "week": 3, "rows": 75, ...}
{"endpoint": "games", "season": 2026, "week": 4, "rows": 71, ...}
{"endpoint": "games", "season": 2026, "week": 5, "rows": 59, ...}
{"endpoint": "games", "season": 2026, "week": 6, "rows": 58, ...}
{"endpoint": "games", "season": 2026, "week": 7, "rows": 62, ...}
{"endpoint": "games", "season": 2026, "week": 8, "rows": 56, ...}
{"endpoint": "games", "season": 2026, "week": 9, "rows": 56, ...}
{"endpoint": "games", "season": 2026, "week": 10, "rows": 63, ...}
{"endpoint": "games", "season": 2026, "week": 11, "rows": 67, ...}
{"endpoint": "games", "season": 2026, "week": 12, "rows": 70, ...}
{"endpoint": "games", "season": 2026, "week": 13, "rows": 65, ...}
{"endpoint": "games", "season": 2026, "week": 15, "rows": 1, ...}
{"season": 2026, "partitions_written": 14, "partitions_skipped": 2, "rows_written": 888, "event": "cfbd_season_done", ...}
seasons=[2026] partitions_written=14 partitions_skipped=2 rows_written=888 raw_files=2
```

No week 0, 14, or 16 partitions (CFBD returned none for `classification=fbs`).
888 is below 2025’s 934 — expected in mid-August; later weeks and bowls are
not fully posted.

---

## 5. Week-1 verification

From `week1_verify.json` (`inspected_at` 2026-08-18T11:57:18Z):

| Check | Result |
|-------|--------|
| n | **99** |
| kickoff (`start_date`) | **2026-08-29T16:00Z – 2026-09-07T23:30Z** |
| kickoffs in the future | **99/99** |
| timezone | UTC on `start_date` |
| `event_time` | ingest clamp (`2026-08-18T11:56:21Z`) for all unplayed — not kickoff |
| id pattern `^[0-9]{6,12}$` | **99/99 match, 0 failing** |
| home/away → 2026 teams (684) | **0 unresolved** |
| week 0 partition | **does not exist** (n=0) |
| `week_of(now)` 2026-08-18 | **0** (Labor Day week-1 Monday is 2026-09-07; dates before that clamp to week 0) |
| `predict_publish_flow` default | `week if week is not None else 1` — **publish aims at CFBD week 1, not calendar week 0** |

Five ids (shape-check sample):

```
401858424
401866408
401864494
401864495
401858210
```

Five corresponding kickoffs UTC: 2026-09-04 01:00Z, 2026-08-29 22:30Z,
2026-08-29 19:00Z, 2026-09-04 22:30Z, 2026-09-06 02:30Z.

**Count caveat:** 99 vs 2024 week 1 = 146 and 2025 week 1 = 142. The 99 rows
are what CFBD returned for week=1 `classification=fbs` on 2026-08-18. A later
incremental ingest may add games. IDs already on disk all pass the live push
shape guard.

---

## 6. Rating-state readiness

**Entering-2026 ratings exist** on disk in `filter_history.parquet` (Task 14
GT-active filter through 2025 postseason). This task did not produce them and
did not fit anything.

**What would produce them if they were missing:** a Kalman `run_filter` over
staged 2014–2025 observations (Task 14 cost: **2.7 s** wall for 10,316 obs /
37,870 history rows). That is **not** a mapping-layer retrain. A mapping fit
is forbidden here and was not required.

**Live `initialize_season(2026)` cost on this machine (not used):** plays-based
`build_observations_from_staged` for 2019–2025 (~1M plays) ran 15+ min CPU-
saturated and was killed. Advanced-box `initialize_season` then hung >10 min
wall (PIDs killed). Last log line was `initialize_season(2026) with 2025
observations...`. Cause not fully diagnosed; `record_weekly=True` on a large
hist plus Python datetime conversion of every `event_time` is a suspect. **Do
not claim live Kalman replay is cheap.** Snapshot-from-history is the
readiness path this task used.

`filter_history` SHA-256 before and after dry predict (unchanged):

```
cc1e9a947cfbb074c0bad6b148b96df523f6ec607b7b53ddec1c9f776aa78814
```

---

## 7. Dry predict (export off, no R2)

Script: `docs/notes/_artifacts/webapp-w9i/dry_predict.py`.

- WalkForwardConfig replay `[2019, 2020, 2021, 2022, 2023, 2024]`;
  `assert_lockbox_excluded` passed.
- `as_of` = **2026-09-01T10:00:00+00:00** (Tuesday 06:00 ET) from week-1
  `start_date` calendar (unplayed `event_time` is the ingest clamp).
- Rating source: last postgame `filter_history` row per team with
  `event_time < as_of`.
- Predictor: `load_production_ensemble` of registry v2 pickle.
- Export: `run_isolated_week_export` → `_artifacts/webapp-w9i/dry_export/`,
  state → `dry_state/`, `push=False`.
- `team_ratings_2026.json` has `"teams": {}` because `filter_history` was
  **not** passed into `export_publish_artifacts` (avoids publishing 2025
  posteriors as a ratings page).

### 7.1 Predict summary

```json
{
  "n_games_predicted": 99,
  "mu_range": [-16.97190126008196, 48.61001623762129],
  "sigma_range": [15.200248885097194, 23.772736777775233],
  "conviction_tier_distribution": {
    "strong_lean": 73,
    "clear_lean": 17,
    "toss_up": 2,
    "lean": 7
  },
  "n_null_tier_suppressed": 0,
  "n_sigma_refused": 0,
  "n_null_reason_rows": 0,
  "as_of": "2026-09-01T10:00:00+00:00",
  "model_identity": {
    "champion_version": 3,
    "model_version": "production-v0_reduced_v3",
    "registry_name": "ncaa-quant",
    "run_id": "task23_fundamental_reduced_v3"
  },
  "expected_possessions_nan": 99,
  "rating_n_moved": 137,
  "rating_n_identical": 0
}
```

`load_config.export_enabled=False` (env override). Isolated
`export_enabled=False`. `W9-P push=False (R2 disabled; no upload)`. Elapsed
**3.844 s**. Quantile-crossing warning on 99 margin rows (existing head
behavior; predictions sorted ascending).

### 7.2 Rating sanity (STOP #5)

```text
W9-I rating_compare n_common=137 n_identical=0 n_moved=137
W9-I eoy2024_as_of=2025-08-23T19:29:59+00:00
```

Sample `off_epa` (EOY-2024 → enter-2026):

| team_id | EOY-2024 | enter-2026 | Δ |
|--------:|---------:|-----------:|--:|
| 8 | 0.248 | 0.168 | −0.080 |
| 61 | 0.122 | 0.119 | −0.003 |
| 99 | 0.158 | −0.052 | −0.210 |
| 130 | 0.012 | 0.054 | +0.042 |
| 194 | 0.339 | 0.167 | −0.172 |
| 251 | 0.093 | 0.183 | +0.089 |
| 265 | 0.006 | −0.000 | −0.006 |
| 333 | 0.016 | 0.035 | +0.019 |

Not identical. Failure mode 2 is not live on this snapshot.

### 7.3 Week-1 structure vs v3 backtest week 1

v3 `predictions.parquet` week 1 (not 2025; 2020–2024):

| season | n | μ range | \|μ\|>30 | p_ml strong-like (>0.85 or <0.15) |
|--------|--:|---------|---------:|----------------------------------:|
| 2020 | 35 | [−21.8, 29.0] | 0 | 5 |
| 2021 | 127 | [−29.2, 46.9] | 12 | 38 |
| 2022 | 136 | [−19.5, 43.8] | 16 | 44 |
| 2023 | 136 | [−20.4, 44.4] | 30 | 52 |
| 2024 | 146 | [−18.5, 45.5] | 26 | 79 |
| **2026 dry** | **99** | **[−17.0, 48.6]** | **49** | **73** |

μ max is a cupcake (Northern Arizona @ Arizona 48.6; backtest max 46.9). σ
range 15.2–23.8 sits inside v3 week-1 σ 11.4–29.3. Zero nulls, matching v3
week 1 `null_reason=None` on 545 rows (2021–2024).

73/99 `strong_lean` is a **higher fraction** than 2024 week 1’s 79/146
strong-like p_ml. The current CFBD week-1 slate is FCS-opener heavy (largest
|μ|: NAU @ Arizona, SDSU @ Northwestern, Tennessee State @ Georgia, Bryant @
Army, UAPB @ Missouri, …). Empty isolated hysteresis does not create that
skew — `p_win_home` already exceeds 0.85 on those 73 games. Not a null/σ-
refusal failure; not an alien μ/σ envelope.

`expected_possessions` all NaN is the week-1 / no-drives information set.
v3 week 1 still emitted finite μ; so did this run.

### 7.4 Isolation hashes (must be identical)

Before and after:

```
tier_state.json     23c6f9cace86ded254715a195114aa64f8704ee8dd8bd55fa419dff779082a19
tier_changes.jsonl  9f0cd81d304964475f7b57f3fbe2427848a5720a37986d659821ebe8b7c3528b
idempotency.json    0ef29a1e9fca010687dbbe968d5a5e43227f3b20c8f4e080ea56cf22f76fc699
```

`W9-I isolation_changed=[]`

Redirected writes landed only under `docs/notes/_artifacts/webapp-w9i/dry_state/`.

### 7.5 Zero R2

```text
W9-I load_config.export_enabled=False
W9-I isolated_export_enabled=False
W9-P push=False (R2 disabled; no upload)
```

Grep of `dry_predict.log`:

```text
PutObject: 0
r2.cloudflarestorage.com: 0
amazonaws.com: 0
cloudflarestorage: 0
```

Cadence-shortfall and `new_bet_candidate` lines are `notification_suppressed`
(null notifier). Dummy 0.05-edge candidates are the existing default
`build_candidates` stub — not a bet-slip write.

`track_record.json` in the dry export is the existing W9-G 2021–24 snapshot
copy (`W9G_REGRADE`). It does not grade 2025.

---

## 8. `make test`

```text
uv run pytest -m "not live"
collected 889 items / 1 deselected / 888 selected
========= 888 passed, 1 deselected, 32 warnings in 315.40s (0:05:15) ==========
Required test coverage of 80% reached. Total coverage: 80.15%
```

New coverage: `test_normalize_unplayed_future_game_event_time_not_after_ingest`
(`tests/unit/test_cfbd.py` — 26 tests in that file, including the new one).

---

## 9. What was read from 2025, under which policy

| Read | Policy |
|------|--------|
| Staged `games` / `plays` / `advanced_box` season=2025 | **Hygiene counts only** (row counts, completed flag, missing scores, date range). No ATS, no MAE, no log-loss, no calibration metric. |
| `filter_history.parquet` season=2025 rows | **Kalman state snapshot** for entering-2026 ratings and the EOY-2024 vs enter-2026 `off_epa` sanity check. Not a confirmatory evaluation. Not written to `docs/lockbox_access.md` because this is not the annual confirmatory metric read. |
| 2025 **not** loaded | odds snapshots, WalkForwardConfig replay, `load_production_prediction_rows(2025)`, grade export, `results_2025.json`, any 2025 prediction frame. |

The lockbox forbids **evaluating** 2025. Propagating rating state through 2025
observations is in bounds. No guard was relaxed.

---

## 10. Could a week-1 publish now produce a correct artifact?

**Locally, on this dry path: a structurally plausible 99-game artifact, yes**
— staged week-1 games with CFBD-shaped ids, serialized champion, CQR/σ inside
the pickle, ratings that actually moved through 2025.

**On the production publish path: not yet.**

1. Wired `predict_fn` still loads champion week parquet (W9-P). There is no
   `season=2026_week=1.parquet`. First live run is a loud `FileNotFoundError`,
   not a silent end-of-2024 forecast.
2. No `predict_publish` Prefect deployment / worker (W9-0).
3. Week-1 slate may still grow (99 vs ~140 historically). Re-ingest before
   kickoff.
4. Live `initialize_season` was not shown to be cheap; the on-disk Task 14
   history is the rating source that passed the sanity check.
5. `expected_possessions` will be NaN until 2026 games exist in the
   possessions map — same class of missingness the backtests already handled
   at week 1.

Do not treat this dry export as a publish. Do not write it to R2.
