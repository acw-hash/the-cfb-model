# W9-P — wire `predict_fn` and prove it against the fixture oracle

**Date:** 2026-08-17  
**Status:** Complete  
**Authority:** `docs/notes/webapp-w9-0.md` (`7c31100`); ADR 0013 / 0014;
DESIGN §1.1–§1.3, §1.7; `docs/notes/webapp-w1.md`; lockbox rule in
`docs/notes/23-readout.md`.

**This task ends at a local artifact.** No R2 write, no push, no revalidation,
no deployment, no schedule, no 2025 read, no grading, no `track_record`
recomputation.

---

## Phase 0 contract (reported before the seam was wired)

### 1. `predict_fn` stub — signature, module, callers

`PredictFn = Callable[[StaleContext], list[dict[str, Any]]]` in
`src/ncaa_quant/pipelines/predict.py`. The only argument is `StaleContext`
(no season/week). Season and week live on `execute_predict_publish`.

Pre-task stub:

```python
def _default_predict(_stale_ctx: StaleContext) -> list[dict[str, Any]]:
    return []
```

Callers:

| Site | What it passes |
|------|----------------|
| `execute_predict_publish` | `predict_fn or _default_predict` |
| `run_predict_publish` | forwards `predict_fn` |
| `predict_publish_task` / `predict_publish_flow` | **does not pass** `predict_fn` → empty default |
| `run_fixture_week_publish` | synthetic `g-fix-1` / `g-fix-2` with stub names |
| `run_chaos_stale_publish` | synthetic `g-chaos-1` |
| `tests/unit/test_webapp_w1.py` | synthetic `999` |
| CLI `ncaa-quant predict` | `_not_wired` (unchanged) |

`PredictFn` is **not** on the Prefect `@flow`/`@task` signature (Task 24:
Prefect rejects Callable params). Wiring the default inside
`execute_predict_publish` is the live path.

### 2. What callers expect back; where the rename sits

Two consumers of the same `list[dict]`:

1. **`stamp_predictions`** (immediate) requires **stub names** and `float()`:

```python
StampedPrediction(
    game_id=str(row["game_id"]),
    mu_margin=float(row["mu_margin"]),
    sigma_margin=float(row["sigma_margin"]),
    ...
)
```

Index is list order (no DataFrame index). Missing `mu_margin` /
`sigma_margin` is a `KeyError`. `None` is a `TypeError`.

2. **`export_publish_artifacts`** merges `prediction_rows` (raw return) then
   overlay `predictions` (stamped). `build_game_prediction` rename is
   **downstream** via `_field` first-present:

| Artifact field | First key | Fallback (production) |
|----------------|-----------|------------------------|
| `mu_margin` | `mu_margin` | `pred_margin` |
| `sigma_margin` | `sigma_margin` | `sigma_m` |
| `p_win_home` | `p_win_home` | `p_ml_home` |
| `mu_total` | `mu_total` | `pred_total` |
| `sigma_total` | `sigma_total` | `sigma_t` |
| interval lo/hi | `margin_interval_*` | `cqr_lo`/`cqr_hi` / `pred_margin_q05`/`q95` |

**Contract (unambiguous):** return production columns **and** stamp aliases.
Do not drop `pred_margin` / `sigma_m` / `p_ml_home`. The rename layer stays
in export. The seam adds aliases only so `stamp_predictions` can `float()`.

### 3. Real predictor entry point

`ProductionEnsemblePredictor.predict(features: pd.DataFrame) -> pd.DataFrame`
(`src/ncaa_quant/evaluation/production_stack.py`). Required live state:

- fitted heads + NNLS + σ + CQR + PIT calibrators (`fit()`);
- `ProductionStack.rating_engine.state_snapshot()` as of the Tuesday
  decision;
- `ProductionFeatureProvider.compute_game_features(games, as_of, rating_state)`;
- champion via `ModelRegistry.resolve_champion()` — **`registry_index.json`
  is absent**. No pickle / ONNX bundle on disk. Calibrator files = 0.

How a caller obtains a week frame **without** retraining:

`data/backtests/task23_fundamental_reduced_v2/full/weeks/season={Y}_week={W}.parquet`

