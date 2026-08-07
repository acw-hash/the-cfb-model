# Coding Agent Task Prompts

Paste-ready prompts expanding `docs/DESIGN.md` §15. **One fresh Cursor agent session per task.**

## How to run a task

1. Open a new Cursor agent/composer session.
2. Paste the task block below verbatim. Prefix it with `@docs/DESIGN.md` so the spec is in context.
3. When the agent claims done, verify the acceptance criteria **yourself** by running the commands. Do not take its word.
4. `git commit` with message `feat(taskNN): <short description>`.
5. Close the session. Open a new one for the next task.

## Order note

**Run Task 4 (odds capture) as early as you possibly can** — live odds snapshots cannot be backfilled, and every day without it is permanently lost data. The minimum viable path to get it running is 1 → 2 → 4a (raw archival only), then return to 3 and finish 4 properly. If you have a week of patience, run in order; if not, do this.

## Two failure modes to watch for in every review

- Any appearance of `train_test_split`, `KFold`, `cross_val_score`, or `shuffle=True`. Time-ordered splits only.
- Any `.merge()` / `JOIN` on `game_id` or `team_id` without a timestamp bound. Every join must be as-of.

Both produce beautiful backtests and worthless models. Reject on sight.

---

# TASK 1 — Repository scaffold

```
TASK 1 of 25: Repository scaffold. Read @docs/DESIGN.md §11 and §15 item 1.

Build the empty project skeleton only. No business logic.

Deliverables:
1. Full directory tree from §11, with __init__.py in every package and a one-line
   module docstring stating that package's responsibility per the spec. Empty
   packages get .gitkeep.
2. pyproject.toml using uv, Python 3.11. Deps (pin major versions only): pandas,
   polars, duckdb, pyarrow, pandera, pydantic, pydantic-settings, omegaconf,
   structlog, typer, httpx, tenacity, scikit-learn, lightgbm, xgboost, catboost,
   ngboost, optuna, mlflow, prefect, great-expectations, matplotlib, dvc, scipy,
   shap, jinja2, plotly. Optional extra `research`: numpyro, jax, pymc-bart
   (install via `uv sync --extra research`). This Task 1 dependency list is the
   approved set under `.cursorrules` — do not add further deps without an explicit
   amendment. Dev group: pytest, pytest-cov, hypothesis, ruff, mypy, pre-commit.
   Generate uv.lock.
3. Tool config in pyproject: ruff (line length 100), mypy strict on src/, pytest
   with --cov=src --cov-fail-under=80, testpaths=tests. Register `pytest.mark.live`
   for tests that hit the network; CI and default `make test` exclude `@pytest.mark.live`.
4. Dockerfile on an NVIDIA CUDA 12.x runtime base for Ubuntu 22.04, non-root user,
   uv-installed deps, workdir /app. docker-compose.yml with services: app, mlflow
   (local **file-store** backend + local artifact volume — not SQLite under
   multi-writer / parallel logging; see §10), prefect server, named volume for data/.
   Compose is a deferred artifact; Phase 1 runs natively.
5. Makefile: install, lint, typecheck, test, format, ingest, features, ratings,
   train, predict, backtest, clean. Not-yet-built targets invoke the CLI and exit
   with "not implemented" — that is expected at this stage.
6. src/ncaa_quant/cli.py: typer app with command groups matching Makefile verbs,
   each raising NotImplementedError with a clear message.
7. .github/workflows/ci.yml: on push/PR — uv sync, ruff check, ruff format --check,
   mypy, pytest excluding `@pytest.mark.live` (no live network in CI).
8. .pre-commit-config.yaml: ruff, ruff-format, mypy, trailing-whitespace,
   check-yaml, detect-private-key.
9. .gitignore (python, data/, .env, mlruns/, .dvc cache). .env.example listing
   CFBD_API_KEY and ODDS_API_KEY with empty values.
10. tests/test_scaffold.py asserting the package imports and CLI --help exits 0.
11. docs/: mkdocs.yml, docs/index.md, docs/adr/0001-record-architecture-decisions.md,
    empty docs/notes/.

Acceptance — verify each and show output:
- `make install && make lint && make typecheck && make test` all pass
- `docker build .` succeeds
- `python -m ncaa_quant.cli --help` lists all command groups

Then write docs/notes/01.md per the working agreement.
```

---

# TASK 2 — Config, seeding, logging, time utilities

```
TASK 2 of 25: Config and core utilities. Read @docs/DESIGN.md §11 and §15 item 2.

Implement src/ncaa_quant/utils/ and the config loader only. Touch no other package.

Deliverables:
1. configs/ layered YAML per §11: base.yaml, data.yaml, ratings.yaml, betting.yaml,
   pipeline.yaml, models/ (empty dir with .gitkeep). Populate with the parameters
   the spec already names (e.g. betting.yaml: min_edge_sides 0.025, min_edge_totals
   0.03, kelly_fraction 0.25, max_stake_pct 0.015, max_bets_per_week, bowl edge
   multiplier). Where the spec does not name a value, use a clearly-labeled
   placeholder and list it in the notes file.
2. src/ncaa_quant/config.py: OmegaConf + pydantic-settings loader. Precedence is
   base.yaml < domain yaml < env vars < explicit CLI override. Typed pydantic models
   for each config section — no raw dict access anywhere in the codebase. Secrets
   (CFBD_API_KEY, ODDS_API_KEY) come from env only and must never appear in a
   config dump or log line.
3. utils/seeding.py: set_global_seed(seed) covering python random, numpy, and env
   vars for lightgbm/xgboost determinism; a SeedManifest dataclass recording every
   seed used in a run, serializable to JSON for MLflow.
4. utils/logging.py: structlog JSON config, run-id binding, a redaction processor
   that strips any key matching /key|token|secret|password/i.
5. utils/timeutils.py: all timestamps UTC-aware. Functions: to_utc, season_of(ts),
   week_of(ts, season) using the CFB convention documented in a docstring,
   as_of_bound(ts) helper, and an assert_tz_aware guard used at boundaries. Include
   a NAIVE-DATETIME-FORBIDDEN docstring note. Decision points are defined in
   America/New_York local time and resolved to UTC per date via zoneinfo (same
   contract as Task 5B). Provide helpers that take (decision_point_name, local_date)
   → aware UTC instant.
6. Tests: config precedence (all four levels), secret redaction in both config dump
   and log output, seed manifest reproducibility, timeutils DST and season-boundary
   cases (early January bowl games belong to the prior season — test this). Explicit
   fixtures for DST-transition weeks in early November (America/New_York EST↔EDT)
   proving decision-point local → UTC resolution.

Acceptance:
- make lint typecheck test pass
- A test proves a secret set via env never appears in logged or dumped config
- A test proves a January 2 bowl game maps to the prior season

docs/notes/02.md.
```

---

# TASK 3 — Storage layer and data schemas

```
TASK 3 of 25: Storage and schemas. Read @docs/DESIGN.md §3, §4.7, §11, §15 item 3.

Implement src/ncaa_quant/data/ only.

Deliverables:
1. data/schemas.py: pandera DataFrameModels for games, plays (PBP), drives,
   advanced_box, lines_historical, odds_snapshots, teams, venues, coaches, rosters,
   returning_production, recruiting, portal. Every schema must include:
   - explicit dtypes and nullability
   - range checks per §8 step 2 (0 <= points <= 100, |spread| < 70, totals 20-100)
   - an `event_time` (UTC) column on every fact table — the timestamp at which the
     information became knowable — and an `ingested_at` column
   - a check that event_time <= ingested_at
2. data/storage.py: Parquet-on-disk store partitioned by (season, week) for game-
   grained tables and (season) for reference tables, with a DuckDB query layer.
   API: write_partition(table, df, partition, mode), read(table, filters),
   query(sql). Writes are atomic (temp file + rename) and idempotent — rewriting a
   partition with identical data is a no-op producing identical bytes.
3. data/asof.py: the ONLY sanctioned join helper. as_of_join(left, right, on, ts_col,
   as_of) returning, for each left row, the most recent right row with
   right.event_time < as_of. Must raise if ts_col is missing or tz-naive. Include a
   module docstring stating that direct pandas .merge on entity ids is forbidden
   outside this module.
4. Directory conventions created under data/: raw/{source}/{date}/, staged/,
   features/, predictions/. Document the layout in docs/data_layout.md.
5. Tests: schema round-trip, schema violations raise with useful messages, partition
   write idempotency (byte-identical), as_of_join correctness including the
   boundary case where right.event_time == as_of (must be EXCLUDED), and a test that
   as_of_join raises on tz-naive input.

Acceptance:
- make lint typecheck test pass
- The boundary test (event_time == as_of excluded) passes — this is a leakage guard
- Rewriting a partition twice produces identical file hashes

docs/notes/03.md.
```

