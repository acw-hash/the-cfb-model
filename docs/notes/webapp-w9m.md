# W9-M — serialize a deployable champion and verify it through `predict()`

**Date:** 2026-08-17  
**Status:** STOP — serialize and cold-load work; 2024 week-5 fixture oracle does not match  
**Authority:** ADR 0013 / 0014; `docs/notes/webapp-w9p.md` (`90312d9`);
`docs/notes/webapp-w9-0.md` (`7c31100`); `docs/notes/23-readout.md`; DESIGN
§1.2, §9.8.

**This task ends at a local registry artifact.** No R2 write, no publish, no
2026 ingest, no Prefect deployment, no grading / `track_record` recompute.

---

## Phase 0 (reported before any fit)

### 0.1 — Lockbox guard is evaluation-scoped, not access-scoped

**Did not trip STOP #2.** The guards refuse *evaluating* 2025 (predictions,
metrics, grading, walk-forward test/warmup/continuity seasons). They do not
guard Kalman-filter access to 2025 observations. Rating-engine code has no
lockbox check. This task's fit does not include 2025 in any walk-forward
season list and does not need 2025 to reproduce 2024 week 5.

#### Every site that raises `LockboxSeasonError`

One class, one raise:

```46:48:src/ncaa_quant/pipelines/predict.py
class LockboxSeasonError(ValueError):
    """predict_fn refused a lockbox season (2025 is never evaluated)."""
```

```137:142:src/ncaa_quant/pipelines/predict.py
    if season == LOCKBOX_SEASON:
        msg = (
            f"season {LOCKBOX_SEASON} is lockbox; predict_fn refuses it "
            "(producing predictions for 2025 is not permitted)"
        )
        raise LockboxSeasonError(msg)
```

**Operation guarded:** loading a stored champion *prediction frame* for season
2025 (`load_production_prediction_rows`). Publish/evaluate path. Not data
load, not filter propagation, not grading (that's a different exception).

#### Related evaluation guards (not `LockboxSeasonError`)

`LockboxViolation` in `src/ncaa_quant/evaluation/lockbox.py` — raised when a
development-time evaluation would *read* lockbox seasons. Call sites:

```45:73:src/ncaa_quant/evaluation/lockbox.py
def assert_lockbox_excluded(
    seasons: Iterable[int],
    *,
    context: str,
    confirmatory_read: bool = False,
) -> None:
    """Raise unless ``seasons`` excludes the lockbox season.
    ...
    seasons:
        Seasons the caller is about to evaluate on.
```

```375:379:src/ncaa_quant/evaluation/walkforward.py
        assert_lockbox_excluded(
            self.all_replay_seasons(),
            context=f"walk-forward run {self.run_id}/{self.ablation_id}",
            confirmatory_read=self.lockbox_confirmatory_read,
        )
```

- `WalkForwardConfig.validate_ablations` → `all_replay_seasons()` (test ∪
  continuity ∪ warmup).
- `ncaa-quant backtest run` → `assert_lockbox_excluded(replay_seasons)`
  (`src/ncaa_quant/cli.py`).
- `load_staged_odds_snapshots` → same assert, plus a refuse if loaded rows
  contain season 2025.

`GradeExportError` — grading, not state propagation:

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

Frontend `loadResultsSeason(2025)` remains a TypeScript throw (W5). Untouched.

#### Can the rating engine advance through 2025?

**Yes.** `StateSpaceRatingEngine.initialize_season` / `update_after_games`
call `run_filter` on observations with `event_time < as_of`. There is no
lockbox import in `src/ncaa_quant/ratings/`. Putting 2025 *games* in the
observation table and a 2026 as_of filters through 2025 as state
propagation. Putting 2025 in `WalkForwardConfig` test/warmup/continuity
raises `LockboxViolation` before compute — that is the evaluation guard.

Policy (`docs/lockbox_access.md`): prohibited without a confirmatory-read
row are coverage metrics and **any model or betting output** (predictions,
edges, CLV, walk-forward metrics). Kalman-updating team state from 2025
completed games is not those things. Training the mapping layer on 2025
*labels* would be, and would require 2025 in warmup → blocked. That is
correct. Not relaxed here.

#### Would a 2026 fit/predict need 2025 and be blocked?

- **2026 week-1 predict, mapping last fitted on 2019–2024:** needs rating
  state entering 2026 ⇒ 2025 observations in the Kalman table. Rating engine
  will not trip. Sanctioned `backtest run` would trip *if* 2025 were added to
  replay seasons. A production predict path can load 2025 observations
  without listing 2025 as a walk-forward evaluation season.
- **2026 mapping-layer retrain using 2025 outcomes as labels:** blocked by
  `assert_lockbox_excluded` if 2025 is a walk-forward season. That is
  evaluation-scoped, and a policy decision for a later task.

**This fit** uses champion config seasons `{2019,2020,2021,2022,2023,2024}`
truncated through 2024 week 5. 2025 is not loaded as games, plays, or
odds snapshots.

#### State on disk at end of 2025

`data/artifacts/state_space/filter_history.parquet` — 37,869 rows, seasons
2014–2025, 3,386 rows with `season==2025`, mtime 2026-08-10T19:10:49Z.
Matches Task 14's full 2014–2025 filter (`history_rows=37870`, wall clock
~2.7 s, `docs/notes/14.md`). Produced as Stage-1 state hygiene / production
artifact, **not** from the champion walk-forward (which never loaded this
file and only replayed 2019–2024 observations). Legitimate as Kalman
history; **not** the rating path that produced champion-3 week-5 μ. Using
it as-of 2024-09-24 would not be the same object as the walk-forward
in-process snapshot. This task does not load it (priors hit
`data/tmp/priors_acceptance_15/week1_priors.parquet`, seasons 2023–2024
only — same CLI cache path the champion run used).

