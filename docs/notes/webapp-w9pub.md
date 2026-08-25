# W9-PUB — live week-1 publish

## Launch narrative (2026-08-25)

### Aborted 13:35 run (slate regression)

First live attempt (`data/tmp/publish_w1_live.log`) finished predict at
`2026-08-25T13:35:44Z` with `n=99`, then failed export:

```
webapp_export_failed
error="slate regression: future-kickoff game_id(s) present in prior publish but
absent now: ['401000001']; refusing export (forgotten as_of?)"
```

Cause: production `data/webapp/publish_history/2026_w1.jsonl` still held **28
test stub lines** (each a single synthetic game `401000001`, future kickoff).
The live slate correctly omitted that id → `SlateRegressionError` → no history
append, no R2 push. Tier write for the 99 live games still landed
(`published_at=2026-08-25T13:35:44Z` in `tier_changes.jsonl`).

### Stub-history move (pre-retry)

Operator moved the contaminated history file aside before the successful retry:

- Parked at: `data/tmp/2026_w1_STUBS_FROM_TESTS_20260825.jsonl` (28 lines)
- Production `data/webapp/publish_history/2026_w1.jsonl` cleared for a clean
  append on the retry

### Successful retry (13:51)

`data/tmp/publish_w1_live_retry.log` — export+push+revalidate completed
(~`2026-08-25T13:51:38Z`).

### Three post-launch fixes (documented; not code-changed in this verify)

1. **Test `tmp_path` leak A** — unit tests writing week-1 publish history with
   synthetic `401000001` reached the production history path (stubs above).
2. **Test `tmp_path` leak B** — same contamination class (28 stub lines total
   from repeated test runs into production `2026_w1.jsonl`).
3. **99 orphan `tier_changes` lines from the abort** — abort batch remains in
   `data/webapp/tier_changes.jsonl` at `published_at=2026-08-25T13:35:44Z`
   (99 lines) alongside the successful `2026-08-25T13:51:33Z` batch (99 lines).
   Tier *values* identical across batches; no value drift. Orphans retained
   (append-only JSONL; not deleted this verify).

---

## T+ VERIFY — live publish verification (2026-08-25)

Read-only against live R2 + workstation. No re-publish, push, deploy, code
edit, or `make test`. Notes append + one commit only.

**Verdict: PASS — no rollback trigger.**

### 1. Push audit (`data/tmp/publish_w1_live_retry.log`)

From `webapp_export.push`:

| Field | Value |
|-------|-------|
| `bucket` | `ridge-artifacts` |
| `upload_order` | `['team_ratings_2026.json', 'track_record.json', 'week_predictions.json', 'meta.json']` |
| `meta_last` | `True` |
| `revalidation.ok` | `True` |
| `revalidation.status_code` | `200` |
| `revalidation.response.at` | `2026-08-25T13:51:36.827Z` |

**Eight keys uploaded (versioned + latest):**

1. `v1/2026/w1/tuesday_primary/team_ratings_2026.json`
2. `latest/team_ratings_2026.json`
3. `v1/2026/w1/tuesday_primary/track_record.json`
4. `latest/track_record.json`
5. `v1/2026/w1/tuesday_primary/week_predictions.json`
6. `latest/week_predictions.json`
7. `v1/2026/w1/tuesday_primary/meta.json`
8. `latest/meta.json`

`meta.json` is **LAST** in `upload_order` and last among the eight upload
entries (`meta_last: True`).

### 2. Leaked-secret audit

P0-A item 5 asked for a leaked-secret-names field. Actual return construction
in `push_artifacts_to_r2` (`src/ncaa_quant/webapp/push.py`):

```python
return {
    "bucket": bucket,
    "upload_order": ordered,
    "uploads": uploads,
    "content_hashes": content_hashes,
    "meta_last": ordered[-1] == META_FILENAME if ordered else False,
    "revalidation": revalidation,
}
```

**Keys that SHOULD be present:** `bucket`, `upload_order`, `uploads`,
`content_hashes`, `meta_last`, `revalidation`.

**`audit_leaked_secret_names`:** does **not** exist in code — not renamed, not
conditional. It was only computed by the W7-CLOSE *wrapper* script (post-hoc
scan of `json.dumps(result)` for known secret *names*) and recorded in
`docs/notes/webapp-w7.md`; never part of `push_artifacts_to_r2`.

**Artifact credential scan** (case-insensitive substrings `R2_`, `SECRET`,
`TOKEN`, `KEY`, `ACCESS`, `password`, `bearer` on JSON keys/values of the four
uploaded artifacts): **no credential-shaped names**. One English false-positive
substring `key` in `track_record.json` metrics notes text path
`.metrics[2].notes` (“key totals”) — not a secret name. Expected: none
(credentials).

### 3. `scripts/verify_published_artifacts.py latest/`

```
prefix='latest/' bucket='ridge-artifacts'
fetching latest/week_predictions.json and latest/meta.json
[PASS] 3_early_kickoffs: {...eight ids + kickoffs...}
[PASS] 7_game_id_shape: {'failures': [], 'n': 99}
[PASS] 8_kickoff_gt_as_of: {'as_of': '2026-08-25T10:00:00+00:00', 'violations': []}
[PASS] 9_schema_and_fixture: {'week.schema_version': '1.3.0', 'meta.schema_version': '1.3.0', 'fixture': None}
[PASS] 10_identity_stamps: {vintage W9A_REVAL, ensemble REDUCED_PER_ADR_0013,
  feature FEATURE_TIME=TUESDAY_DECISION, champion_version 2,
  model_version production-v0_reduced_v3, meta.champion_model {...}}
[PASS] meta_last_readable: ...
[PASS] schema_major: {'schema_version': '1.3.0', 'major': '1'}

ALL CHECKS PASSED
```

