# ADR 0005: Task 23 backtest artifacts are unreproducible and non-citable

## Status

Accepted

## Context

DESIGN §1.4 makes a model artifact's hash the reproducibility anchor: any
prediction must be regenerable from a recorded git SHA, config hash, seed
manifest, and environment lockfile hash. The Task 23 walk-forward artifacts
under `data/backtests/task23_*` record all four fields, so they *appear* to
satisfy that requirement. They do not. Three independent facts break the chain:

1. **The recorded commit does not exist.** Every manifest records
   `git_sha = b81cb536f894a5bcfce5472dfb98615907f18265`. That object is absent
   from this repository (`git cat-file -t` fails, and it appears in no ref or
   reflog entry). The repository's root commit `0d70347` is dated
   2026-08-06T12:10:51-04:00, i.e. *after* the runs it supposedly contains,
   because the pre-audit implementation history was squashed into that single
   commit. The exact code state that produced the artifacts is therefore
   unrecoverable from this repository.

2. **The runner does not exist.** All Task 23 artifacts were produced by
   `scripts/_task23_backtest.py` (referenced in `docs/notes/23.md` and
   `docs/task-23-fix.md`). That file is not present in the working tree and was
   never committed on any branch (`git log --all -- scripts/_task23_backtest.py`
   is empty). The same applies to the D-series runners, which survive only as
   loose scripts under `docs/notes/_artifacts/D*/run_d*.py`.

3. **There is no CLI path that could regenerate them.** `src/ncaa_quant/cli.py`
   exposes `features`, `ratings`, `train` and `predict` as group callbacks that
   raise `NotImplementedError` with no subcommands registered. Only `ingest`,
   `quality`, `backtest plan|run` and `diag mu` are wired.

A corroborating signal: every Task 23 manifest reports a wall clock of 33-39
seconds for a seven-season walk-forward over a five-member ensemble with the
§2.6 Monte Carlo distributional path. That is not physically plausible, and it
agrees with the independently documented findings that the distributional path
was disconnected on the predict path (`docs/notes/23.md` DIAG amendment D-1,
`docs/notes/22b.md`), that σ was hardcoded at 14, that CLV was identically 0,
and that ablations A1 and A5 were silent no-ops.

What does survive is the *output data*: `predictions.parquet`,
`predictions_enriched.parquet`, `bets.parquet` and per-week frames. The D2-D7
diagnostics were computed by re-scoring those frames, so their arithmetic is
checkable even though the generating process is not.

## Decision

1. The `data/backtests/task23_*` artifacts and every metric derived from them
   are **historical only**. No promotion decision, acceptance claim, ADR, or
   external statement may cite them as evidence about model performance.
2. `docs/notes/23.md`, `docs/task-23-fix.md`, and the D1-D7 notes are retained
   unaltered as the record of what was observed and when. They are evidence
   about the *project's history*, not about the model's performance.
3. Statistical conclusions that were computed by re-scoring the surviving
   prediction frames (D2-D7, including the D6 stop-rule determination) keep
   their status as findings about *those frames*. Whether the corrected pipeline
   reopens the questions they settled is adjudicated separately, and must be
   pre-registered before the corrected numbers are seen.
4. Before any new evaluation is run, a single reproducible entry point must
   exist in `src/` (not in `scripts/` or `docs/`), invocable from the committed
   CLI, and the recorded `git_sha` must be verified resolvable in this
   repository as part of writing the manifest.

## Consequences

- The authoritative walk-forward has to be re-run from scratch once the
  measurement-critical code is corrected. There is no shortcut that reuses the
  existing artifacts.
- Manifest writing gains a validation step: a manifest whose `git_sha` does not
  resolve locally is a hard failure, not a warning. This is the check that would
  have caught the problem at the time.
- Going forward, evaluation logic lives in `src/ncaa_quant/evaluation` where
  `make test` covers it; `docs/notes/_artifacts/` holds outputs, not logic. The
  existing one-shot D2-D7 runners there are **not** migrated: they read the
  non-citable frames and Phase 4 supersedes them, so rewriting them under test
  would buy coverage of code that is about to be retired. They stay as evidence,
  with `docs/notes/_artifacts/README.md` recording that status. The substantive
  D-series logic already lives in `src/ncaa_quant/evaluation/d*_eval.py` and is
  tested.
- The project's honest status reverts to: no validated performance measurement
  exists yet. That is a less comfortable claim than `docs/notes/23.md` implies,
  and it is the accurate one.
