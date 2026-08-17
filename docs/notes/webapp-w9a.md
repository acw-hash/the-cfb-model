# W9-A — Path A revalidation and full champion serialization

**Date:** 2026-08-17  
**Status:** DONE — full current-code walk-forward, local registry **v2**
champion, citable memo `docs/notes/23-reval.md`. Site literals **not**
restamped.  
**Authority:** `docs/notes/webapp-w9v.md` (`157bc7d`);
`docs/notes/webapp-w9m.md`; `docs/notes/23-readout.md` (FINAL — pointer
only); ADR 0005 / 0013 / 0014; week-align (`c6404fc`) + market-as-of.

**This task does not change anything the site renders.** No `build_track_record`,
`copy.ts`, fixtures, `/results`, or R2. No writes into
`data/backtests/task23_fundamental_reduced_v2/`. `force=True` forbidden.

Citable tables for a `/results` restamp successor: `docs/notes/23-reval.md`.

---

## Phase 0 (reported before any fit)

HEAD `157bc7d34e64462fb00a646d449bccdbf5bd15fe`. Tracked tree clean
(`git status --porcelain --untracked-files=no` empty). Machine idle; one fit
at a time.

Champion 3 tree SHA-256 (94 files under
`data/backtests/task23_fundamental_reduced_v2/`):

```
93561bae9b2d77da8d8393e9c76d5eebbec1afbb31e1d634c9900393074bd7d3
```

Unchanged after fundamental fit, A2, and grade.

Isolation five (identical before fit, after fit, after A2+grade):

| Path | SHA-256 |
|---|---|
| `data/webapp/tier_state.json` | `2b9f790bbf7e458e9866a0be9f4027d0d878b283942a0c4140d09e7b207d5f5a` |
| `data/webapp/tier_changes.jsonl` | `5cf2b943a97b3e1c615759ae0b27ec3b2799b5d59043b9d9a20713adc1b8d909` |
| `data/pipeline_state/idempotency.json` | `88c47e11c3180ed59344fa3fbd4fc8519025f4f2c6ffc6c6abb7578e8abd6452` |
| `data/artifacts/state_space/filter_history.parquet` | `cc1e9a947cfbb074c0bad6b148b96df523f6ec607b7b53ddec1c9f776aa78814` |
| `data/artifacts/expected_possessions/live.json` | `e1101588c1bdb77b38a63a635802467793d2cf341537fe8311e2e2a312676df1` |

`resolve_git_dirty` uses `--untracked-files=no`, so the new YAML / runner
were untracked at fit time and the manifests still record `git_dirty=false`.
This follow-up commit adds those files. ADR 0005 mechanical citable at SHA
`157bc7d`; YAML is now in the tree for a successor.

Workstation `AppConfig.export_enabled` was **True** at preflight. Both fits
were launched with `NCAA_QUANT_WEBAPP__EXPORT_ENABLED=false`. Code default
remains False. Logs: no `PutObject`, no R2 hostnames.
`docs/notes/_artifacts/webapp-w9a/preflight_hashes.json` had
`r2_bucket` / `r2_endpoint_url` stripped before commit.

---

## What was built

- `configs/ablations/task23_fundamental_full_reduced_v3.yaml` —
  `run_id=task23_fundamental_reduced_v3`,
  `model_version=production-v0_reduced_v3`, seasons 2019 + 2021–2024,
  continuity 2020, no 2025.
- `configs/ablations/task23_A2_rating_updates_frozen_reduced_v2.yaml` —
  `run_id=task23_a2_reduced_v2`,
  `model_version=production-v0_a2_reduced_v2`,
  `rating_updates=frozen_after_week_1`.
- `src/ncaa_quant/registry/w9a_revalidate.py` — one-process full walk-forward
  via `run_backtest`, 2024 week-5 capture vs W9-M oracle, serialize,
  `evaluate_promotion_gate(..., force=False, manual_approve=True)`. Never
  writes champion 3. Copies W9-M registry to `data/registry_w9m_truncated/`
  first.
- `tests/unit/test_webapp_w9a.py` — YAML lockbox, isolation, week-5 helper,
  promotion `force=False`.
- `scripts/_w9a_grade.py` — regrade **only** the new runs; skip existing
  `grade_v2`; A2 ATS-plausibility trip is recorded, not fatal.

Label:
`W9A-PATH-A;FEATURE_TIME=TUESDAY_DECISION;ensemble_scope=REDUCED_PER_ADR_0013;ADR_0014`

---

## Fundamental fit (pid **37468**)

```
START 2026-08-17T20:01:36Z
EXIT  2026-08-17T20:41:48Z
harness wall_clock_sec=2408.849
process wall_clock_sec=2412.666
log=docs/notes/_artifacts/webapp-w9a/fundamental.log
```

Manifest `data/backtests/task23_fundamental_reduced_v3/full/manifest.json`:

```
git_sha=157bc7d34e64462fb00a646d449bccdbf5bd15fe
git_dirty=false
created_at=2026-08-17T20:41:46Z
seasons_executed=[2019, 2020, 2021, 2022, 2023, 2024]
quality_gate: passed=true failures=[] n_scored=4286 n_null_mu=0
              n_ungradable=90 (blocks 2019 w2–4 only, no_credible_members)
              absent_blocks=[[2019,1]]
```

`n=4944`, **`N_2025=0`**, seasons 2019–2024.

Week-5 vs W9-M `data/registry/artifacts/v1/week_predictions.parquet` (and
backup): **0.0** on `mu_margin` / `sigma_margin` / `p_ml_home`, 56/56.
`docs/notes/_artifacts/webapp-w9a/week5_crosscheck.json`.