**Exit code: 0**

### 4. GET `latest/*` from R2

| Check | Observed |
|-------|----------|
| `meta.fixture` / `week.fixture` | **ABSENT** (correct; not `true`) |
| `schema_version` | `1.3.0` (week + meta) |
| Published row count | **99** |
| Eight Aug 29–30 `game_id`s | all present with expected kickoffs |
| Every `game_id` `^[0-9]{6,12}$` | pass (0 failures) |
| Rows with `kickoff_utc <= 2026-08-25T10:00:00Z` | **none** |
| `as_of_source` | `operator` |
| `vintage_label` | `W9A_REVAL` |
| `ensemble_scope_label` | `REDUCED_PER_ADR_0013` |
| `feature_time_label` | `FEATURE_TIME=TUESDAY_DECISION` |
| `champion_version` | `2` |
| `model_version` | `production-v0_reduced_v3` |
| `meta.champion_model` | `{"registry_name":"ncaa-quant","champion_version":2,"model_version":"production-v0_reduced_v3","registered_at":"2026-08-17T20:41:49Z"}` |
| `published_at` VERBATIM | `2026-08-25T13:51:33Z` |
| `next_expected_publish_utc` VERBATIM | `2026-08-27T13:51:33Z` |

**`registered_at` placement (string that SHOULD be present):**

- `week_predictions.model_identity.registered_at` — **ABSENT** (keys:
  `champion_version`, `model_version`, `registry_name`, `run_id`)
- `meta.champion_model.registered_at` — **PRESENT** as
  **`2026-08-17T20:41:49Z`**

### 5. Thursday deadline / staleness banner

Banner condition (`webapp/site/src/lib/formatting/time.ts`):

```typescript
const STALE_THRESHOLD_HOURS = 36;
// ...
return hoursSincePublish > STALE_THRESHOLD_HOURS && now > nextExpected;
```

Both conjuncts required. With:

- `published_at` = `2026-08-25T13:51:33Z` → +36h = `2026-08-27T01:51:33Z`
- `next_expected_publish_utc` = `2026-08-27T13:51:33Z`

the **past-next-slot** condition governs (36h already satisfied before next
slot). Banner first fires at:

| Zone | Instant |
|------|---------|
| UTC | **2026-08-27T13:51:33Z** |
| America/New_York | **2026-08-27T09:51:33-04:00** (Thu) |

### 6. Publish history

- `data/webapp/publish_history/2026_w1.jsonl`: **exactly 1 line**
- games count: **99**
- `as_of` / `as_of_source` / `published_at` match artifact:
  `2026-08-25T10:00:00+00:00` / `operator` / `2026-08-25T13:51:33Z`
- Stubs parked: `data/tmp/2026_w1_STUBS_FROM_TESTS_20260825.jsonl` (28 lines)

### 7. `tier_state` / `tier_changes`

| Metric | Value |
|--------|-------|
| `tier_state` 2026 key count (`tiers`) | **99** (unchanged vs abort baseline count) |
| Tier VALUE changes abort → now | **0** (abort `13:35:44Z` vs success `13:51:33Z` `new_tier` maps identical) |
| `tier_changes.jsonl` season=2026 lines | **198** (99 abort orphan + 99 success) |

### 8. Full `latest/` inventory

| Key | Size | Last-modified (UTC) | Disposition |
|-----|------|---------------------|-------------|
| `latest/meta.json` | 904 | 2026-08-25T13:51:35.934Z | live 2026 w1 |
| `latest/week_predictions.json` | 177885 | 2026-08-25T13:51:35.585Z | live 2026 w1 |
| `latest/track_record.json` | 6303 | 2026-08-25T13:51:35.108Z | live 2026 w1 |
| `latest/team_ratings_2026.json` | 107 | 2026-08-25T13:51:34.646Z | live 2026 w1 |
| `latest/results_2024.json` | 59909 | 2026-08-18T01:06:45.411Z | **non-2026 orphan — RETAINED** (not deleted) |
| `latest/team_ratings_2024.json` | 991908 | 2026-08-18T01:06:46.695Z | **non-2026 orphan — RETAINED** (not deleted) |

### 9. Week-1 monitoring baselines

| Baseline | Value |
|----------|-------|
| Published count | **99** |
| Coherence-suppression count | **15** |
| Suppressed `game_id`s | `401856666`, `401860880`, `401856767`, `401858422`, `401858427`, `401856769`, `401866411`, `401856771`, `401856635`, `401856773`, `401856779`, `401858431`, `401858211`, `401862701`, `401856775` |
| Fraction of interval positions outside `[0.25, 0.75]` | **31 / 84 = 0.3690** (among rows with non-null margin interval after coherence gate) |

---

## Acceptance

T+ VERIFY complete. Live `latest/` coherent with operator week-1 publish.
No rollback trigger. Orphans retained by design.