---

# TASK 4 — Odds snapshot ingester (RUN THIS EARLY)

```
TASK 4 of 25: Odds snapshot ingestion. Read @docs/DESIGN.md §3.2, §10, §15 item 4.

This captures unbackfillable data. Correctness and reliability matter more than
elegance. Implement src/ncaa_quant/ingestion/odds_api.py plus its Prefect
deployment only.

Deliverables:
1. A typed httpx client for The Odds API (americanfootball_ncaaf), with:
   - tenacity retry: exponential backoff, 5 attempts, retry on 429/5xx/timeout only
   - a rate-limit budget guard reading remaining-requests response headers, logging
     them, and refusing to proceed below a configurable reserve threshold
   - configurable book list and markets (spreads, totals, h2h)
2. Raw archival FIRST: every API response written verbatim to
   data/raw/odds_api/{date}/{captured_at_iso}.json before any parsing. Parsing
   failures must never lose the raw payload.
3. Normalization to the odds_snapshots schema from Task 3:
   (game_key, book, market, side, line, price_american, captured_at, source_version).
   Include `snapshot_source='live'`, `decision_point=null`, and `n_books_available`
   so live and historical rows share one schema. If Task 4 is already built and
   running, this is a schema migration — do it as part of Task 5B, and do not
   interrupt the live capture to do it.
   **Canonical game key:** CFBD's stable numeric game id. Odds API events are
   matched to it via normalized team pair + kickoff within ±36h, persisted in a
   crosswalk table; ambiguous matches are quarantined, never guessed. The derived
   (season, home_team, away_team, kickoff_date) key is retained only as a matcher
   input — put the team-name normalization map in configs/ and make it testable
   (team naming mismatches across sources are the #1 integration bug). Required
   fixture: a game postponed by one day retains a single canonical key and
   continuous snapshot history across the postpone.
4. Deduplication: identical (game_key, book, market, side, line, price, captured_at
   rounded to minute) is written once. Snapshots that differ only in captured_at are
   all retained — line movement history is the point.
5. Prefect deployment `ingest_odds` on a 6x/day cron (configurable), with failure
   notification hook stubbed to a logger call for now.
6. CLI: `ncaa-quant ingest odds --once` for manual/smoke runs.
7. Tests: retry behavior against a mocked failing endpoint, rate-limit guard trips
   correctly, dedupe logic, team-name normalization fixtures, raw archival happens
   before parse (test by making the parser throw and asserting the raw file exists),
   postponed-game fixture (one-day postpone → single CFBD game id, continuous
   snapshot history), raw-archive request metadata scrubbed of API keys.

Acceptance:
- make lint typecheck test pass
- `ncaa-quant ingest odds --once` against the live API writes both a raw JSON file
  and normalized parquet rows — show me the row count and a sample
- Running it twice in the same minute does not duplicate rows

After this task, deploy the schedule and leave it running permanently.

docs/notes/04.md.
```

---

# TASK 5 — CFBD ingestion and historical backfill

```
TASK 5 of 25: CFBD ingestion. Read @docs/DESIGN.md §3.1, §15 item 5.

Implement src/ncaa_quant/ingestion/cfbd.py only.

Deliverables:
1. Typed client covering: /games, /plays, /drives, /games/teams (box), /stats/game/
   advanced, /lines, /talent, /player/returning, /recruiting/teams, /player/portal,
   /coaches, /venues, /roster. Same retry/rate-limit discipline as Task 4. Raw
   archival before parse, same pattern.
2. Backfill CLI: `ncaa-quant ingest cfbd --seasons 2014-2025 [--endpoints ...]`,
   resumable — completed (endpoint, season, week) partitions are skipped unless
   --force. Progress logged per partition.
3. Incremental mode: `ncaa-quant ingest cfbd --incremental` pulls only the current
   season's unfetched or recently-changed weeks.
4. event_time assignment per endpoint, documented explicitly in the module docstring.
   This is a spec-critical judgment call. Game-result event_time uses the actual
   completion timestamp when available; otherwise kickoff + a deliberately generous
   upper bound (5h; more when OT is flagged), with `event_time_estimated=True`
   recorded. The "most conservative (latest) defensible time" rule applies to
   results too, not only timestamp-free endpoints. Season-level reference data
   (recruiting, returning production, talent) gets event_time = a documented
   preseason date; portal entries get their transaction date. Get this wrong and
   every downstream point-in-time guarantee is void. If an endpoint gives you no
   usable timestamp, assign the most conservative (latest) defensible time and flag
   it in the notes.
5. Team-name normalization and Odds-API↔CFBD game-id crosswalk shared with Task 4 —
   refactor to a common module if needed, this is the one sanctioned cross-task edit.
   Canonical game key is CFBD's stable game id; the derived (season, teams, date) key
   is matcher input only. Ambiguous Odds API matches quarantine, never guess.
   Required fixture: postponed game (kickoff slips one day) keeps one CFBD id and
   continuous snapshot history.
6. Tests: mocked-endpoint parsing per endpoint, resumability (kill and restart mid-
   backfill leaves no partial partitions), event_time assignment fixtures.

Acceptance:
- Backfill one season (2023) fully; report FBS game count and confirm it matches the
  known ~800-900 range, and that every game has PBP rows
- Re-running the same backfill fetches nothing new
- make lint typecheck test pass

docs/notes/05.md — including the full event_time assignment table you used.
```

---

# TASK 5B — Historical odds backfill

```
TASK 5B of 25: Historical odds backfill. Read @docs/DESIGN.md §3.2, §3.4, §7.2 item 8,
§9.8. Depends on Tasks 3, 4, 5.

Extend src/ncaa_quant/ingestion/odds_api.py (this is a sanctioned edit to the Task 4
module — nothing else).

CONTEXT: this is credit-metered spend against a 20,000/month quota, and a bug that
pulls wrong timestamps costs real money that re-running cannot recover. The cost
estimator and dry-run gate are first-class deliverables, not conveniences.

Deliverables:

1. Snapshot schedule in configs/data.yaml, PRE-REGISTERED before any spend. Each
   entry is a named decision point stored on every row. Decision points are defined
   in America/New_York local time and resolved to UTC per date via zoneinfo; every
   snapshot row stores both the decision-point name and the resolved UTC instant.
   Explicit test fixtures for DST-transition weeks (early November EST↔EDT).
   v1 schedule:
     - `tuesday_0600_et`  — one request per week (the §7.2 item 1 primary as-of)
     - `slot_close`       — one request per distinct kickoff slot, at slot minus 5 min
   Do NOT add per-game T-6h/T-1h points in v1; see the budget note below. Adding or
   removing a decision point later invalidates backtest comparability with earlier
   runs — state this in the module docstring.

2. Historical client method, distinct from the live one:
   GET /v4/historical/sports/americanfootball_ncaaf/odds with a `date` parameter.
   The response is the live schema wrapped in an envelope carrying `timestamp`,
   `previous_timestamp`, `next_timestamp`.

   CRITICAL: event_time is the envelope's RETURNED `timestamp`, never the requested
   `date`. The API returns the closest snapshot at or before the request, and the gap
   is up to 10 minutes before Sept 2022 and 5 minutes after. Storing the requested
   date claims information was knowable later than it was and corrupts as-of joins.
   Write a test asserting stored event_time == returned timestamp != request param.

3. As-of fallback tolerance must be configurable and default to >= 10 minutes for
   seasons before Sept 2022, >= 5 minutes after. A single hardcoded 5-minute
   tolerance silently drops valid 2020-2022 snapshots.

4. Cost estimator and dry-run mode, built and verified BEFORE any live historical
   call: `ncaa-quant ingest odds-historical --estimate --seasons 2021-2025` prints
   request count by season and decision point, credit cost at 10 x markets x regions,
   and projected remaining quota. Refuse to proceed past a configured ceiling without
   --force.

5. Calibration gate: the first real historical call is a SINGLE request that prints
   `x-requests-last`. Assert it equals the predicted 30 credits before the loop runs.
   If it differs, stop and report — do not proceed on an unverified cost model.

6. Separate credit budget buckets for live and historical. The historical path must be
   structurally incapable of consuming the live capture's reserve; the live snapshot
   job is the unbackfillable one and takes absolute priority. Test that the historical
   guard trips while the live reserve remains intact.

7. Resumable backfill keyed by (season, week, decision_point). Completed units skipped
   unless --force. Progress and running credit spend logged per unit. A crash mid-run
   must never re-spend credits on stored units.

8. Raw archival before parse, same discipline as Task 4:
   data/raw/odds_api_historical/{date}/{requested_ts}_{returned_ts}.json

9. Schema: extend odds_snapshots with `snapshot_source` {live, historical},
   `decision_point`, and `n_books_available`. Backfill n_books_available for existing
   live rows. Both sources share one normalizer and are otherwise identical in shape.

10. Reconciliation report: where both a CFBD close and a snapshot slot_close exist,
    compute the spread and total difference and report the distribution. Systematic
    bias beyond tolerance is a finding to write up, NOT something to correct away.

BUDGET (verify with --estimate, do not trust these numbers blindly):
  30 credits/call at 3 markets x 1 region.
  ~7 calls/week (1 tuesday + ~6 slots) = ~210 credits/week
  x ~15 weeks = ~3,150/season x 5 seasons (2021-2025) = ~15,750 credits.
  Live capture uses ~540/month. Quota is 20,000/month. This fits in ONE month with
  ~3,700 to spare. It does not fit alongside additional decision points — those get
  added in later months, one pass at a time.

Tests: returned-vs-requested timestamp discipline, tolerance by era, budget bucket
isolation, resumability across simulated crash, estimator arithmetic vs hand-computed
fixtures, envelope parsing, dedupe against live rows covering the same moment,
DST-transition decision-point resolution via zoneinfo (early November fixtures).

Acceptance:
- `--estimate` for 2021-2025 prints a credit figure BEFORE any spend. Show me that
  number and WAIT for my explicit go-ahead before running the real backfill.
- Calibration gate passes: x-requests-last == 30 on the single probe call.
- Post-backfill: snapshot coverage % per season per decision point; the
  n_books_available trajectory by season (it will rise — quantify it); the
  CFBD-close reconciliation distribution; total credits spent.
- make lint typecheck test pass

docs/notes/05b.md
```

