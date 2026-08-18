# W9-1 — make the guards binding

**Date:** 2026-08-18  
**Status:** Complete. Amendment 1 (same date) supersedes STOP #1: two union-grep runners, and pytest CI is green without lake secrets.  
**Authority:** `docs/notes/webapp-w8d.md`; `docs/notes/webapp-w9push.md` (`7d7fea5`); `docs/notes/webapp-w9r.md`; W0 grep list in `docs/notes/webapp-spec.md`; DESIGN §6; ADR 0018.

Test and deploy plumbing only. No product behavior, copy, artifacts, schemas, fixtures, or model-code change (prettier whitespace on three already-committed site files so `npm run lint` is green under lockfile Prettier 3.9.6). No R2 write, no revalidation POST, no Prefect, no `data.end_season` change.

---

## 1. TS2540 / `npm run typecheck`

`tests/gallery-gate.test.ts` assigned `process.env.NODE_ENV` (four TS2540s). Switched to `vi.stubEnv` / `vi.unstubAllEnvs`. Assertions unchanged: development → gallery enabled; production → disabled.

```
> ridge-site@0.1.0 typecheck
> tsc --noEmit
```

Exit 0. STOP #5 did not trip.

---

## 2. Site suite in CI

New workflow `.github/workflows/site.yml`. On push/PR touching `webapp/site/**` or the workflow file:

1. `npm ci`
2. `npm run typecheck`
3. `npm run lint`
4. `npm run test`
5. `npm run build` (`ARTIFACT_SOURCE=fixtures`)

Pytest CI (`.github/workflows/ci.yml`) is unchanged. No `continue-on-error`.

CI run (after this commit is pushed): see the `site` workflow on the commit SHA.

---

## 3. Vercel deploy gating

`webapp/site/vercel.json`:

- `installCommand`: `npm ci` (already)
- `buildCommand`: `npm run guard` = `typecheck && lint && test && next build`

Checks in the Vercel build: **typecheck, lint, test (vitest + token guard), next build**.

Local wall-clock (Windows, warm `node_modules`, existing `.next`):

| Step | Seconds |
|------|--------:|
| `npm run typecheck` | 2.7 |
| `npm run lint` | 7.7 |
| `npm run test` | 6.9 |
| `npm run build` | 35.6 |
| **before (build only)** | **35.6** |
| **after (guard)** | **52.9** |
| **added** | **+17.3** |

STOP #3 did not trip.

Vercel CLI on this workstation is **logged out** (`npx vercel whoami` → Logged out). No `VERCEL_TOKEN` in env. A preview with a deliberate violation could not be created from here. GitHub-connected Vercel will run `npm run guard` on the next git push; a failing vitest/typecheck/lint fails that deploy. Local analog of a failed deploy: `npm run guard` with gallery ungated (bite 2) exits 1 on `npm run test` before `next build`.

---

## 4. Allowlist into `push.py`

`assert_push_artifact_allowlists` runs at the top of `push_artifacts_to_r2` — before credentials, CFBD id-shape, or any `put_object`. Live, sandbox, and operator restore all hit it. No skip flag.

Exact permitted keys per artifact type (unknown keys fail; `fixture` optional at top level):

| File | Objects walked |
|------|----------------|
| `week_predictions.json` | top-level, `model_identity`, `publish_stale`, `stale_sources[]`, each `GamePrediction`, `conviction_basis` |
| `track_record.json` | top-level, `verdict`, each metric |
| `meta.json` | top-level, `champion_model`, `publish_schedule`, `artifact_pointers` |
| `results_<YYYY>.json` | top-level, each graded game, `graded_from` |
| `team_ratings_<YYYY>.json` | top-level, each team entry (`teams` keys must match `^[0-9]+$`), each week snapshot |

Unknown filenames fail. GamePrediction keys are `PUBLISHED_GAME_PREDICTION_KEYS` (schema 1.2.0). Withdrawn 1.1.0 names (`p_cover_home`, …) are extra keys and cannot be restored onto `latest/`.

