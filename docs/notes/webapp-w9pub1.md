# W9-PUB1 — publish history, idempotency partition, week-1 as_of override

**Branch:** `w9-pub1`  
**Date:** 2026-08-20  
**Authority:** `docs/webapp/DESIGN.md` §1.2, §1.3, §3.1; ADR 0017 Amendment 3

## Phase 0

1. `NCAA_QUANT_WEBAPP__EXPORT_ENABLED=false` in `.env`; `load_config().webapp.export_enabled is False`. Confirmed before any `make test`.
2. Tree clean; branch `w9-pub1` created from `main` @ `545f5ce`.
3. Slate simulation (in-memory, staged 2026 week 1):
   - `as_of=2026-08-25T10:00:00Z` → 99 games, 0 excluded; early ids  
     `{401856766, 401864494, 401858202, 401864577, 401866408, 401858201, 401864570, 401862693}` all present.
   - Calendar `as_of=2026-09-01T10:00:00Z` → 91 games, 8 excluded.

## What shipped

### Phase 1 — publish history store

- Module: `src/ncaa_quant/webapp/publish_history.py`
- Config: `webapp.publish_history_path` default `data/webapp/publish_history`
- Path: `{root}/{season}_w{week}.jsonl` — append-only JSONL, one full `week_predictions` object per line
- Written on every successful `export_publish_artifacts` (even when `push=False` / `export_enabled=False`)
- Never added to R2 artifact dicts; `push.assert_push_artifact_allowlists` refuses keys containing `publish_history` or ending in `.jsonl`
- Slate-regression guard: if a prior-record `game_id` is missing and `kickoff_utc > now`, raise `SlateRegressionError` and write nothing; post-kickoff absence passes
- `grade_export`: when `publish_history` is `None`, loads the season store; explicit list (including `[]`) still wins

### Phase 2 — idempotency partition

Token form:

```
predict_publish:{season}-w{week}-{refresh_kind}-{as_of:%Y%m%dT%H%M}
```

Examples:

- `predict_publish:2026-w1-daily_refresh-20260827T1000`
- `predict_publish:2026-w1-daily_refresh-20260828T1000`

Checked against existing `data/pipeline_state/idempotency.json`: **no** prior `predict_publish:` entries. Other sources use a similar time fragment (`ingest_odds:20260813T1201`). Same-day reruns collide; different calendar days do not.

### Phase 3 — `as_of` override

- Added `as_of: datetime | None = None` to `live_predict_rows`, `execute_predict_publish`, `run_predict_publish`, `predict_publish_task`, `predict_publish_flow`, `run_isolated_week_export`
- `None` → `week_decision_as_of` (unchanged calendar behavior)
- Set → used directly for kickoff filter + Kalman (`as_of_source="operator"`)
- No config / YAML / env / calendar special-case
- Artifact fields (schema **1.3.0** minor bump): `as_of`, `as_of_source` (`"calendar"` | `"operator"`)
- Prefect flow parameters are the operator CLI surface; `cli.py` `predict` remains unwired (outside sanctioned edits)

### Phase 4 — ADR 0017 Amendment 3

Recorded: one Tuesday `as_of` per **publish weekend**; 2026 week 1 takes two `tuesday_primary` publishes (Aug 25, Sept 1); operator override for multi-weekend CFBD weeks. DESIGN §1.3 not edited.

## Tests

- **`test_containment_week2_as_of_none_slate_unchanged`** — week-2 `as_of=None` slate equals calendar filter (byte-identical kept frame). Required containment.
- Week-1 operator vs calendar (99 / 91)
- History append-only + grade loader
- Early-game pre-kickoff selection under Aug 25 + Aug 29 + Sept 1 history
- Slate-regression future vs past
- Idempotency same-day no-op / different-day execute
- Export with `export_enabled=False` writes history; push refuses history keys

`make test`: **941 passed**, 1 deselected.

## Dry-run (export gated, R2 untouched)

Operator `as_of=2026-08-25T10:00:00Z`, `export_enabled=False`, `push=False`:

- 99 games, all eight early ids present
- `as_of_source=operator`, `schema_version=1.3.0`
- One history line written under a temp `publish_history/` path
- `push is None`

## Ambiguities / decisions