---

# TASK 6 — Weather and venue enrichment

```
TASK 6 of 25: Weather and venue. Read @docs/DESIGN.md §3.3, §15 item 6.

Implement src/ncaa_quant/ingestion/weather.py and the venue reference table only.

Deliverables:
1. venues reference table built from CFBD /venues plus manual corrections in
   configs/venues_overrides.yaml: venue_id, name, lat, lon, elevation_m, surface,
   is_dome, capacity, timezone. Missing lat/lon for any venue hosting an FBS game in
   the backfill range is a hard error — list them for manual fill rather than
   silently defaulting.
2. Open-Meteo client: historical archive endpoint for past games, forecast endpoint
   for upcoming. Match to kickoff hour in the venue's local timezone. Fields:
   temp_c, wind_speed_ms, wind_gust_ms, precip_mm, precip_prob, humidity, snow.
3. Dome handling: is_dome venues get weather fields set to neutral sentinel values
   AND a weather_applicable=False flag. Downstream code must key off the flag, never
   off the sentinel.
4. Forecast versioning: forecasts pulled on different days for the same game are all
   retained with their own captured_at (forecast skill degrades with horizon; the
   model may later want horizon as a feature). Historical actuals stored separately
   from forecasts — never overwrite a forecast with the actual.
5. CLI: `ncaa-quant ingest weather --seasons ... ` and `--forecast-upcoming`.
6. Tests: dome flag behavior, timezone matching (a 7pm local kickoff in Hawaii and
   in Boston must both resolve correctly), forecast/actual separation, missing-venue
   hard error.

Acceptance:
- Weather attached for all 2023 outdoor FBS games; report coverage % and list any
  gaps
- make lint typecheck test pass

docs/notes/06.md.
```

---

# TASK 7 — Data quality layer

```
TASK 7 of 25: Data quality. Read @docs/DESIGN.md §8 step 2, §15 item 7.

Implement src/ncaa_quant/quality/ only.

Deliverables:
1. Great Expectations suites per table: schema, ranges, completeness against expected
   game counts per (season, week), referential integrity (every plays.game_key exists
   in games; every game's venue exists), duplicate detection.
2. Custom validators not expressible in GE:
   - temporal sanity: no row where event_time > ingested_at
   - score consistency: box score points sum to final score
   - PBP consistency: drive-level points reconcile to game score within tolerance,
     play sequence numbers are monotone within a drive
   - line sanity: opening and closing lines for the same game differ by < 20 points
     (flag, not fail — genuine large moves exist and are interesting)
   - Snapshot monotonicity: within a (game_key, book, market), snapshots ordered by
     event_time must not contain duplicate timestamps, and the last pre-kickoff
     snapshot must actually precede kickoff
   - Source reconciliation: flag (not fail) games where the CFBD close and the
     snapshot close differ beyond tolerance. Genuine late movement exists;
     systematic divergence is a bug
   - Snapshot cadence: per (game, decision window), captured snapshot count >=
     expected minus configured tolerance; shortfalls alert within 24h (§10)
3. A quarantine flow: failures mark the affected partition
   status=QUARANTINED in a validation_results table with the failing expectation and
   sample rows; downstream consumers must skip quarantined partitions rather than
   crash. Other partitions in the same run continue.
4. `ncaa-quant quality run --seasons ...` and a summary report writer producing
   docs-friendly markdown/HTML.
5. Tests: seeded corrupt fixtures (negative score, orphan PBP game, duplicated rows,
   future event_time, mismatched box/final) are each caught by the right check; a
   clean season passes with zero failures; cadence shortfall fixture fires the
   expectation; live-network tests if any are marked `@pytest.mark.live` and excluded
   from CI.

Acceptance:
- Run against the full backfill; show the summary. Real data WILL have real issues —
  document what you found in the notes rather than loosening checks to make them pass.
  If you loosen any check, justify it explicitly.
- make lint typecheck test pass

docs/notes/07.md.
```

---

# TASK 8 — EPA/WP normalization and garbage-time filter

```
TASK 8 of 25: EPA and garbage-time filtering. Read @docs/DESIGN.md §3.6, §4.2,
§15 item 8.

Implement src/ncaa_quant/features/epa.py (or a dedicated module under features/)
only. Do not build the feature registry yet — that is Task 9.

Deliverables:
1. Normalize CFBD's EPA and win-probability fields into a clean per-play table:
   game_key, offense_team, defense_team, play_type, down, distance, yardline,
   period, clock, epa, wp_before, wp_after, is_rush, is_pass, is_special_teams,
   is_penalty, garbage_time flag.
2. Garbage-time filter per §4.2: primary rule excludes plays where wp_before > 0.98
   or < 0.02. Fallback rule (used when WP is missing) is Connelly-style score-margin
   by quarter — implement both, with the fallback flagged in the output so you can
   measure how often it fires.
3. Play-weighting utilities: functions returning per-play weights for later
   aggregation (e.g. down-and-distance leverage weighting) — implement the plain
   uniform weighting now with the interface designed for alternatives, since §3.6
   flags leverage-weighted EPA as a research option.
4. Aggregation helpers: given a filtered play set and a grouping, produce
   EPA/play, success rate (standard down thresholds: 50% of needed yards on 1st,
   70% on 2nd, 100% on 3rd/4th — document the definition used), explosiveness
   (EPA on successful plays), havoc rate, all split by rush/pass.
5. Tests: garbage-time filter matches a hand-labeled fixture of ~30 plays across 3
   games; success-rate definition unit tests at each down; aggregation helpers
   verified against hand-computed small fixtures; fallback rule fires when WP is null.

Acceptance:
- Report what fraction of 2023 plays are filtered as garbage time (sanity: expect
  roughly 8-15%; a wildly different number means the filter is wrong)
- make lint typecheck test pass

docs/notes/08.md.
```

---

# TASK 9 — Feature registry and as-of engine

