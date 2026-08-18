# W9-L — put the live predict path into `predict_publish`

**Date:** 2026-08-18  
**Status:** Complete (Amendment 2)  
**Authority:** original W9-L; Amendment 1 (2026-08-18); Amendment 2
(2026-08-18); Phase 0 in this file; `docs/notes/webapp-w9i.md`; ADR 0004;
ADR 0016; ADR 0017; `docs/notes/week-align-fix.md`.

**This task ends at a local isolated artifact.** No R2 write, no fit/retrain/
promotion, no lockbox-guard edit, no Prefect change, no site edit, no real
hysteresis/ledger/`filter_history` write.

Artifacts: `docs/notes/_artifacts/webapp-w9l/`.

---

## Headline

**`predict_publish` now runs the champion-method live path.** Default
`predict_fn` is `live_predict_rows`: staged plays-preferred observations
(2019 through the target season, including 2025 when predicting 2026),
fitted Task 15 priors, `run_filter` of `event_time < as_of`, registry
champion ensemble, `market_features=False`. Task 14 `filter_history` is
not read. There is no observation/filter cache; ~4 min Kalman is expected
and now logs progress per week.

The parquet loader is `oracle_predict_fn` only (W9-P unit tests inject it
so pytest does not run Kalman).

**2026 week 1 (re-ingested, unclamped):** 99 staged games, `as_of=
2026-09-01T10:00:00Z`, **8 excluded** because kickoff precedes `as_of`,
**91 published**. Isolated export: `FEATURE_TIME=TUESDAY_DECISION`,
kickoff min `2026-09-03T22:00Z`, none of the 8 early IDs in the artifact.
Determinism: two full Kalman passes, identical rating and prediction
hashes (pasted below).

---

## STOP AND REPORT (Amendment 2)

| # | Condition | Result |
|---|-----------|--------|
| 1 | Calendar reads `event_time` | **Closed.** Unchanged. D1 reverts the clamp. 2024 w5 clock `2024-09-24T10:00Z`. |
| 2 | Two rating reconstructions | **Closed.** Champion method is production (ADR 0017). Task 14 is hygiene. |
| 3 | 2024 week-5 oracle 0.0 | **Withdrawn** (Amendment 1). Live vs v2 parquet and vs v3 week file reported; not a stop. |
| 4 | Production `as_of` vs kickoff | **D2 addendum.** One Tuesday per week. Published slate **excludes** kickoff `< as_of`. 2026 w1: 8 of 99 excluded, 91 publish. |
| 5 | 2026 run vs W9-I dry | **Ran.** 91 vs 99 games (kickoff filter); ratings are champion-method vs W9-I’s Task 14 snapshot. μ `[−17.28, 49.81]` vs dry `[−16.97, 48.61]`. |
| 6 | Quantile crossing | **Yes, report only.** 2024 w5 56-row batch and 2026 w1 91-row batch both warned. No fix. |
| 7 | Isolation hash changes | **Did not trip.** `live_publish.py` hashes before/after and raises on change; the run completed. `filter_history` still `cc1e9a94…`; possessions `e1101588…`. |
| D3 | Rating source | **Wired.** Champion method. Progress logging inside `run_filter`. |

Forbidden actions **not taken:** no R2 put; no fit/retrain/promotion; no
lockbox module edit; no Prefect create/apply; no site/`push.py` edit; no
real `tier_state` / `idempotency` / `filter_history` write.

---

## Amendment 2 — D3: champion method is production

### Construction

```text
obs_seasons = range(2019, season+1)   # 2025 included for 2026; NOT a WF replay list
wf          = task23_fundamental_full_reduced_v3  # replay 2019–2024
assert_lockbox_excluded(wf.replay)
obs         = build_observations_from_staged(plays-preferred)
priors      = load_fitted_priors_frame_for_backtest(staged, replay)  # 2019–2024
engine.initialize_season(season, as_of)  # run_filter event_time < as_of
```

2025 has no Task 15 prior rows. `_priors_for_season` returns `{}` when the
priors frame has no rows for that season, so `run_filter` **season-regresses**
at the 2025 boundary instead of cold-starting every team at 0.

`run_filter` logs `run_filter_start`, per-week `run_filter_progress`
(`i`, `n_obs`, `season`, `week`, `elapsed_sec`), and `run_filter_done`.