---

### 0.2 — Is the fit deterministic, and would it reproduce champion 3?

**Prediction, stated before the fit ran: approximately, with a plausible
path to exact μ/σ on this machine.** Record, then test. Exact 0.0 on the
oracle is the acceptance bar; if the test shows nonzero delta, STOP.

#### Seeds (all pinned in config, not defaulted at the walk-forward)

Champion YAML `configs/ablations/task23_fundamental_full_reduced_v2.yaml`:

```
  seed: 42
  model_version: production-v0_reduced_v2
```

Manifest from the original run (`data/backtests/task23_fundamental_reduced_v2/full/manifest.json`):

```
"seed_manifest": {
  "extra": {},
  "global_seed": 42,
  "lightgbm_seed": 42,
  "numpy_seed": 42,
  "python_hash_seed": "42",
  "xgboost_seed": 42
}
```

`set_global_seed(42)` inside `WalkForwardHarness.run` sets `random`,
`numpy`, and env `LIGHTGBM_RANDOM_SEED` / `XGBOOST_RANDOM_SEED` /
`PYTHONHASHSEED`. **`PYTHONHASHSEED` is process-start only** — setting it
mid-run does not freeze hash randomization in the current interpreter.
The original run had the same limitation. Heads use `seed=42` via
`BasePredictor` default; `ProductionEnsemblePredictor.seed` is
`cfg.seed` (42) for MC / epistemic draws. LightGBM params include
`"seed": self.seed`, `"deterministic": True`, `"force_row_wise": True`.
ElasticNet `random_state=self.seed`.

#### Order / threads / BLAS

- Walk-forward is **order-dependent by design** (revealed labels accumulate;
  retrains at week 0 / 5 / 10). Same game order ⇒ same training windows.
  Truncating *after* 2024 week 5 is PIT-prefix identical to the original
  2024-w5 predict (later weeks cannot leak backward).
- LightGBM `num_threads` is **not** pinned. `deterministic` + `force_row_wise`
  are intended to make histogram building thread-count independent; residual
  OpenMP risk remains.