```
TASK 9 of 25: Feature registry. Read @docs/DESIGN.md §4.1, §4.7, §15 item 9.

Implement src/ncaa_quant/features/registry.py and the builder base class only. No
actual feature builders yet — those are Tasks 10-12.

Deliverables:
1. features/registry.yaml schema and loader. Each entry: name, version, dtype,
   builder class path, dependencies (other features or raw tables), as_of semantics,
   null policy, lookback window, and a REQUIRED `hypothesis` field stating why this
   feature should predict. Per §4.1, a feature with an empty hypothesis is a
   registry validation error — enforce this in code.
2. FeatureBuilder abstract base class with the mandated signature
   build(entity_ids, as_of: datetime) -> DataFrame. The base class must:
   - assert as_of is tz-aware
   - route ALL data access through data/asof.py (Task 3) — no direct storage reads
   - validate output against the registered dtype and null policy
3. Materialization engine: incremental by (season, week), writing to
   data/features/, skipping already-computed partitions, with a content hash per
   partition for change detection. DVC hooks: `dvc add` the feature outputs and
   document the workflow in docs/.
4. Dependency resolution: topological sort of the registry so builders run in
   dependency order; cycle detection with a clear error.
5. pit_audit.py — the leakage test harness per §4.7 and §14. Given a materialized
   feature partition, it re-derives a random sample of rows using ONLY data with
   event_time < the row's as_of, and asserts equality with the stored value. This
   is the single most important test in the repository.
6. Tests:
   - pit_audit catches a deliberately planted leak (a builder that reads a future
     row) — write this test FIRST and make sure it fails before the guard exists
   - registry validation rejects a feature with no hypothesis
   - dependency cycle raises
   - incremental materialization skips unchanged partitions

Acceptance:
- The planted-leak test demonstrably fails without the as-of guard and passes with it
- make lint typecheck test pass

docs/notes/09.md.
```

---

# TASK 10 — Efficiency feature builders

```
TASK 10 of 25: Efficiency features. Read @docs/DESIGN.md §4.3, §4.4, §4.5,
§15 item 10.

Implement src/ncaa_quant/features/builders/efficiency.py only.

Deliverables:
1. Ridge opponent adjustment per §4.3: solve y = off_i - def_j + hfa + eps over the
   season-to-date play or game set, with L2 shrinkage toward zero. Implement for each
   metric family: EPA/play, success rate, explosiveness, havoc, finishing drives,
   field position — each split rush/pass where meaningful. lambda is a config
   parameter with a documented default; it will be tuned in a later task, do not tune
   it here.
2. FCS opponents: pooled into a single FCS-tier entity rather than getting individual
   ratings (they have too few observed games). Document the pooling.
3. Bayesian shrinkage per §4.4: season-to-date adjusted means shrunk toward the
   preseason prior with weight n/(n+k), k per metric family from config. Until Task 15
   builds real priors, shrink toward the league mean and leave a documented seam for
   swapping in the prior.
4. EWMA variants per §4.4 with configurable per-metric half-life, plus last-3-games
   deltas.
5. All builders registered in registry.yaml with hypotheses.
6. Tests:
   - SYNTHETIC RECOVERY TEST (critical): generate a fake season with known planted
     team strengths and a known schedule, run the ridge adjustment, and assert the
     recovered ratings correlate > 0.95 with the planted truth and that the ranking
     of the top and bottom 10 is essentially preserved. If this fails the adjustment
     is wrong and nothing downstream matters.
   - shrinkage math unit tests at n=0, n=k, n>>k
   - EWMA against hand-computed sequences
   - pit_audit passes on the materialized output

Acceptance:
- Synthetic recovery test passes at the stated threshold
- Materialize 2023 and show the top 15 teams by adjusted off EPA and def EPA at
  season end. These should be recognizable — if the list looks absurd, something is
  broken, and say so rather than shipping it.
- make lint typecheck test pass

docs/notes/10.md.
```

---

# TASK 11 — Tempo, possession, and situational builders

```
TASK 11 of 25: Tempo and situational features. Read @docs/DESIGN.md §4.5,
§15 item 11.

Implement src/ncaa_quant/features/builders/tempo.py and situational.py only.

Deliverables:
1. Tempo: adjusted plays per game, situation-neutral seconds per play (excluding
   end-of-half and garbage time and clear hurry-up/kneel situations — document the
   exclusion rules), run/pass rate over expectation given down-distance-score-clock.
2. Expected possessions model per §4.5: a regression predicting total game
   possessions from both teams' pace and pass rates. This is the key totals feature.
   Fit on historical games, stored as a fitted artifact, applied point-in-time.
3. Situational builders: rest days differential, short-week and bye flags, travel
   distance (haversine, venue-to-venue), time zones crossed with direction, altitude
   delta vs home venue elevation, surface and surface-change flag, neutral site,
   week number, month, conference game, rivalry flag (rivalry pairs in a config
   YAML — hand-curated, ~60 pairs is fine), post-rivalry and lookahead flags,
   **rule-era categorical** (`pre_2023_clock` vs `post_2023_clock` per §4.5 — the
   2023 clock-rule change hypothesis for plays/totals).
4. All registered with hypotheses.
5. Tests: haversine against known city pairs, timezone-crossing direction sign,
   rest-day computation across bye weeks and midweek games, expected-possessions
   predictions land in a sane range (roughly 20-30 possessions), pit_audit passes.

Acceptance:
- Expected-possessions model reports out-of-sample MAE on held-out seasons; state
  the number
- make lint typecheck test pass

docs/notes/11.md.
```

---

# TASK 12 — Roster, recruiting, coaching, QB-status builders

```
TASK 12 of 25: Roster and prior-input features. Read @docs/DESIGN.md §3.1, §3.3,
§4.5, §15 item 12.

Implement src/ncaa_quant/features/builders/roster.py only.

Deliverables:
1. Returning production (offense, defense) from CFBD, as a preseason-dated feature.
2. Talent composite, blue-chip ratio, and 4-year weighted recruiting composite with
   configurable year weights.
3. Transfer portal net rating (2021+ only) with an explicit portal_era flag and
   wide-uncertainty handling per §3.4 — the feature must be null with an indicator
   before 2021, never zero-filled.
4. Coach features: head coach tenure years, new-HC flag, OC/DC tenure and change
   flags from configs/coordinators.yaml (hand-maintained; seed it with the last 3
   seasons for P5 teams and leave the rest null with an indicator).
5. QB status table and manual-entry CLI: `ncaa-quant roster set-qb --game ... --team
   ... --status {starter,backup,unknown}` writing to a versioned table with
   event_time. Plus a depth-chart scrape stub that raises NotImplementedError with a
   docstring explaining what it would do. Per §3.4, historical injury data is not
   reliably available — v1 is QB-only and prospective.
6. OL returning starts if obtainable from roster data; if not, document why and skip.
7. Tests: era-flag behavior at the 2020/2021 boundary, null-vs-zero discipline
   (assert portal features are null not zero pre-2021), recruiting weight math,
   QB-status CLI round trip, pit_audit passes.

Acceptance:
- A test asserts that no builder in this module ever zero-fills a missing value
- make lint typecheck test pass

docs/notes/12.md.
```

---

# TASK 13 — Elo baseline

```
TASK 13 of 25: Elo baseline. Read @docs/DESIGN.md §9.1, §15 item 13.

Implement src/ncaa_quant/ratings/elo_baseline.py only.

This is a benchmark and sanity anchor, not the production rating engine. Keep it
simple and correct.

Deliverables:
1. Margin-aware Elo: standard update with a margin-of-victory multiplier and an
   autocorrelation correction for favorites (the FiveThirtyEight-style approach —
   document the exact formula used). Home-field advantage as a rating adjustment.
2. Between-season regression toward the mean with a configurable coefficient.
3. K-factor and MOV parameters tuned by walk-forward one-step-ahead log-loss on
   historical seasons — a simple scan is fine here, no Optuna yet.
4. Produces a per-team, per-week rating history table consumable as a feature and as
   an evaluation baseline.
5. Tests: single-update math against hand computation, symmetry (a win of margin M
   moves ratings equal and opposite before HFA), season regression, monotonicity
   (bigger margin against the same opponent never lowers your rating).

Acceptance:
- Run over 2014-2024. Report end-of-season top 15 for two seasons and the rank
  correlation against SP+ for those seasons — §15 sets a sanity bar of > 0.85. If it
  comes in materially below, investigate and report rather than moving on.
- Report one-step-ahead log-loss and ATS accuracy vs closing lines as the baseline
  number every later model must beat.
- make lint typecheck test pass

docs/notes/13.md.
```

