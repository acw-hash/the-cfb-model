# TASK 22B — Production wiring and ablation switches

**Insert between Task 22 and Task 23 in `TASKS.md`.**

This task exists because Tasks 15, 16, 17, and 22 were closed with deferred
integration seams. Task 23 is a *run-only* task and cannot execute honestly until
those seams are closed. Nothing here is new capability — it is wiring, repair, and
the switch surface Task 16 deliverable 3 was supposed to provide.

**Data prerequisite:** this task is wiring-only and must be completed against
whatever partitions exist (currently 2023). It must not be blocked on the
2014–2025 backfill, and the 2023 smoke run it produces is a *wiring proof*, never
a result. The backfill and the Task 5B odds purchase are separate decisions and
gate Task 23, not this task.

---

```
TASK 22B of 25: Production wiring and ablation switches. Read @docs/DESIGN.md §7.2,
§9.6, §5.2, §11, §15 items 16, 17, 22, and the historical-odds change set.

This is repair and integration work. No new model families, no new features, no new
betting math, no tuning of anything. If a task below cannot be completed as written,
STOP and report it — do not stub it, do not approximate it, do not work around it.

SANCTIONED EDITS — this task may touch only these paths:
  src/ncaa_quant/evaluation/production_stack.py    (new)
  src/ncaa_quant/evaluation/backtest_runner.py     (new)
  src/ncaa_quant/evaluation/walkforward.py         (config surface + flag plumbing)
  src/ncaa_quant/ratings/state_space.py            (prior-injection seam only)
  src/ncaa_quant/ratings/priors.py                 (export surface only)
  src/ncaa_quant/features/builders/                (flag plumbing only, no new logic)
  src/ncaa_quant/models/ensemble.py                (single-member selection switch only)
  src/ncaa_quant/registry/                         (MLflow call sites)
  src/ncaa_quant/cli.py                            (backtest command group)
  configs/                                         (ablation run configs)
  tests/
Anything outside this list is out of scope. If you believe another file must change,
STOP and report why rather than changing it.

---

DELIVERABLE 0 — PRE-FLIGHT AUDIT, before writing any code.

Write docs/notes/22b-preflight.md recording, with file and symbol references, the
actual current state of each of the following. Do not trust the earlier notes files;
verify against the code.
  - Which FeatureProvider / RatingEngine / Predictor implementations exist, and which
    of them are toy stubs.
  - Whether ncaa_quant.cli.backtest is implemented or raises.
  - Which ablation controls exist on WalkForwardConfig today.
  - Whether priors are injected into run_filter (Task 15 item 3).
  - Whether the harness passes real features at retrain, or empty ones (Task 17).
  - Whether MLflow is wired into training and evaluation runs (Task 22 item 1).
  - Whether the line-source fallback ladder from the historical-odds change set
    (snapshot at decision point -> nearest earlier within tolerance -> null with
    indicator, logged per game) exists in the harness, and whether line_source and
    n_books_available are recorded per prediction row.
  - Which seasons are actually materialized in data/staged/ and data/features/.
If reality differs from the list above in either direction, report the difference.
Additional gaps found here are in scope for this task if they fall inside the
sanctioned-edit list, and are a STOP-and-report if they do not.

DELIVERABLE 1 — Production stack adapter.

evaluation/production_stack.py: real implementations of the three harness protocols,
composing features/ + ratings/ + models/ into the objects WalkForwardHarness already
expects. This is a composition layer — it contains no modeling logic of its own.
  - Construction is config-driven and returns a fully-specified stack for a named run
    (fundamental or market-aware).
  - Both stacks per §5.2: Fundamental (no market features) and Market-aware.
  - The existing toy stubs (LeagueAverageMarginPredictor, RunningMarginRatingEngine)
    move to tests/fixtures/ and stay there — the Task 16 placeholder test must keep
    passing, but nothing in src/ may import them.
  - Feature-signature contracts from Task 17 item 5 are enforced at the adapter
    boundary, not deeper. A mismatch raises; it never realigns columns silently.

DELIVERABLE 2 — Backtest runner and CLI.

evaluation/backtest_runner.py plus the `ncaa-quant backtest` command group.
  - `backtest run --config <name>` executes one named walk-forward run end to end.
  - `backtest plan --config <name>` prints the run plan — seasons, weeks, retrain
    points, estimated wall clock — and spends nothing. Mirror the Task 5B
    cost-estimator discipline: you see the bill before you commit to it.
  - Resumable, keyed by (run_id, season, week). Completed units are skipped unless
    --force. A crash mid-run never duplicates or half-writes a week.
  - Every run writes a manifest per §8 item 8: git SHA, DVC hash, config hash, seed
    manifest, ablation settings, and the season list actually executed.
  - Each run's predictions table carries run_id and ablation_id on every row. Two
    runs' outputs must be impossible to confuse after the fact.

DELIVERABLE 3 — Ablation switches on WalkForwardConfig.

Six switches, all at harness level, all recorded in the manifest. Each is implemented
by CONFIGURING the production path — never by forking into a parallel code path, and
never by post-processing the full-system output.
  - A1 preseason_priors: {fitted, league_mean}. league_mean replaces BOTH the prior
    mean with the league mean AND the prior variance with a single pooled variance.
    Replacing only the mean confounds the prior's location with its uncertainty;
    document the choice in the module docstring.
  - A2 rating_updates: {continual, frozen_after_week_1}.
  - A3 market_features_available: bool (exists today — bring it under the same
    manifest and no-op-test discipline as the rest).
  - A4 mapping_layer: {ensemble, single_lgbm}.
  - A5 garbage_time_filter: bool.
  - A6 market_feature_source: {snapshots, cfbd_open_close}, valid for 2021-2025 only.
    Requesting it outside that window is a hard error, not a silent fallback.

  A2 SCOPE — define this precisely, because it is the headline result and it is easy
  to measure the wrong thing. A2 freezes the Stage-1 rating state after Week 1.
  It does NOT freeze season-to-date efficiency features, mapping-layer retrains, or
  market features, all of which keep updating. That means A2 measures the rating
  engine's continual-learning contribution specifically, and is therefore a LOWER
  BOUND on the system's total in-season learning gain. State this boundary in the
  module docstring and require Task 23 to state it in the results memo. If you
  believe a different boundary is the right one, raise it before implementing —
  do not change it after seeing any numbers.

DELIVERABLE 4 — Close the Task 15 seam.

Priors auto-injected into run_filter initialization per Task 15 item 3, and into the
Task 10 shrinkage seam. Missing prior inputs widen the prior variance; they never
default to the league mean with false confidence. A1 is then implemented by
configuring this injection, not by bypassing it.

DELIVERABLE 5 — Close the Task 17 seam.

The harness passes real, as-of-correct feature vectors at every retrain point. The
feature-bank workaround is removed, not left alongside as a fallback. If the removal
breaks a test that was passing against the workaround, that test was asserting the
workaround's behavior — fix the test and say so in the notes.

DELIVERABLE 6 — Close the Task 22 seam.

MLflow tracking wired into the actual training and evaluation call sites per Task 22
item 1: params, per-season metrics, artifacts, and the manifest from Deliverable 2.
The registry's promotion gate must resolve its comparison runs from these logged
runs, not from hand-passed metric dicts.

DELIVERABLE 7 — Line-source discipline, if the pre-flight audit found it missing.

The change set's Task 16 edit: as-of resolution to the configured decision point with
the logged fallback ladder, CFBD open/close excluded from that ladder for
snapshot-backed seasons, and line_source + n_books_available on every prediction row.
A6 and the entire bet-layer backtest are unmeasurable without it.

DELIVERABLE 8 — Re-run the leakage suite against the production stack.

The Task 16 audits passed against a placeholder predictor, which proves almost
nothing about the real feature path. Re-run and report:
  - information-set audit on at least 20 sampled (season, week) points, against the
    production FeatureProvider
  - determinism: two runs, same config and seed, byte-identical prediction tables
  - shifted-label test per §14, now actually wired to the production predictor —
    report the achieved score and confirm it is approximately chance

TESTS — the load-bearing one first.

1. NO-OP FLAG TEST, one per switch A1-A6. Flipping the flag must (a) change the
   prediction table on a fixture season, and (b) change it via the intended
   mechanism. Assert the mechanism, not just the difference:
     - A1: the Week-1 posterior mean equals the league mean for every team
     - A2: the rating state at Week 10 is identical to the state at Week 1
     - A3: no market feature is non-null at any decision point
     - A4: the ensemble weight vector is a single unit weight on the LGBM member
     - A5: the play count entering the efficiency builders rises by the expected
       garbage-time share
     - A6: every market feature's provenance field reads cfbd, and no snapshot row
       is read
   A flag that is silently a no-op would produce an ablation delta of zero and be
   written up as a real finding. This test is the guard against that, and it is the
   single most important test in this task.
2. Resumability: kill mid-run, restart, assert no duplicated or skipped week.
3. Manifest completeness: every run records all four hashes plus the ablation
   settings; a run with any of them missing fails rather than writing.
4. Adapter contract: a feature-signature mismatch raises, and the raised error names
   the offending columns.
5. The Task 16 placeholder test still passes against the fixture stubs.

ACCEPTANCE — show me each:
- The pre-flight audit table from Deliverable 0
- The no-op flag test passing for all six switches, with the A2 mechanism assertion
  shown explicitly
- Information-set audit, determinism, and shifted-label results against the
  PRODUCTION stack, with the shifted-label score reported as a number
- `ncaa-quant backtest plan` output for the full Task 23 run set, including estimated
  wall clock
- One end-to-end smoke run on 2023 through the CLI, producing a metrics-ready table
  and an MLflow run with a complete manifest. Label it WIRING PROOF. Do NOT report
  accuracy, CRPS, CLV, or ablation deltas from it — a single-season 2023 number is
  not a backtest result and must not be able to be mistaken for one later.
- Resumability demonstrated by killing and restarting a run
- make lint typecheck test pass

EXPLICITLY FORBIDDEN:
- Tuning any hyperparameter, threshold, prior weight, or filter cutoff. If something
  looks wrong, report it; do not adjust it.
- Reporting any Task 23 metric, or writing docs/notes/23.md.
- Substituting a stub, a synthetic fixture, or a hardcoded value for a component that
  does not exist. Fail loudly instead.
- train_test_split, KFold, cross_val_score, shuffle=True.
- Any .merge() or JOIN on game_id or team_id without a timestamp bound.

docs/notes/22b.md — including:
  1. A debt-attribution table: each item closed here, and the task that should have
     delivered it. This is the audit trail for the gap between Task 22 and Task 23.
  2. The A2 scope boundary as implemented.
  3. A plain statement of what Task 23 can and cannot run given the data actually
     materialized at the end of this task, and what would have to be ingested or
     purchased to lift each remaining restriction.
```

---

## After this task

Task 22B closes the code gaps only. Task 23 additionally requires:

1. `ingest cfbd --seasons 2014-2025` plus weather, quality gates, feature
   materialization, and Task 14's full-history filter run — none of which were
   executed beyond 2023.
2. A decision on Task 5B (historical odds backfill, 2021–2025). Run `--estimate`
   first. Without it there are no bet-time snapshot prices for any backtest season,
   which makes Task 23 deliverable 4 and ablation A6 unmeasurable — in which case
   both must be struck from the Task 23 prompt *before* it is run, not discovered
   partway through.

## Process fix worth adopting now

No task closes with a deferred integration seam unless the deferral is written into
a named successor task in `TASKS.md` at the moment of closing. Four tasks deferred
seams silently, and the debt surfaced at the one task whose entire value is that its
numbers are real.
