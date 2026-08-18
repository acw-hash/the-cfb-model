# W9-PUSH — land the W9-R restamp on production (attended fixture push + week-1 rehearsal)

**Date:** 2026-08-18 (UTC)  
**Status:** Complete. Production `latest/` is schema **1.2.0**, `published_at=2024-09-24T10:00:00Z`, `fixture: true`.  
**Authority:** `docs/notes/webapp-w9r.md` Phase 1 (`a4a1e88`); `docs/notes/webapp-w8a.md`; `docs/notes/webapp-w8c.md`; `docs/notes/webapp-w7.md` (W7-TESTPUBLISH-GUARD, W7-BUCKET-AUDIT); DESIGN §3.2, §3.3.

Attended only. No schedule, no Prefect, no CI. No product-code change. FIXTURE banner left up — this published the 2024 fixture set, not 2026.

Artifacts: `docs/notes/_artifacts/webapp-w9push/`.

---

## 0. What this rehearsal is

Week 1 live publish will call `export_publish_artifacts(..., push=True)` → `push_artifacts_to_r2(..., publish_scope="live")`. That path:

1. Builds artifacts (`assert_game_prediction_allowlist` inside `build_game_prediction`).
2. Validates CFBD `game_id` shape (`^[0-9]{6,12}$`) **before any upload**.
3. Writes `v{major}/{season}/w{week}/{refresh_kind}/*` then `latest/*`, `meta.json` last.
4. POSTs on-demand revalidation.

This task cannot run a live export (forbidden: 2026/live data; W9-R left `team_ratings_2024.json` on the previous champion). The sanctioned operator restore is: read committed `webapp/fixtures/*` → stock `push_artifacts_to_r2`. Same `push.py`, same CFBD guard, same revalidation. `run_fixture_week_publish()` / `run_chaos_stale_publish()` were **not** called (they route to `sandbox/` and emit synthetic ids).

---

## 1. Commands run (in order)

From repo root, workstation `.env` loaded by `load_config()` / `load_secrets()`. No secrets in this file.

```
uv run python docs/notes/_artifacts/webapp-w9push/backup_latest.py
uv run python docs/notes/_artifacts/webapp-w9push/push_fixtures.py sandbox
uv run python docs/notes/_artifacts/webapp-w9push/push_fixtures.py live
uv run python docs/notes/_artifacts/webapp-w9push/push_fixtures.py rollback-sandbox
```

`push_fixtures.py rollback-live` exists for a real rollback and was **not** executed.

Production curls used `curl.exe` + vscode `rg`. Full after-push paste: `_artifacts/webapp-w9push/acceptance_commands.txt`.

---

## 2. Backup manifest (before any write)

Local only: `docs/notes/_artifacts/webapp-w9push/latest-pre/`. No backup prefix was created in the bucket.

Captured `2026-08-18T01:03:26Z` from `ridge-artifacts` `latest/`:

| Key | Bytes | SHA-256 | Last-modified (object) |
|-----|------:|---------|------------------------|
| `latest/meta.json` | 925 | `3e03dd6722e43749d1207872efec7687974368af12ec2b4b073d863bce293acf` | 2026-08-14T22:21:34Z |
| `latest/results_2024.json` | 59717 | `ccf8d8bbdf9ec79b41c2428ee94d79350ff6c955249621007a216eb7da94a8c7` | 2026-08-14T22:21:32Z |
| `latest/team_ratings_2024.json` | 991908 | `6c15eed9d203f0c1fcd4d96caf2832ab3fbc841cc4a2cdd008c92836d27939ca` | 2026-08-14T22:21:33Z |
| `latest/track_record.json` | 5624 | `5ac8a644cbb31d8cb50a8facaf9c1114a60d323f9fa6c0c4009a2c21ceb8a9d6` | 2026-08-14T22:21:33Z |
| `latest/week_predictions.json` | 107264 | `d0736af934ab254219add3f38ac0967257debbe6b4561bb25650116402055567` | 2026-08-14T22:21:34Z |

