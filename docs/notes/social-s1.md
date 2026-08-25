# S1 — Persist bet candidates to local social sidecar

**Date:** 2026-08-23  
**Status:** Complete  
**Authority:** `docs/social/TASKS-social.md` S1; scope amendment (site/R2 stay
forecasts-only; social layer is private, local-only).

---

## Built

| Module | Role |
|--------|------|
| `src/ncaa_quant/social/__init__.py` | Package exports |
| `src/ncaa_quant/social/candidates.py` | `CandidateRecord`, orientation, `export_social_candidates` |
| `src/ncaa_quant/config.py` | `SocialConfig` on `AppConfig` (default **off**) |
| `src/ncaa_quant/pipelines/predict.py` | Sidecar export after `apply_bet_filters` |
| `src/ncaa_quant/pipelines/notifications.py` | `AlertKind.SOCIAL_EXPORT_FAILURE` |
| `tests/unit/test_social_candidates.py` | Acceptance coverage |
| `docs/notes/social-s1.md` | This note |

### Sidecar path

```
{social.output_dir}/{season}/w{week}/{refresh_kind}/candidates.json
```

Default root: `data/social`. Enable with `NCAA_QUANT_SOCIAL__ENABLED=true`.

### Payload shape

```json
{
  "season": 2024,
  "week": 5,
  "refresh_kind": "tuesday_primary",
  "published_at": "...",
  "fixture": true,
  "accepted": [ /* CandidateRecord */ ],
  "rejected": [ /* CandidateRecord + reasons: FilterReason values */ ]
}
```

`CandidateRecord` fields: `game_id`, `market`, `side_team`, `market_line`,
`model_line`, `edge`, `expected_value`, `american_odds`, `p_win`,
`stake_fraction` (from `recommended_stake()`), `model_market_residual_points`.

### Orientation

`side_team` / `market_line` / `model_line` are derived at export time from
CFBD-home-anchored inputs (`market_line_home`, `model_line_home`, `bet_on`),
matching `filter_home_side_spreads` (not Odds listing home). Away bets negate
both lines. Totals use `Over` / `Under` with the shared number.

### Failure semantics

Mirrors `webapp_export`: try/except around the social path; on failure notify
`SOCIAL_EXPORT_FAILURE` and set `result["social_export"] = {"ok": False, ...}`
without failing `predict_publish`.

---

## Wiring notes / ambiguities

1. **`BetCandidate` is thinner than `CandidateRecord`.** Filter output alone
   lacks orientation + odds + `p_win`. `records_from_filter_result` requires a
   `details` map keyed by `"{game_id}:{market}"` whenever the filter returns
   any candidates. The default `build_candidates` still returns `[]`, so a
   normal publish with `social.enabled=true` writes an empty sidecar. A future
   candidate builder (post-S1) must supply those details; S1 only persists.

2. **`AlertKind.SOCIAL_EXPORT_FAILURE`** was added in `notifications.py` (not
   on the sanctioned-edits sketch) so the notify path can mirror webapp export
   without overloading `WEBAPP_EXPORT_FAILURE`.

3. **`fixture` / `published_at`** are stamped on every `execute_predict_publish`
   result. `run_fixture_week_publish` sets `fixture=True` and the fixture-week
   clock so the sidecar inherits `"fixture": true`. These keys are not read
   into R2 artifacts by `export_publish_artifacts` (fixture on webapp artifacts
   remains a separate export parameter).

4. **No `configs/social.yaml` yet** — defaults live on `SocialConfig`. S3 is
   the task that writes calibrated thresholds back to YAML.

5. **R2 boundary:** `accepted` / `rejected` / `candidates` remain on the
   ODDS denylist; webapp export only reads prediction rows / stale / as_of.
   Social files never enter the push allowlist.

---

## Acceptance

| Criterion | Result |
|-----------|--------|
| Sidecar on fixture-week publish; `fixture: true` | Covered |
| `social.enabled=False` → no file | Covered |
| Export failure does not fail publish | Covered |
| No bet fields in R2-bound artifacts | Covered (denylist + explicit assert) |
| Rejected `reasons` are `FilterReason` values | Covered |
| `make test` green | **962 passed**, 1 deselected (2026-08-23) |

---

## Follow-ups (not S1)

- Real `build_candidates` must supply `social_candidate_details` (or equivalent)
  so non-empty filter output can be persisted.
- Committing `docs/social/*` (playbook / task brief) will raise the
  betting-language ratchet pin — those docs intentionally use card vocabulary;
  bump `BASELINE_*` in `scripts/check_betting_language.py` in the same commit.
