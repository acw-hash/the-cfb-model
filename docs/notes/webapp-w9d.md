# W9-D — dress rehearsal for the week-1 publish

**Date:** 2026-08-18  
**Status:** Complete, plus Amendments 1–2. Vintage derives from the producing
run. Margin intervals publish only where pre-CQR `q10 < μ < q90`. Asymmetry
is not a suppress. CQR-on-real-heads is W9-CQR (post-launch).  
**Authority:** `docs/notes/webapp-w9l.md` (`6a7e66a`); `docs/notes/webapp-w9i.md`;
`docs/notes/webapp-w9push.md` (`7d7fea5`); `docs/notes/webapp-w9-1.md`;
`docs/runbooks/pre_publish.md`.

Artifacts: `docs/notes/_artifacts/webapp-w9d/`.

Nothing in this task wrote `latest/`, `v1/*`, or `v2/*`. No fit, retrain,
promotion, registry change, Prefect deployment, or production Vercel change.

---

## Headline

The full predict → export → push path ran for **2026 week 1** onto
`sandbox/` and the site rendered it from those objects. **91 games**,
`schema_version` 1.2.0, `fixture` absent (banner cleared), 8 kickoffs
before `as_of` omitted, allowlist refused a poisoned key with zero
`put_object`. Production `latest/` is still the 2024 fixture set.

**Ready for an attended week-1 publish to `latest/` on Tuesday
2026-09-01T10:00:00Z, with the operational constraints in the last
section and Amendment 2's per-row interval gate.** Not ready as a
Vercel-preview-of-sandbox check (site loader hard-codes `latest/`; CLI
logged out; production env was not changed).

---

## PHASE 0 — three diagnostics (reported before Phase 1)

### 0.1 — Quantile crossing (report only; no fix)

The batch warning is **not** “every row.” `enforce_quantile_order` logs
once per batch when *any* row is unordered, with `n_rows` = batch size.
W9-I’s 99/99 and W9-L’s “all rows warned” were that batch flag.

Intercept of the raw (pre-sort) margin quantile matrix on the live
champion path:

| slate | n | unordered | fraction | q10 changed | q90 changed | raw q10>q90 | universal |
|-------|--:|----------:|---------:|------------:|------------:|------------:|:---------:|
| 2026 week 1 | 91 | 4 | **4.4%** | 3 | 2 | 0 | no |
| 2024 week 5 | 56 | 4 | **7.1%** | 1 | 3 | 0 | no |

Published `margin_interval_*` are CQR 80% on **q10/q90** (nominal 0.8),
threshold 6.837. Sort vs unsorted published bounds:

**2026 week 1**

- lo changed on 3/91 rows; hi on 2/91
- |Δ lo| median 0, p90 0, max **1.55** points
- |Δ hi| median 0, p90 0, max **2.94** points
- |Δ width| median 0, max 4.48 points

**2024 week 5**

- lo changed on 1/56; hi on 3/56
- |Δ lo| max **0.34** points; |Δ hi| max **0.21** points

Crossing is **rare**, not universal. Most published intervals are
identical to the unsorted q10/q90 ± threshold. The 80% nominal-coverage
label is the CQR construction after the sort; it is not a per-row
coverage claim that the raw LightGBM quantiles already respected.

`total_interval_lo/hi/nominal` are **null** on every GamePrediction.
There is no total quantile head. Sort does not change totals.

JSON: `_artifacts/webapp-w9d/phase0_01_quantile.json`.

STOP #3 (universal crossing) **did not trip.**

### 0.2 — Slate completeness

Live CFBD 2026-08-18T21:40:56Z:

| query | n |
|-------|--:|
| `/games?year=2026&week=1&seasonType=regular&classification=fbs` | **99** |
| same without `classification` | 389 |
| of those 389, home classification FBS | **99** |
| `/games?year=2026&seasonType=regular&classification=fbs` | 888 |

Without `classification`, the extra 290 week-1 rows are FCS / II / III,
not missing FBS. `classification=fbs` week 1 is 51 FBS-vs-FBS + 48
FBS-vs-FCS. Staged week 1 = 99, matching the API. Re-ingest not
indicated.