That `week_predictions` / `meta` pair is the W7-BUCKET-AUDIT restore (schema **1.1.0**, `published_at=2024-09-24T06:00:00Z`, `vintage_label=REGRADED_V2`, four withdrawn keys present, values `0.42531` / `0.44672924450891227` on game `401628373`).

Backup still present after the live push (not overwritten):

```
backup still 1.1.0 2024-09-24T06:00:00Z True
```

JSON: `_artifacts/webapp-w9push/backup_manifest.json`.

---

## 3. Sandbox rehearsal

```
uv run python docs/notes/_artifacts/webapp-w9push/push_fixtures.py sandbox
```

`publish_scope=sandbox`. Source: committed `webapp/fixtures/{week_predictions,track_record,results_2024,team_ratings_2024,meta}.json`. `fixture: true` on all five. `meta_last: true`. Revalidation **not** triggered (sandbox). Elapsed 3.625s.

**Guards on this write:** neither fired. Expected.

- CFBD id-shape: `validate_live_publish_game_ids` is gated on `publish_scope == "live"`. Sandbox skips it.
- Export allowlist: not called by `push.py` at all (see §4).

Round-trip `GET sandbox/latest/week_predictions.json`:

```
schema_version: 1.2.0
fixture: true
published_at / as_of: 2024-09-24T10:00:00Z
n_games: 56
withdrawn_key_counts: p_cover_home=0 p_over=0 p_ats_home=0 p_ou_over=0
                      p_cover_home_credible=0 p_over_credible=0
game0_id: 401628373
game0_kickoff_utc: 2024-09-28T19:30:00Z
sha256: fda7004826304dadc9e8200c7e45164ddb96f5ed4c176e51a24efc701c43d181
```

Full report: `_artifacts/webapp-w9push/sandbox_rehearsal.json`.

Also wrote `sandbox/v1/2024/w5/tuesday_primary/*` (sandbox prefix of the stock key layout). Did not write live `latest/` or `v2/*`.

---

## 4. Guard evidence (live restore)

```
uv run python docs/notes/_artifacts/webapp-w9push/push_fixtures.py live
```

From `_artifacts/webapp-w9push/live_push.json`:

**CFBD id-shape guard — ran inside `push_artifacts_to_r2` (traced wrapper around `validate_live_publish_game_ids`):**

```
"cfbd_id_guard": {
  "ran": true,
  "passed": true,
  "pattern": "^[0-9]{6,12}$",
  "n_ids": 56,
  "all_match": true,
  "elapsed_ms": 14
}
```

Invoked **before** any `put_object`. Pattern is `CFBD_GAME_ID_PATTERN` in `src/ncaa_quant/webapp/push.py`. This is the first real write to `latest/` where that guard is evidenced as having executed (W7 tests used a fake S3).

**Export allowlist — not inside `push.py`.** `assert_game_prediction_allowlist` lives in `export.py` and runs from `build_game_prediction` during artifact **build**. A JSON-file restore never re-enters export.

Attended wrapper called it on the 56 committed fixture games immediately before the live `push_artifacts_to_r2`:

```
"allowlist": {
  "ran": true,
  "passed": true,
  "n_games": 56
}
```

No product-code change. If that wrapper is omitted, the restore path would skip the allowlist and only run CFBD.

**Week-1 implication (the rehearsal finding):** week 1 must go through `export_publish_artifacts` then `push_artifacts_to_r2(live)`, not a fixture-file restore. Export is what fires the allowlist; push is what fires CFBD. Restoring pre-built JSON is a different path. This restamp had to use restore because a live export is forbidden here.

Did **not** STOP the landing: both guards passed on this write, no code change was required, and stopping would have left the three production defects in place. The finding is operational, not a missing function.

---

## 5. Push to `latest/`

Same call as §4. `publish_scope=live`. Objects (all `fixture: true`):

