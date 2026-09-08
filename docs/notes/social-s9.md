# S9 — Backup then sandbox push

**Date:** 2026-09-08  
**Status:** STOP on verifier exit code 1 (step 6). Steps 1–5 passed.
Export gate remained `False`. No non-sandbox writes. No deletes. No product code changes.

## 1. Backup `latest/*` → `backup_latest_20260908/`

| Key | Bytes | SHA-256 |
|-----|------:|---------|
| `latest/meta.json` | 904 | `df9eb2026782dd113efac3d732b332d92e02b5ba2142818461c6615d544f05ab` |
| `latest/results_2024.json` | 59909 | `b5c71da8f3502cb0b8fcab6ab1bbdb9688031e471d6e9306e957f79252be4032` |
| `latest/team_ratings_2024.json` | 991908 | `6c15eed9d203f0c1fcd4d96caf2832ab3fbc841cc4a2cdd008c92836d27939ca` |
| `latest/team_ratings_2026.json` | 107 | `1fde54deb45bd771f9c18236f365ff056c090b4576df1492e3ce8c14f6ff6d5d` |
| `latest/track_record.json` | 6303 | `0bb29a010c47326ac7c14a47f0e719d373ceb449827797532d9d0c48489c934b` |
| `latest/week_predictions.json` | 154180 | `f4304f28113b46cc42fa2d02548ddd0910082bc9e0135bbd12e0f801d04e08dc` |

Manifest: `docs/notes/_artifacts/social-s9/backup_manifest.json`.

## 2. Inventory BEFORE

`n=59` objects. Full dump: `docs/notes/_artifacts/social-s9/inventory_before.json`.

Prefixes present: `latest/` (6), `sandbox/` (23), `v1/` (25), `v2/` (5).

## 3. Sandbox push (S8 artifact set)

```
push_artifacts_to_r2(..., season=2026, week=2, refresh_kind=tuesday_primary,
                     schema_version=1.3.0, publish_scope="sandbox",
                     skip_revalidation=True)
```

Source: `docs/notes/_artifacts/social-s8/local_artifacts/*`  
`meta_last=True`. All 10 upload keys under `sandbox/`. Audit:
`docs/notes/_artifacts/social-s9/push_audit.json`.

## 4. Inventory AFTER + diff

| Change | Keys | Outside `sandbox/`? |
|--------|------|---------------------|
| Added | 6 | **no** |
| Modified | 4 | **no** |
| Removed | 0 | — |

Added (all sandbox):
- `sandbox/latest/results_2026.json` (**new** — was absent before)
- `sandbox/v1/2026/w2/tuesday_primary/{meta,results_2026,team_ratings_2026,track_record,week_predictions}.json`

Modified (sandbox/latest only): `meta.json`, `team_ratings_2026.json`,
`track_record.json`, `week_predictions.json`.

**No `latest/`, `v1/*`, or `v2/*` (non-sandbox) keys changed.**

## 5. GET `sandbox/latest/results_2026.json`

| Check | Result |
|-------|--------|
| graded | **99** |
| postgame_missing | **0** |
| no_pre_kickoff_publish | **0** |
| `fixture` key | **absent** |
| schema_version | **1.3.0** (matches week + meta) |

Also: 789 `game_not_final`.

## 6. Verifier — **STOP**

```
uv run python scripts/verify_published_artifacts.py sandbox/latest/
→ exit code 1
```

| Check | Result |
|-------|--------|
| 3_early_kickoffs | **FAIL** — all 8 week-1 early IDs absent (slate is week **2**, n=86) |
| 7_game_id_shape | PASS |
| 8_kickoff_gt_as_of | PASS |
| 9_schema_and_fixture | PASS |
| 10_identity_stamps | **FAIL** — `meta.champion_model.registered_at` is `2024-08-01T12:00:00Z`; verifier expects `2026-08-17T20:41:49Z` |
| meta_last_readable | PASS |
| schema_major | PASS |

Expected exit 0; observed **1**. No code change (forbidden). Operator decision needed:
whether the verifier’s week-1 early-ID + registered_at baselines should be
updated for week-2 sandbox, or a different artifact set should be pushed.

## Forbidden checks

- Export gate: still `False` after run.
- Non-sandbox writes: none (diff clean).
- Deletes: none.
- Product code: unchanged.