The 99 vs 146 (2024) / 142 (2025) comparison mixed **regular week 1 with
postseason games CFBD labeled week=1** (46 bowls each year, December–
January). Regular-only week 1:

| season | regular week 1 | kickoff span |
|--------|---------------:|--------------|
| 2024 | 100 | 2024-08-24 → 2024-09-02 |
| 2025 | 96 | 2025-08-23 → 2025-09-02 |
| 2026 | **99** | 2026-08-29 → 2026-09-07 |

2026 week 1 is in line with prior regular week 1. Date span is ~9 days
in all three years. 2026 starts later (Labor Day week). **Zero** FBS
games in the week-1 date span carry another CFBD week. Staged week 2
starts 2026-09-11. Not a query artifact and not a partition-boundary
bug.

Labor-Day `week_of` would call 98/99 of these week 0; ingest uses CFBD
week labels, as specified.

The count can still grow if CFBD adds remaining FBS openers before
kickoff. The ~47-game “shortfall” vs 146/142 is bowls not yet posted,
not missing week-1 regular games. Full-season 888 vs 920/934 will grow
as later weeks and bowls land.

JSON: `_artifacts/webapp-w9d/phase0_02_slate.json`.

STOP #4 (query artifact) **did not trip.** No re-ingest before Phase 1.

### 0.3 — First-publish date

Production calendar, no override (`WeekDecisionCalendar.from_games` +
champion `as_of` clock Tuesday 06:00 America/New_York):

```
as_of = intended publish = 2026-09-01T10:00:00+00:00
                         = 2026-09-01T06:00:00-04:00
saturday refresh         = 2026-09-05T10:00:00+00:00
```

Config cron `predict_publish_cron_tuesday = "0 6 * * 2"` is Tuesday
**06:00 UTC**, four hours earlier than the calendar Tuesday. The
attended run should follow the calendar (`as_of`), not the cron string.
`published_at` on a live export is `datetime.now()`; a Tuesday morning
run stamps near `as_of`. `next_expected_publish_utc` is +2 days for
`tuesday_primary`.

**8 of 99 week-1 games kick off before `as_of`** (all 2026-08-29 /
08-30). They are omitted from the published slate (W9-L). 91 remain;
earliest published kickoff 2026-09-03T22:00Z.

**What the site shows between now and then:** production `latest/` is
still schema 1.2.0, `fixture: true`, season 2024 week 5, 56 games,
`published_at=2024-09-24T10:00:00Z`. No 2026 artifact is on `latest/`.
The public site therefore serves **2024 fixture data through the
opening weekend** (first kickoffs 2026-08-29, before Tuesday publish).
That is an operator decision, not a defect.

JSON: `_artifacts/webapp-w9d/phase0_03_calendar.json`.

---

## PHASE 1

### 1.1 Runbook `make test` gate

Four workstation tests:

```
uv run pytest -m workstation -o addopts= --tb=short
===================== 4 passed, 929 deselected in 14.87s ======================
```

Full `make test` on this tree, before pin repair:

```
uv run pytest -m "not live"
FAILED tests/unit/test_betting_language_guard.py::test_ratchet_matches_exact_pin
==== 1 failed, 931 passed, 1 deselected, 32 warnings in 298.71s (0:04:58) =====
Required test coverage of 80% reached. Total coverage: 80.50%
```

Ratchet was 348/237/74 vs pin 323/229/72 left by `c58d6b7` (W9-1 bite
artifacts committed without re-pinning). Published-copy runner stayed
clean (`matches=0`). Pin re-measured at the end of this task to
**348/237/74** (Amendment 2: exact, not a padded ceiling). These notes
and the runbook added no union hits. Four lake tests did not skip.

After the pin repair:

```
========= 932 passed, 1 deselected, 32 warnings in 315.10s (0:05:15) ==========
Required test coverage of 80% reached. Total coverage: 80.50%
```


### 1.2 Production path, sandbox destination

`execute_predict_publish` (not the idempotent wrapper, not
`run_isolated_week_export`) for 2026 week 1. Inner `export_enabled=false`
so the default live push cannot fire. Artifacts built, then
`push_artifacts_to_r2(..., publish_scope="sandbox", skip_revalidation=True)`.

