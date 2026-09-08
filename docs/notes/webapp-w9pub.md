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


---

## T+ VERIFY ? v3 live publish (2026-09-08 week-2)

Read-only against live R2 + workstation. No re-publish, push, deploy, or
product fix. Notes append + one commit only.

**Verdict: ROLLBACK TRIGGER ? stop. Do not treat v3 as verified-clean.**

Source log: `publish_w2_live_v3.log` (predict+export completed
`~2026-09-08T15:34:16Z` / 11:34 ET). `scripts/publish_week2.py` only prints
`webapp_export.ok`, so a full `push_artifacts_to_r2` return dict is **not** in
the log; per-key audit below is reconstructed from R2 object listing + SHA-256
of GET bodies for the v3 upload window, plus log lines for revalidation.

### 1. Push audit

**Log (verbatim fragments):**

```
2026-09-08 11:34:16 [info     ] webapp_revalidate_ok           status_code=200
webapp_export = True
```

**Reconstructed uploads (10 keys ? not the expected 12):**

| Key | Bytes | SHA-256 | Last-modified (UTC) |
|-----|------:|---------|---------------------|
| `v1/2026/w2/tuesday_primary/results_2026.json` | 804333 | `318c515589739c2a7606462ec6507a7e47df991ade75b091a2e4a2a4dbeb7e25` | 2026-09-08T15:34:13Z |
| `latest/results_2026.json` | 804333 | `318c5155?dbeb7e25` | 2026-09-08T15:34:13Z |
| `v1/2026/w2/tuesday_primary/team_ratings_2026.json` | 107 | `7de8edb41b53b14abea6c2b7ef72c353f15441913df1b2069af0ecec3e76564a` | 2026-09-08T15:34:14Z |
| `latest/team_ratings_2026.json` | 107 | `7de8edb4?3e76564a` | 2026-09-08T15:34:14Z |
| `v1/2026/w2/tuesday_primary/track_record.json` | 6303 | `a06037d49e382cc5de1ed2ebf7f0f220f947f4d3b819e33689e42ec943112b88` | 2026-09-08T15:34:14Z |
| `latest/track_record.json` | 6303 | `a06037d4?43112b88` | 2026-09-08T15:34:14Z |
| `v1/2026/w2/tuesday_primary/week_predictions.json` | 154266 | `bd96caa0b7b48e36cc26b56d67901930f71a59f74dc3431ca8cf4e91ed0594a0` | 2026-09-08T15:34:15Z |
| `latest/week_predictions.json` | 154266 | `bd96caa0?ed0594a0` | 2026-09-08T15:34:15Z |
| `v1/2026/w2/tuesday_primary/meta.json` | 904 | `c97e8770d51664db13ca91bcf28a741953963629762f618d6e7498eb0e4e56ef` | 2026-09-08T15:34:15Z |
| `latest/meta.json` | 904 | `c97e8770?0e4e56ef` | 2026-09-08T15:34:16Z |

| Field | Observed |
|-------|----------|
| Upload key count | **10** (5 artifacts ? versioned+latest) ? **expected 12 ? FAIL** |
| `meta_last` (ordering) | **consistent with True** ? `latest/meta.json` last-modified after other `latest/*` from this batch |
| Revalidation | **ok**, HTTP **200** (`webapp_revalidate_ok`) |
| `audit_leaked_secret_names` | **absent** from `push_artifacts_to_r2` return (same as W9-PUB); post-hoc credential-pattern scan on five live artifacts: **empty** |

### 2. GET `latest/*` from R2

| Check | Observed | Gate |
|-------|----------|------|
| `meta.champion_model.registered_at` | **`2026-08-17T20:41:49Z`** (not fallback) | PASS |
| `latest/results_2026.json` present | yes | PASS |
| graded / postgame_missing / no_pre_kickoff_publish | **99** / **0** / **0** | PASS |
| `fixture` key on results | **absent** | PASS |
| Shared `published_at` (meta, week, track, team_ratings_2026, results_2026) | **`2026-09-08T15:34:11Z`** all five | PASS |
| `schema_version` | **1.3.0** all five | PASS |
| `week_predictions` row count | **86** | PASS |

`meta.champion_model` verbatim:
`{"champion_version":2,"model_version":"production-v0_reduced_v3","registered_at":"2026-08-17T20:41:49Z","registry_name":"ncaa-quant"}`

### 3. Suppression count (`week_predictions`)

Coherence-suppressed rows (`margin_interval_lo` and `margin_interval_hi` both null): **20** / 86.

IDs: `401866417`, `401864500`, `401858215`, `401860882`, `401858222`, `401856672`, `401858218`, `401868008`, `401868187`, `401864503`, `401864506`, `401856784`, `401856787`, `401866413`, `401856791`, `401867929`, `401856785`, `401856786`, `401866416`, `401856789`.

(Week-1 rehearsal baseline was 15/99 ? informational; not used as a hard gate here.)

### 4. Publish history `2026_w2.jsonl` ? **ROLLBACK TRIGGER**

| Line | `refresh_kind` | `published_at` | n games | carries `d4b06285`? |
|------|----------------|----------------|--------:|---------------------|
| 0 | `tuesday_primary` | `2026-09-08T14:09:14Z` | 86 | no |
| 1 | `tuesday_primary` | `2026-09-08T14:30:28Z` | 86 | no |
| 2 (newest) | `tuesday_primary` | `2026-09-08T15:34:11Z` | 86 | **no** |

Three `tuesday_primary` lines: **yes**. Newest carries digest `d4b06285`: **NO**.

Predict log *did* compute
`rating_digest=d4b062850728825520a87e39139a9567754ae41c92970227db7f60fe4267b718`
and stamps it on prediction rows, but `week_predictions` / history objects have
**no** `rating_digest` / `digest` field ? digest is absent from the newest
JSONL line and from live `latest/week_predictions.json`.

**ROLLBACK TRIGGER:** criterion ?newest carries digest d4b06285? failed.

### 5. Full `latest/` inventory

| Key | Size | Last-modified (UTC) | Disposition |
|-----|------|---------------------|-------------|
| `latest/meta.json` | 904 | 2026-09-08T15:34:16.121Z | live 2026 w2 v3 |
| `latest/week_predictions.json` | 154266 | 2026-09-08T15:34:15.583Z | live 2026 w2 v3 |
| `latest/track_record.json` | 6303 | 2026-09-08T15:34:14.975Z | live 2026 w2 v3 |
| `latest/team_ratings_2026.json` | 107 | 2026-09-08T15:34:14.460Z | live 2026 w2 v3 |
| `latest/results_2026.json` | 804333 | 2026-09-08T15:34:13.970Z | live 2026 w2 v3 (S8) |
| `latest/results_2024.json` | 59909 | 2026-08-18T01:06:45.411Z | **orphan ? RETAINED** (not deleted) |
| `latest/team_ratings_2024.json` | 991908 | 2026-08-18T01:06:46.695Z | **orphan ? RETAINED** (not deleted) |

### Acceptance

**STOP.** Item 4 digest gate failed (ROLLBACK TRIGGER). Item 1 key-count
expected 12 / observed 10 also fails the stated expect. No product fix applied
in this verify. Orphans retained.