ADR: `docs/adr/0018-push-artifact-key-allowlist.md`.

Existing push tests that sent stub JSON (`{}`, incomplete week objects) now use committed fixtures so the guard is what is under test. `test_live_push_refuses_synthetic_game_ids` poisons `game_id` on a complete GamePrediction so CFBD still fires after the allowlist.

---

## 5. W0 grep-list reconciliation — STOP #1

Union (not narrowed):

```
best bet|yes bet|\bplay\b|edge vs market|\bunits\b|lock it in|must bet|recommended bet
```

Canonical runner: `uv run python scripts/check_betting_language.py` (`git ls-files`, same union, exit 1 on any match).

Repo-wide `rg -n -i --pcre2` of that union (first run this task, before any artifact dump):

```
283 matches
217 matched lines
67 files contained matches
860 files searched
```

Hits include football `play` (play-by-play, EPA/play), backfill `units`, parent DESIGN.md `recommended bets` / `line units`, and notes that quote the grep itself. **Existing copy is flagged.** Per the task: do not narrow; STOP AND REPORT.

`webapp/site/src` is clean (rg exit 1, empty stdout). Product copy is not the problem; `\bplay\b` and `\bunits\b` are.

The union is **not** a blocking CI job. Wiring it would keep every pipeline red until the parent spec and feature code stop using the English words “play” and “units”. That would be narrowing-by-exclusion if we scoped it, which is forbidden. The script is in the repo; it currently exits 1.

---

## 6. Dependency determinism

- `webapp/site/package-lock.json` is tracked (`git ls-files`).
- CI and Vercel use `npm ci`, not `npm install`.
- `package.json` uses caret ranges (`next ^15.1.0`, `react ^19.0.0`, `prettier ^3.4.2`, …).
- Lockfile pins: next **15.5.23**, prettier **3.9.6**, typescript **5.9.3**, vitest **2.1.9**, eslint **9.39.5**.

STOP #4 did not trip: with `npm ci`, those caret ranges cannot change a Vercel build without a lockfile commit. A workstation `npm install` (not ci) could float; that is why CI/Vercel are `npm ci`.

Three site files were prettier-rewritten under 3.9.6 (trailing newlines / wrapping). Required for `lint` to be a blocking gate.

---

## Bite tests (fail, then revert, then pass)

### 1. Payload projection

Injected `p_cover_home: 0.42` onto the This Week DTO.

```
FAIL  tests/payload-leak.test.tsx > … > projected This Week games JSON omits p_cover_home and p_over
AssertionError: expected '[{"game_id":"401628373"…' not to contain 'p_cover_home'
EXIT=1
```

Revert:

```
 ✓ tests/payload-leak.test.tsx (3 tests)
 Test Files  1 passed (1)
EXIT=0
```

### 2. Gallery gate

`isGalleryEnabled` forced `return true`.

```
FAIL  tests/gallery-gate.test.ts > gallery production gate > returns false in production
AssertionError: expected true to be false
EXIT=1
```

Revert:

```
 ✓ tests/gallery-gate.test.ts (2 tests)
EXIT=0
```

### 3. Demo-states import walk

`import "@/lib/results/demo-states"` on `app/about/page.tsx`.

```
AssertionError: expected [ Array(1) ] to deeply equal []
+   "app\\about\\page.tsx -> lib\\results\\demo-states.ts"
EXIT=1
```

Revert:

```
 ✓ tests/no-demo-states-in-production-routes.test.ts (1 test)
EXIT=0
```

### 4. Token guard

`--text-tertiary: #aeaeb2` in `tokens.css`.

```
Token diff-check FAILED:
light --text-tertiary: expected #75757a, got #aeaeb2
contrast light --text-tertiary/--bg-primary: 2.21:1 < AA 4.5:1
EXIT=1
```

Revert:

```
Token diff-check PASSED — all §4.1/§4.2 values match tokens.css
… light --text-tertiary/--bg-primary: 4.58:1 (>= 4.5) …
EXIT=0
```

### 5. Consumed-or-withdrawn

`unsanctioned_edge` on fixture game 0.

```
FAIL  … > 1.2.0 fixture games have exactly the published keys
FAIL  … > fails on a key that is neither a named consumer nor withdrawn
  unpublished or withdrawn keys in GamePrediction: unsanctioned_edge
EXIT=1
```

Revert:

```
 ✓ tests/published-keys.test.ts (6 tests)
EXIT=0
```

### 6. Fixture `as_of`

`401628373` `kickoff_utc` set to `2024-09-24T09:00:00Z` (before `published_at`).

```
AssertionError: assert '2024-09-24T09:00:00Z' == '2024-09-28T19:30:00Z'
AssertionError: 401628373: 2024-09-24T10:00:00+00:00 >= 2024-09-24T09:00:00+00:00
EXIT=1
```

Revert:

```
2 passed
EXIT=0
```

### 7. Union grep

Site src baseline: rg exit 1 (no matches). Injected `best bet / recommended bet` into `copy.ts`:

```
webapp/site/src\lib\results\copy.ts:8:  "best bet / recommended bet. No betting edge has been demonstrated against the closing line.",
rg EXIT=0
```