Hysteresis and the idempotency directory were redirected under
`_artifacts/webapp-w9d/rehearsal_state/` so this rehearsal cannot
contaminate the real week-1 publish.

Log:

```
W9-L slate n_week=99 n_excluded_kickoff_before_as_of=8 n_publish=91
as_of=2026-09-01T10:00:00+00:00
W9-L Kalman start n_obs=5997 ...
run_filter_done elapsed_sec=234.7 n_obs=5997
rating_digest=96e8030a9c413a14b84175a4690921f7dce9de5b300234f7e7c110f7eb35e859
live_predict_done n=91 champion_version=2 model_version=production-v0_reduced_v3
run_id=task23_fundamental_reduced_v3
```

Rating digest matches W9-L. Cadence shortfall notified and suppressed
(`snapshots_24h=0 expected_min=5`) — no Odds snapshots in 24h; expected
on this workstation.

Wall-clock:

| stage | seconds |
|-------|--------:|
| rating filter (`initialize_season` / `run_filter`) | **235.2** |
| predict after ratings (features + ensemble + stamp) | 8.0 |
| export | 0.03 |
| push (8 objects, sandbox only) | 2.5 |
| `execute_predict_publish` total | 243.3 |

**STOP #8:** 235 s of Kalman is long enough to matter on Tuesday
morning. Start the attended run by ~05:50 ET / 09:50 UTC if the stamp
should land near 06:00 ET. Export and push are not the cost.

Keys written (all `sandbox/`):

```
sandbox/v1/2026/w1/tuesday_primary/{team_ratings_2026,track_record,week_predictions,meta}.json
sandbox/latest/{same}
meta last; revalidation null
```

No `latest/` or `v1/` or `v2/` without the sandbox prefix.

### 1.3 Artifact verification, from R2

GET `sandbox/latest/*` after the write:

| check | result |
|-------|--------|
| schema_version | **1.2.0** on week, track, meta, ratings |
| fixture | key **absent** (treated false) on all four |
| n games | **91** |
| excluded | 8; none of those ids in the artifact; kickoff min **2026-09-03T22:00Z** |
| game_id `^[0-9]{6,12}$` | 91/91 |
| W8-C withdrawn keys | all counts 0 |
| allowlist | ran (push succeeded); drill in §1.5 |
| as_of vs kickoff | 2026-09-01T10:00Z < every published kickoff |
| published_at | 2026-08-18T22:01:05Z (rehearsal clock = `now()`) |
| next_expected_publish_utc | 2026-08-20T22:01:05Z (+2 days, tuesday_primary) |

`results_2026.json` is **absent** (`NoSuchKey`). Production `latest/`
listing unchanged: meta, results_2024, team_ratings_2024, track_record,
week_predictions. Read-back of production meta: still `fixture: true`,
season 2024 week 5.

JSON: `_artifacts/webapp-w9d/r2_verify.json`.

### 1.4 Render

A Vercel **preview** pointed at `sandbox/` would require either a
production env change (forbidden) or a site code change: `loader.ts`
always fetches `latest/${fileName}`. Vercel CLI is logged out. STOP #5
reported; production config was not changed.

Render used a local Next dev server (`ARTIFACT_SOURCE=local`,
`ARTIFACT_BASE_PATH` = the R2-GET round-trip directory). That is the
same JSON the bucket holds under `sandbox/latest/`. Not reachable by a
reader.

All four routes HTTP 200. **FIXTURE banner absent** on every page
(`fixture` key absent). 91 matchups on This Week (` @ ` count 91).
Tiers 70 strong / 13 clear / 5 lean / 3 toss-up, matching W9-L.

**This Week header and three rows** (visitor-local kickoff):

```
2026  Week 1
Updated 2m ago
Aug 18, 2026, 10:01 PM UTC
Tuesday primary

Thursday, September 3
Thu, Sep 3, 6:00 PM   Massachusetts @ Rutgers          +41.5  [ −2.1 , +70.0 ]  Strong lean Rutgers
Thu, Sep 3, 7:00 PM   Bethune-Cookman @ UCF            +43.4  [ −19.1 , +48.5 ]  Strong lean UCF
Thu, Sep 3, 7:00 PM   Akron @ Wake Forest              +28.0  [ −0.2 , +55.8 ]  Strong lean Wake Forest
```

