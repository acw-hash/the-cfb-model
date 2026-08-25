# P0-E-FIX — align week ``model_identity`` with the 1.3.0 push contract

**Date:** 2026-08-25  
**Trigger:** P0-E Phase B — `push_artifacts_to_r2` refused live 2026 w1 export:
`week_predictions.json.model_identity` carried `registered_at`, which is not on
the push exact-keys allowlist (`registry_name`, `champion_version`,
`model_version`, `run_id`). `execute_predict_publish` → export → push has no
operator strip seam.

## Fix

- `src/ncaa_quant/webapp/export.py`
  - `_model_identity_from_rows` still collects `registered_at` from producing
    rows when present (for meta).
  - New `_week_model_identity` projects to the four allowlisted keys only.
  - `build_week_predictions` stamps `model_identity` via `_week_model_identity`.
- `build_meta` / `meta.champion_model.registered_at` unchanged — still reads
  `registered_at` from the fuller identity dict.

DESIGN §1.5 already showed week `model_identity` without `registered_at` and
`champion_model` with it; code now matches.

## Test

`tests/unit/test_webapp_w9d.py::test_week_model_identity_omits_registered_at_meta_keeps_it`
— export with row `registered_at`; week identity exact four keys; meta keeps
the stamp; `assert_push_artifact_allowlists` passes.

## Deliverable 4 — stock sandbox push (no strip)

Re-ran after the fix (2026-08-25): gate ON, `_isolated_publish_config`
redirects, inner export OFF, `execute_predict_publish` →
`export_publish_artifacts(push=False)` → `push_artifacts_to_r2(publish_scope=
"sandbox")` with the **raw** artifact dict (no strip). Allowlist passed on
export output; artifact SHA-256 unchanged through push.

- `meta_last`: true
- 8 keys, all under `sandbox/`
- week `model_identity` had no `registered_at`; `meta.champion_model.registered_at`
  present
- `export_enabled` restored False

Audit: `data/tmp/p0e_rehearsal/p0e_fix_d4/push_audit.json`

## Ambiguity

None — push allowlist and DESIGN already agreed; export was over-emitting.