That file is the WalkForwardHarness record of
`ProductionEnsemblePredictor.predict()` (W1 fixture source; 56 rows for
2024 week 5; `run_id=task23_fundamental_reduced_v2`;
`model_version=production-v0_reduced_v2`).

Live `.predict()` on a cold `ProductionEnsemblePredictor` is a mapping-layer
**retrain**. Full champion walkforward wall clock was **2810 s (~47 min)**
(`data/backtests/task23_fundamental_reduced_v2/full/manifest.json`).
Forbidden here. Rating Kalman replay of history before 2024 week 5 is
seconds (`filter_history` Task-14 replay ~2.7 s) — but **loading
`filter_history.parquet` would read season 2025 rows** (seasons
2014–2025 present). This task does not load it.

### 4. Columns `build_game_prediction` reads vs predictor names

Exact published keys: `PUBLISHED_GAME_PREDICTION_KEYS` (schema 1.2.0).
From the predictor/walkforward frame it reads (via `_field`): `game_id`,
`pred_margin`/`mu_margin`, `sigma_m`/`sigma_margin`, `sigma_m_is_missing`,
`null_reason`, `cqr_lo`/`cqr_hi`/`cqr_nominal` or `pred_margin_q05`/`q95`,
`pred_total`/`mu_total`, `sigma_t`/`sigma_total`, `sigma_t_is_missing`,
`p_ml_home`/`p_win_home`, `p_ml_home_is_missing`. Stamp overlay supplies
`is_stale` / `stale_stamp` / `stale_sources`. Schedule supplies team names,
ids, kickoff. Conviction is computed at export.

Predictor names that **must not** be copied through: `spread_*`, `total_*`,
`p_ats_home`, `p_ou_over`, `p_mkt_*` (denylist / ADR 0015 withdrawn
published keys). `build_game_prediction` does not copy them.

### 5. Stateful writes (every path)

| File | Writer | This task |
|------|--------|-----------|
| `data/webapp/tier_state.json` | `TierStateStore.save` in `build_week_predictions` | redirected to temp |
| `data/webapp/tier_changes.jsonl` | `append_tier_change_records` when `record_tier_changes=True` | redirected to temp |
| `data/pipeline_state/idempotency.json` | `run_predict_publish` / `IdempotencyStore` | **not used** (`execute_predict_publish` only); dir still redirected |
| `data/pipeline_state/dead_letter/` | DLQ | unused; redirected |
| MLflow `mlruns/` | backtest runner, not `predict()` | unused |
| `registry_index.json` | `ModelRegistry._write_index` | **absent**; unused |
| `data/artifacts/state_space/filter_history.parquet` | Stage-1 filter | **not loaded** (contains 2025) |
| `data/artifacts/expected_possessions/live.json` | live possessions | unused |
| Champion week parquet | backtest | **read only** |

SHA-256 (identical before and after the isolated run):

```
9ec5af263df32ff1ed49e7867038f402f526a0a0e5c94bbbac50beeaaeb11fc9  data/webapp/tier_state.json
c253dc136a961348c2075f89e4bd2c49aa5e76a4786e52ce8963aad4d5135a2d  data/webapp/tier_changes.jsonl
3d48d7bb6f41a8ac1cc1441d16ca293177ef443b734323ca48db026efc446db5  data/pipeline_state/idempotency.json
cc1e9a947cfbb074c0bad6b148b96df523f6ec607b7b53ddec1c9f776aa78814  data/artifacts/state_space/filter_history.parquet
e1101588c1bdb77b38a63a635802467793d2cf341537fe8311e2e2a312676df1  data/artifacts/expected_possessions/live.json
f2bd72de058f4d75fd806ad486b101187df2704ca0a24714806797f4ce033c52  data/backtests/task23_fundamental_reduced_v2/full/weeks/season=2024_week=5.parquet
```

### 6. 2024 week-5 rating state: load vs retrain

| Layer | Reconstruct? | Time |
|-------|--------------|------|
| Stage-1 ratings | Load `filter_history` as-of Tuesday **or** Kalman-replay observations with `event_time < as_of` | load / ~seconds. **Not done** — filter file contains 2025. |
| Mapping layer + calibrators | No serialized champion. `fit()` + full walkforward | **~47 min**. Forbidden. |
| Stored `predict()` frame | Week parquet present | **immediate** |