**Game Detail** `/game/401858424` including provenance:

```
UAB @ Illinois
Thu, Sep 3, 9:00 PM
Margin +20.4 [ −7.0 , +48.3 ]  σ 20.1  80% nominal coverage
Total 53.9  σ 16.4
Interval not computed — Conformal/quantile bounds were not emitted for totals in this export.
Probabilities  Home win 89%
Conviction  Strong lean Illinois
Provenance
  Vintage       REGRADED_V2
                Which graded training run produced these numbers.
  Ensemble      REDUCED_PER_ADR_0013
                Which models were combined. Reduced means a smaller set than the full experimental ensemble.
  Feature time  FEATURE_TIME=TUESDAY_DECISION
                When inputs were frozen. Tuesday decision means later information is not in this forecast.
Updated 2m ago  Aug 18, 2026, 10:01 PM UTC  Tuesday primary
```

Ratings chart: empty honest absence (`team_ratings_2026` has `"teams": {}`;
the route still looks up `team_ratings_2024`, which is not in this
prefix). Forecasts render.

**/results:**

```
Results
Track record
Finding  NOT CURRENTLY FIT TO BET
…recorded 23-reval metrics table unchanged (ATS 48.9% [47.5%, 50.5%], …)…
Graded games
Results available after Week 1 completes. Live grading for 2026 has not started yet — this is the empty launch state, not a missing file.
```

Empty 2026 season is honest absence, not an error. Track record section
unaffected.

**/about:** identity paragraph, How the forecast is built, What the
numbers mean, Data sources and publish schedule, Honesty commitments
(including NOT CURRENTLY FIT TO BET), Disclaimer © 2026, Responsible
gambling, Attribution. Full text in
`_artifacts/webapp-w9d/render_about.txt`.

STOP #6 **did not trip.**

### 1.5 Deliberate allowlist refusal

Same artifacts, `games[0].unsanctioned_edge = 0.42`, fake S3:

```
PublishedKeyAllowlistError: unpublished keys in week_predictions.json.games[0]: ['unsanctioned_edge']
put_calls: 0
```

Reverted (in-memory only; bucket objects unchanged). Guard runs before
any `put_object`.

### 1.6 Isolation

Production paths, SHA-256 before = after:

```
tier_state.json      66596cbc1a974c12c77820d60e819c6844f0624a360b649262576df0c832ba0d
tier_changes.jsonl   341c070dd32fd303c7e36c574a05e9addd4fd6602960d16c1b1919cbf6eb120d
idempotency.json     ee51e24e2d4357ef419f5bd1258f6bdaa01920962ce0fa969b1011d804f59bfa
possessions live.json e1101588c1bdb77b38a63a635802467793d2cf341537fe8311e2e2a312676df1
isolation_changed=[]
```

Possessions hash matches W9-L / W9-I.

A real publish **is** expected to write hysteresis. This rehearsal
**would have contaminated** week-1 hysteresis (91 NEW_BET-free tier
entries keyed `2026:<game_id>`). Writes were redirected:

```
rehearsal_state/tier_state.json      D38863F79B506D3D5A04497019E791C3FF6F7E46A3C62773AD882BA417B3E6AF
rehearsal_state/tier_changes.jsonl   260CC913321462DC9FF50F4FDC024A2327FF8401E76A7BA1BB8F32BAFABD0F73
```

The idempotent wrapper was not used, so the live ledger key
`predict_publish/2026-w1-tuesday_primary` was not recorded. Using
`run_predict_publish` for a rehearsal would make the attended run a
no-op.

STOP #7 **did not trip** (redirected).

### 1.7 Runbook

`docs/runbooks/pre_publish.md` updated with: Kalman wall-clock, calendar
vs cron, `execute_predict_publish` vs idempotent wrapper, hysteresis
redirect for rehearsals, sandbox vs live destination, site loader
always `latest/`, cadence shortfall when Odds snapshots are absent,
`published_at = now()`.