---

# TASK 14 — State-space rating engine (the core of the system)

```
TASK 14 of 25: Kalman rating engine. Read @docs/DESIGN.md §9.2 through §9.5 in full,
plus §0.1. This is the most important module in the repository — read the spec
carefully before writing code.

Implement src/ncaa_quant/ratings/state_space.py and diagnostics.py only.

Deliverables:
1. State per §9.2: a **single joint league state vector** with full cross-team
   covariance. v1 per-team block is [off_epa, def_epa, st_value, pace]; design the
   code so extending to the 7-dim v1.1 block is a config change, not a rewrite.
   League-level states: hfa_global, hfa_team_deviation (heavily shrunk), and the
   identified season scoring-environment state. Dimension ≈540 for v1; per-game
   update applies both teams' measurement equations in one joint update.
2. Measurement model per §9.3: observations are garbage-filtered per-play offensive
   EPA for each side, plays run, ST EPA, plus final margin as a secondary
   high-noise observation. Observation noise scaled by informativeness — fewer plays
   means larger sigma. FCS opponents pinned to the pooled FCS prior with large
   variance. **Identifiability:** after every update, project off and def blocks to
   league-mean zero (constraint projection on mean and covariance); scoring_env
   carries the absolute level.
3. Process noise per §9.4: weekly Q, tuned per state dimension by maximizing
   one-step-ahead predictive likelihood over historical seasons. Event-triggered Q
   inflation on QB change, coordinator change, and a manual override hook.
4. Robustness per §9.5: winsorize standardized innovations at +/-2.5 sigma, and when
   |z| > 2.5 inflate effective observation noise
   R_eff = R * (|z|/2.5)^2 so the covariance update is consistent with the dampened
   state update.
5. diagnostics.py: standardized innovation series per team, the 3-consecutive-
   same-signed->2-sigma flag, filter health checks (innovation mean ~0, variance ~1 —
   if standardized innovations have variance far from 1 the noise model is
   misspecified and you must report it, not tune it away silently).
6. Full history run: filter all seasons 2014-2025 producing a per-team, per-timestamp
   posterior (mean + covariance) table, queryable as-of.
7. Tests:
   - 1-D analytic Kalman test against closed-form hand computation
   - PARAMETER RECOVERY: simulate a league with known latent strengths following a
     known random walk and a real schedule; assert the filter recovers the latent
     paths within stated tolerance and that posterior variances are well-calibrated
     (empirical coverage of the 95% band is 93-97%)
   - IDENTIFIABILITY / INVARIANCE: shift all initial off and def states (and the
     scoring_env level, consistently) by an arbitrary constant c; run the filter on
     the same game sequence; assert that after constraint projection the team off/def
     states and all one-step-ahead predictions are unchanged to numerical tolerance
     (only scoring_env may absorb the level shift — relative ratings and forecasts
     must be invariant)
   - winsorization triggers on an extreme planted result and bounds the update
   - CLIPPED-UPDATE VARIANCE: on a synthetic game with |z| > 2.5, the posterior
     variance after the winsorized update with R_eff inflation shrinks *strictly less*
     than the same update without clipping (full residual, uninflated R) — proving
     the covariance stays consistent with the dampened information content
   - Q inflation widens posterior variance as expected
   - as-of queries never return a posterior computed from future games (pit_audit)

Acceptance:
- Parameter recovery test passes with calibrated coverage — report the actual
  coverage number
- Full 2014-2025 filter runs in under 5 minutes; report the wall clock
- Show end-of-2023 top 15 by off and def rating, and show the posterior SD trajectory
  for one team across the season (it must shrink as games accumulate — this is the
  visible proof the core thesis works)
- make lint typecheck test pass

docs/notes/14.md — include the fitted Q values and the filter health statistics.
```

---

# TASK 15 — Preseason prior builder

```
TASK 15 of 25: Preseason priors. Read @docs/DESIGN.md §9.6, §15 item 15, ADR 0003.

Implement src/ncaa_quant/ratings/priors.py only.

Deliverables:
1. Prior mean blend per §9.6: last-season final posterior regressed toward conference
   mean, returning production adjustment, recruiting/talent, portal net (2021+),
   coaching-change discontinuity, QB carryover. For the market-aware stack only, also
   include SP+ preseason as a blend component (ADR 0003); fundamental priors MUST omit
   SP+. Weights FIT — not assumed — by regressing each season's LATE-SEASON (≥8 games)
   posterior ratings FROM A DIFFUSE-INITIALIZATION FILTER RUN on the preseason
   predictors over 2015-2024. Do NOT fit against prior-initialized early ratings
   (that recovers the assumed weights — circular). Preferred upgrade path (implement
   or stub behind a config flag with a clear NotImplemented until ready): maximize
   Weeks 1-4 one-step-ahead predictive likelihood of game observations w.r.t. the
   weights. Report fitted weights and standard errors.
2. Prior variance: base variance inflated by roster turnover, so a team returning 40%
   of production starts with a materially wider posterior than one returning 85%.
   This is what makes the prior-decay schedule self-adjusting per §9.6 — verify the
   behavior differs between high- and low-continuity teams and show it.
3. Wire priors into the state-space filter's initialization, and back into the
   shrinkage seam left in Task 10.
4. Handle the regime issues honestly: portal features exist only 2021+, coordinator
   data is partial. Missing inputs must widen the prior variance, never silently
   default to the league mean with false confidence.
5. Tests: weight-fitting reproducible under a fixed seed, turnover-variance
   monotonicity, missing-input widens variance, a new-HC team's prior is pulled
   toward talent-implied level, and CIRCULARITY DEMONSTRATION: on synthetic data
   where the true generative prior weights differ from a planted "assumed" blend
   used to initialize an informative-prior filter, fitting against that filter's
   early (prior-dominated) ratings recovers the planted ASSUMED weights (not the
   true generative ones) — proving why the diffuse-late / likelihood targets are
   required.

Acceptance:
- Report fitted weights with standard errors, and out-of-sample score of the prior
  against REALIZED GAME OBSERVATIONS or against diffuse-run late ratings for 2
  held-out seasons — NEVER against prior-initialized early posteriors
- Generate and store Week 1 predictions for 2023 and 2024 for later evaluation
- Show the prior-vs-evidence crossover: for a high-continuity and a low-continuity
  team, at what game number does accumulated evidence outweigh the prior? §9.6
  expects roughly games 5-7 on average with real variation between them.
- Circularity demonstration test passes and is documented in notes
- make lint typecheck test pass

docs/notes/15.md.
```

---

# TASK 16 — Walk-forward harness

```
TASK 16 of 25: Walk-forward evaluation harness. Read @docs/DESIGN.md §7.1, §7.2,
§15 item 16.

Implement src/ncaa_quant/evaluation/walkforward.py only. No models yet — the harness
must work against a trivial placeholder predictor.

Deliverables:
1. The replay engine per §7.2 item 1: for a test season Y, initialize priors from
   pre-Week-1 information, then loop weeks — compute features as-of the configured
   day/time (default Tuesday), call the predictor, record predictions against lines
   as-of that timestamp AND against closing lines, then reveal results and update
   ratings. The information set at each step must exactly match what production would
   have.
2. Line lookup must resolve to the snapshot at the configured decision point via
   as-of join, with an explicit fallback ladder that is **logged per game**:
   snapshot at decision point → nearest earlier snapshot within a configured
   tolerance → null with indicator. The CFBD open/close must never enter this ladder
   for snapshot-backed seasons. Record `line_source` and `n_books_available` on every
   prediction row so Task 21 can slice by them.
3. OOF prediction storage: a predictions table with prediction_id, game_key, as_of,
   model_version, feature_hash, all predicted quantities, `line_source`,
   `n_books_available`, and the realized outcome joined after the fact.
4. Configurable: test seasons, retrain cadence (to support the Task 23 ablations),
   as-of day/time, whether market features are available.
5. 2020 handling per §7.2 item 5: included for rating continuity, excluded from
   headline metrics, with a flag to run the sensitivity comparison.
6. Determinism: two runs with the same config, seed, and **fixed model artifacts
   (pinned by content hash)** produce byte-identical prediction tables. Scope matches
   §1.4: bit-for-bit applies to inference / walk-forward replay given pinned
   artifacts — not to re-running asynchronous parallel HPO (see Task 18 / §6).
7. Tests:
   - determinism test (pinned-artifact replay scope above)
   - INFORMATION-SET AUDIT: for a sample of (season, week) points, recompute the
     feature vector from raw data using only rows with event_time < as_of and assert
     equality with what the harness fed the predictor. Also assert no line used at a
     Tuesday decision point has an event_time later than that Tuesday timestamp.
   - WITHIN-WEEK LABEL PERMUTATION TEST per §14: train a model on labels permuted
     within week; out-of-sample it must score approximately at chance. Build the
     hook for this now.
   - PLANTED PROPHECY TEST per §14: add a deliberately future-leaking feature in a
     test fixture; BOTH pit_audit and the information-set audit must catch it.
   - the harness runs end-to-end with a placeholder predictor that always predicts
     the league-average margin

Acceptance:
- Information-set audit passes on at least 20 sampled week-points
- Placeholder predictor runs across 2019-2024 and produces a metrics-ready table
  (lockbox 2025 excluded per §7.2 item 9)
- make lint typecheck test pass

docs/notes/16.md.
```

