# Phase 0 — Freeze the authority, restore provenance

Date: 2026-08-07

Goal: make the specification singular and committed, and make the
reproducibility claim in DESIGN §1.4 something the system actually checks,
before any measurement-critical code is touched in Phase 1.

## What was built

### 1. The amended spec is now the committed authority (`59cec08`)

The AUDIT-1..AUDIT-6 documentation amendments were sitting uncommitted in the
working tree. They are now committed, so all Phase 1 code is written against a
single current specification rather than against a tree that could be reverted
or diverge.

Verified before committing (grep counts in the committed files):

| check | file | matches |
|---|---|---|
| `n_books_available` | DESIGN.md | 2 |
| `previous_timestamp` | DESIGN.md | 1 |
| `clv_method` | DESIGN.md | 5 |
| `line_shopping_capture` | DESIGN.md | 7 |
| `lockbox` | DESIGN.md | 6 |
| sum-to-zero / league-mean zero | DESIGN.md | 1 |
| `Adaptive Conformal` | DESIGN.md | 1 |
| stale `20,000 rows` / `20k-game` | DESIGN.md | 0 (corrected) |
| `TASK 5B` | TASKS.md | 2 |
| `zoneinfo` | TASKS.md | 3 |
| `A6` | TASKS.md | 4 |
| `dvc` dependency | TASKS.md | 3 |

`docs/historical_odds_change_set.md` was recorded as a rename to
`docs/adr/0002-historical-odds-source.md` now that it is applied. The duplicate
`ncaa_prediction_system_design.md` is gone.

### 2. The reproducibility anchor is now verifiable (`a66985f`, ADR 0005)

**Finding.** Every manifest under `data/backtests/task23_*` pins
`git_sha = b81cb536f894a5bcfce5472dfb98615907f18265`. That commit exists in no
ref or reflog of this repository — the pre-audit history was squashed into
`0d70347`, which is dated *after* the runs it supposedly contains. The runner
that produced the artifacts, `scripts/_task23_backtest.py`, was never committed
on any branch and is not on disk. So the headline Task 23 numbers cannot be
regenerated, and nothing in the system noticed.

Corroborating signal: every Task 23 manifest reports 33-39 seconds of wall clock
for a seven-season walk-forward over a five-member ensemble with the §2.6 Monte
Carlo path. That is not plausible, and it matches the independently documented
findings that the distributional path was disconnected on predict, σ was
hardcoded at 14, CLV was identically 0, and ablations A1/A5 were no-ops.

**Fix.**

- `RunManifest` gains `git_dirty`. A run from a dirty tree is not regenerable
  from its SHA alone. An unverifiable tree (no git, git error) records as dirty
  rather than clean, so the failure direction is conservative. Untracked files
  alone do not count as dirty.
- `verify_provenance` / `require_citable_provenance` check that the recorded SHA
  resolves to a commit *in this repository* and that the tree was clean.
- Verification is deliberately **separate from writing**. A run whose provenance
  is broken should still record what it did; it just may not be used to make
  claims. Gating `write_manifest` would have destroyed evidence instead of
  flagging it, and would not have caught this case anyway — the history was
  replaced *after* the runs.
- `ncaa-quant backtest verify` audits manifests and exits non-zero on failure so
  it can gate publication.

Audit of the existing artifacts:

```
$ uv run ncaa-quant backtest verify --output-root data/backtests
REJECTED data\backtests\task23_a1\A1_league_mean
         - git_sha b81cb536... does not resolve to a commit in this repository
... (10 runs, all identical) ...
0/10 runs citable
```

ADR 0005 rules those artifacts and every metric derived from them historical
only: not citable as evidence about model performance.

### 3. `backtest run` verified as the reproducible entry point

`backtest run` already drives the full path (staged load → observations →
walk-forward → manifest → MLflow); `scripts/_task23_backtest.py` was only a
multi-config wrapper around it. Verified by running the 2023 wiring-proof config
twice from a clean tree into separate output roots:

| | run 1 | run 2 |
|---|---|---|
| `predictions.parquet` SHA-256 | `73AECEEA…83D2` | `73AECEEA…83D2` |
| `config_hash` | `dac74b666f92` | `dac74b666f92` |
| `environment_lockfile_hash` | `935eb1fa8195` | `935eb1fa8195` |
| `git_sha` | `a66985f…` (resolves) | `a66985f…` (resolves) |
| `git_dirty` | False | False |
| provenance | CITABLE | CITABLE |

Byte-identical predictions across independent runs, and both citable. The Phase 0
gate is met: a full walk-forward is reproducible from a single committed CLI
command with a verifiable manifest.

### 4. Unwired CLI verbs stop lying

`features`, `ratings`, `train` and `predict` were listed in `--help` with
confident descriptions and raised a bare `NotImplementedError` traceback when
invoked. They now exit 2 with a message naming the path that does work
(`backtest run`), and `--help` marks them `NOT WIRED`. They get real
implementations when the weekly production loop is built for 2026 paper trading
(Phase 5) — this change is about not overstating what exists, per the audit's D-1
principle.

## Decisions and their rationale

1. **Provenance verification is a read-time gate, not a write-time gate.** See
   above. Recorded in ADR 0005.
2. **The one-shot D2-D7 runners under `docs/notes/_artifacts/` are not migrated
   into `src/`.** My initial plan said to migrate them for test coverage. On
   inspection they hold ~1,300 lines of inline analysis, but they read the
   non-citable frames and Phase 4 supersedes them, so migrating would buy
   coverage of code about to be retired. They stay as evidence with a README
   recording that status; the reusable logic already lives in
   `src/ncaa_quant/evaluation/d*_eval.py` under test. ADR 0005 was amended to
   match the decision actually taken rather than leaving a stale consequence.
3. **`docs/notes/23.md` and the D-notes are left unaltered.** They are the record
   of what was observed and when. Rewriting them to match current understanding
   would destroy the audit trail; ADR 0005 supplies the correction instead.

## Ambiguities the spec left open

- DESIGN §10 lists `ratings`, `train`, `predict` as production verbs but the
  walk-forward harness drives all three internally. The spec never says whether
  the standalone verbs are required for the weekly loop or whether
  `backtest run` plus a future `predict` covers it. Deferred to Phase 5 with the
  verbs marked unwired rather than removed.
- `backtest run --stack fundamental` overrides the config's `ablation_id`, so
  output landed under `.../wiring_proof_2023/fundamental` while the config
  declares `ablation_id: full`. Cosmetic here, but it means directory names are
  not a reliable key for a run. Noted for Phase 4, where per-run identity
  matters.

## Verification

- `uv run ruff check src tests` — all checks passed
- `uv run ruff format --check src tests` — 140 files already formatted
- `uv run mypy` — no issues in 95 source files
- `make test` — 538 passed, 1 deselected, coverage 80.68% (gate 80%)
- `ncaa-quant backtest verify --output-root data/backtests` — 0/10 citable (the
  expected finding)
- `ncaa-quant backtest verify --output-root data/tmp/phase0_repro` — 2/2 citable

## Not done in this phase

Nothing from Phase 1 was touched: CLV, calibration, the state-space filter,
priors, the variance pipeline, the leakage suite, and the lockbox guard are all
still pre-audit. That is the next phase.