Wiring uses the stored frame. Ratings were not reconstructed.

**Did not trip STOP #2** (rating backfill) because ratings were not rebuilt.
**Did not trip STOP #5** (`champion_version`): both sides are `3`.
**Did not read 2025.**

---

## What was built

- Default `predict_fn` loads the champion week parquet, refuses season 2025
  (`LockboxSeasonError`), aliases `pred_margin`→`mu_margin` and
  `sigma_m`→`sigma_margin` for the stamp layer, keeps production names for
  export.
- `run_isolated_week_export`: `export_enabled=False`, temp
  `tier_state` / `tier_changes.jsonl` / idempotency dir, `push=False`,
  writes JSON under a caller output dir.
- `export_publish_artifacts` passes `run_id` / `model_version` from
  production rows into `model_identity` (still `champion_version: 3`;
  registry still unused).
- Tests in `tests/unit/test_webapp_w9p.py`.

No predictor/ensemble/calibrator/schema/ADR/DESIGN change.

---

## Isolation harness + run command

```
uv run pytest tests/unit/test_webapp_w9p.py::test_isolated_2024w5_oracle_against_fixture -s -o addopts=
```

Config proving export was disabled (printed by the harness, not from `.env`):

```
W9-P isolated export_enabled=False
```

Artifacts written (pytest temp; not repo `data/webapp/`):

```
W9-P wrote .../artifacts/week_predictions.json
W9-P wrote .../artifacts/track_record.json
W9-P wrote .../artifacts/team_ratings_2024.json
W9-P wrote .../artifacts/meta.json
W9-P push=False (R2 disabled; no upload)
```

`track_record.json` is the frozen 23-readout template (`build_track_record`)
with the replay `published_at`. That is a copy of locked metrics, not a new
evaluation. `results_*.json` was not written. `grade_export` was not called.

Champion identity loaded:

```
production_predict_loaded  champion_version=3
  model_version=production-v0_reduced_v2 n=56
  path=.../season=2024_week=5.parquet
  run_id=task23_fundamental_reduced_v2 season=2024 week=5
W9-P champion_version=3
W9-P model_version=production-v0_reduced_v2
W9-P run_id=task23_fundamental_reduced_v2
W9-P n_prediction_rows=56
```

R2 evidence: `export_enabled=False` + `push=False` + grep of the run log
for `PutObject` / `r2.cloudflarestorage.com` / `amazonaws.com` → **zero**.

---

## Fixture-oracle comparison (2024 week 5 Tuesday primary)

Produced `week_predictions.json` vs committed
`webapp/fixtures/week_predictions.json` (schema 1.2.0).

| Check | Result |
|-------|--------|
| Game count | **56 = 56** |
| `game_id` sets | **identical** (all match `^[0-9]{6,12}$`) |
| `schema_version` | 1.2.0 / 1.2.0 |
| `champion_version` | 3 / 3 |
| `run_id` | `task23_fundamental_reduced_v2` / same |
| `model_version` | `production-v0_reduced_v2` / same |

Per-field maximum absolute delta (56 games):

```
field                    max_abs_delta
mu_margin                0.0
sigma_margin             0.0
margin_interval_lo       0.0
margin_interval_hi       0.0
mu_total                 0.0
sigma_total              0.0
p_win_home               0.0
p_favored                0.0
```

`conviction_tier` agreement: **56 / 56**. Disagreements: **none**.

Exact equality on μ/σ/intervals/probabilities/tiers is expected: W1
generated the fixture from the same parquet; this task sends that frame
through `predict_fn` → stamp → export with empty hysteresis (fixture
`conviction_basis.previous_tier` is also `null`).

Explained, non-numeric differences:

| Item | Fixture | Produced | Why |
|------|---------|----------|-----|
| Top-level `fixture` | `true` | absent | Replay is not labeled fixture |
| `team_ratings_2024.json` | 2024 trajectories from `filter_history` | `"teams": {}` | Must not load `filter_history` (contains 2025). Empty stub is the live export path when `filter_history=` is omitted. |
| `webapp/fixtures/week_predictions.legacy-1.1.0.json` | schema 1.1.0 + four withdrawn keys | n/a | 1.2.0 withdraws those keys; not an oracle for this run |

