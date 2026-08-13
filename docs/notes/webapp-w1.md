# W1 — Artifact export, grade export, and R2 push seam

**Date:** 2026-08-13  
**Status:** Complete  
**Authority:** `docs/webapp/DESIGN.md` §1–§3; `docs/webapp/TASKS.md` W1

---

## Built

### Modules (sanctioned scope only)

| Module | Role |
|--------|------|
| `src/ncaa_quant/webapp/export.py` | Artifact builders, tier/hysteresis, fixture generator |
| `src/ncaa_quant/webapp/grade.py` | `results_<season>.json` grade export + lockbox guard |
| `src/ncaa_quant/webapp/push.py` | S3-compatible R2 upload with meta-last ordering |
| `src/ncaa_quant/webapp/schemas/*.schema.json` | JSON Schema contract files (§1) |
| `src/ncaa_quant/pipelines/predict.py` | Final export+push step behind `webapp.export_enabled` (default **OFF**) |
| `src/ncaa_quant/config.py` | `WebappConfig` + R2 secrets via env |
| `tests/unit/test_webapp_w1.py` | Acceptance tests |
| `webapp/fixtures/` | Labeled 2024 week-5 fixture artifacts for W2–W6 |

### Wiring

- `execute_predict_publish` now passes `prediction_rows` (pre-stamp production dicts) to export.
- Export failure raises `AlertKind.WEBAPP_EXPORT_FAILURE`; core prediction result is preserved.
- R2 credentials: `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` (`.env.example` documented).
- Tier hysteresis state: `data/webapp/tier_state.json` (workstation file, not in artifacts).

---

## Mapping table — stub vs production shapes

| Artifact field | Task-24 stub (`StampedPrediction`) | Production walkforward |
|----------------|-----------------------------------|----------------------|
| `game_id` | `game_id` | `game_id` |
| `mu_margin` | `mu_margin` | `pred_margin` |
| `sigma_margin` | `sigma_margin` | `sigma_m` |
| `sigma_margin_credible` | derived | `!sigma_m_is_missing && null_reason == null` |
| `margin_interval_*` | null | `cqr_lo/hi/nominal` or `pred_margin_q05/q95` |
| `mu_total` | null | `pred_total` |
| `sigma_total` | null | `sigma_t` |
| `p_win_home` | null | `p_ml_home` |
| `p_cover_home` | null | `p_ats_home` |
| `p_over` | null | `p_ou_over` |
| `*_credible` | derived from `*_is_missing` flags | same |
| `null_reason` | null | `null_reason` (ADR 0014) |
| `is_stale` / `stale_stamp` | stamped overlay | stale overlay wins over production row |
| `stale_sources` | from `StaleContext.sources` | same |

Merge rule: `merge_prediction_rows(stamped, production)` — stamp fields overlay production row.

---

## Schema validation (committed JSON Schemas)

```
VALID week_predictions.json
VALID meta.json
VALID track_record.json
VALID results_2024.json
VALID team_ratings_2024.json
```

### Sample records (fixture week 2024 w5)

**`week_predictions.json` — one game:**

```json
{
  "game_id": "401628373",
  "home_team": "Texas A&M",
  "away_team": "Arkansas",
  "mu_margin": 4.1460979146876005,
  "sigma_margin": 16.732584934396137,
  "sigma_margin_credible": true,
  "p_win_home": 0.6758793711506627,
  "conviction_tier": "strong_lean",
  "conviction_label": "Strong lean Texas A&M",
  "fixture": true
}
```

**`meta.json`:** `schema_version` 1.0.0, `fixture: true`, pointers to `latest/*`.

**`track_record.json` — one metric (verbatim 23-readout):**

```json
{
  "id": "fund_ats_snapshots",
  "label": "Fundamental ATS snapshots 2021–24",
  "value": 50.7,
  "ci_lower": 48.7,
  "ci_upper": 52.7,
  "n": 3496,
  "vintage": "REGRADED_V2"
}
```

**`results_2024.json` — one graded row:**

```json
{
  "game_id": "401629032",
  "grade_status": "graded",
  "graded_from": {
    "refresh_kind": "tuesday_primary",
    "published_at": "2024-09-24T06:00:00Z"
  },
  "actual_margin": 44,
  "margin_interval_hit": false
}
```

**`team_ratings_2024.json` — one team week point:**

```json
{
  "school": "Boston College",
  "weeks": [{"week": 1, "off_epa": 0.081, "def_epa": 0.058, "pace": -0.053, "off_sd": 0.122, "def_sd": 0.126}]
}
```

---

## ODDS / bet-candidate denylist

Explicit denylist in `ODDS_FIELD_DENYLIST` (`export.py`):

