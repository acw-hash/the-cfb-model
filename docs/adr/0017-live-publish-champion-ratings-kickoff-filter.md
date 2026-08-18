# ADR 0017: Live publish uses champion-method ratings and drops kickoff-before-as_of games

## Status

Accepted (W9-L Amendment 2).

## Context

W9-P wired `predict_fn` to stored champion week parquet. That path cannot
emit 2026. Two live rating reconstructions exist:

1. Task 14 `filter_history.parquet` (advanced-box, 2014–2025, hygiene).
2. Champion method: staged plays-preferred observations, fitted Task 15
   priors, `run_filter` of `event_time < as_of`.

They are not the same estimator. Amendment 1 measured a 2024 week-5 μ
median |Δ| of 5.6 vs Task 14. Production follows the champion walk-forward
construction. Task 14 history is not a production input.

Season 2025 is the lockbox. Entering-2026 state requires Kalman
observations through 2025. Those observations must not be listed on
`WalkForwardConfig` replay/test/warmup, where `assert_lockbox_excluded`
still fires.

The validated v3 run used **one Tuesday `as_of` per CFBD week**, including
27 week-1 rows whose kickoff preceded that Tuesday. Publishing those games
as This Week / Game Detail forecasts would present a post-kickoff μ as a
pre-kickoff decision. Amendment 2 keeps the single Tuesday clock and
omits already-kicked-off games from the forecast artifact.

## Decision

1. **`predict_publish` default `predict_fn` is `live_predict_rows`.** Ratings
   recompute per publish (no observation/filter cache). ~4 min `run_filter`
   is expected. The parquet loader remains `oracle_predict_fn` only.
2. **Kalman observations** for a live predict of season `Y` are
   `range(2019, Y+1)`, including 2025 when `Y >= 2025`. This tuple is not a
   WalkForwardConfig replay list and is not passed to
   `assert_lockbox_excluded`.
3. **WalkForwardConfig** for the live path is the v3 champion YAML
   (replay 2019–2024). `assert_lockbox_excluded` still raises if 2025 is in
   replay, test, warmup, or continuity.
4. **Published slate** drops any game whose kickoff (`start_date`) is
   strictly before the week's Tuesday `as_of`. Equal kickoff is kept. The
   rule is keyed on kickoff vs `as_of`, not on week number. Excluded games
   are not forecasts; they reach the site through Results once graded.
5. **`FEATURE_TIME=TUESDAY_DECISION` stays global.** Every published game
   now has kickoff ≥ `as_of`.
6. **Bet-candidate stub is off** on the publish default (`[]`). Chaos tests
   may still inject dummy candidates.

## Consequences

- A 2026 week-1 Tuesday (`2026-09-01T10:00:00Z` after D1 unclamp) omits
  the Labor-Day-weekend kickoffs (8 of 99 on the 2026-08-18 slate).
- `ProvenanceStrip` remains a true claim on every published game.
- 27 v3 week-1 rows (2021–2024) with `as_of` after kickoff remain inside
  23-reval metrics. Successor: `W9-L-residual-week1-straddle-metrics`.
  Do not reopen the revalidation.
- Live 2024 week 5 (one-shot `initialize_season` to that Tuesday) is not
  bit-identical to the yearly walk-forward (`initialize_season` at week 1
  then `update_after_games`). Amendment 1 withdrew expect-0.0.
- 2025 is not a fitted-prior year. Missing-season priors inject `{}` so
  `run_filter` season-regresses at the 2025 boundary instead of
  cold-starting every team at 0.