---

## Forbidden actions not taken

- No write to `latest/`, `v1/*`, or `v2/*` (sandbox prefix only).
- No production Vercel configuration or alias change; no revalidation
  POST against production.
- No fit, retrain, promotion, or registry change.
- No quantile, model, calibrator, or threshold change.
- Guards / CI / allowlist not edited to pass a bad artifact. Ratchet
  pin set to the exact post-notes `git ls-files` count (Amendment 2).
- No `noindex` / robots / domain / analytics change.
- No Prefect deployment, schedule, or worker.
- Nothing a reader can reach; local preview only.

---

## Is the system ready for an attended week-1 publish?

**Not as a single yes.** The path still supports an attended Tuesday
2026-09-01 push to production `latest/`, with the operational constraints
in the original close (Kalman start time, 8 omitted kickoffs, 2024 fixture
live until then, `execute_predict_publish` with export on, real hysteresis,
Odds cadence shortfall). **Amendment 2 removes the interval-publish
operator stop:** incoherent pre-CQR heads (`q10 < μ < q90` fails) emit
null and render as absence; coherent-but-skewed rows publish. Vintage on
a v3-champion export is `W9A_REVAL` (Amendment 1). See Amendments 1–2.

---

## Amendment 1 — provenance vintage + interval/point coherence

**Date:** 2026-08-18  
**Status:** Vintage fixed in export. Interval diagnostic is STOP AND
REPORT — no model, calibrator, threshold, or CQR change.  
**Authority:** Amendment 1 to W9-D (pre-publish, both blocking).

Artifacts: `_artifacts/webapp-w9d/amendment1_interval.json` (and the
diagnostic script beside it). Local sandbox JSON vintage restamped;
production `latest/` still untouched; R2 `sandbox/` not re-pushed.

### 1. Provenance vintage

**Where it was set.** `build_game_prediction` copies `vintage_label`
from `build_week_predictions`. The live path is
`export_publish_artifacts`, which did not pass a vintage, so every game
inherited `DEFAULT_VINTAGE_LABEL = "REGRADED_V2"`. That constant is the
v2 walk-forward label. The 2026 rehearsal was
`run_id=task23_fundamental_reduced_v3` /
`model_version=production-v0_reduced_v3` and still rendered
`REGRADED_V2` on Game Detail.

**What it is now.** Vintage (and ensemble / feature-time when a `run_id`
is present) comes from `provenance_for_run(run_id)`:

| producing `run_id` | per-game `vintage_label` |
|---|---|
| `task23_fundamental_reduced_v3` | `W9A_REVAL` |
| `task23_fundamental_reduced_v2` | `REGRADED_V2` |
| any other non-empty `run_id` | raises `UnknownRunProvenanceError` |

W9-G restated ATS rates on the same v3 numbers; it did not produce a new
forecast run. Per-game vintage is therefore `W9A_REVAL`. Track-record ATS
rows stay `W9G_REGRADE`. Stub rows with no `run_id` still fall back to
the historical defaults (finding below).

Live `predict` now stamps `champion_version` and `registered_at` from
`registry.resolve_champion()` onto each row so `model_identity` /
`meta.champion_model` can pass them through. Local 2026 sandbox JSON
(top-level + 91 games + meta) was restamped `W9A_REVAL` to match the
producing run. R2 `sandbox/` was not rewritten.

Test: `tests/unit/test_webapp_w9d.py` — derivation map, live export for
v3 vs v2 vs unknown, 2024 fixture (56 games, `run_id` v3 → `W9A_REVAL`),
2026 sandbox (91 games, same).

#### Audit of other provenance fields