W9-M backup: `data/registry_w9m_truncated/` (do not delete).

Promotion: `approved=true`, `force=false`,
`reason="gate passed and manually approved"`, registry **v2 champion**
(v1 archived, artifacts kept).
`docs/notes/_artifacts/webapp-w9a/promotion_gate.json`.

```
uv run ncaa-quant backtest verify --run-dir data/backtests/task23_fundamental_reduced_v3/full
→ 1/1 runs citable
```

---

## A2 fit (pid **19860**)

```
START 2026-08-17T20:42:45Z
EXIT  2026-08-17T22:11:35Z
wall_clock_sec=5286.791 (~88 min)
log=docs/notes/_artifacts/webapp-w9a/a2.log
path=data/backtests/task23_a2_reduced_v2/A2_frozen_after_week_1/
```

Same git pin, `git_dirty=false`, `n=4944`, **`N_2025=0`**, CITABLE.

Quality: `n_scored=4291`, `n_ungradable=85` still only 2019 w2–4 (five of
those rows scored under frozen ratings). Attributable; not a STOP.

The A2 launcher print used a broken strftime (`%Y-m-d`); UTC times above
are from the log JSON timestamps / manifest `created_at`.

```
uv run ncaa-quant backtest verify --run-dir data/backtests/task23_a2_reduced_v2/A2_frozen_after_week_1
→ 1/1 runs citable
```

---

## Grade

Regrade of new μ/σ vs already-fixed closes → `grade_v2/predictions.parquet`
under the **new** run dirs only (champion 3 untouched).
`scripts/_w9a_grade.py` / `docs/notes/_artifacts/webapp-w9a/grade.log`.

**A2 ATS plausibility:** in-run A2 published. Regrade 2019 ATS **43.81%**
n=662 just below band `[44.17%, 55.83%]`. Caught `AtsPlausibilityError`;
recorded in `metrics_summary.json` `ats_plausibility.tripped=true` and still
measured. Frozen + honest clock, not a grading bug, not fatal.

Canonical numbers: `docs/notes/_artifacts/webapp-w9a/metrics_summary.json`.
Tables: `docs/notes/23-reval.md`.

Headline MAE n=4285 (90 ADR 0014 2019 w2–4 rows left the sample vs
REGRADED_V2 n=4375). Snapshot ATS n is the same 3496 and moved 50.7% →
48.9%. Verdict stays **`NOT CURRENTLY FIT TO BET`**. No STOP on that
label — both fundamental ATS CIs still include 50%; log-loss still loses
to 0.693; CLV still unmeasurable.

`suite.crps_margin` in the JSON is `NaN` (headline includes rows without σ);
use `crps_all_seasons` / `a2_components_by_basis` (10.0239 / 10.7546).

---

## N_2025 (paste)

Both `predictions.parquet` frames:

```
n=4944  N_2025=0  seasons=[2019, 2020, 2021, 2022, 2023, 2024]
```

---

## Stop-condition checklist

| # | Condition | Result |
|---|---|---|
| Isolation five unchanged | fit + A2 + grade | **pass** |
| Champion 3 tree hash unchanged | 94 files | **pass** `93561bae…` |
| `force=True` | never | **pass** |
| Writes into `task23_fundamental_reduced_v2` | never | **pass** |
| 2025 in YAML / predictions / grade | `N_2025=0` | **pass** |
| `n_null_μ` | 0 | **pass** |
| Ungradable extra beyond ADR 0014 2019 w2–4 | none | **pass** |
| Week-5 vs W9-M truncated oracle | 0.0, 56/56 | **pass** |
| `export_enabled` env for fits | `false`; no PutObject | **pass** |
| Site render files | untouched | **pass** |
| Verdict label changed | no; still NOT CURRENTLY FIT TO BET | **pass** (no STOP) |
| A2 2019 ATS plausibility on regrade | tripped, recorded | **not a STOP** |
| `make test` | 874 passed, 1 deselected, coverage 80.05% | **pass** |

---

## Decisions

1. **One full current-code walk-forward is both the revalidation and the
   deployable champion.** W9-M truncated pickle is not enough for `/results`.
2. **Do not restamp `build_track_record` / `copy.ts` here.** 23-readout.md
   stays FINAL as the record of what the site claimed. Successor copies
   `docs/notes/23-reval.md`.
3. **Promotion is `force=False`, `manual_approve=True`.** Local registry v2;
   published export `champion_version` remains the hardcode 3 until a later
   site task.
4. **A2 ATS plausibility trip on regrade is recorded, not fatal.** In-run
   A2 published; frozen ratings + honest clock can sit below the fair-coin
   band on the small 2019 CFBD-close sample.
5. **MAE/CRPS looking better than 14.85 / 10.68 is not a modeling win.** n
   composition (90 rows left) plus every Tuesday as_of moving. Snapshot ATS
   on matched n=3496 is the leak-removal landing.

---

## Spec gaps recorded

- DESIGN / 23-readout do not name a `reduced_v3` run id. Chose
  `task23_fundamental_reduced_v3` / `production-v0_reduced_v3` per W9-V
  advisory.
- `resolve_git_dirty` ignores untracked files, so YAML was not in the SHA
  tree at fit. This commit adds the YAML. Successor restamp should pin the
  post-commit SHA if they need YAML-in-tree provenance.
- `get_settings` does not exist; runner uses `load_config()`.
- A2 launcher strftime `%Y-m-d` was cosmetic; not worth a second fit.
- Loading `_ats_regrade.py` via `importlib` requires registering the module
  in `sys.modules` before `exec_module`.

`data/registry/**` and `data/backtests/**` remain gitignored.