1. `cli.py` not in sanctioned edits — override exposed on Prefect flow/task params only.
2. Push allowlist: `as_of` / `as_of_source` are **optional** so frozen 1.2.0 fixtures still validate; new exports always emit them. Beyond “history exclusion only” but required for additive fields to be push-legal later.
3. `resolve_week_publish_as_of` reads games parquet directly (no team-name attach) so oracle/isolated tests with incomplete `teams` frames still resolve calendar `as_of`.
4. `SCHEMA_VERSION` → `1.3.0`. Frozen fixtures remain `1.2.0`.

## Stop and report — §1.3 grading precedence

**Do not write the amendment** (operator decision).

Under current §1.3 / `select_pre_kickoff_publish`, precedence is `refresh_kind` first (`daily_refresh=2` > `tuesday_primary=1`), then latest `published_at`.

If week 1 has:

1. Aug 25 `tuesday_primary` (operator `as_of`)
2. Aug 29 `daily_refresh`
3. Sept 1 `tuesday_primary` (calendar)

then for any game whose kickoff is **after** the Sept 1 primary stamp, both (2) and (3) are pre-kickoff candidates and **Aug 29 `daily_refresh` wins** over the better-informed Sept 1 primary.

On the real 2026 week-1 slate (**99** games): **91** games have `start_date > 2026-09-01T10:00:00Z` (all non-early Labor Day weekend games). Those 91 are affected if an Aug 29 (or any pre–Sept 1) `daily_refresh` exists in history alongside the Sept 1 primary.

The eight early games kick Aug 29–30; Sept 1 primary is post-kickoff for them, so they grade from Aug 25 / Aug 29 pre-kickoff rows only — not this precedence bug.

**Proposed amendment (not implemented):** select the pre-kickoff row with the latest `published_at`; use `refresh_kind` precedence only as a tiebreak when stamps are equal (or within the same publish minute).

---

## W9-PUB1-VERIFY (2026-08-20)

Precondition: `NCAA_QUANT_WEBAPP__EXPORT_ENABLED=false`; `load_config().webapp.export_enabled is False`.

### Item 1 — idempotency token collision (MERGE BLOCKING) — FIXED

`daily_refresh` uses the **same** `resolve_week_publish_as_of` path as Tuesday (no `refresh_kind` branch):

- `as_of is None` → `week_decision_as_of` via staged schedule + champion walkforward clock → **2026-09-01T10:00:00+00:00** (`calendar`) for week 1.
- `as_of` set → normalized operator instant (`operator`).

Partition previously stamped **`as_of`**, so run clock was ignored.

**BEFORE (as_of stamp) — all six collide within each set:**

```
# as_of=None → resolved 2026-09-01T10:00:00Z
predict_publish:2026-w1-daily_refresh-20260901T1000   # run 2026-08-27T06:00Z
predict_publish:2026-w1-daily_refresh-20260901T1000   # run 2026-08-28T06:00Z
predict_publish:2026-w1-daily_refresh-20260901T1000   # run 2026-08-29T06:00Z

# as_of=2026-08-25T10:00:00Z
predict_publish:2026-w1-daily_refresh-20260825T1000   # run 2026-08-27T06:00Z
predict_publish:2026-w1-daily_refresh-20260825T1000   # run 2026-08-28T06:00Z
predict_publish:2026-w1-daily_refresh-20260825T1000   # run 2026-08-29T06:00Z
```

Any two tokens within each set are **identical**.

**Fix:** `idempotency_partition_for_publish(..., published_at=)` — run clock / export `published_at`, minute resolution. `run_predict_publish` defaults `published_at` to `datetime.now(tz=UTC)`. `as_of` resolution untouched.

**AFTER:**

```
# as_of=None (resolved still 2026-09-01T10:00:00Z; stamp is run clock)
predict_publish:2026-w1-daily_refresh-20260827T0600
predict_publish:2026-w1-daily_refresh-20260828T0600
predict_publish:2026-w1-daily_refresh-20260829T0600

# as_of=2026-08-25T10:00:00Z (resolved still operator; stamp is run clock)
predict_publish:2026-w1-daily_refresh-20260827T0600
predict_publish:2026-w1-daily_refresh-20260828T0600
predict_publish:2026-w1-daily_refresh-20260829T0600
```