- NumPy/BLAS version: original `environment_lockfile_hash` was
  `935eb1fa…`. Current `uv.lock` SHA-256 is `f083e403…` (**mismatch**). Diff
  vs original git SHA `4e29b7f` is **boto3 / botocore / jmespath / s3transfer
  / jsonschema only** — no numpy / scipy / scikit-learn / pandas / lightgbm
  hunks. ML library vintage is reconstructible.

#### Data vintage

Champion `created_at` 2026-08-11T19:19:59Z, `git_sha=4e29b7f`, `git_dirty=false`.
Staged parquet files modified after that timestamp: **8 files, all 2026 odds
partitions** (2026-08-12). Fundamental stack has
`market_features_available: false`. Replay seasons are 2019–2024. Those 2026
odds files are not on the load path.

#### 0.2 outcome

**The truncated walk-forward on this tree did not reproduce champion 3.**
The Phase 0 prediction of "approximately, possible exact" was tested and
failed. Cold-load `predict()` matches **this** fit at 0.0 (serialize is
faithful). It does not match the published fixture. See the run section.
Not retried: another 87-minute fit on the same code cannot recover the
`4e29b7f` mapping after ADR 0014 / week-align / market as-of.

---

### 0.3 — What must be serialized for `predict()` to work

`ModelRegistry` index schema (reader: `src/ncaa_quant/registry/store.py`):

```python
@dataclass
class RegistryIndex:
    model_name: str
    versions: list[ModelVersionRecord] = ...
    champion_history: list[int] = ...
    overrides: list[dict[str, Any]] = ...
```

Each `ModelVersionRecord`: `version`, `stage`, `run_id`, `artifact_dir`,
`registered_at`, `manifest`, `metrics`, `notes`, `feature_signature`,
`prior_champion_version`. Index file name: `registry_index.json`. Inference
must call `resolve_champion()` (never a hardcoded version id).

| State | Where it lives today | Registry format |
|---|---|---|
| Rating-engine `_states` at 2024-w5 Tuesday as_of | In-process during walk-forward only (filter_history is a *different* 2014–2025 Kalman) | `rating_snapshot.json` captured at week-5 `compute_game_features` |
| Mapping-layer heads (LGBM μ margin/total, ENet μ + `StandardScaler` + training medians, σ heads, quantile head) | In-process; `BasePredictor.save` pickle exists per head but the ensemble was never saved | `production_ensemble.pkl` (whole `ProductionEnsemblePredictor`) |
| NNLS / `FittedEnsemble` / `member_status` / `_null_reason` | In-process after last fit | inside the pickle |
| CQR (`_cqr`) | In-process | inside the pickle |
| PIT maps (`_calibration`); `ml` / `ats_close` route to margin map, `ou_close` to total (`_apply_calibrator`). `models/calibrate.py` remains diagnostics-only | In-process | inside the pickle |
| Key-number kernel / ρ | In-process | inside the pickle |
| ADR 0014 thresholds (`MAX_CREDIBLE_MARGIN_PRED=80`, `MEMBER_DEGENERACY_SD_EPS=1e-12`, `NULL_SHARE_DROP_THRESHOLD=0.50`) | Code constants | recorded in `state_inventory.json` (not fitted) |
| Feature scalers | ENet `_scaler` only | inside the pickle |
| Team-id index | Keys of rating snapshot `tid:dim` | `rating_snapshot.json` |
| As-of features for the oracle week | Walk-forward `feature_log`, never persisted | `predict_features.parquet` (Tuesday as_of, pre-reveal) |
| Possessions PIT artifacts | `ProductionFeatureProvider._possessions_artifacts`; live.json is **not** the walk-forward path | `possessions_artifacts.pkl` |
| CFBD close lines (for ATS/OU derived probs; ML `p_win_home` does not need them) | `predictor.cfbd_lines` DataFrame | inside the pickle |
| `n_mc_draws=100_000`, `n_epistemic_draws=50`, seed 42 | constructor args | inside the pickle |