No unexplained divergence. Not a wiring error.

This week has **zero σ-refused rows** (`sigma_m_is_missing` all False).
σ-refusal → null probabilities + `sigma_margin_credible: false` is covered
by `test_sigma_refused_aliases_preserve_null_probabilities` (no zero-fill).

---

## Artifact conformance

- `schema_version` **1.2.0**
- Withdrawn keys absent: `p_cover_home`, `p_cover_home_credible`, `p_over`,
  `p_over_credible`
- `assert_game_prediction_allowlist` on every game
- `assert_no_denylisted_fields` empty
- `game_id` regex passed

`jq`-equivalent keys on one produced game (Python `sorted(game.keys())`):

```
away_team, away_team_id, conference_game, conviction_basis, conviction_label,
conviction_team, conviction_tier, ensemble_scope_label, feature_time_label,
game_id, home_team, home_team_id, is_stale, kickoff_utc, margin_interval_hi,
margin_interval_lo, margin_interval_nominal, mu_margin, mu_total, neutral_site,
null_reason, p_win_home, p_win_home_credible, published_at, refresh_kind,
season, sigma_margin, sigma_margin_credible, sigma_total, sigma_total_credible,
stale_sources, stale_stamp, tier_primary, tier_revised_since_primary,
total_interval_hi, total_interval_lo, total_interval_nominal, vintage_label,
week
```

Withdrawn four: **absent**. Retained `p_win_home` / `mu_margin` /
`sigma_margin` / `sigma_margin_credible`: **present**.

---

## Lockbox / 2025 / grading

- `load_production_prediction_rows(2025, 1)` raises `LockboxSeasonError`.
- Loaded path is `season=2024_week=5.parquet` (no `2025` token).
- `load_schedule_frame(season=2024, week=5)` and `load_teams_frame(season=2024)`
  only — not `season=2025`.
- `filter_history` not passed (and not mentioned in the run log).
- `grade_export` / `build_results_season` not called; `results_*.json` not
  written. `loadResultsSeason(2025)` TS throw: **untouched**.
- `make test` lockbox tests still pass.

No evaluation claim for any season.

---

## Allowlist bite (acceptance 8)

```
poisoned["unsanctioned_edge"] = 0.03
pytest.raises(PublishedKeyAllowlistError, match="unsanctioned_edge")
assert_game_prediction_allowlist(game)  # pass after remove
```

`tests/unit/test_webapp_w9p.py::test_allowlist_bite_on_wired_game`.

---

## Acceptance

1. **`make test`:** 850 passed, 1 deselected (`live`), 29 warnings.
   Coverage 80.60% (≥ 80). ~4 m 32 s.
2. Run console: isolation prints + `production_predict_loaded` (above).
3. Comparison table: above; all deltas 0.0; tiers 56/56.
4. SHA-256: above; identical.
5. R2: `export_enabled=False`; log grep `PutObject` / endpoint hostnames = 0.
6. Keys dump: above.
7. No 2025 / no grading / no `track_record` recompute: path + lockbox test +
   no `results_*.json` + no `filter_history` load.
8. Allowlist bite: test named above.

---

## Decisions

1. **Stored `predict()` frame, not a live `ProductionEnsemblePredictor.fit()`.**
   Minimum viable wiring that closes the stub without retraining or reading
   2025. Live 2026 week 1 still needs a serialized champion (later task).
2. Stamp aliases live in `predict_fn`; rename stays in export `_field`.
3. Isolation helper calls `execute_predict_publish` (not `run_predict_publish`)
   so the real idempotency ledger cannot be written even if a path is missed.
4. Empty `teams: {}` ratings artifact rather than loading `filter_history`.
5. `published_at` for the 2024 w5 replay is `2024-09-24T06:00:00Z` (same
   Tuesday clock as the fixture) so the oracle compares forecast fields, not
   wall-clock stamps.

CLI `predict` remains `_not_wired` (out of scope).