---

# TASK 17 — Mapping-layer model heads

```
TASK 17 of 25: Model heads. Read @docs/DESIGN.md §5.2, §2.3, §15 item 17.

Implement src/ncaa_quant/models/heads/ only. No HPO (Task 18), no ensembling
(Task 19).

Deliverables:
1. LightGBM mu heads for margin and total, with monotone constraints per §5.2 on
   rating-differential features (a rating increase may never decrease predicted
   margin). Verify the constraints are actually applied — LightGBM silently ignores
   malformed constraint specs.
2. LightGBM quantile heads for margin and total at q in {0.05,0.1,0.25,0.5,0.75,0.9,
   0.95}.
3. Sigma heads per §5.2 Predictive variance: LightGBM trained on absolute OOF
   residuals of the stacked mean, |y - mu_stack|, then multiplied by sqrt(pi/2) for
   an unbiased Normal sigma. Training labels MUST be net of the member-mean
   (fixed Stage-1 point estimates; no posterior-draw mixture in the sigma-head
   target) so epistemic variance is not double-counted when member-disagreement and
   Stage-1 draw variance are added later. Situational and rating-uncertainty
   features are allowed as inputs.
4. Diversity members: XGBoost and CatBoost mu heads (GPU-enabled via config),
   ElasticNet on the top-30 features, NGBoost Normal(mu, sigma) for margin and total.
5. A uniform Predictor interface: fit(X, y, sample_weight), predict(X) ->
   structured output, save/load, and a FEATURE SIGNATURE CONTRACT — the saved model
   records its exact expected feature names/dtypes/order and predict() raises on
   mismatch rather than silently misaligning columns.
6. Sample weighting hook: time-decay weights across seasons, configurable, default
   documented.
7. Tests: monotone constraint respected (construct inputs proving it), feature
   signature mismatch raises, quantile crossing check (q05 <= q50 <= q95 — if
   crossing occurs, sort and log a warning), save/load round trip reproduces
   predictions exactly, each member trains on a 3-season fixture without error,
   SIGMA UNBIASEDNESS: on synthetic heteroskedastic data with known sigma(x), the
   abs-residual head times sqrt(pi/2) recovers E[sigma] within stated tolerance
   (and a control without the sqrt(pi/2) factor is biased low).

Acceptance:
- All members train through the Task 16 harness on 3 train seasons and produce OOF
  predictions
- Report MAE and RMSE for each member on one held-out season, alongside the Task 13
  Elo baseline. Any member that cannot beat Elo should be flagged, not hidden.
- make lint typecheck test pass

docs/notes/17.md.
```

---

# TASK 18 — Optuna hyperparameter optimization

```
TASK 18 of 25: HPO framework. Read @docs/DESIGN.md §6 in full, §15 item 18.

Implement src/ncaa_quant/models/hpo.py only.

Deliverables:
1. Optuna studies per head with TPE (multivariate=True) and Hyperband/ASHA pruning.
   Persistent storage (SQLite or journal) so studies resume after interruption.
2. Objective per §6: mean walk-forward validation loss averaged over the last 3
   validation seasons — never a single season. CRPS for distributional models,
   pinball for quantile heads, MSE for mu heads.
3. NESTED ISOLATION — enforce this at the API level, not by convention: the objective
   function must be structurally incapable of reading the outer test seasons. Pass it
   only a restricted data handle. Write a test that attempts to access a test season
   from inside the objective and asserts it raises.
4. Parallelism per §6: CPU-parallel LightGBM trials (4 concurrent), GPU trials for
   XGBoost/CatBoost via device=cuda. Wall-clock guard and MaxTrialsCallback.
   Asynchronous parallel TPE is **not** run-order deterministic — do not claim
   bit-for-bit search replay. The reproducibility unit is the logged champion
   artifact (params + seed + deterministic refit → content hash), per §1.4 / §6.
5. Seed management: seed = f(study_name, trial_number), logged per trial to MLflow
   along with params, per-season losses, and a feature-importance snapshot. Final
   champion refits use deterministic settings (CPU or framework deterministic mode).
   MLflow logging uses a local file store or single-writer pattern — not SQLite
   under 4-way parallel writers (§10).
6. Quarantine-season tiebreak per §6: after the study, compare the top-5 configs on
   season **2024** (never used in the study; distinct from lockbox **2025** per
   §7.2 item 9); if their ranking is unstable, select the more regularized config by
   a codified rule (document the rule precisely — e.g. higher min_child_samples, then
   lower num_leaves, then higher lambda_l2).
7. Tests: resumability (kill mid-study, restart, trial count continues), nested
   isolation test above, seed logging + deterministic champion refit → stable
   artifact hash (not full async-search bit-identity), tiebreak rule unit tests.

Acceptance:
- The nested isolation test passes — show me the code path that makes leakage
  structurally impossible, not just unlikely
- Run a 100-trial study on the margin mu head; report best params and the improvement
  over Task 17 defaults, and state whether the improvement exceeds season-to-season
  noise
- make lint typecheck test pass

docs/notes/18.md.
```

---

# TASK 19 — Ensemble, calibration, conformal, distribution assembly