No collisions within either set. Same-minute reruns still share a token.

Phase 2 test updated to pin `as_of=OPERATOR_AS_OF` and vary `published_at` by day. **Pre-fix failure demonstrated** (`TypeError: run_predict_publish() got an unexpected keyword argument 'published_at'`; pre-fix as_of tokens also identical as printed above). Post-fix: that test passes.

Caller update (necessary): `tests/integration/test_pipelines_e2e.py::test_idempotent_rerun_fixture_week` no longer asserts the as_of time fragment.

### Item 2 — §1.3 precedence real scope (not merge blocking)

Planned calendar stamps at 10:00Z: Aug 25 primary, Aug 27/28/29 refresh, Sept 1 primary, Sept 3/4/5 refresh. Selection via real `select_pre_kickoff_publish` on staged 2026 week-1 slate (99 games).

Games where Aug 29 `daily_refresh` wins over a **later** pre-kickoff publish: **count = 0**, ids = `[]`.

Earliest post–Labor-Day kickoffs are 2026-09-03T22:00Z+; Sept 3 `daily_refresh` at 10:00Z is pre-kickoff and beats Aug 29 on same-kind latest `published_at`. Under the proposed amendment the Aug29-over-later count is also **0**; current vs amended winners differ on **0** games for this calendar.

**Reclassification:** §1.3 amendment is post-launch cleanup; do not schedule before Aug 25.

### Item 3 — test count reconciliation (MERGE BLOCKING)

Exact invocation:

```
make test
# → $(UV) run pytest -m "not live"
```

(also confirmed equivalent: `uv run pytest -m "not live"`)

| | |
|---|---|
| Deselected | `tests/unit/test_odds_api.py::test_live_odds_ingest_once_writes_raw_and_parquet` |
| Marker / filter | `@pytest.mark.live` deselected by `-m "not live"` |
| On 2026-08-19 runs? | **Yes** — `make test` has used `-m "not live"` throughout; notes routinely record `N passed, 1 deselected`. |

**Delta vs main @ `545f5ce` (pre–W9-PUB1 working tree):**

| | count | ids |
|---|---|---|
| Added | 9 | all `tests/unit/test_webapp_w9pub1.py::*` (containment, early-grade, as_of_source, export+history, idempotency, history append, push refuse, slate regression, week1 operator slate) |
| Removed | 0 | — |
| Renamed | 0 | — |
| Skipped | 0 | — |
| Deselected (unchanged) | 1 | live odds ingest (above) |

No main-tip test is silently absent on this branch.

**942 vs 941:** committed W9-D acceptance line is **`932 passed, 1 deselected`** (matches main collect). A transcript paste of `942 passed, 1 deselected` (W9-D Amendment 2) is **not** on committed `webapp-w9d.md`. Current branch: **941 passed + 1 deselected** = 932 main + 9 W9-PUB1. The remembered “942 passed” is not a silent deletion from `545f5ce`.

### Ambiguity 2 — push allowlist authorization record

Operator authorization for the W9-PUB1 push-allowlist change (optional `as_of` / `as_of_source` on week_predictions so frozen 1.2.0 fixtures still validate; new exports always emit them): **recorded here as accepted for merge** — required for additive schema fields to be push-legal; history exclusion alone was insufficient. DESIGN.md not edited.

---

## W9-PUB1-VERIFY-2 (2026-08-21)

Precondition: `NCAA_QUANT_WEBAPP__EXPORT_ENABLED=false` in `.env`;
`load_config().webapp.export_enabled is False`. Confirmed before pipeline import /
`make test`.

### Item 1 — Amendment 2 on main? (MERGE BLOCKING) — ABSENT

Grep against **`main @ 545f5ce`** (not the working tree):

```
git grep -n "assert_no_incoherent_margin_interval\|test_incoherent_band_assertion_bite" 545f5ce
# → no matches (exit 1)
```

Same symbols **are** on branch **`w9-d`** @ `95c85e1`
(`W9-D: interval amendments, sandbox export, and GameDetail absence work.`):

```
w9-d:src/ncaa_quant/webapp/export.py:340:def assert_no_incoherent_margin_interval(
w9-d:tests/unit/test_webapp_w9d.py:282:def test_incoherent_band_assertion_bite() -> None:
```

