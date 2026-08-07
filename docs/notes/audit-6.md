# AUDIT-6 — Engineering spec closure

**Date:** 2026-08-06
**Scope:** Documentation only (no code changes). Touched: `docs/DESIGN.md`,
`docs/TASKS.md`.

## Changes summary

| Item | Where | Action |
|---|---|---|
| Dependencies | Task 1 | Add dvc/scipy/shap/jinja2/plotly; `research` extra; list = approval |
| Reproducibility | §1.4, §6, §14, Tasks 16/18 | Artifact-anchored bit-for-bit; async TPE not run-order deterministic |
| Game key | Tasks 4/5 | CFBD stable id + Odds crosswalk; postpone fixture |
| Event-time | Task 5 item 4 | Completion ts or kickoff+5h (OT+); `event_time_estimated` |
| Decision points | Tasks 2/5B | America/New_York → UTC via zoneinfo; DST fixtures |
| Ops | §10, Tasks 1/7/24; §9.5 | File-store MLflow; live mark; cadence; archive/UI/scrub; CUSUM |

---

## 1. Amended Task 1 dependency block

```
2. pyproject.toml using uv, Python 3.11. Deps (pin major versions only): pandas,
   polars, duckdb, pyarrow, pandera, pydantic, pydantic-settings, omegaconf,
   structlog, typer, httpx, tenacity, scikit-learn, lightgbm, xgboost, catboost,
   ngboost, optuna, mlflow, prefect, great-expectations, matplotlib, dvc, scipy,
   shap, jinja2, plotly. Optional extra `research`: numpyro, jax, pymc-bart
   (install via `uv sync --extra research`). This Task 1 dependency list is the
   approved set under `.cursorrules` — do not add further deps without an explicit
   amendment. Dev group: pytest, pytest-cov, hypothesis, ruff, mypy, pre-commit.
   Generate uv.lock.
```

Also: `pytest.mark.live` registration + CI exclude; docker-compose MLflow uses
local **file-store** (not SQLite under multi-writer).

---

## 2. New §1.4 reproducibility text

```
- **Reproducibility:** bit-for-bit reproducibility applies to **inference and
  walk-forward replay given fixed model artifacts** (pinned by content hash),
  together with (git SHA, DVC data hash, config hash, seed). Training and HPO
  are **reproducible-to-artifacts**: every trial's params and seed are logged;
  final champion refits run with deterministic settings (CPU or framework
  deterministic mode); the artifact hash is the reproducibility anchor.
  Asynchronous parallel TPE is not run-order deterministic — the search itself
  is not the unit of bit-for-bit replay (see §6). All randomness is seeded and
  logged.
```

§6 Budget & parallelism and Task 18 note the same async-TPE limitation; Task 16
determinism is scoped to pinned-artifact replay.

---

## 3. Game-key paragraph (Task 4 item 3)

```
**Canonical game key:** CFBD's stable numeric game id. Odds API events are
matched to it via normalized team pair + kickoff within ±36h, persisted in a
crosswalk table; ambiguous matches are quarantined, never guessed. The derived
(season, home_team, away_team, kickoff_date) key is retained only as a matcher
input — put the team-name normalization map in configs/ and make it testable
(team naming mismatches across sources are the #1 integration bug). Required
fixture: a game postponed by one day retains a single canonical key and
continuous snapshot history across the postpone.
```

Task 5 item 5 mirrors the crosswalk / postpone fixture.

---

## 4. Other amendments (brief)

- **Task 5 item 4:** completion timestamp when available; else kickoff + 5h
  (more if OT flagged) with `event_time_estimated=True`; conservative-latest
  rule applies to results.
- **Task 5B item 1 / Task 2:** decision points in America/New_York, resolved via
  `zoneinfo` to UTC; both name and resolved UTC on every snapshot row; early-
  November DST fixtures in both tasks.
- **§9.5:** primary alert = per-team CUSUM on standardized innovations
  (~1–2 flags/week league-wide); 3×2σ consecutive rule kept as loud tier.
- **§10 / Tasks 1, 7, 24:** file-store MLflow; `@pytest.mark.live` out of CI;
  snapshot cadence expectation + 24h alert; off-machine raw-odds replicate
  within 24h + quarterly restore drill; no off-host MLflow/Prefect without
  auth; raw-archive API-key scrub with test; Task 24 runbooks cover these.

---

## Verification

- Amended Task 1 dependency block: see §1 above / `docs/TASKS.md` Task 1 item 2.
- New §1.4 text: see §2 above / `docs/DESIGN.md` §1.4.
- Game-key paragraph: see §3 above / `docs/TASKS.md` Task 4 item 3.
- `zoneinfo` in Task 5B: present (decision-point America/New_York → UTC via
  zoneinfo; DST fixtures in Tests block).
