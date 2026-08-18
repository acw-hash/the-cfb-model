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