| File | Bytes | SHA-256 |
|------|------:|---------|
| `week_predictions.json` | 99558 | `fda7004826304dadc9e8200c7e45164ddb96f5ed4c176e51a24efc701c43d181` |
| `track_record.json` | 6322 | `a5990385ba3eed5b022ade2e90af7e15ec544b3aa67b4ac824d20bbcbeb0d2ff` |
| `results_2024.json` | 59909 | `b5c71da8f3502cb0b8fcab6ab1bbdb9688031e471d6e9306e957f79252be4032` |
| `meta.json` | 923 | `d6b31ef3c683d090a483e3636f7c6d26687dcc9b2aa2f4483b0ddc0730742baf` |
| `team_ratings_2024.json` | 991908 | `6c15eed9d203f0c1fcd4d96caf2832ab3fbc841cc4a2cdd008c92836d27939ca` |

`team_ratings_2024.json` is **byte-identical** to the pre-push `latest/` object (W9-R did not regenerate it; still schema 1.1.0 / `published_at=2024-09-24T06:00:00Z`). Included because the W7 restore path writes the full five-file set. Schema gate reads `meta.json` (now 1.2.0).

Stock push also overwrote `v1/2024/w5/tuesday_primary/*` — same side effect as W7-BUCKET-AUDIT and as week 1 (`schema_version` 1.2.0 → major `v1`). **Did not** write `v2/*`, `v1/2024/w5/daily_refresh/*`, `v1/2024/w6/*`, or delete any synthetic prefix. W7-CLOSE-2 doctored 2.0.0 object untouched.

Round-trip `GET latest/week_predictions.json` after the write: schema 1.2.0, withdrawn keys absent, `as_of=2024-09-24T10:00:00Z`, `fixture: true`.

---

## 6. Revalidation and elapsed time

Stock `push_artifacts_to_r2` POSTed after `meta.json` landed. Bearer from `WEBAPP_REVALIDATE_SECRET`; bypass header from env if set. Response:

```
webapp_revalidate_ok  status_code=200

"revalidation": {
  "ok": true,
  "status_code": 200,
  "response": {
    "ok": true,
    "revalidated": true,
    "paths": ["/", "/results", "/about", "/game"],
    "at": "2026-08-18T01:06:48.762Z"
  }
}
```

| Clock | Event |
|-------|--------|
| 2026-08-18T01:06:44Z | live push start |
| 2026-08-18T01:06:48.762Z | revalidate 200 (`at`) |
| 2026-08-18T01:07:07Z | first production GET `/` already showed `Sep 24, 2024, 10:00 AM UTC` (poll attempt 1) |

Push call itself 4.312s (uploads + revalidate). Production reflected the new `published_at` on the first GET after the call returned (**≤23s** from push start; site was already updated). W8-A measured **10.8s** with a tighter in-process loop — same mechanism, not 6h ISR.

---

## 7. Production after revalidation

### Before (1.1.0 still live)

```
rg -o "48\.9|49\.9|14\.53|0\.78"   → (empty)
rg -c "50\.7|51\.3|14\.85|47\.8|1\.35"  → 1
rg -o "NOT CURRENTLY FIT TO BET" → 2 hits
rg -o "FIXTURE" → 2 hits
published stamp: Sep 24, 2024, 6:00 AM UTC
banners: FIXTURE DATA + Data may be stale
```

### After — acceptance 5

```
curl -s https://the-cfb-model.vercel.app/results | rg -o '48\.9|49\.9|14\.53|0\.78'
48.9
49.9
0.78
48.9
49.9
14.53
0.78
48.9
49.9
0.78
48.9
49.9
0.78
48.9
49.9
14.53
0.78
48.9
49.9
0.78

curl -s https://the-cfb-model.vercel.app/results | rg -c '50\.7|51\.3|14\.85|47\.8|1\.35'
0

curl -s https://the-cfb-model.vercel.app/results | rg -o 'NOT CURRENTLY FIT TO BET'
NOT CURRENTLY FIT TO BET
NOT CURRENTLY FIT TO BET

curl -s https://the-cfb-model.vercel.app/        | rg -o 'FIXTURE'
FIXTURE
FIXTURE

curl -sI https://the-cfb-model.vercel.app/about  | rg -i 'x-robots-tag'
X-Robots-Tag: noindex, nofollow, noarchive
```