```
spread_asof, total_asof, line_source_asof, n_books_asof,
spread_close, total_close, line_source_close, n_books_close,
p_mkt_ats_home, p_mkt_ou_over, p_mkt_ml_home,
spread, total_line, total, price, book, bookmaker,
implied_prob, implied_probability, edge, ev, expected_value, clv, kelly, stake,
n_candidates, n_accepted, n_rejected, stale_rejections,
accepted, rejected, bet_candidate, bet_candidates, candidates, market, min_edge
```

**Grep evidence:** `test_odds_denylist_on_fixture_artifacts` walks all fixture artifact trees — **0 hits**.

Walkforward source columns (`spread_asof`, `spread_close`, etc.) are **never copied** into export; only mapped public fields pass through.

---

## Null-preservation (ADR 0014)

`test_null_sigma_preserves_nulls_and_suppresses_tier`:

- Input: `sigma_m=null`, `sigma_m_is_missing=true`, `null_reason=cold_start_insufficient`
- Output: `sigma_margin=null`, `p_win_home=null`, `conviction_tier=null`, `sigma_margin_credible=false`
- No zero-fill or defaulting.

---

## Hysteresis demonstration

`test_week_predictions_hysteresis_end_to_end`:

1. Tuesday primary: `p_favored=0.58` → **lean**
2. Daily refresh: `p_favored=0.53` (raw toss-up) → **holds lean** (`hysteresis_applied=true`, exit band 0.52)
3. Daily refresh: `p_favored=0.48` → **toss_up** (below 0.52 exit)

Prior tier loaded from `TierStateStore` keyed by `2024:{game_id}`.

---

## Lockbox refusal

`test_lockbox_refuses_season_2025`:

```
GradeExportError: grade export refused for season 2025: live publish begins 2026+ (lockbox guard)
```

`track_record.json` is frozen 23-readout only — no aggregate accuracy computed from other seasons.

Fixture `results_2024.json` uses `allow_historical_fixture=True` for W5 dev only.

---

## R2 push ordering + idempotency

`test_push_meta_uploads_last`: upload order ends with `meta.json`; all data keys uploaded before any `latest/meta.json` key.

`test_idempotent_repush_same_content`: identical SHA-256 on re-push; object bytes unchanged.

---

## No credentials in repo

`test_no_credentials_in_repo` scans `src/`, `tests/`, `webapp/`, `configs/` for AWS/R2 credential patterns — **pass**.

---

## Tier distribution — fixture week 2024 w5 (56 games)

| Tier | Count | % |
|------|------:|--:|
| Strong lean | 38 | 67.9% |
| Lean | 13 | 23.2% |
| Toss-up | 5 | 8.9% |
| Suppressed | 0 | 0.0% |

**Degeneracy verdict:** **FLAG** — Strong lean exceeds 50% of slate (67.9%). Per §2 spec, this is a boundary-degeneracy finding for a spec amendment; boundaries were **not** retuned in W1.

Source: real `task23_fundamental_reduced_v2` walkforward parquet for `season=2024_week=5`.

---

## Export failure isolation

`test_export_failure_does_not_fail_predict_flow`:

- Simulated export exception → `webapp_export.ok=false`, `AlertKind.WEBAPP_EXPORT_FAILURE` sent
- `predictions` payload still returned intact

---

## Fixture artifacts path

```
webapp/fixtures/
  week_predictions.json   (fixture: true)
  meta.json
  track_record.json
  results_2024.json
  team_ratings_2024.json
```

Generated via `generate_fixture_week_artifacts()` from:

- Walkforward: `data/backtests/task23_fundamental_reduced_v2/full/weeks/season=2024_week=5.parquet`
- Schedule: `data/staged/games/season=2024/week=5/part.parquet`
- Teams: `data/staged/teams/season=2024/part.parquet`
- Ratings: `data/artifacts/state_space/filter_history.parquet`

---

## Decisions / ambiguities

1. **`webapp.export_enabled` default OFF** until W7 deploy flips it in config/env.
2. **`prediction_rows` in publish result** — minimal plumbing so export receives production columns without modifying `stamp_predictions`.
3. **Fixture `published_at`** set to `2024-09-24T06:00:00Z` (Tuesday before week-5 kickoffs) so grading rule can select pre-kickoff snapshots.
4. **Total intervals** null in v1 export when walkforward does not emit total conformal bands.
5. **boto3** added to main dependencies for S3-compatible R2 push.
6. **Degeneracy** on fixture week flagged; no boundary retune (per task).

---

## Acceptance — `make lint typecheck test`

```
ruff check/format: pass
mypy: Success (120 source files)
pytest -m "not live": 825 passed
coverage: 80.49%
```

---

*End of W1 task notes.*