Nothing required by `predict(features)` was "never persisted and cannot be
reconstructed": heads have pickle hooks; ensemble state is in-memory after
a walk-forward `fit()`. This task persists that object. Features for the
oracle week are captured at the same call the original harness used.

**Did not trip STOP #7.**

Gated retrain flow (`execute_retrain_gate`) is a stub (`_default_retrain`).
Documented fit entry point is `WalkForwardHarness.run` via the same data
loading as `ncaa-quant backtest run`. This task uses that path, truncated
through 2024 week 5, writing **only** under `data/registry/` (no
`data/backtests/` overwrite, no MLflow).

---

## Isolation hashes (this fit / this verify)

`W9-M isolation_changed=[]` on fit. `W9-M verify_isolation_changed=[]` on
verify. Week parquets, `filter_history.parquet`, and
`expected_possessions/live.json` kept their W9-P hashes. Webapp / idempotency
files already differed from the W9-P snapshot **before** this fit started
(tests / other local work); this task did not mutate them.

| Path | SHA-256 before = after this fit |
|---|---|
| `data/artifacts/expected_possessions/live.json` | `e1101588c1bdb77b38a63a635802467793d2cf341537fe8311e2e2a312676df1` |
| `data/artifacts/state_space/filter_history.parquet` | `cc1e9a947cfbb074c0bad6b148b96df523f6ec607b7b53ddec1c9f776aa78814` |
| `data/backtests/.../season=2024_week=5.parquet` | `f2bd72de058f4d75fd806ad486b101187df2704ca0a24714806797f4ce033c52` |
| `data/pipeline_state/idempotency.json` | `98b081420332327daacdda16b39142deec0f592372b6c0b57618ee7b3cd10412` |
| `data/webapp/tier_changes.jsonl` | `8b32d00199ccb9503a7378a19339fad2d6d5047cd8aa8aa990037e31accfdfb5` |
| `data/webapp/tier_state.json` | `15b561567283a92f708e61861e455b85c4454e5614a78d3dcd28864c9cb97b8f` |

W9-P snapshot (for contrast; not a mutation by this task):

```
9ec5af26…  data/webapp/tier_state.json
c253dc13…  data/webapp/tier_changes.jsonl
3d48d7bb…  data/pipeline_state/idempotency.json
```

---

## Fit / serialize / gate / oracle

### Fit

```
uv run python -m ncaa_quant.registry.champion_serialize fit
```

- **pid** 8644. Started `2026-08-17T17:00:52Z`, ended `2026-08-17T18:28:31Z`.
  Wall clock **5259.540 s** (~87.7 min). Log:
  `docs/notes/_artifacts/webapp-w9m/fit.log`.
- A second copy of the same command was started ~12 min later and **killed**
  at ~2:10 PM so only pid 8644 finished. CPU contention during the overlap
  is noted; it is not the primary explanation for the oracle miss (see
  below).
- `replay_seasons=[2019, 2020, 2021, 2022, 2023, 2024]`. Lockbox 2025 not in
  replay. `2025_rows_in_fit_games=0`. Filter-history **not loaded**.
- `games_full=5069` → `games_truncated=4556`, `max_2024_week=5`.
- Seed manifest: all 42 (same as champion YAML).
- Quality gate **passed** (`force` unused):
  `passed=true`, `failures=[]`, `n_scored=3773`, `n_ungradable=90`,
  `n_null_mu=0`, `zero_mu_rate=0.0`, `absent_blocks=[[2019, 1]]`.
- Promotion: `evaluate_promotion_gate(..., force=False, manual_approve=True)`
  → `approved=true`, reason `"gate passed and manually approved"`. Registry
  `v1` candidate → challenger → **champion**. Published export still stamps
  `champion_version=3`.