```
TASK 19 of 25: Ensemble and distribution. Read @docs/DESIGN.md §2.3, §2.6, §5.2,
§15 item 19.

Implement src/ncaa_quant/models/ensemble.py, calibrate.py, conformal.py and
src/ncaa_quant/distribution/ only.

Deliverables:
1. Level-1 stacking per §5.2: simplex-constrained least squares over level-0 OOF
   predictions per target (weights >= 0, sum to 1), solved as a constrained QP —
   NOT plain NNLS with post-hoc normalization. No intercept (rationale in §5.2).
   Fit ONLY on out-of-fold predictions — add an assertion that the stacking input
   contains no in-fold predictions. Test that returned weights lie on the simplex
   by construction (including cases where unconstrained NNLS would need renorm).
2. Ensemble sigma via §5.2 Predictive variance: sigma_pred^2 = sigma_aleatoric^2
   (sigma-head) + member-disagreement Var_k(mu_k) + Stage-1 posterior-draw variance.
   Do not fold epistemic terms into the sigma-head target.
3. Distributional recalibration per §2.6 / §5.2 Level 2: a single monotone map fit
   on the PIT values of the OOF margin predictive CDF, and one for total — so ALL
   derived market probabilities recalibrate coherently. Do NOT fit separate isotonic
   maps per market (that breaks §2.2 consistency). Per-market reliability diagrams
   (ML, ATS@close, OU@close) are diagnostics only. For the fundamental stack,
   calibration targets are market-free (moneyline / distribution PIT only);
   ATS@close reliability is reported, never fit. Platt/parametric-PIT fallback when
   OOF n is thin. Report Cox slope/intercept before and after on diagnostic markets.
4. Conformal layer per §2.6: Adaptive Conformal Inference (ACI) as the production
   variant (online alpha adjustment under non-exchangeability), initialized from
   trailing-2-season split conformal / CQR on the quantile heads. Split-conformal
   coverage is approximate under season-over-season drift — report empirical
   coverage vs nominal at 50/80/95% and monitor weekly; do not claim a
   distribution-free guarantee.
5. distribution/bivariate.py: assemble the joint (margin, total) predictive
   distribution with estimated correlation rho (estimate it from residuals per §2.3 —
   do not assume zero, but report the estimated value; §2.3 expects small positive
   ~0.05-0.15).
6. distribution/key_numbers.py: empirical discretization kernel per §2.3 learned
   CONDITIONAL on the predicted margin — at minimum by buckets of mu_M; a smooth
   model of P(M=k | mu_M, sigma_M) preferred. Do NOT hand-tune key-number bumps.
   Validation: compare empirical exact-margin frequencies by predicted-spread
   bucket to kernel output (same buckets); material divergence is a fail.
7. distribution/simulate.py: Monte Carlo (100k draws, seeded) producing probabilities
   for any spread, total, or moneyline from the joint distribution.
8. Epistemic uncertainty per §2.6: 50 draws from the Stage-1 rating posteriors pushed
   through the mapping layer to produce a mixture predictive distribution.
9. Tests (property-based where possible):
   - all probabilities in [0,1]; P(over) + P(under) + P(push) == 1
   - INTERNAL CONSISTENCY (POST-CALIBRATION): after applying the PIT maps, P(win)
     derived from the margin distribution equals P(cover at spread 0) within
     tolerance — this must run on POST-calibration outputs, not pre-calibration
   - probabilities are monotone in the line (P(cover) decreases as the spread
     against you grows)
   - conformal approximate coverage within tolerance on held-out seasons
   - stacking weights non-negative, sum to 1, on the simplex by construction
     (constrained QP; include a fixture where plain NNLS+renorm would differ)
   - simulation is deterministic under a fixed seed
   - key-number kernel vs empirical margin frequencies by predicted-spread bucket

Acceptance:
- Report ensemble CRPS, log-loss, and Brier vs BOTH the Elo baseline and the
  de-vigged market baseline per §7.3. The market comparison is the one that matters —
  report it honestly even if the model loses.
- Reliability diagram and PIT histogram generated for a held-out season
  (diagnostics; fundamental stack must not have been fit on ATS@close)
- Report estimated rho and conformal coverage numbers (state approximate coverage;
  ACI vs initializer)
- make lint typecheck test pass

docs/notes/19.md.
```

---

# TASK 20 — Betting layer

```
TASK 20 of 25: Betting layer. Read @docs/DESIGN.md §12, §2.7, §15 item 20.

Implement src/ncaa_quant/betting/ only.

Deliverables:
1. de-vig: proportional method as default, with multiplicative and Shin methods
   implemented for comparison and selectable in config. Document which is default and
   why.
2. edges.py: edge = calibrated model probability minus de-vigged market probability,
   computed per side against the BEST available captured price across books (line
   shopping is alpha per §16 item 5). Add `n_books_available` to the output and
   require Task 21 to report edge and ROI stratified by it — otherwise line-shopping
   alpha in §16 item 5 will be overestimated in later seasons purely from coverage
   growth. Also emit `line_shopping_capture` per §2.7
   (`implied_prob(best@bet_time) − implied_prob(consensus@bet_time)`); Task 21
   reports it alongside CLV and must not fold it into CLV aggregates.
3. EV computation per bet at the actual available price.
4. kelly.py: fractional Kelly at 25% of full, capped at 1.5% of bankroll per bet, per
   §12. Include a weekly aggregate exposure limit and per-team exposure limit.
   Full-Kelly must be computable for reporting but never used for staking — enforce
   the cap in code, not config alone.
5. filters.py per §12: min edge (2.5% sides / 3% totals), no-bet on STALE inputs,
   no-bet when QB status is unknown, stricter bowl threshold, max bets per week.
   All configurable, all defaults from configs/betting.yaml.
6. clv.py: store book identity, line-at-recommendation, bet-time price, consensus
   price at bet time, same-book close, and consensus close (fallback diagnostic)
   for every recommendation. Settle CLV per §2.7: same-book closing price first;
   if that book's close is missing, flag `clv_settlement=fallback_consensus` and
   never pool those rows with `same_book`. For spreads/totals, translate the close
   to the bet's line (`clv_method` ∈ alt_line_price / model_dist / line_units /
   same_line). Compute probability-space CLV only when method is probability-valued;
   report `line_shopping_capture` as a separate field. Weekly settlement job.
7. Tests: hand-computed fixtures for each de-vig method, Kelly math against known
   closed-form cases, the cap holds even when Kelly recommends more, filters fire
   correctly for each condition, CLV sign convention verified with a fixture where
   the line moved in our favor and one where it moved against. Additionally: a
   fixture where the spread moves from -6.5 to -7 with unchanged -110 / -110 prices
   at the same book; supply a fixed margin distribution; hand-compute the correct
   line-translated CLV (`clv_method=model_dist`); assert that naive price-only CLV
   (`implied_prob(close_price) − implied_prob(bet_price)` at the moved line, no
   translation) differs from the line-translated value — this test must fail any
   implementation that still settles CLV as price-only against a moved line (the
   bug §2.7's definition change prevents). Also assert same-book vs
   fallback_consensus rows are not pooled in the settlement summary helper.

Acceptance:
- A test proves the staking cap cannot be bypassed by configuration
- CLV computed correctly on fixtures — show both directions, same-book settlement,
  and the -6.5→-7 line-translation case (naive price-only ≠ line-translated)
- line_shopping_capture is present and distinct from CLV on fixtures
- make lint typecheck test pass

docs/notes/20.md.
```

---

# TASK 21 — Evaluation metrics and reporting

```
TASK 21 of 25: Metrics and reports. Read @docs/DESIGN.md §7.3, §15 item 21.

Implement src/ncaa_quant/evaluation/metrics.py, significance.py, reports.py only.

Deliverables:
1. Full Tier 1-4 metric suite per §7.3: CLV (mean, % positive), CRPS, log-loss,
   Brier, calibration slope/intercept, PIT, interval coverage and width, MAE/RMSE,
   ATS/OU/SU accuracy. EVERY probabilistic metric reported alongside the de-vigged
   market baseline.
2. significance.py: block bootstrap by week (respecting intra-week correlation) for
   CIs on all rate and ROI metrics; paired bootstrap for champion-vs-challenger
   comparisons.
3. THE ANTI-METRIC RULE from §7.3: every rate reported must print its confidence
   interval next to it. Implement this as a formatting function that makes it
   impossible to render a bare win rate. This is a cultural safeguard — enforce it.
4. Slice analysis per §7.2 item 3: by conference, P5/G5, favorite/dog, totals bucket,
   ranked, bowl, rivalry, weather, plus `line_source` and book-count bucket, plus
   **totals bias per rule era** (`pre_2023_clock` vs `post_2023_clock` per §4.5 —
   mean residual and CRPS/calibration on totals sliced by era). Output as a
   heatmap-ready table. Label these as DIAGNOSTIC — not model-selection inputs.
5. Economic simulation: flat-stake ROI, quarter- and half-Kelly bankroll paths, max
   drawdown distribution, all with bootstrap CIs.
6. reports.py: HTML weekly report (predictions, edges, confidence, rating movements)
   and HTML backtest report (metric tables with CIs, reliability plots, weekly error
   curves, slice tables, equity curves, SHAP summaries).
7. Tests: metric implementations against hand-computed fixtures (especially CRPS and
   Brier), block bootstrap gives wider CIs than naive iid bootstrap on correlated
   data, the bare-rate formatter refuses to render without a CI, golden report test on
   a fixture season.

Acceptance:
- Generate the full backtest report for one season and show it to me
- Demonstrate the bare-rate guard by trying to bypass it
- make lint typecheck test pass

docs/notes/21.md.
```

---

# TASK 22 — MLflow, registry, promotion, rollback

