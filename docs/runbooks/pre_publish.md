# Pre-publish (live R2)

Required human checks **before** a live `push_artifacts_to_r2` / `export_enabled` write. GitHub pytest does **not** run `@pytest.mark.workstation` tests (no lake on the runner). Those four tests are this step.

Do not substitute `pytest -m "not live and not workstation"` — that is the CI job.

## Required: `make test` including workstation tests

On the workstation, with gitignored `data/` present (champion week parquet under `data/backtests/task23_fundamental_reduced_v2/full/weeks/`):

```
make test
```

That is `uv run pytest -m "not live"`, which **includes** `@pytest.mark.workstation`. Paste the pytest session summary into the publish notes.

The four lake tests that must not skip:

- `tests/unit/test_webapp_w9p.py::test_wired_rows_have_production_and_stamp_aliases`
- `tests/unit/test_webapp_w9p.py::test_allowlist_bite_on_wired_game`
- `tests/unit/test_webapp_w9p.py::test_isolated_2024w5_oracle_against_fixture`
- `tests/unit/test_webapp_w9p.py::test_execute_predict_publish_uses_wired_default`

To see only those four:

```
uv run pytest -m workstation -o addopts= --tb=short
```

Expect 4 passed. If they skip or fail, **do not publish**.

## Also before publish

1. `uv run python scripts/check_betting_language.py published` — exit 0.
2. `uv run python scripts/check_betting_language.py ratchet` — counts equal the pin in that script.
3. Confirm `webapp.export_enabled` and R2 credentials are intended for this write.
4. Confirm the slate is 2026+ live export, not `run_fixture_week_publish` / sandbox.

## W9-D rehearsal findings (2026-08-18)

These are operator facts from the sandbox dress rehearsal. They do not change the gate above.

**Kalman wall-clock.** `initialize_season` / `run_filter` for 2026 week 1 was **235 s** on this workstation (5997 observations, 2019–2025). Features + ensemble after that were 8 s; export 0.03 s; R2 push 2.5 s. Start the attended Tuesday run by **~05:50 ET / 09:50 UTC** if `published_at` should land near 06:00 ET. That duration is expected; it is not a hang.

**Calendar vs cron.** Decision `as_of` is Tuesday 06:00 America/New_York from the staged-games calendar (`2026-09-01T10:00:00Z` for week 1). Config cron `0 6 * * 2` is Tuesday 06:00 **UTC** (four hours earlier). Follow the calendar. Export stamps `published_at = datetime.now()`; a Tuesday morning run is near `as_of`. `next_expected_publish_utc` is +2 days for `tuesday_primary`.

**Which function.** Use `execute_predict_publish`, not `run_predict_publish`. The idempotent wrapper keys `predict_publish/2026-w1-tuesday_primary` and would make a second call a no-op. W9-D rehearsal used `execute` and left the live ledger hash unchanged.

**Hysteresis.** A real publish writes `tier_state.json` / `tier_changes.jsonl`. A rehearsal that uses the production paths contaminates week-1 hysteresis. Redirect those paths (and the idempotency directory) for any non-live run. W9-D redirected; production hashes were unchanged.

**Destination.** `execute_predict_publish` with `webapp.export_enabled=true` calls `push_artifacts_to_r2` with default `publish_scope="live"` (`latest/` + `v1/...`). There is no env flag for sandbox on that path. Rehearsal: inner export off, then an explicit `publish_scope="sandbox"` push. Live week-1: export on, live scope, skip_revalidation false if the preview should refresh.

**Site prefix.** The Next loader always GETs `latest/...`. Pointing a Vercel preview at `sandbox/` needs a code change or a production env change. Do not change production env for a rehearsal. W9-D rendered locally from GET'd `sandbox/latest` objects.

**Odds cadence.** With no snapshots in 24h the publish path emits `cadence_shortfall` (`snapshots_24h=0 expected_min=5`). Null notifier suppresses. Expected until Odds ingest is running; not a reason to abort a forecast-only publish.

**Week-1 slate.** 99 CFBD FBS week-1 games staged; 8 kick off before `as_of` and are omitted; **91 published**. Regular week 1 is in line with 2024/2025 regular week 1 (the 146/142 figures include postseason rows CFBD labeled week=1). Re-ingest only if a later CFBD fetch returns more than 99 FBS week-1 rows.

**What is live until Tuesday.** Production `latest/` remains the 2024 fixture week-5 set (`fixture: true`) until the attended live push. The public site therefore shows 2024 fixture data through opening weekend (first 2026 kickoffs 2026-08-29). Operator decision, not a defect.

**Quantile crossing.** The batch warning is not “every row.” On this rehearsal: 4/91 (4.4%) 2026 week-1 rows unordered before sort; published 80% CQR bounds moved on at most 3 rows (max |Δ| 2.94 points). No model change in this runbook.