- Writes only under `data/registry/` (gitignored). No MLflow, R2, Prefect.
  Fit/verify logs: no `PutObject`, no R2 hostnames.

Registry inventory (`data/registry/artifacts/v1/`):

| Artifact | bytes | sha256 |
|---|---|---|
| `production_ensemble.pkl` | 8,134,593 | `dc40f500150ccf5740603fc059b15935ed70996544cf26e1a2b186d289446e64` |
| `predict_features.parquet` | 16,750 | `3e5cc15ca156eea5820da2c36595bd4d5f40d4973c86cd395d446528654b2d2b` |
| `rating_snapshot.json` | 73,970 | `ea5b19a65bfe364fec6429b455fe3a4d9706e212e5b6ef7029c46f4c6c4694db` |
| `week_predictions.parquet` | 51,371 | `b9e36f7e468ad1c18c648ebc73884c0f3afb88bd93312098fdd5237382cd36bb` |
| `possessions_artifacts.pkl` | 482 | `8c3c8bd2d0e273089596264c34c7161674708958c790a486267f1410d9db714f` |

`rating_snapshot.json`: 1,928 keys, 241 team ids. Features: 56 rows,
Tuesday 2024 week 5 as-of, captured **before** that week's reveal.

Retrain at week 5 runs **before** that week's `predict()` (labels through
week 4 only). Serializing after `harness.run()` returns is therefore the
same mapping used for the in-run week-5 rows. Confirmed: in-run parquet vs
cold-load `mu_margin` max |Δ| = **0.0**.

### Verify (fresh process)

```
uv run python -m ncaa_quant.registry.champion_serialize verify
```

- **verify pid** 33504. Fit had already exited. `pids_differ=True`.
- Isolated export: `export_enabled=False`. Writes under
  `data/registry/oracle_export/` only (including a `track_record.json` from
  the reused W9-P helper). Live `data/webapp/` untouched.
- Identity vs fixture: `champion_version=3`,
  `run_id=task23_fundamental_reduced_v2`,
  `model_version=production-v0_reduced_v2`. Game-id sets identical, 56/56.

### Oracle vs `webapp/fixtures/week_predictions.json`

**STOP. Nonzero delta. Bar was 0.0.**

| Field | max \|Δ\| |
|---|---|
| `mu_margin` | 20.19956592913842 |
| `sigma_margin` | 6.22622556295941 |
| `margin_interval_lo` | 14.423024319553612 |
| `margin_interval_hi` | 16.31250632337884 |
| `mu_total` | 9.369518059996835 |
| `sigma_total` | 4.633380013741702 |
| `p_win_home` | 0.41364220718108063 |
| `p_favored` | 0.0 |

Conviction-tier agree: **19/56**.

Disaggregation:

| Comparison | max \|Δμ_margin\| |
|---|---|
| This fit in-run week-5 parquet vs original champion parquet | 20.199… |
| This fit in-run vs cold-load `predict()` | **0.0** |
| Original champion parquet vs fixture | **0.0** |

So pickle → cold-load `predict()` is exact. The truncated walk-forward
**itself** is not champion 3. New μ range [−18.18, 35.51] vs original
[−6.92, 15.31]; correlation of the two μ vectors is 0.915 (ranking
similar, scale more extreme). Example: game `401628378` 15.31 → 35.51.

Primary cause is **code drift after the champion fit**, not truncation
and not a broken serializer. Champion 3 `git_sha=4e29b7f`
(2026-08-11). That SHA is an ancestor of later mapping-path commits that
this tree includes:

- `ccf4032` Fix market feature as-of so post-kickoff snapshots cannot leak
- `c6404fc` Align CFBD-week decision points to kickoffs
- `18cf69f` Establish member-credibility contract (ADR 0014)
- `1dd439f` fixed technical specs