| Field | Derived? | Notes |
|---|---|---|
| Per-game / file `vintage_label` | **Derived** from `run_id` | Required fix. Unknown run refuses export. |
| `ensemble_scope_label` | **Derived** when `run_id` present | Mapped to `REDUCED_PER_ADR_0013` for both known runs. Still the same string as the old default. |
| `feature_time_label` | **Derived** when `run_id` present | Mapped to `FEATURE_TIME=TUESDAY_DECISION` for both known runs. |
| `model_identity.run_id` | **Derived** from producing rows | Pass-through. |
| `model_identity.model_version` | **Derived** from producing rows when present | Fallback hardcode `production-v0_reduced_v1` if missing. |
| `model_identity.champion_version` | **Derived** when the row carries it | Live predict now stamps registry version. Fallback hardcode **3** if missing. Rehearsal sandbox identity still shows 3 because it was exported before the stamp; registry champion is **2**. |
| `model_identity.registry_name` | Hardcoded | `ncaa-quant`. Stable. |
| `meta.champion_model.model_version` | **Derived** when identity has it | Was hardcoded `production-v0_reduced_v1` on every live meta (rehearsal meta still has that; next export will pass v3). |
| `meta.champion_model.champion_version` | **Derived** when identity has it | Fallback 3. |
| `meta.champion_model.registered_at` | **Derived** when identity has it | Fallback `2024-08-01T12:00:00Z`. Fixture path still pins W9-A `2026-08-17T20:41:49Z`. |
| Fixture `champion_version` | Hardcoded **2** | Matches current registry v2 / v3 walk-forward. Wrong if a later registry version is the fixture source. |
| Track-record metric `vintage` | Frozen literals | `W9A_REVAL` / `W9G_REGRADE` per metric, from the amended 23-reval memo. Correct for this champion's graded numbers; not a function of the weekly predict. |
| `publish_schedule` strings | Hardcoded | `Tue 06:00 UTC` vs calendar Tuesday 06:00 America/New_York (already in this runbook). |

**Findings (hardcoded labels that can be wrong for a future run):**

1. Stub export with no `run_id` still stamps `REGRADED_V2` /
   `REDUCED_PER_ADR_0013` / `FEATURE_TIME=TUESDAY_DECISION`. Live predict
   always sets `run_id`.
2. `model_identity.champion_version` fallback remains 3. A live export
   that forgot to stamp the registry version would again claim champion 3
   while the index is at 2.
3. Fixture generator still hardcodes registry version 2 and the W9-A
   `registered_at`.
4. Ensemble and feature-time maps currently repeat the same two strings
   for every known run. A future non-reduced or non-Tuesday champion
   must be registered in `PROVENANCE_BY_RUN_ID` or export raises — that
   raise is the control.
5. Rehearsal `meta.champion_model` on disk still says
   `production-v0_reduced_v1` / champion 3 / `2024-08-01`. Vintage was
   restamped; identity was not rewritten. Next `execute_predict_publish`
   is the clean stamp.

### 2. Interval / point coherence — diagnose only

No model, calibrator, threshold, or CQR change. Construction at predict
time: published `[lo, hi] =` sorted LightGBM **q10/q90 ± CQR threshold**
(nominal 0.8). Champion `_cqr.score_thresholds[0.8] = 6.837` (calibration
seasons 2023–2024, n=1533, empirical coverage 0.802). The add-on is
**symmetric**. It cannot move `(μ − lo)/(hi − lo)` off 0.5 unless q10/q90
are already asymmetric around μ. ACI is not present on the bundle.

Position ratio = `(μ − lo) / (hi − lo)`. Gaussian 80% comparison uses
`z = Φ⁻¹(0.9) = 1.28155`, so implied bounds `μ ± zσ`.

#### 2026 week 1 (n=91)

| | min | p10 | median | p90 | max |
|--|--:|--:|--:|--:|--:|
| `(μ−lo)/(hi−lo)` | 0.426 | 0.492 | **0.803** | 0.936 | **1.023** |
| same, raw q10/q90 | 0.398 | 0.489 | 0.865 | 1.054 | 1.169 |
| published lo − Gaussian lo | −42.4 | −37.7 | −19.5 | −1.9 | +3.9 |
| published hi − Gaussian hi | −28.2 | −22.5 | −12.2 | +3.7 | +8.3 |

**46/91 (50.5%) outside [0.25, 0.75].** All 46 are high-side (μ closer to
hi than lo). **1 row has μ above the published hi** (Southeast Missouri
State @ Iowa State: μ=+47.6, interval [−16.6, +46.1]).