```
TASK 22 of 25: Experiment tracking and model registry. Read @docs/DESIGN.md §8 items
7-8, §10, §15 item 22.

Implement src/ncaa_quant/registry/ and MLflow integration only.

Deliverables:
1. MLflow tracking wired into training and evaluation: params, metrics per season,
   artifacts (models, calibration maps, reports), and the manifest binding git SHA +
   DVC hash + config hash + seed manifest per §8 item 8.
2. Registry stages per §10: candidate -> challenger -> champion -> archived.
3. Promotion gate per §8 item 7: a candidate is promoted ONLY if it beats the
   incumbent on the pre-registered metric set (CRPS + log-loss + CLV backtest) on the
   SAME walk-forward seasons (lockbox 2025 excluded), with a paired block-bootstrap
   test whose alpha is Bonferroni-adjusted for promotion multiplicity, AND passes
   calibration and leakage gates. Exact rule: the registry maintains an append-only
   promotion-attempt ledger; within each calendar year, let k = number of promotion
   attempts recorded that year including the current attempt, and alpha_0 = 0.10;
   the attempt is significant only if p < alpha_0 / k. The comparison report MUST
   print k, alpha_0, and the adjusted threshold alpha_0/k. Failing candidates are
   archived with the comparison report, not discarded. Live forward paper-trade
   (§16 item 2; named in §1.6) is the confirmatory instrument for success criteria
   after promotion.
4. The gate must be non-bypassable from config — promotion of a failing candidate
   requires an explicit, logged, human --force flag that writes an override record.
5. Rollback CLI: one command re-pins any prior champion. Inference always resolves
   'champion' at runtime — never a hardcoded version.
6. Tests: promotion blocked on a deliberately worse candidate, promotion succeeds on
   a deliberately better one, Bonferroni adjustment tightens when k>1 (fixture with
   two attempts in the same year), comparison report prints k and alpha_0/k, --force
   writes an override record, rollback restores golden predictions byte-identically,
   inference fails loudly if no champion exists.

Acceptance:
- Show the gate blocking a worse model and the comparison report it produced
  (including attempt count and adjusted threshold)
- Rollback verified against golden predictions
- make lint typecheck test pass

docs/notes/22.md.
```

---

# TASK 22B — Production wiring and ablation switches

```
See docs/task-22b.md for the full paste-ready prompt. Inserted between Task 22 and
Task 23 to close deferred integration seams (priors → run_filter, real retrain
features, production stack adapters, ablation switches A1–A6, backtest CLI,
MLflow call-site wiring) before the run-only Task 23.

docs/notes/22b-preflight.md, docs/notes/22b.md.
```

---

# TASK 23 — Full backtest and ablation suite

```
TASK 23 of 25: Backtest and ablations. Read @docs/DESIGN.md §7.2, §15 item 23.

No new subsystems. Run the system and report results honestly.

Deliverables:
1. Full walk-forward backtest 2019-2024 (2020 handled per §7.2 item 5; lockbox
   **2025** excluded from all development/HPO/ablation/promotion evaluations per
   §7.2 item 9) for BOTH the fundamental and market-aware stacks.
2. Ablation suite, each a full walk-forward run:
   - A1: preseason priors off (league-mean initialization)
   - A2: rating updates FROZEN at Week 1 — this quantifies the entire continual-
     learning gain and is the system's central empirical claim. If A2 is not
     materially worse than the full system, the core thesis is wrong and you must
     say so plainly.
   - A3: market features off
   - A4: ensemble vs single LightGBM
   - A5: garbage-time filter off
   - A6: market features from CFBD open/close only, vs full snapshot history.
     This is the direct measurement of whether the historical odds purchase paid for
     itself. Run it on 2021-2024 only, where both are available and lockbox is
     excluded. Report the delta in CRPS, log-loss, and mean CLV with CIs. If A6
     shows no material difference, say so plainly — that is a legitimate and useful
     result, and it tells you whether to keep renewing the subscription.
3. The within-season weekly error curve per §7.2 item 2 — verify Week 10 error is
   below Week 4 error, or report that it is not.
4. Bet-layer backtest per §7.2 item 7: bets at stored snapshot lines (never the
   close), realistic pricing, CLV per bet, equity curves with bootstrap CIs. Split all
   headline bet-layer metrics by line-source regime per §7.2 item 8, and state
   explicitly that 2019 market-aware results are not comparable to 2021+.
5. All runs archived to MLflow with full manifests.

Acceptance — report these numbers without spin:
- Fundamental model ATS and OU accuracy vs close, with CIs, per §1.6
- CRPS and log-loss vs the de-vigged market baseline
- The A2 ablation delta — the headline result
- The A6 ablation delta (snapshot history vs CFBD open/close) on 2021-2024
  (lockbox 2025 excluded)
- Mean CLV of simulated bets with CI, split by line-source regime
- The weekly error curve
- If any success criterion in §1.6 is missed, state it explicitly in the notes. Do
  not tune anything to hit a target — that is backtest overfitting and it will cost
  real money later.

docs/notes/23.md — this is the most important notes file in the project. Write it as
an honest results memo, including what did not work.
```

---

# TASK 24 — Prefect production flows

```
TASK 24 of 25: Production automation. Read @docs/DESIGN.md §9.8, §10, §15 item 24.

Implement src/ncaa_quant/pipelines/ only.

Deliverables:
1. Deployments per §10: ingest_odds (6x/day, already live from Task 4 — migrate it
   here), postgame_ingest (Sat 23:30 + hourly to 03:00), weekly_update (Sun 06:00),
   retrain_gate (gated, Weeks ~5 and ~10 per §9.7), predict_publish (Tue 06:00 +
   daily Thu-Sat refresh + T-6h and T-1h), settle_clv (Sun).
2. Idempotency keyed by (source, partition); tenacity retries with exponential
   backoff; dead-letter queue for poisoned partitions; every flow resumable from the
   last successful task.
3. STALE MODE per §10: if ingestion fails, the prediction flow still runs on last-good
   data and stamps every output STALE(source, age). The betting filter from Task 20
   must then suppress bets. Test this end to end.
4. Notifications (ntfy or Telegram, configurable): flow failure, quality-gate failure,
   rating-innovation flags, new bet candidates, calibration alarm, weekly CLV summary.
5. The two human gates per §9.8: model promotion and bet confirmation. Everything
   else automated. The bet-confirmation output must show line-at-recommendation and
   enforce §16 item 3 — a recommendation is VOID if the line has moved past its
   threshold. Implement that check.
6. Runbooks in docs/runbooks/: what to do when each alarm fires. Must include:
   - raw odds archive off-machine replication (versioned S3-class target within 24h
     of capture) and the quarterly restore drill;
   - MLflow/Prefect UIs never exposed off-host without auth;
   - cadence shortfall response (snapshot count < expected − tolerance within 24h);
   - API-key scrub verification on raw-archive request metadata.
7. Tests: end-to-end dry run on a fixture week; chaos test (kill ingestion mid-flow,
   assert STALE predictions still publish and bets are suppressed); idempotency
   (re-running a flow changes nothing); notification hooks fire on seeded failures;
   raw-archive metadata scrub strips API keys; any live-network tests marked
   `@pytest.mark.live` and excluded from CI.

Acceptance:
- Chaos test passes — show the STALE-stamped output and the suppressed bets
- Fixture-week dry run completes end to end
- make lint typecheck test pass

docs/notes/24.md.
```

---

# TASK 25+ — Research sprints (gated, pre-registered)

Do not start these until Tasks 1-24 are complete and Task 23's results are in hand.
Each sprint gets its own session and must be **pre-registered**: before running
anything, commit a `docs/research/RNN-preregistration.md` stating the hypothesis,
the metric, the success threshold, and the seasons to be used. Per §16 item 1, this
is the vaccine against unconscious backtest iteration.

Template:

```
RESEARCH SPRINT R<N>: <name>. Read @docs/DESIGN.md §13.

Before writing code, write docs/research/R<N>-preregistration.md stating:
- the hypothesis in one sentence
- the exact metric and the success threshold (must beat champion CRPS/log-loss on
  walk-forward at p < 0.10, paired block bootstrap)
- which seasons are used and which are quarantined
- what result would cause you to abandon the approach

Then implement in a clearly isolated module. Do not modify production code paths.
Run the comparison. Report the result — including a null result — in
docs/research/R<N>-results.md.

If the pre-registered threshold is not met, the work is archived, not merged.
```

Sprints per §13, in priority order:
- **R1** — closing-line-residual target (highest expected value)
- **R2** — possession-level Monte Carlo simulator
- **R3** — QB-specific latent state
- **R4** — BART bake-off
- **R5** — FT-Transformer bake-off
- **R6** — hierarchical Bayesian prior generation (NumPyro, GPU)

Expect R4 and R5 to be null results. Per §13 that is a legitimate, publishable-to-
yourself outcome and closes the question.