New figures present; old REGRADED_V2 and W9-A first-pass figures **zero**; verdict intact; FIXTURE banner up; `noindex` unchanged.

### After — acceptance 6 (W8-C residual)

`latest/` is no longer 1.1.0. Withdrawn names and the two 401628373 values are gone from HTML **and** from the served object.

```
p_cover_home: 0
p_over: 0
p_ats_home: 0
p_ou_over: 0

game p_cover_home: 0
0.42531 → (no match)
0.44672924450891227 → (no match)
```

R2 `GET latest/week_predictions.json` withdrawn_key_counts all 0 (see live_push round-trip). The backup copy still has those keys; that is the rollback source, not what the site reads.

---

## 8. Provenance sentence, now true

Live `/game/401628373` (`_artifacts/webapp-w9push/provenance_401628373.txt`):

```
Vintage: W9A_REVAL
  Which graded training run produced these numbers.
Ensemble: REDUCED_PER_ADR_0013
  Which models were combined. Reduced means a smaller set than the full experimental ensemble.
Feature time: FEATURE_TIME=TUESDAY_DECISION
  When inputs were frozen. Tuesday decision means later information is not in this forecast.
Published: Sep 24, 2024, 10:00 AM UTC
Refresh: Tuesday primary
```

| Clock | Value |
|-------|--------|
| Served `published_at` / as_of | `2024-09-24T10:00:00Z` |
| Game `401628373` `kickoff_utc` | `2024-09-28T19:30:00Z` |
| `published_at < kickoff` | **true** |

The Tuesday-decision gloss is no longer sitting on a 06:00Z stamp. 6:00 AM UTC count on the game page after push: **0**.

---

## 9. Freshness rendering unchanged

`layout.tsx` still shows FIXTURE when `meta.fixture === true` and the site-staleness banner when `isSiteStale(published_at, next_expected_publish_utc)` (>36h **and** past next slot). The fixture flag does **not** suppress site staleness — it never did (W7/W8 production both showed both banners).

| | Before | After |
|---|--------|--------|
| FIXTURE DATA banner | present | present |
| `Data may be stale — last updated …` | present | present |
| Stamp in that copy | Sep 24, 2024, **6:00** AM UTC | Sep 24, 2024, **10:00** AM UTC |
| `next_expected_publish_utc` (meta, not displayed) | 2024-09-26T06:00:00Z | 2024-09-26T10:00:00Z |
| Per-game `stale_stamp` | empty | empty |

Moving 06:00Z → 10:00Z does not flip the 36h test against a 2026 clock. Fixture path still looks like fixture data, not a live current publish. 6:00 AM leftover on `/` after push: **0**.

---

## 10. Rollback procedure (2 a.m. runbook)

**Do not run this unless `latest/` is bad.** A good push was left in place. Dry-run used `sandbox/` only.

### 10.1 Confirm the local backup still exists

```
uv run python -c "import json; from pathlib import Path; m=json.loads(Path('docs/notes/_artifacts/webapp-w9push/latest-pre/meta.json').read_text(encoding='utf-8')); print(m['schema_version'], m['published_at'], m['fixture'])"
```

Expect: `1.1.0 2024-09-24T06:00:00Z True`. If that file is missing, **stop** — there is no in-bucket backup.

SHA-256 must match §2. If anyone re-ran `backup_latest.py` after the live push, the backup would be the *new* 1.2.0 set and would not roll back. Do not re-run it.

### 10.2 Dry-run (sandbox) — already verified

```
uv run python docs/notes/_artifacts/webapp-w9push/push_fixtures.py rollback-sandbox
```

Output (abridged; full: `_artifacts/webapp-w9push/rollback_sandbox_dryrun.json`):