Revert: site src rg exit 1. Repo-wide script still exit 1 (STOP #1).

### 8. `push.py` allowlist

`p_cover_home` on fixture game 0, then `test_committed_fixtures_pass_push_allowlist`:

```
PublishedKeyAllowlistError: unpublished keys in week_predictions.json.games[0]: ['p_cover_home']
FAILED tests/unit/test_webapp_w91.py::test_committed_fixtures_pass_push_allowlist
EXIT=1
```

Zero `put_object` (allowlist is before upload; `test_extra_game_key_fails_on_sandbox_restore` asserts `s3.put_calls == []`). Revert:

```
1 passed
EXIT=0
```

All bite mutations were `git checkout`'d. Fixtures and product files are not left dirty.

---

## Acceptance counts

```
make test
========= 917 passed, 1 deselected, 32 warnings in 281.77s (0:04:41) ==========
Required test coverage of 80% reached. Total coverage: 80.33%

cd webapp/site && npm test
 Test Files  21 passed (21)
      Tests  129 passed (129)
Token diff-check PASSED
```

`npm run typecheck` green. `npm run lint` green after the Prettier 3.9.6 whitespace fix.

---

## Ambiguities / decisions

1. **Union grep vs CI.** Spec asked to put the union in CI and to stop if it flags existing copy. Stop wins: script exists, not a blocking job.
2. **Allowlist location.** Enforcement is inside `push_artifacts_to_r2`; GamePrediction key set imported from `export.py` so it cannot drift from `build_game_prediction`.
3. **Optional `fixture`.** Builders omit the key when false; allowlist treats it as the only optional top-level key.
4. **Vercel fail-deploy paste.** Blocked by logged-out CLI. Guard is in `buildCommand`; next connected deploy runs it.

---

*End of original W9-1 (STOP #1 on the repo-wide union grep).*

---

# Amendment 1 — two union-grep runners; pytest CI green

**Date:** 2026-08-18  
**Authority:** Amendment 1 to W9-1. Union pattern unchanged from §5 above. Do not narrow.

Test and deploy plumbing plus the minimum CI-path hardening so GitHub pytest can reach `--cov-fail-under=80` without gitignored `data/`. No R2 write, no revalidation POST, no Prefect, no `data.end_season` change, no secret committed.

---

## A. Union grep — two runners, same pattern

`scripts/check_betting_language.py` takes a mode:

| Mode | Scope | Fail rule | Wired in |
|------|--------|-----------|----------|
| `published` | Explicit `PUBLISHED_COPY_SURFACES` only | Any hit, or an unlisted `copy.ts` / `copy.tsx` under `webapp/site/src`, or a listed path missing | `.github/workflows/ci.yml` quality job; `npm run check:copy` inside `npm run guard` and `.github/workflows/site.yml` |
| `ratchet` | `git ls-files` (repo-wide) | Any of matches / lines / files **greater than** the constants in the script | `.github/workflows/ci.yml` quality job (after published) |

Published surface list (Python; adding a copy module without adding it here fails):

- `webapp/site/src/lib/about/copy.ts`
- `webapp/site/src/lib/results/copy.ts`
- `webapp/site/src/lib/game-detail/absence.ts`
- `webapp/site/src/lib/game-detail/provenance.ts`
- `src/ncaa_quant/webapp/export.py` (verdict / metrics strings reach `track_record.json`)
- committed fixtures: `week_predictions.json`, `track_record.json`, `meta.json`, `results_2024.json`, `team_ratings_2024.json`

Not listed: `week_predictions.legacy-1.1.0.json` (withdrawn schema, not a published artifact).

Vercel `buildCommand` is `npm run guard` with project root `webapp/site/`. That tree has no Python and cannot see repo-root fixtures, so `webapp/site/scripts/check-published-copy.mjs` enumerates the four `src/lib/...` surfaces only (same union, same unlisted-copy inventory). `export.py` and fixtures stay on the Python published runner in the `ci` workflow.

### Baseline pin (in the script, not here as the source of truth)

W9-1 recon (`rg --pcre2`, before the checker and these notes landed): **283 matches / 217 lines / 67 files**. That is not the ratchet ceiling.

`git ls-files` + Python `finditer` counts **matches** (a line can contribute more than one) and includes this checker, tests that quote the union, and notes that quote the union. Pinning 283 would fail every SHA. After Amendment 1 files (guard tests + site checker that contain the pattern): **323 / 229 / 72**. The fail-ceiling lives as `BASELINE_MATCHES` / `BASELINE_LINES` / `BASELINE_FILES` in `scripts/check_betting_language.py`. Existing tracked hits do not fail; only an increase does.

### Bite tests (A)

1. Appended a prohibited phrase to `webapp/site/src/lib/about/copy.ts`. Published runner: exit 1 (`matches=1`). Revert: exit 0 (`matches=0`).
2. Intent-to-add `docs/notes/_w91_a1_ratchet_bite.md` with a new union hit. Ratchet: exit 1 (counts +1 on each of matches / lines / files). File removed; ratchet: exit 0 at the then-current ceiling.
3. Node `npm run check:copy` is the site-side published runner (same copy-module bite).

---

## B. Pytest CI — why it was red; how it is green

Last green `ci` workflow on this repo: `1dd439f` (2026-08-13). Red from W7 (`c32f290`) through W9-1 (`c16f6a4`). `make test` stayed green on the workstation because gitignored `data/` is present.

Original CI command (through W9-1): `uv run pytest -m "not live"` — same marker as `make test`, plus ruff/mypy, plus `--cov-fail-under=80` from `pyproject.toml`. Not a CFBD-key failure. One live test is already excluded. GitHub has no `.env`, no `CFBD_API_KEY`, and no lake.

Data-less reproduction (worktree, empty staged/backtests/registry, no `.env`), **before** this amendment, `pytest -m "not live"`:

8 failed, 904 passed, 5 skipped, coverage **78.81%**.

| Test | Cause |
|------|--------|
| `test_webapp_w1.py::test_odds_denylist_on_fixture_artifacts` | `generate_fixture_week_artifacts` read `data/registry/artifacts/v2/week_predictions.parquet` |
| `test_schema_validation_sample_records` | same |
| `test_tier_distribution_report` | same |
| `test_webapp_w9m.py::test_isolation_paths_include_w9p_state_files` | `isolation_state_paths` globbed week parquets only if the directory existed |
| `test_webapp_w9p.py` (four tests) | `data/backtests/task23_fundamental_reduced_v2/full/weeks/season=2024_week=5.parquet` |

Five other tests already `skip` without staged data (2019 ATS, d2 predictions, odds teams, W1 staged export). Locally they **run**, which is why workstation coverage was ~80.33%.

### Blocking job vs non-blocking job

- **quality / Pytest:** `uv run pytest -m "not live and not workstation"`. Must be green. No secrets. Uses committed `webapp/fixtures` and tmp parquets written by tests.
- **workstation-data (non-blocking):** `continue-on-error: true`, `uv run pytest -m workstation -o addopts=`. Needs gitignored champion week parquet (and, for other skips, staged 2019 / task23 predictions). A red result here is expected on GitHub and must not fail the workflow. No CFBD/Odds key is required for these tests; they read files, they do not call APIs. `make test` is still `pytest -m "not live"` so workstation tests run locally when the lake is present.

Marker: `workstation: needs gitignored data/ artifacts; excluded from GitHub pytest` in `pyproject.toml`.

### Code that made the blocking job green

- W1 denylist / schema / tier-dist assert against **committed** fixtures. New tests build artifacts from synthetic walkforward + tmp staged schedule / teams / filter history.
- Four W9-P lake tests marked `workstation`. CI replacements: tmp parquet + monkeypatch of `production_week_predictions_path`; synthetic allowlist bite; `execute_predict_publish` / `run_isolated_week_export` against tmp staged 2024 week 5.
- `isolation_state_paths` always includes `season=2024_week=5.parquet` (ABSENT is a valid hash).
- `build_team_ratings` no longer assumes `season` / `team_id` columns on an empty frame.

Data-less worktree after the above: **922 passed, 5 skipped, 5 deselected**, coverage **80.11%** (`--cov-fail-under=80`).

Green blocking pytest does **not** need `CFBD_API_KEY`, `ODDS_API_KEY`, or a populated `data/` tree. The non-blocking job needs the lake files named above; do not commit them.

### Bite test (B) — export allowlist

Local analog (W9-1 §8): `p_cover_home` on fixture game 0 fails `test_committed_fixtures_pass_push_allowlist` (`PublishedKeyAllowlistError`); zero `put_object`. Revert passes.

GitHub analog (acceptance): after this amendment is on a clean SHA, poison the fixture allowlist, show `ci` red, revert, show `ci` green. Recorded on that SHA's Actions tab — not in this paragraph as a fake URL.

---

## Acceptance counts (Amendment 1)

```
data-less: pytest -m "not live and not workstation"
==== 922 passed, 5 skipped, 5 deselected, 32 warnings in 254.98s ====
Required test coverage of 80% reached. Total coverage: 80.11%

make test (workstation lake present)
========= 931 passed, 1 deselected, 32 warnings in 273.69s ==========
Required test coverage of 80% reached. Total coverage: 80.50%

published: union_grep published matches=0 lines=0 files=0 surfaces=10  EXIT=0
ratchet after Amendment 1 extras: 323/229/72  EXIT=0
```

`uv run mypy` green. `uv run ruff check/format` green. `npm run check:copy` green (`matches=0 surfaces=4`).

---

## Ambiguities / decisions (Amendment 1)

1. **283 vs the script pin.** The amendment named 283/217/67. That was the rg recon. The ratchet uses `git ls-files` + `finditer` and therefore a higher ceiling, stored in the script. Comment in the script still records 283.
2. **Two published runners.** Node cannot scan `export.py` or `webapp/fixtures`. Python CI does. An unlisted `copy.ts` fails both.
3. **Empty `filter_history` columns.** Guarding missing `season` / `team_id` is CI-path hardening, not a live-export behavior change when the lake file is present.
4. **`continue-on-error`.** GitHub may still paint the workstation job red; the workflow succeeds if `quality` is green.

---

*End of W9-1 Amendment 1.*