By `|μ|`:

| `|μ|` | n | n outside [0.25, 0.75] | median pos |
|--|--:|--:|--:|
| [0, 7) | 10 | 0 | 0.494 |
| [7, 14) | 8 | 0 | 0.501 |
| [14, 21) | 14 | 0 | 0.524 |
| [21, 28) | 6 | 1 | 0.548 |
| [28, ∞) | 53 | **45** | **0.881** |

Skew is concentrated in large favorites. Close games are centered.

**19/91 reconstructed q90 < μ** (quantile head 90th percentile below the
ensemble point). Raw q10/q90 ratios are *more* extreme than published
CQR ratios: CQR's +6.837 on both sides pulls the ratio toward 0.5; it
does not create the skew.

Published bounds vs `N(μ,σ)` 80%: lo is typically ~19 points *below* the
Gaussian lo (extra lower tail); hi is typically ~12 points *below* the
Gaussian hi (upper bound too low). The interval is shifted down, not
merely wider.

#### 2024 week 5 (n=56)

| | min | p10 | median | p90 | max |
|--|--:|--:|--:|--:|--:|
| `(μ−lo)/(hi−lo)` | 0.386 | 0.453 | **0.504** | 0.564 | 0.591 |
| published lo − Gaussian lo | −11.2 | −9.8 | −5.2 | −0.3 | +6.2 |
| published hi − Gaussian hi | −6.3 | +1.1 | +4.3 | +7.6 | +11.0 |

**0/56 outside [0.25, 0.75].** 0 rows with μ outside the interval. 0
rows with q90 < μ. Max `|μ|` on this slate is 35.5 (2 games ≥ 28), and
those two stay centered (median pos 0.535). vs Gaussian the published
interval is a bit wide (median width +9.2 points ≈ the 2×6.84 CQR add)
and roughly balanced.

#### Training / CQR support

v3 walk-forward `pred_margin` (n=4854): min −62.4, p99 **39.3**, max
51.6. Realized margin: min −66, p99 59, max 77. Week-1 pred_margin max
**46.87**. CQR calib 2023–24 pred_margin max **45.51**.

No 2026 week-1 μ sits outside the full-sample pred_margin min/max, so a
naive "outside the CQR head's μ range" test is 0/91. Of the 46 anomalous
rows: **26 have `|μ|` > training p99 (39.3)**; **9 have `|μ|` > historical
week-1 pred max (46.87)**; 0 have μ outside realized-margin min/max.
CQR `_fit_cqr_layer` fits thresholds on **placeholder Gaussian bands
around OOF μ**, not on the LightGBM quantile columns, then predict-time
applies that constant to the real q10/q90. That is why CQR cannot encode
quantile-head skew.

#### Mechanism (STOP AND REPORT)

Not conformal adjustment (symmetric; damps the ratio). Not genuine
residual skew (that would still have q10 < μ < q90; 19 rows violate
q90 > μ, and one published interval misses μ entirely).

**q10/q90 head extrapolation / quantile–mean incoherence on cupcake
blowouts.** The ensemble μ runs to +40…+50 on FCS openers; the LightGBM
q90 saturates (~39–52) so the 80% band does not keep up. 2024 week 5
does not show it because that slate has almost no `|μ|≥28` games.

This is a defect in the published interval, not a property of the
week-1 residual distribution. **Operator decision: whether margin
intervals publish on Tuesday 2026-09-01.** Options (not taken here):
omit `margin_interval_*` on the live export, publish only for
`|μ|` below a cutoff, or retune/refit the quantile head / CQR (out of
scope). Point μ/σ and conviction can still publish.

JSON: `_artifacts/webapp-w9d/amendment1_interval.json`.

### Forbidden actions not taken (Amendment 1)

- No write to `latest/`, `v1/*`, or `v2/*`. R2 `sandbox/` not re-pushed.
- No fit, retrain, promotion, registry change, CQR/threshold/quantile
  change, calibrator change.
- Local sandbox JSON vintage restamp only (`REGRADED_V2` → `W9A_REVAL`).

*End of W9-D Amendment 1.*