```
source_dir: docs/notes/_artifacts/webapp-w9push/latest-pre
publish_scope: sandbox
meta_last: true
content_hashes:
  week_predictions.json  d0736af934ab254219add3f38ac0967257debbe6b4561bb25650116402055567
  meta.json              3e03dd6722e43749d1207872efec7687974368af12ec2b4b073d863bce293acf
  …identical to backup_manifest…

round_trip sandbox/latest/week_predictions.json:
  schema_version: 1.1.0
  published_at: 2024-09-24T06:00:00Z
  sha256: d0736af9…  (matches pre-push latest/)
  p_cover_home: 56   ← old object restored, as intended for a rollback test
```

After dry-run:

```
latest/meta.json            schema 1.2.0  published_at 2024-09-24T10:00:00Z  fixture True
sandbox/latest/meta.json    schema 1.1.0  published_at 2024-09-24T06:00:00Z  fixture True
local backup meta           1.1.0 2024-09-24T06:00:00Z unchanged
```

Production `latest/` was **not** rolled back.

### 10.3 Real rollback of `latest/` (only if needed)

```
uv run python docs/notes/_artifacts/webapp-w9push/push_fixtures.py rollback-live
```

That is stock `push_artifacts_to_r2(publish_scope="live")` of the five `latest-pre` files. It **will** also overwrite `v1/2024/w5/tuesday_primary/*`. It **will** revalidate. It will **not** run the 1.2.0 allowlist — the backup still carries withdrawn keys and would fail that check. CFBD ids on the backup are real and will pass.

Then confirm:

```
curl -s https://the-cfb-model.vercel.app/ | rg -o "Sep 24, 2024, 6:00 AM UTC"
```

Expect the 6:00 stamp and the old `/results` figures. Then you are back to the pre-W9-PUSH site.

Do **not** use `run_fixture_week_publish()` to roll back.

---

## 11. Week-1 path vs this restore (rehearsal notes)

| Step | This restamp | Week 1 |
|------|--------------|--------|
| Source | committed `webapp/fixtures/*` (2024 w5) | `export_publish_artifacts` from `predict_publish` |
| Allowlist | wrapper; **not** inside `push.py` | `build_game_prediction` during export |
| CFBD `^[0-9]{6,12}$` | `push_artifacts_to_r2(live)` — **passed** | same function — will run |
| Prefixes | `latest/` + `v1/2024/w5/tuesday_primary/` | `latest/` + `v1/{season}/w{week}/{refresh_kind}/` |
| `run_fixture_week_publish` | not used | must not be used |
| Revalidate | stock, 200 | stock |
| `fixture: true` | preserved | omit / false for live |
| Data year | 2024 fixtures only | 2026 live |

Nothing about `push.py` itself needed a code change for this write. The allowlist-on-restore gap is the one thing a tired operator must not “fix” by editing `push.py` at 2 a.m. — week 1 should export, then push.

---

## 12. Explicitly not done

- No 2026 / live data.
- FIXTURE banner and `fixture: true` left on.
- No `run_fixture_week_publish` / `run_chaos_stale_publish` to `latest/`.
- No writes to `v2/*`; no deletes of synthetic prefixes.
- Local backup not deleted and not overwritten after the push.
- No product-code change.
- No custom domain, `noindex` change, analytics, W8-R2-PUBLIC, Prefect, or unattended run.

---

## 13. STOP items — disposition

| # | Trip? | Notes |
|---|-------|-------|
| 1. Guard skipped on restore | **Partial — reported, did not halt landing** | CFBD ran and passed inside `push.py`. Allowlist is not in `push.py`; wrapper ran it on this write. See §4 / §11. |
| 2. Revalidate ≠ 200 / stale `published_at` | No | 200 at 01:06:48Z; 10:00 AM UTC on first GET. |
| 3. Old / first-pass figures survive | No | count 0. |
| 4. Withdrawn fields/values survive | No | names 0; `0.42531` / `0.4467…` no match; R2 object clean. |
| 5. Staleness or FIXTURE rendering changes | No | both banners still present; stamp 06:00→10:00 only. |
| 6. Push requires a code change | No | operator scripts live under `_artifacts/` only. |
| 7. Restore path ≠ week 1 | **Reported** | JSON restore vs export-then-push; same `push.py` + revalidate. §11. |