`test_incoherent_band_assertion_bite` has **no** `@pytest.mark.live`; it runs under
`-m "not live"`.

| | |
|---|---|
| Branch carrying Amendment 2 | **`w9-d`** |
| Tip | `95c85e1` |
| Merge base with `main` / `w9-pub1` | `92a245f` (W9-INT) |
| Ancestor of `main` / `545f5ce`? | **no** |
| Conflicts with `w9-pub1`? | **yes** — `changed in both`: `docs/runbooks/pre_publish.md`, `webapp/site/src/components/GameDetail/GameDetail.tsx`, `webapp/site/tests/results.test.tsx` (plus many add/modify-only paths on each side, including `export.py` / `predict.py`) |

**942 vs 932 gap:** `w9-d` adds **10** tests in `tests/unit/test_webapp_w9d.py` (vintage + coherence suite). `932 + 10 = 942`. The Aug 19 `942 passed, 1 deselected, … coverage 80.53%` line is on **`w9-d`'s** `docs/notes/webapp-w9d.md` (Amendment 2 acceptance), not on `main @ 545f5ce`. Prior VERIFY text that said the 942 paste is “not on committed `webapp-w9d.md`” was wrong for the `w9-d` tip; it is absent from **main’s** copy of that file.

**Aug 19 full test-id set:** **not recoverable.** Reflog shows the Aug 19 `w9-d` commit / checkouts; notes and agent transcripts retain only the summary line (`942 passed…`), not a `--collect-only` / junit / nodeid dump. Current `.pytest_cache/v/cache/nodeids` is dated 2026-08-20 and has 979 entries — not the Aug 19 set.

**Coherence check on current staged 2026 week-1 slate (99 rows)** using gate logic from `w9-d` (`sorted q10 < μ < q90` on `pred_margin_q10`/`q90`) via `live_predict_rows(..., as_of=2026-08-25T10:00:00Z)`, `export_enabled=False`, no R2/push:

- **n_suppressed = 15** (earlier 91-game rehearsal was 19; early eight not among suppressed)
- ids: `401856635`, `401856666`, `401856767`, `401856769`, `401856771`, `401856773`, `401856775`, `401856779`, `401858211`, `401858422`, `401858427`, `401858431`, `401860880`, `401862701`, `401866411`

**STOP:** Amendment 2 is **not** on main. Branch to merge is **`w9-d`**. Merge order is an operator decision — not performed here.

### Item 2 — token resolution: day, not minute — FIXED

Prior same-day test (`test_idempotency_same_day_noop_different_day_runs`) pinned **identical** `published_at` (06:00 twice) and asserted minute-form tokens `…T0600` / `…T0600` across days. That matched the minute-resolution implementation and did **not** catch same-day 06:00 vs 06:07.

**Pre-fix failure (minute stamp, tightened test):** second call at 06:07 executed (`calls["n"] == 2`); ledger keys `…20260827T0600` and `…20260827T0607`.

**Fix:** stamp `{published_at:%Y%m%d}` only.

**Eight tokens (day resolution):**

```
# six from VERIFY item 1 (run clock; as_of=None and operator share stamp)
predict_publish:2026-w1-daily_refresh-20260827
predict_publish:2026-w1-daily_refresh-20260828
predict_publish:2026-w1-daily_refresh-20260829
predict_publish:2026-w1-daily_refresh-20260827
predict_publish:2026-w1-daily_refresh-20260828
predict_publish:2026-w1-daily_refresh-20260829

# same-day 06:00 and 06:07 — identical
predict_publish:2026-w1-daily_refresh-20260827
predict_publish:2026-w1-daily_refresh-20260827
```

### Acceptance

```
make test
# → uv run pytest -m "not live"
# ========= 941 passed, 1 deselected, 32 warnings in 366.95s (0:06:06) ==========
# Required test coverage of 80% reached. Total coverage: 80.50%
```

VERIFY-2 edits only: `predict.py` (partition stamp `%Y%m%d`), `tests/unit/test_webapp_w9pub1.py` (same-day 06:00/06:07), `docs/notes/webapp-w9pub1.md`. No merge of `w9-d`.
