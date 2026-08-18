# ADR 0018: Push-time exact-key allowlist for every R2 write

## Status

Accepted

## Context

`assert_game_prediction_allowlist` lives in `export.py` and runs from
`build_game_prediction`. W9-PUSH (`7d7fea5`) showed that the operator restore
path — committed JSON files into `push_artifacts_to_r2` — never re-enters
export. A denylist of market field names also cannot see a renamed or
invertible leak. The write gate had to be an allowlist of the exact permitted
key set, on every object about to be written, including sandbox and restore.

## Decision

1. `push_artifacts_to_r2` calls `assert_push_artifact_allowlists` **before**
   credentials, CFBD id-shape, or any `put_object`. No skip flag.
2. The check is an **allowlist of exact keys** per artifact type
   (`week_predictions`, `track_record`, `meta`, `results_<season>`,
   `team_ratings_<season>`) and per nested object (`GamePrediction`,
   `conviction_basis`, `stale_sources[]`, metrics, graded games, rating
   weeks). Unknown keys fail. `fixture` is the only optional top-level key.
3. Unknown filenames fail. Dynamic `teams` map keys must match `^[0-9]+$`.
4. Withdrawn 1.1.0 fields (`p_cover_home`, `p_over`, and `_credible`
   companions) are not in the GamePrediction allowlist and therefore cannot
   be restored onto `latest/`.

## Consequences

- Restore of a 1.1.0 backup that still carries withdrawn keys fails closed.
- Week-1 export-then-push still runs the export allowlist **and** this
  push-time walk; they must agree.
- Push unit tests that previously sent stub JSON now use committed fixtures
  (or complete objects) so the guard is what is under test.