### Lockbox both halves

| Half | Behavior | Test |
|------|----------|------|
| State through 2025 | `live_observation_seasons(2026)` includes 2025; live path loads those seasons as Kalman observations | `test_live_observation_seasons_include_2025_for_2026`; mocked `live_predict_rows` asserts 2025 in the observation load |
| 2025 as replay | `assert_lockbox_excluded` / `validate_ablations` raise; `live_predict_rows(2025, …)` raises `LockboxSeasonError` | `test_2025_as_replay_season_refused`; `test_live_predict_refuses_lockbox_season`; v3 YAML replay excludes 2025 |

### Determinism (paste both hashes)

Two consecutive `live_predict_rows(2026, 1)` calls, no cache:

```text
rating_digest_a = 96e8030a9c413a14b84175a4690921f7dce9de5b300234f7e7c110f7eb35e859
rating_digest_b = 96e8030a9c413a14b84175a4690921f7dce9de5b300234f7e7c110f7eb35e859
pred_hash_a     = 5d714e90f97dc91c4b95c364b757349ef3b730062fcdb0f201f2dcee09588a0e
pred_hash_b     = 5d714e90f97dc91c4b95c364b757349ef3b730062fcdb0f201f2dcee09588a0e
max_mu_delta = 0.0
```

2026 week 1 Kalman: `n_obs=5997` (`n_2025_obs=933`), filter ~223 s.
2024 week 5 Kalman: obs built 5064, filter 4452, ~150 s.

### 2026 week 1 live vs W9-I dry

| | W9-I dry (Task 14 snapshot) | W9-L live (champion method) |
|--|----------------------------:|----------------------------:|
| n | 99 | **91** (8 kickoff `< as_of`) |
| μ | [−16.97, 48.61] | [−17.28, 49.81] |
| σ | [15.20, 23.77] | [15.11, 23.31] |
| tiers | strong 73 / clear 17 / toss 2 / lean 7 | strong 70 / clear 13 / toss 3 / lean 5 |
| null μ / σ-refused | 0 / 0 | 0 / 0 |
| `as_of` | overrode `event_time←start_date`; would have been ingest Tuesday on clamped lake | `2026-09-01T10:00:00Z` from staged `event_time` |

Difference = champion vs Task 14 **plus** the 8-game kickoff filter.

Model identity on the artifact: `champion_version=3`,
`model_version=production-v0_reduced_v3`,
`run_id=task23_fundamental_reduced_v3`.

---

## Amendment 2 — D2 addendum: published slate excludes played games

`as_of` policy stands: one Tuesday per CFBD week, matching the validated
run. The artifact then **drops** games with `start_date < as_of`. Kickoff
is `start_date`; `event_time` is kickoff+duration (ADR 0016) and is not
the filter key. General rule, not a week-1 special case.

2026 week 1 publish log:

```text
W9-L slate n_week=99 n_excluded_kickoff_before_as_of=8 n_publish=91
as_of=2026-09-01T10:00:00+00:00
```

Early IDs (not forecasts; must not render on This Week / Game Detail):

```text
401866408  2026-08-29T22:30:00Z
401864494  2026-08-29T19:00:00Z
401864577  2026-08-29T21:30:00Z
401858201  2026-08-29T23:00:00Z
401858202  2026-08-29T19:30:00Z
401856766  2026-08-29T16:00:00Z
401864570  2026-08-29T23:00:00Z
401862693  2026-08-30T02:00:00Z
```

Isolated `week_predictions.json`: 91 games, `early_in_artifact=[]`,
kickoff min `2026-09-03T22:00:00Z` (after `as_of`). Game Detail already
`notFound()` when `game_id` is missing from the artifact. No site edit.

`ProvenanceStrip` / `FEATURE_TIME=TUESDAY_DECISION` stay global and stay
true: every published game kicks off after `as_of`.

---

## Amendment 2 — D1 accepted: re-ingest before acceptance

Clamp reverted in `normalize_games_payload` (ADR 0016). Staged 2026 week 1
still carried W9-I clamped rows until this session re-ingested:

```text
uv run ncaa-quant ingest cfbd --seasons 2026 --endpoints teams,venues,games --force
```

Post-ingest:

```text
n_2026_games=888
n_week1=99
n_event_eq_ingested=0
n_event_gt_ingested=99
as_of=2026-09-01T10:00:00+00:00
```

Slate is still **99** vs 2024 w1=146 / 2025 w1=142. CFBD as of 2026-08-18
is still light; not a clamp artifact. `event_time_estimated` retained.
Calendar still reads `event_time`. 2024 w5 clock verified
`2024-09-24T10:00Z`.

---

## Residual 27 (record, not fix)

27 of 545 v3 week-1 rows (2021: 5, 2022: 11, 2023: 7, 2024: 4) carry
`as_of` after kickoff and sit inside the published 23-reval metrics.
Successor: **`W9-L-residual-week1-straddle-metrics`**. Revalidation was
**not** reopened.

Cheap ATS on v3 `predictions.parquet` (`p_ats_home` +
`realized_margin + spread_close`; home covers iff > 0; skip missing-p /
pushes):

| slice | n | ATS | log-loss |
|-------|--:|----:|---------:|
| week 1 2021–24 (gradable) | 516 | 46.9% | 0.896 |
| the 27 straddle rows | 27 | **81.5%** | 0.493 |
| week 1 without the 27 | 489 | 45.0% | 0.918 |

The 27 leaked rows **helped** week-1 ATS.

---

## 2024 week 5 through the live path

`as_of=2024-09-24T10:00Z`, 0 excluded, 56 publish. Quantile-crossing
warning on the 56-row batch.

Vs W9-P oracle parquet (`task23_fundamental_reduced_v2` weeks — different
mapping layer):

| field | min \|Δ\| | median | p90 | max |
|-------|----------:|-------:|----:|----:|
| μ | 0.208 | 9.643 | 17.630 | 34.340 |
| σ | 0.015 | 2.996 | 4.791 | 9.812 |
| p_ml | 0.002 | 0.163 | 0.244 | 0.300 |

Vs v3 week parquet
`data/backtests/task23_fundamental_reduced_v3/full/weeks/season=2024_week=5.parquet`
(`w5_v3_delta.json`):

| field | min \|Δ\| | median | p90 | max |
|-------|----------:|-------:|----:|----:|
| μ | 0.069 | **8.739** | 17.108 | 22.446 |
| σ | 0.027 | 2.710 | 5.493 | 9.677 |
| p_ml | 0.0004 | 0.117 | 0.331 | 0.534 |

Not 0.0 even vs v3: live is one-shot `initialize_season(2024, week-5
Tuesday)` over 4452 obs; walk-forward is yearly `initialize_season`
(week 1 − ε) + `update_after_games`. 0.0 was withdrawn; report, don’t
stop. 2026 week 1 has no in-season updates, so one-shot through 2025
**is** the specified entering-2026 construction.

Amendment 1 D3 (Task 14 vs champion method on the same 56 games, not
wired) remains the rating-source measurement:

| Field | min \|Δ\| | median | p90 | max |
|-------|----------:|-------:|----:|----:|
| `mu_margin` | 0.102 | **5.589** | **12.209** | **36.747** |
| `sigma_margin` | 0.207 | 2.408 | 4.582 | 6.102 |
| `p_ml_home` | 0.0004 | 0.075 | 0.291 | 0.507 |

---

## Isolation and zero R2

Script: `docs/notes/_artifacts/webapp-w9l/live_publish.py`. Isolated
export under `live_export/` / `live_state/` (`export_enabled=false`,
`push=False`). `execute_predict_publish` (not the idempotent wrapper).

`filter_history.parquet` (not a production input; hygiene only):

```
cc1e9a947cfbb074c0bad6b148b96df523f6ec607b7b53ddec1c9f776aa78814
```

`expected_possessions/live.json`:

```
e1101588c1bdb77b38a63a635802467793d2cf341537fe8311e2e2a312676df1
```

Both match W9-I / W9-P. The script hashed the five isolation paths before
and after and would have raised on any change; it completed
(`elapsed_sec≈621.8`). Workstation hysteresis/ledger files are under
`data/**` (gitignored) and are allowed to differ from earlier tasks; they
were not the write target.

Zero R2: `NCAA_QUANT_WEBAPP__EXPORT_ENABLED=false`; isolated `push=False`;
no `PutObject` / R2 hostname in the publish path. Redirected writes landed
only under `docs/notes/_artifacts/webapp-w9l/live_state/`.