Refitting the ADR 0013 reduced stack on **today's** heads/credibility
rules cannot reproduce a `4e29b7f` parquet. Truncation after 2024 week 5
is still a PIT prefix of *this* walk-forward; it is not a time machine
for those commits. LightGBM `num_threads` remains unpinned (Phase 0);
overlap with the killed duplicate fit could add noise, but a 20-point
max / 6-point mean shift with more extreme tails matches a contract
change, not OpenMP jitter.

No second fit. No `force=True`. No lockbox edit. No R2.

### 2025 statement

Replay seasons exclude 2025. Truncated games exclude 2025. Priors cache
hit `data/tmp/priors_acceptance_15/week1_priors.parquet` (2023–2024).
`filter_history.parquet` was not loaded.

---

## Decisions

1. **Evaluation-scoped lockbox; do not relax any guard.** 2025 stays out of
   walk-forward seasons. Filter-history is not loaded.
2. **Truncate games after 2024 week 5** rather than running the full
   2019–2024 walk-forward. Week-5 predict is a PIT prefix of *this* run;
   it does not rewind ADR 0014 / week-align / market-as-of.
3. **Serialize the whole `ProductionEnsemblePredictor`** (not per-head
   `save()` only) so CQR / PIT / NNLS / member_status survive.
4. **Human gate is `evaluate_promotion_gate(..., force=False, manual_approve=True)`**
   with `gate_passed` from the walk-forward D2 quality gate. First registry
   pin is local `v1`; published `champion_version: 3` remains the export
   hardcode (changing it would change a published field vs the fixture).
5. **Cold-load `predict()` consumes captured Tuesday features**, not
   end-of-run (post-reveal) ratings. Mapping state after `harness.run()` is
   the week-5-retrain mapping (week-5 labels not yet in that fit).
6. **Oracle miss is a STOP, not a tolerance.** Do not "fix" by swapping in
   the original parquet or editing fixture fields. Deploying this `v1`
   pickle as if it were champion 3 would publish different μ/σ.

---

## What was built

- `src/ncaa_quant/registry/bundle.py` — pickle save/load of a fitted
  `ProductionEnsemblePredictor`.
- `src/ncaa_quant/registry/champion_serialize.py` — truncated sanctioned
  walk-forward, registry promotion, two-process verify vs fixture.
- `src/ncaa_quant/pipelines/predict.py` — optional `predict_fn=` on the
  isolated-week export helper (live predictor, captured features).
- `tests/unit/test_webapp_w9m.py` — pickle roundtrip, gate `force=False`,
  truncate, isolation paths, lockbox still raises, mocked fit/verify.

`data/registry/**` remains gitignored.

---

## Spec gaps recorded

- DESIGN / ADRs do not name a serialize format. Chose whole-ensemble pickle
  + JSON snapshot + parquet features under `ModelRegistry` extra artifacts.
- Local registry version (`1`) is not the published `champion_version` (`3`).
  Export still hardcodes 3 so the published identity field is unchanged.
- Reproducing a historical champion μ requires fitting **that** git SHA (or
  loading its pickled mapping). Current-code retrain is a new champion.

---

## Acceptance

| Bar | Result |
|---|---|
| Isolation hashes unchanged by fit and verify | **pass** (`[]`) |
| Quality gate passed without `force=True` | **pass** |
| Promotion `force=False`, `approved=True` | **pass** |
| Fit process exited before verify; pids differ | **pass** (8644 vs 33504) |
| `export_enabled=False`; no R2 / PutObject | **pass** |
| Cold-load vs **this fit** max \|Δμ\| = 0.0 | **pass** |
| Cold-load vs **fixture** max \|Δμ\| = 0.0 | **STOP** (20.20) |
| 56 games, identical `game_id` set, identity fields | **pass** |
| Conviction 56/56 | **STOP** (19/56) |
| `make test` | **pass** — 866 passed, 1 deselected, coverage 80.62% |

No follow-up fit until a later task explicitly chooses either (a) refit at
`4e29b7f` for byte identity with the fixture, or (b) accept current-code
`v1` as a new champion and update the published oracle.