## Amendment 2 — per-row margin-interval coherence gate

**Date:** 2026-08-19  
**Status:** Gate in export. Asymmetry is published. CQR refit is W9-CQR,
not this task. Empirical coverage 0.874 stays out of `/results` until a
post-week-1 restamp.  
**Authority:** Amendment 2 to W9-D (pre-publish).

Artifacts: `_artifacts/webapp-w9d/amendment2_interval.json` (and the
restamp script beside it). Local sandbox JSON intervals restamped;
production `latest/` still untouched; R2 `sandbox/` not re-pushed.

### 1. Gate

`build_game_prediction` publishes `margin_interval_*` only when sorted
LightGBM **q10 < μ < q90** holds **before** the CQR add. Failure emits
JSON null (same contract as `total_interval_*`). No `|μ|` cutoff. No
position or asymmetry gate.

`assert_no_incoherent_margin_interval` then refuses to write a band when
those heads are present and fail the test. Bite-test:
`tests/unit/test_webapp_w9d.py::test_incoherent_band_assertion_bite`.

Missing quantile columns are not a failure (stub rows without heads still
copy `cqr_lo`/`cqr_hi`). Live predict rows carry `pred_margin_q10` /
`pred_margin_q90`.

This Week omits the `[lo, hi]` band when bounds are null (DESIGN §1.8).
Game Detail uses ForecastBlock's "Interval not computed" path with
`MARGIN_INTERVAL_ABSENT_REASON`. Point μ/σ and conviction still publish.

### 2. Counts

| slate | n | gate fires | remaining position outliers `[0.25, 0.75]` |
|---|---:|---:|---:|
| 2026 week 1 (rehearsal) | 91 | **19** | 27 of the 72 published bands (the original **46/91** included the 19) |
| 2024 week 5 fixture | 56 | **0** | 0 |
| v3 backtest analysis set | 4,743 | **0** | 11 (W9-INT; unchanged — the gate is a no-op on history) |

Reconstruction on the rehearsal JSON used champion CQR 80% add
**6.8371215750064245** (`q10 = lo + thr`, `q90 = hi − thr`), matching
Amendment 1's 19 `q90 < μ` rows. After restamp, zero published sandbox
bands fail the reconstructed test.

**46/91** (position outside [0.25, 0.75] on the ungated week-1 slate) is
a **monitoring item for the first four live weeks**, not a suppress.
Median position on that slate was **0.88** among `|μ| ≥ 28`. A long left
tail on a 43-point favorite is plausible; historical coverage on the
published construction is 0.874 (W9-INT). After the gate, 27 coherent
skewed bands remain on the rehearsal slate.

### 3. W9-CQR (named successor, post-launch)

The remaining defect: the CQR constant was fit on placeholder Gaussian
bands around OOF μ, not on the LightGBM quantile heads it is applied to.
Refit against the real heads and re-measure coverage. Baseline (W9-INT,
n=4,743, thr=6.837):

| construction | coverage |
|---|---:|
| published sorted q10/q90 ± 6.837 | **0.874** |
| raw heads (no CQR add) | **0.752** |
| μ ± 1.28σ | **0.815** |

See `docs/notes/webapp-w9cqr.md`. No CQR, quantile, or threshold change
in this amendment.

### 4. Coverage on `/results`

0.874 on n=4,743 is the 23-readout calibration-slope cell that was
**UNMEASURABLE**. It is now measured. Adding it to `/results` is a
restamp after week 1, not now. Pointer: `docs/notes/webapp-w9int.md`.

### Forbidden actions not taken (Amendment 2)

- No write to `latest/`, `v1/*`, or `v2/*`. R2 `sandbox/` not re-pushed.
- No fit, retrain, promotion, registry change. No CQR constant, quantile
  head, or calibrator change.
- Local sandbox JSON interval restamp only (19 rows → null bounds).

```
========= 942 passed, 1 deselected, 32 warnings in 288.30s (0:04:48) ==========
Required test coverage of 80% reached. Total coverage: 80.53%
```

Site: `webapp/site` vitest 132 passed; lint/prettier clean.

*End of W9-D Amendment 2.*