Default `build_candidates` is `[]` (no dummy 0.05-edge `NEW_BET_CANDIDATE`).
Chaos still injects `_stub_build_candidates`.

---

## Quantile crossing (report only)

Warning fired on both the 2024 w5 56-row batch and the 2026 w1 91-row
batch (`quantile crossing detected; sorting predictions ascending`). Same
family as W9-I dry 99/99. No fix this task.

---

## `make test`

```text
uv run pytest -m "not live"
========= 909 passed, 1 deselected, 32 warnings in 269.65s (0:04:29) ==========
Required test coverage of 80% reached. Total coverage: 80.19%
```

`make lint` and `make typecheck` also green (ruff 210 files; mypy 123 source files).

New tests in `tests/unit/test_webapp_w9l.py`: observation seasons include
2025; v3 replay excludes 2025; 2025 in test/warmup/continuity and
`assert_lockbox_excluded` raise; `live_predict_rows(2025)` raises;
kickoff filter (including empty frame and missing `start_date`); missing-
season priors → `{}`; mocked champion path (2026 obs load includes 2025,
8-game-style filter keeps the post-`as_of` game); empty priors / obs /
season / all-kicked-off errors; no bet-candidate alerts; digest
determinism. W9-P isolated export tests inject `oracle_predict_fn` so
pytest never runs the 4-min Kalman.

---

## Amendment 1 (historical) — D1 clamp reverted, D2 single-week `as_of`, D3 measured

Amendment 1 applied D1 in code, reported D2 as policy, and **stopped
before wiring** either rating source. Amendment 2 overrides the stop:
champion method is wired; kickoff `< as_of` games are omitted from the
artifact rather than published under a global Tuesday label that would
be false for those rows.

D1: unplayed games keep `event_time = kickoff + duration`. Pandera and
`check_temporal_sanity` apply `event_time <= ingested_at` only when
`completed == True`. DESIGN §8 amended (ADR 0016). `event_time_estimated`
stays (ADR 0004).

D2 evidence (v3 walk-forward, one `as_of` per week):

```text
policy=single_week_as_of
n_week1_rows_2021_2024=545
n_early_kickoff_rows=27
2021 n=127 as_of=2021-08-31T10:00:00+00:00  kickoff_before_as_of=5
2022 n=136 as_of=2022-08-30T10:00:00+00:00  kickoff_before_as_of=11
2023 n=136 as_of=2023-08-29T10:00:00+00:00  kickoff_before_as_of=7
2024 n=146 as_of=2024-08-27T10:00:00+00:00  kickoff_before_as_of=4
```

---

## Phase 0 (closed)

### 0.1 — Decision calendar reads `event_time`, not `start_date`

```477:486:src/ncaa_quant/evaluation/walkforward.py
    def from_games(cls, games: pd.DataFrame) -> WeekDecisionCalendar:
        """Build the calendar from a games frame with ``season``, ``week``, ``event_time``."""
        if games.empty:
            return cls({})
        work = games.copy()
        work["event_time"] = pd.to_datetime(work["event_time"], utc=True)
        mapping: dict[tuple[int, int], WeekDecisionPoints] = {}
        for (season, week), wg in work.groupby(["season", "week"], sort=True):
            kicks = [to_utc(pd.Timestamp(ts).to_pydatetime()) for ts in wg["event_time"]]
            mapping[(int(season), int(week))] = decision_points_from_kickoffs(kicks)
        return cls(mapping)
```

After D1 unclamp + re-ingest, 2026 week 1 Tuesday is
`2026-09-01T10:00:00Z`.

### 0.2 — Live ratings vs walk-forward

Task 14 `filter_history` and champion v3 are the same Joseph-form Kalman,
not the same estimator over the same observations. Production uses the
champion method (ADR 0017). Hang diagnosis: W9-I’s “hang” was `run_filter`
itself (~4 min, no log line). Progress logging is now in the loop.
`record_weekly` True vs False is a ~2 s wash.

| Step | Wall |
|------|-----:|
| `run_filter` advanced 2019–2025 | ~226–236 s |
| `run_filter` plays 2019–2024 | ~190 s |
| `initialize_season(2024)` plays | ~157 s |
| `initialize_season(2026)` live (Amendment 2) | ~223 s |
