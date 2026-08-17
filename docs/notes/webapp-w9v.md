# W9-V — validation-status recon (read-only)

**Date:** 2026-08-17  
**Status:** RECON — no fit, no promotion, no Path A/B in code.  
**Authority:** `docs/notes/23-readout.md` (FINAL); `docs/notes/webapp-w9m.md`
(`2b0f16e`); ADR 0013 / 0014; DESIGN §1.2, §5.3.

This task measures what is true and what revalidation costs. **It decides
nothing and fits nothing.** Recommendation at the end is advisory.

**STOP conditions (1–5): none tripped.** Reasons in each section below.

---

## 1. Delta diagnosis

Compare existing artifacts only (not a fit):

- Champion 3: `data/backtests/task23_fundamental_reduced_v2/full/weeks/season=2024_week=5.parquet`
- W9-M truncated current-code fit: `data/registry/artifacts/v1/week_predictions.parquet`
- Fixture: `webapp/fixtures/week_predictions.json`

Champion 3 parquet vs fixture max |Δμ_margin| = **0.0** (56/56). Identity on
both: `run_id=task23_fundamental_reduced_v2`,
`model_version=production-v0_reduced_v2`. Merged n=56.

### 1.1 Distribution of |Δ| (W9-M minus champion 3)

Pasted from a pandas compare of the two week-5 parquets (2026-08-17):

```
DELTA_MU     {"n": 56, "min": 0.36987844719892427, "median": 5.069712470224175, "p90": 12.230449864104987, "max": 20.19956592913842, "mean": 6.179246001759828}
DELTA_SIGMA  {"n": 56, "min": 0.009707073398381283, "median": 1.09934751980939, "p90": 2.431343889628393, "max": 6.22622556295941, "mean": 1.2878674559845404}
DELTA_PWIN   {"n": 56, "min": 0.0026102738850783958, "median": 0.12159028084180795, "p90": 0.2154732308742709, "max": 0.41364220718108063, "mean": 0.12469164206961152}
```

| Field | min \|Δ\| | median | p90 | max | mean |
|---|---:|---:|---:|---:|---:|
| μ_margin (points) | 0.37 | 5.07 | 12.23 | 20.20 | 6.18 |
| σ_margin (points) | 0.010 | 1.10 | 2.43 | 6.23 | 1.29 |
| p_win_home | 0.0026 | 0.122 | 0.215 | 0.414 | 0.125 |

Signed μ (new − old): median +1.91, mean +0.64. **47/56** games have larger
|μ| under current code. Correlation of the two μ vectors = **0.915**.
Champion μ range [−6.92, 15.31]; W9-M [−18.18, 35.51].
`feature_hash` agrees on **0/56** rows.

### 1.2 Tier disagreements

W9-M verify.log: `conviction_tier_agree=19/56` (37 disagreements). Same 37
when recomputed from parquet `p_ml_home` + `pred_margin` via export.py
`compute_p_favored` / raw ladder (0.85 / 0.70 / 0.575). No hysteresis store
on a first publish, so raw tier = published tier.

**Direction** (old → new):

```
produced_higher_conviction: 31
produced_lower_conviction: 6
transitions:
  lean → clear_lean          14
  toss_up → lean              7
  clear_lean → strong_lean    6
  clear_lean → lean           3
  lean → toss_up              2
  toss_up → clear_lean        2
  toss_up → strong_lean       1
  lean → strong_lean          1
  clear_lean → toss_up        1
```

**Boundaries vs systematic shift.** Hysteresis band is ±0.03
(`HYSTERESIS_BAND` in `export.py`). Distance of *old* `p_favored` to the
nearest of {0.575, 0.70, 0.85}:

```
DISAGREE_OLD_NEAR_BOUND_0.03     21 / 37
DISAGREE_OLD_NEAR_BOUND_0.05     28 / 37
DISAGREE_OLD_MEDIAN_DIST_TO_BOUND  0.0270
AGREE_OLD_NEAR_BOUND_0.03        11 / 19
DELTA_PFAV_DISAGREE_MEDIAN       0.135
DELTA_PFAV_AGREE_MEDIAN          0.061
```

Not a pure hysteresis effect. 21/37 sit near an old boundary (so some of the
37 would flip under a 3-point `p` nudge), but median |Δp_favored| on
disagreements is **0.135** (4.5× the band), 16/37 are >0.03 from any
boundary, and there are multi-rung jumps (`toss_up` → `strong_lean`,
`toss_up` → `clear_lean`). Combined with 47/56 more-extreme μ, this is a
**systematic scale expansion**, with a boundary overlay.

Example (max |Δμ| game, from W9-M notes): `401628378` 15.31 → 35.51 (tier
agreed; not in the 37). Example multi-rung: `401628498` toss_up → strong_lean,
μ −1.64 → −17.73, kickoff 2024-09-28T23:30Z.

### 1.3 Attribution of the three post-`4e29b7f` mapping-path commits

Ancestry (this tree, 2026-08-17):

```
4e29b7f_is_ancestor_of_HEAD = yes
4e29b7f ... HEAD ahead by 33 commits
ccf4032 is NOT an ancestor of 4e29b7f
4e29b7f IS an ancestor of ccf4032
```

| SHA | Date (local) | Subject |
|---|---|---|
| `4e29b7f` | 2026-08-11 13:16 −04 | Record Task 23 decision memo from corrected REGRADED_V2 / RERUN_V2 results. |
| `ccf4032` | 2026-08-11 17:00 −04 | Fix market feature as-of so post-kickoff snapshots cannot leak. |
| `c6404fc` | 2026-08-11 20:16 −04 | Align CFBD-week decision points to kickoffs so Tuesday features are honest. |
| `18cf69f` | 2026-08-12 20:27 −04 | Establish member-credibility contract (ADR 0014) and repair SDMU silent paths. |

Champion 3 fit `created_at` 2026-08-11T19:19:59Z with `git_sha=4e29b7f`,
`git_dirty=false` (`data/backtests/task23_fundamental_reduced_v2/full/manifest.json`).
That is after `4e29b7f` was committed and **before** `ccf4032`.

**Cheap isolation that does not require a refit:** both week-5 parquets carry
`as_of`. Predicting 2024 w5 at `ccf4032` / `c6404fc` / `18cf69f` would
require a walk-forward (no pickled mapping exists at those SHAs — serialize
is W9-M). Per the task: **stopped at the diff plus stored `as_of`**. Not a
refit.

```
ASOF_C_UNIQUE  ['2024-10-01 10:00:00+00:00']   # champion 3 (Labor-Day Tuesday)
ASOF_N_UNIQUE  ['2024-09-24 10:00:00+00:00']   # W9-M (kickoff-calendar Tuesday)
ASOF_MAX_ABS_DELTA_SEC  604800.0               # exactly 7 days
KICK_MIN  2024-09-26 23:30:00+00:00
KICK_MAX  2024-09-29 03:00:00+00:00
N_CHAMP_ASOF_AFTER_KICK  56 / 56
N_NEW_ASOF_AFTER_KICK    0 / 56
```

Fixture games kick off 26–29 Sep 2024. Champion 3 “Tuesday” is **1 Oct 10:00Z
(06:00 ET)** — after every kickoff. Current-code Tuesday is **24 Sep 10:00Z**
— before every kickoff. Site stamps
`FEATURE_TIME=TUESDAY_DECISION` and
`published_at: 2024-09-24T06:00:00Z` on the fixture; the μ it publishes were
computed at 1 Oct.

Champion 3 full `predictions.parquet` (n=4944, seasons 2019–2024, **0 rows of
2025**) uses Labor-Day Tuesdays throughout. 2024 blocks:

```
2024 w1  2024-09-03 10:00Z
2024 w4  2024-09-24 10:00Z   # honest instant for CFBD week 5
2024 w5  2024-10-01 10:00Z   # honest instant for CFBD week 6
```

The Labor-Day week index sits ~1 CFBD week ahead of the kickoff week.
`mkt-asof-fix.md` already counted 3445/3613 snapshot-era games with
`kickoff < week_as_of`.

Walk-forward order (`walkforward.py` ~1546 then ~1730): snapshot ratings →
features(`as_of`) → predict → **then** `update_after_games`. Same-week Kalman
reveal is after predict. Leak is still real:

1. **Mapping features** at `as_of` after kickoff admit any
   `event_time < as_of` join (plays / box / rolling team stats), including
   this game and earlier games that same CFBD week.
2. **`initialize_season(prior_as_of)`** (`production_stack.py` 173–199) runs
   the Kalman on `observations.event_time < as_of`. For 2024, week-1
   Labor-Day Tuesday is 3 Sep 10:00Z; `prior_as_of` is one second earlier —
   still after Labor Day weekend kickoffs. Week-1 observations can enter
   ratings **before** week-1 is “predicted.”
3. **Retrain at 2024 w5** trains on feature rows captured at every earlier
   leaky `as_of`. The mapping is fit on a contaminated information set even
   if the Kalman for week 5 itself only has weeks 1–4 via `update_after_games`.

That is enough, from the artifacts and the diffs, to attribute a 20-point
max |Δμ| without a new fit.

#### `ccf4032` — market as-of (ADR-adjacent leak fix)

Commit message (verbatim):

```
Fix market feature as-of so post-kickoff snapshots cannot leak.

Per-game feature as_of falls back to the latest decision point before kickoff
when the fixed week Tuesday is after the game; the ladder now hard-requires
event_time before kickoff. Re-audit CLEAN; market-aware v2 publishes inside
the guard band.
```

Stat: `src/ncaa_quant/features/market_lines.py` +131, `walkforward.py` +90
(lines resolver only). `feature_as_of_for_game` + `event_time < kickoff` on
the **snapshot ladder**. Champion YAML
`configs/ablations/task23_fundamental_full_reduced_v2.yaml` has
`market_features_available: false`. Harness `as_of` (Labor-Day Tuesday) did
**not** change in this commit — champion week-5 `as_of` is still 1 Oct.
**Does not plausibly account for a 20-pt fundamental μ shift.** It would
matter for market-aware μ, and it still matters for stored `spread_asof` on
the fundamental parquet (evaluation lines), not for mapping μ.

#### `c6404fc` — week-align

Commit message (verbatim):

```
Align CFBD-week decision points to kickoffs so Tuesday features are honest.

Derives tuesday/saturday 06:00 ET from each CFBD week's modal ET Monday,
cuts slot_close fallback to Week-0 exceptions, and republishes market-aware
at FEATURE_TIME=TUESDAY_DECISION (MAE regresses toward fundamental).
```

This commit **rewires the harness clock**. Diff excerpt (`walkforward.py`):
`WalkForwardHarness.run` builds `WeekDecisionCalendar.from_games(work)` and
passes it into every `week_decision_as_of(...)`. Labor-Day arithmetic becomes
the **test/no-schedule fallback** (`labor_day_week_decision_as_of`).

**Week-align is a fix to a defect, not a change of convention.** The advertised
information set (DESIGN / site: Tuesday before the week’s games;
`FEATURE_TIME=TUESDAY_DECISION`; provenance gloss “later information is not
in this forecast”) was not what champion 3 computed. Labor-Day week numbers
are not CFBD weeks; using them put Tuesday **after** kickoff for the oracle
week (56/56) and for the snapshot-era majority already measured in
`mkt-asof-fix.md`. Champion 3 was fit on that misaligned clock. The
published μ for 2024 week 5 is a post-kickoff information set labeled as a
Tuesday decision.

**This commit alone is large enough to explain the observed delta.** A
seven-day later `as_of` changes rating initialization, every PIT feature
join, and every retrain feature bank. More-extreme honest μ (47/56) is the
shape expected when post-game / same-week information is removed.

#### `18cf69f` — ADR 0014

Commit message (verbatim):

```
Establish member-credibility contract (ADR 0014) and repair SDMU silent paths.

Exclude non-credible members from NNLS instead of fabricating constants;
add ENet train-window NaN drop/impute; close quality-gate ABSENT and
partial-death blind spots; re-publish Tuesday market-aware under the contract.
```

Stat: `production_stack.py` +798/−, `elasticnet.py` +143, `walkforward.py` +188.
Item 5 of `member-health-fix.md` inspected **stored** champion-3 / A3 / A6
state: fundamental_v2 **clean** (no dead/degenerate weighted member). W9-M
week-5: both members credible, NNLS `w_lgbm=0.452`, `w_enet=0.548`,
`null_reason` all null. `MAX_CREDIBLE_MARGIN_PRED=80` does not bind on week 5
(max |μ| = 35.5).

ADR 0014 **can** still shift a refit: possessions are structurally null
except partial 2023; the new ENet policy drops columns with null share >0.50
and median-imputes the rest (previously sklearn NaN could fail into
`suppress` + 2.5 fill). W9-M quality gate `n_ungradable=90` matches ADR 0014’s
2019 w2–4 per-row OOD nulls; champion 3 gate had `n_null_mu=0`. Those 90 rows
are 2019, not 2024 w5, but they change the training mix for later retrains.
**Plausible secondary contributor; not required to explain 20 points given
the 7-day `as_of` leak.**

**STOP #1 does not trip.** The delta is attributable from the diffs plus
stored `as_of`: primary = week-align (harness clock), secondary = ADR 0014
training-input policy, market as-of ≈ none for this fundamental stack.

---

## 2. What exactly does `23-readout.md` describe?

### 2.1 Provenance fields recorded in the memo (verbatim)

**Header (body, `23-readout.md` lines 1–8):**

```
# TASK 23-READOUT — Decision memo (corrected v2)

**Date:** 2026-08-11
**Status:** DECISION — documentation only.
**ensemble_scope:** `REDUCED_PER_ADR_0013` (not §5.2-complete; ADR 0013).
**Vintages in force:** **REGRADED_V2** (fundamental, A1, A2, A4, A5 — fixed
closes on stored μ/σ) and **RERUN_V2** (A3, A6 published; market-aware refused
by ATS plausibility guard).
```

The memo **does not record a git SHA or a backtest `created_at`**. Run
identity is implied by the sources (`regrade_summary.json` /
`rerun_v2_summary.json`) and by the fundamental path name used everywhere
else: `task23_fundamental_reduced_v2` / `production-v0_reduced_v2`.

**Addendum header (lines 311–316):**

```
## ADDENDUM — 23-READOUT-CLOSE (2026-08-13) — FINAL

**Status:** CLOSED. Documentation only; no new runs.
**Vintage / scope:** `FEATURE_TIME=TUESDAY_DECISION` /
`REDUCED_PER_ADR_0013` / **ADR_0014** (`member-health-fix.md`,
`docs/adr/0014-member-credibility-contract.md`).
```

### 2.2 Champion 3 artifact provenance (the run the site μ came from)

From `data/backtests/task23_fundamental_reduced_v2/full/manifest.json`:

```
"created_at": "2026-08-11T19:19:59Z"
"git_sha": "4e29b7f1c9cec7c7873fa55333401c83a8134dfd"
"git_dirty": false
"extra.label": "v2-baseline-det2;ensemble_scope=REDUCED_PER_ADR_0013"
"extra.stack_kind": "fundamental"
"extra.seasons_executed": "[2019, 2020, 2021, 2022, 2023, 2024]"
"extra.wall_clock_sec": "2810.036"
"extra.quality_gate": "... n_scored=4376, n_null_mu=0 ..."
run_id (path + YAML): task23_fundamental_reduced_v2
model_version (YAML): production-v0_reduced_v2
```

Git: `4e29b7f` **is** the commit that added `docs/notes/23-readout.md` (307
lines). The body and the champion fit share that SHA. The fit is ~6 hours
after the commit (`created_at` 19:19Z vs commit 17:16Z).

### 2.3 Does that run predate `4e29b7f` and the three mapping commits?

- **At `4e29b7f`, not before it.** Same SHA.
- **Predates all three mapping commits** (`ccf4032`, `c6404fc`, `18cf69f`),
  which are 17:00 −04 the same day, 20:16 −04, and 20:27 −04 the next day.

`git log -- docs/notes/23-readout.md`:

```
6a4ef14 2026-08-13T07:45:22-04:00 Close Task 23 readout: final market-features finding and campaign summary.
3787723 2026-08-11T20:52:40-04:00 STOP readout campaign close: 2019 snapshot-source features resolve to CFBD close.
4e29b7f 2026-08-11T13:16:20-04:00 Record Task 23 decision memo from corrected REGRADED_V2 / RERUN_V2 results.
```

`6a4ef14` is **after** all three mapping commits (+51/−41 in the memo). It
adds the addendum. **§5 scorecard and §7 verdict are explicitly
unchanged.** Addendum: “Documentation only; no new runs” — meaning no new
**fundamental** evaluation; it cites later **market-aware Tuesday** numbers
(50.27% ATS) from `member-health-fix` Item 6, which are **not** in
`track_record.json`.

**STOP #2 does not trip.** The published site metrics describe the
`4e29b7f` / champion-3 / REGRADED_V2 fundamental run. Champion 3’s parquet is
the live oracle, not a stale leftover of a later evaluation. The addendum
talks about post-`4e29b7f` mapping-path **market-aware** work without
replacing the frozen scorecard.

### 2.4 Have readout metrics been recomputed since?

**No.** `build_track_record` (`export.py` 754–757): “Frozen 23-readout
metrics — verbatim, no recomputation.” Numbers in
`webapp/fixtures/track_record.json` match the original body (ATS 50.7% /
51.3%, MAE 14.85, CRPS 10.68, log-loss 0.82–1.04, verdict paragraph).
Addendum market-aware 50.27% is **not** on the site. Git history of the memo
after `4e29b7f` does not replace §5.

### 2.5 Same artifact set?

**Yes, for the published week-5 μ and for the fundamental REGRADED_V2
metrics’ stored μ/σ.**

| Consumer | Source |
|---|---|
| Champion 3 parquet | `data/backtests/task23_fundamental_reduced_v2/full/` (`predictions.parquet` n=4944; week-5 `season=2024_week=5.parquet` n=56) |
| `webapp/fixtures/week_predictions.json` | `generate_fixture_week_artifacts` default `walkforward_path` = that week-5 parquet (`export.py` 1247–1250). Δμ vs parquet = 0.0 |
| `track_record.json` | Hardcoded from `23-readout.md` §1–§5 (REGRADED_V2 of that run’s stored μ/σ, plus A2 / log-loss band) |
| W9-M registry pickle | **Different mapping.** Same `run_id` string stamped by W9-M for identity; μ does not match |

---

## 3. What on the site derives from the readout?

### 3.1 `track_record.json` — generation

- Builder: `src/ncaa_quant/webapp/export.py` `build_track_record` **lines
  754–969**. Literal constants copied from `23-readout.md`. No parquet read.
- Called from live export (`export.py` 1178) and fixture generator
  (`export.py` 1300).
- Fixture on disk: `webapp/fixtures/track_record.json` (`schema_version`
  **1.1.0**; `source_memo`: `docs/notes/23-readout.md`; `fixture: true`;
  `published_at`: `2024-09-24T06:00:00Z`).
- Python `SCHEMA_VERSION` is now `"1.2.0"` (`export.py` line 18) for all
  artifacts; the committed fixture track_record was not bumped.

**Every field `/results` renders from it** (`TrackRecord` type
`webapp/site/src/lib/artifacts/types.ts` 174–186):

| JSON field | Where rendered | File:line |
|---|---|---|
| `verdict.label` | Finding h2 | `VerdictBlock.tsx` 19–21 |
| `verdict.plain_language` | “Recorded finding” | `VerdictBlock.tsx` 23–26 |
| *(not in JSON)* `VERDICT_LAY_SUMMARY` | Lay paragraph, written to match §7 | `copy.ts` 6–11; `VerdictBlock.tsx` 22 |
| `ensemble_scope_label` | Scope line | `TrackRecordSection.tsx` 29–31 |
| `vintage_labels[]` | Scope line | `TrackRecordSection.tsx` 32–34 |
| `metrics[].id` | Row order via `EXPECTED_METRIC_IDS` | `metrics.ts` 4–18; `TrackRecordSection.tsx` 53–61 |
| `metrics[].label` | Metric column | `MetricRow.tsx` 75–76 |
| `metrics[].value` | Value column | `MetricRow.tsx` 20–27, 78–81 |
| `metrics[].unit` | Percent vs number formatting | `MetricRow.tsx` 24–26 |
| `metrics[].ci_lower`, `ci_upper`, `ci_kind` | Interval column; “50 lies inside” | `MetricRow.tsx` 56–57, 83–99 |
| `metrics[].n` | Details | `MetricRow.tsx` 59–60 |
| `metrics[].regime` | “Basis …” | `MetricRow.tsx` 62–63 |
| `metrics[].vintage` | Details | `MetricRow.tsx` 65 |
| `metrics[].run` | Details | `MetricRow.tsx` 66–67 |
| `metrics[].notes` | Notes paragraph | `MetricRow.tsx` 102 |
| `source_memo` | Not interpolated; filename cited in copy | `ScopeSection.tsx` 14–15 |

Expected metric ids (`metrics.ts` 4–18): `fund_ats_snapshots`,
`fund_ats_2019`, `fund_ou_snapshots`, `fund_ou_2019`,
`mae_margin_fund`, `mae_margin_a2`, `crps_margin_fund`,
`crps_margin_a2`, `ats_logloss_band`, `scorecard_clv`,
`scorecard_fund_ats`, `scorecard_fund_ou`, `scorecard_logloss`.

Route: `webapp/site/src/app/results/page.tsx` 19–40 loads `track_record` +
`results_<season>`; composition `ResultsPage.tsx` 22–45.

### 3.2 `/results` elements sourced from readout metrics vs not

**From readout / `track_record`:** verdict block, 13-row recorded-results
table, vintage/scope labels. Interval column on that table is the **ATS/OU
bootstrap CI**, not conformal coverage.

**Not from the readout (per-game grades):** `results_2024.json` via
`GradedGamesSection.tsx` 18–54 / `GradedGameRow.tsx` 96–105
(`margin_interval_hit`, `total_interval_hit`). Those grade **champion-3
week-5 μ/bands vs 2024 actuals**. Copy forbids aggregating them
(`copy.ts` 21–22; `GradedGamesSection.tsx` 46–47):

```
This page does not compute any aggregate accuracy statistic from graded games
for seasons 2025 or earlier — no overall percentages, no interval-coverage totals.
```

There is **no** 23-readout empirical interval-coverage rate on `/results`.
Readout §5 lists calibration slope as **UNMEASURABLE this session**.

**Hardcoded readout-faithful copy, not JSON:** `copy.ts` 6–11 (lay verdict),
14–15 (no single number), 18–19 (scope 2019 + 2021–2024, lockbox 2025).

### 3.3 `/about` and `/game/[id]` calibration / coverage / credibility copy

**`/about`** (`AboutPage.tsx` renders `lib/about/copy.ts`):

| Claim | File:line |
|---|---|
| Conformal layer; “historically, outcomes fall inside it at about the **stated coverage rate** — approximate, not a guarantee.” | `copy.ts` 20 |
| “The fit-to-bet verdict from the recorded track record is published on Results — currently **NOT CURRENTLY FIT TO BET**.” | `copy.ts` 49 |
| Disclaimer: “Past **interval hit rates** and **track-record metrics** do not guarantee future performance.” | `copy.ts` 56–57 |

The “stated coverage rate” sentence is **construction copy** (CQR nominal),
not a 23-readout empirical hit rate. It does not cite 50.7% / 14.85.

**`/game/[id]`:**

| Claim | File:line |
|---|---|
| “{percent} **nominal coverage**” under the band | `ForecastBlock.tsx` 88–91; value from `margin_interval_nominal` / `total_interval_nominal` |
| Fixture nominal | `week_predictions.json` per game, `margin_interval_nominal: 0.8` → **80%** |
| Feature-time gloss: “Tuesday decision means **later information is not in this forecast**.” | `provenance.ts` 17–22; `ProvenanceStrip.tsx` 14–31 |
| σ / p_win credibility (ADR 0014 **export gate**, not readout metrics) | `credibility.ts` 10–14; `ProbabilityList.tsx` 16–18 |

**DESIGN / webapp DESIGN — “stated nominal coverage” wording:**

- Core DESIGN §2.6 (`docs/DESIGN.md` 182): conformal layer provides
  **approximate** coverage; “empirical coverage vs **nominal** at 50/80/95%.”
- Webapp DESIGN §1.2 (`docs/webapp/DESIGN.md` 77):
  `margin_interval_nominal` = “Nominal coverage (e.g. 0.90).”
- No other “stated nominal coverage” string in `docs/webapp/DESIGN.md`.
  The public phrase “stated coverage rate” lives in `about/copy.ts` 20.

The feature-time gloss is **false for champion 3 week 5** (as_of after
kickoff). That is a published honesty claim about the same artifact as the
readout.

### 3.4 NOT CURRENTLY FIT TO BET — verdict text and evidence

Artifact (`track_record.json` 191–193 / `export.py` 954–963), also readout
§7 (`23-readout.md` 245–252):

**Label:** `NOT CURRENTLY FIT TO BET`

**plain_language (recorded finding):** point-prediction machinery is
credible (weekly MAE curve, MAE/CRPS sane, A2 Clause A) **but** no edge vs
the close (ATS ~50% fundamental REGRADED_V2; log-loss loses to 0.693; CLV
unmeasurable) **and** two §1.6 instruments unmeasurable (CLV; honest OU).

**Lay summary** (`copy.ts` 6–11): in-season learning; no betting edge; ATS
CIs include 50%; probabilistic scores lose to the market baseline.

Evidence cited is **exactly** the frozen REGRADED_V2 table (and the
unmeasurables). It does **not** cite addendum 50.27% market-aware ATS.

### 3.5 ADR 0014 σ-credibility thresholds — fitted on the old run?

**No.** They are code constants, not estimated from champion 3:

| Constant | Value | Where |
|---|---|---|
| `MAX_CREDIBLE_MARGIN_PRED` | 80.0 | `production_stack.py` 554 |
| `MEMBER_DEGENERACY_SD_EPS` | 1e-12 | `production_stack.py` 557 |
| `NULL_SHARE_DROP_THRESHOLD` | 0.50 | `elasticnet.py` 33; ADR 0014 text |

`member-health-fix.md`: “Forbidden (honored): no member hyperparameter /
threshold tuning.” W9-M `state_inventory.json` records them as constants.
Site `sigma_margin_credible` (`export.py` 202–209) is “σ present and no
`null_reason`” — not the 80 cap.

**STOP #4 does not trip.** Nothing here needs a statistical refit. Path A
would *exercise* the constants on a new walk-forward, not re-estimate them.

### 3.6 W1A-FIX 42.1% pooled tier-boundary flap

Measured on **`task23_fundamental_reduced_v2/full/predictions.parquet`**
(champion 3, n=4,944, 2019–2024) — `docs/notes/webapp-w1a.md` 28, 66–73.
**Does not survive a champion change.** Current-code week-5 already moves
`p_favored` by median 0.12. The figure is also already **UNRESOLVED** as a
proxy for realized Tue→Sat flicker (W1A-FIX). A new champion would need a
new proximity measurement; it still would not measure intra-week flicker.

---

## 4. Cost of Path A (revalidation)

### 4.1 Command chain (do not run in this task)

Site-facing `/results` table needs **fundamental** (ATS, OU, MAE, CRPS,
log-loss, scorecard) **and A2** (MAE 16.45 / CRPS 11.87 sit on the page).
A1/A4/A5 are memo findings, not `track_record` rows. A3/A6 addendum numbers
are already from later Tuesday re-runs and are not on the site.

**Must not reuse `run_id=task23_fundamental_reduced_v2`.** That path is the
champion-3 parquet the fixture is built from. New ids (advisory):
`task23_fundamental_reduced_v3` / `production-v0_reduced_v3`, and an A2 v2
yaml (today only `task23_A2_rating_updates_frozen_reduced_v1.yaml` exists).
Suggested label:
`W9V-PATH-A;FEATURE_TIME=TUESDAY_DECISION;ensemble_scope=REDUCED_PER_ADR_0013;ADR_0014`.

```
# 0. Copy/adjust YAML: new run_id, model_version, output isolation.
#    Do not --force into data/backtests/task23_fundamental_reduced_v2/.

# 1. Plan only (no compute)
uv run ncaa-quant backtest plan --config <new_fundamental_yaml>

# 2. Walk-forward 2019–2024, lockbox 2025 not in YAML test/continuity
uv run ncaa-quant backtest run --config <new_fundamental_yaml> --stack fundamental --output-root data/backtests --label "W9V-PATH-A;FEATURE_TIME=TUESDAY_DECISION;ensemble_scope=REDUCED_PER_ADR_0013;ADR_0014"

# 3. A2 frozen-ratings walk-forward (needed for page MAE/CRPS comparison)
uv run ncaa-quant backtest run --config <new_A2_yaml> --stack fundamental --output-root data/backtests --label "W9V-PATH-A;A2"

# 4. Grade ATS/OU on the NEW μ/σ vs already-fixed closes (minutes).
#    scripts/_ats_regrade.py is the pattern (seasons [2019,2021-2024], snaps 2021-2024)
#    but must point at the new predictions.parquet — not rewrite champion 3.

# 5. Metric suite (MAE, CRPS, log-loss, weekly MAE curve) on new predictions:
#    ncaa_quant.evaluation.metrics.compute_metric_suite
#    pattern: scripts/_task23_reduced_v1_metrics.py (v1 path names; adapt).

# 6. Rewrite docs/notes/23-readout.md (new memo / vintage) and the
#    frozen literals in build_track_record / fixtures. That is a later
#    explicit task — this recon does not do it.

# 7. Provenance gate
uv run ncaa-quant backtest verify --run-dir data/backtests/<new_run_id>/full
```

Optional memo-complete extra: A1, A4, A5 current-code reruns (same chain).
A3/A6/market-aware Tuesday already exist under ADR 0014; they are not
required to restamp `/results`.

### 4.2 Does W9-M already supply reusable OOS predictions?

**No.** `champion_serialize.py` 408–413 keeps **only 2024 week 5**
(`n=56`). The truncated harness scored `n_scored=3773` in memory
(2019–2024w5) and discarded the rest. Isolation rule: did not write
`data/backtests/.../weeks/*.parquet`. 2024 weeks 6–16 never ran (one more
week-10 retrain outstanding). Original `n_scored=4376`.

Path A is a **full walk-forward + grade + metrics**, not “grade W9-M.”

### 4.3 Wall-clock (idle machine)

Plan-estimator in `backtest_runner.py` (`SEC_PER_WEEK_FULL=0.75s`) is the
fantasy 23-readout already rejected. Use measured clocks:

| Run | wall_clock_sec | Source |
|---|---:|---|
| Champion 3 fundamental **full** 2019–2024 at `4e29b7f` | **2810** (~46.8 min) | manifest `extra.wall_clock_sec` |
| W9-M fundamental **truncated** through 2024w5, current code | **5259** (~87.7 min) | W9-M notes; CPU overlap with a killed duplicate |
| Week-align market-aware Tuesday full | **2885** (~48.1 min) | `week-align-fix.md` |
| ATS-GRADE-FIX A3 / A6 / market-aware v2 | 2348 / 1593 / 2963 | `v2_wall_clocks.json` |
| v1 fundamental (older stack) | 5304 (~88 min) | `23-rerun-r1.md` |

**Idle estimates (this recon, not a timer):**

| Stage | Estimate | Basis |
|---|---|---|
| Fundamental full current-code walk-forward | **50–90 min** (use **75 min** midpoint) | Lower bound: 47–48 min measured full at `4e29b7f` / week-align Tuesday. Upper: W9-M 88 min truncated **with contention**; idle truncated should be closer to 47 × 4556/5069 ≈ 42 min if speed matched, plus ADR 0014 overhead. Remaining 2024w6–16 ≈ 513 games + one w10 retrain is inside the 50–90 band if starting from scratch. |
| A2 full current-code | **50–90 min** | Same stack family; v1 A2 was 5642 s on the slower v1 bill |
| ATS grade + `compute_metric_suite` | **< 10 min** | `_ats_regrade.py` is a parquet rewrite + bootstrap; not LightGBM |
| Memo + `build_track_record` literals | documentation, not machine | — |
| **Site Path A sequential** | **~2–4 hours** | fundamental + A2 + grade |
| Full leftover ablations A1/A4/A5 | **+2.5–4.5 hours** | 3 × 50–90 min |
| **Four-day STOP threshold** | **not approached** |  |

**STOP #5 does not trip.**

### 4.4 2025 lockbox — what must not be touched

YAML `test_seasons`: 2019, 2021–2024; `continuity_seasons`: 2020. **2025
absent.** Champion parquet `N_2025=0`.

Guards on Path A stages:

- `backtest run` → `assert_lockbox_excluded(replay_seasons)` (`cli.py` 793)
- `load_staged_odds_snapshots` → same + refuse if loaded rows have season
  2025 (`cli.py` 577–606)
- `_ats_regrade.py` `main()` seasons `[2019, 2021, 2022, 2023, 2024]`; snaps
  2021–2024 (`scripts/_ats_regrade.py` 255–260)
- `grade.py` `assert_live_season` refuses `< 2026` (live grades; not this
  historical chain)
- Kalman `initialize_season` can *in principle* see 2025 observations if they
  sit in the in-memory `observations` frame; Path A stack load iterates
  `replay_seasons` only (W9-M confirmed `2025_rows_in_fit_games=0` and
  `filter_history.parquet` not loaded)

`walkforward.py` still *names* 2025 in `HISTORICAL_CANONICAL_SEASONS` (line
84); that constant is not the Path A season list.

**STOP #3 does not trip.** No Path A stage above reads 2025 if YAML and the
regrade script keep the listed seasons.

### 4.5 New task id / run id

A new readout is a new citable run (ADR 0005): new `run_id`, new git SHA at
fit time, new notes memo. **Do not reuse** `task23_fundamental_reduced_v2` or
overwrite `23-readout.md` in place without an explicit successor task.
Advisory names: notes `docs/notes/23-reval.md` (or `webapp-w9a.md`) and
`run_id=task23_fundamental_reduced_v3`. Operator chooses.

---

## 5. Cost of Path B (withdrawal)

### 5.1 W8-C mechanism applied to readout-derived fields

W8-C (`docs/notes/webapp-w8c.md`; `export.py` 28–36;
`published-keys.ts` 5–10, 121–127):

- `WITHDRAWN_FIELDS` on **GamePrediction** (`p_cover_home`,
  `p_cover_home_credible`, `p_over`, `p_over_credible`)
- omit from new objects; `assertConsumedOrWithdrawn` allows old keys
- minor schema bump (`1.1.0` → `1.2.0`); major stays 1

Path B analog for **track_record** (not GamePrediction):

| Withdraw | Why |
|---|---|
| All 13 `metrics[]` ids listed in §3.1 | Verbatim 23-readout / REGRADED_V2 of leaky-as_of μ |
| `verdict.plain_language` | Cites MAE/ATS/log-loss of that run |
| `ensemble_scope_label` / `vintage_labels` as “this is the evaluated vintage” | Optional; they also label the (still published) week-5 games |
| `VERDICT_LAY_SUMMARY` (`copy.ts` 6–11) | Hardcoded readout paraphrase |
| About honesty line `copy.ts` 49 | Points at the Results verdict |

**Do not withdraw as W8-C GamePrediction keys:** `mu_margin`, σ, intervals,
`p_win_home`, `margin_interval_nominal` (CQR **nominal**, not readout
coverage), `FEATURE_TIME` (still a true description of *current* code).
Withdrawing those would empty This Week / Game Detail.

Minor bump: track_record fixture is still `1.1.0`; a Path B task would bump
the track_record object (export already claims `1.2.0` globally — a Path B
task should not naively reuse 1.2.0 for a second meaning). Tests:
consumed-or-withdrawn for metric ids; missing-metric UI already exists
(`MISSING_METRIC_COPY`, gallery `results-states`).

### 5.2 Does `/results` stay a coherent page?

**Not empty, not 404.** After metric withdrawal:

- Verdict block: can keep a **product** finding without numbers, or render
  an honest “evaluation withdrawn / pending revalidation” status.
- Recorded-results table: 13 × “Not in the recorded artifact”
  (`MetricRow.tsx` 36–50) — a status, not a blank route.
- Graded-games tab: still 2024 fixture rows + lockbox copy. Those hits still
  grade **champion-3** bands. Path B as specified does not freeze or remove
  `results_2024.json`; an operator who withdraws the readout but leaves
  leaky week-5 grades should say so on the page.
- Scope section still cites `23-readout.md` (`ScopeSection.tsx` 14) — that
  citation would have to be rewritten in the Path B task.

404 is the wrong empty: the route has remaining content. An honest status
banner is the W8-C-shaped choice.

### 5.3 Can NOT FIT TO BET stand without the metrics it cites?

The **label** can stand as a product rule (Ridge does not recommend betting;
CLV still unmeasured; no live 2026 book). The **current supporting
sentences cannot**: they quote ATS ~50%, log-loss vs 0.693, MAE/CRPS, A2
Clause A — all champion-3 REGRADED_V2.

A supporting sentence that does not cite the withdrawn table:

> Ridge does not publish betting recommendations. The walk-forward table
> that previously supported this finding has been withdrawn because those
> numbers were computed on a Tuesday clock that sat after kickoff for the
> published week. Live 2026 grades start empty. CLV remains unmeasured.

About `copy.ts` 49 would need the same treatment in the Path B task.

---

## 6. Recommendation (advisory)

**Path A (revalidate on current code), not Path B.**

Week-align is a **defect**: champion 3’s 2024 week-5 `as_of` is 7 days late
and after 56/56 kickoffs, while the site labels the same rows
`FEATURE_TIME=TUESDAY_DECISION`. The published MAE/ATS/verdict describe that
leaky information set. Current code is the advertised Tuesday. W9-M already
showed the mapping moves (max |Δμ| = 20.20; 19/56 tiers). Leaving the old
table up while running (or preparing to run) honest-Tuesday μ is the
mismatch this recon exists to name. Withdrawing the table (Path B) removes
the contradiction at the cost of an empty recorded-results tab and a
verdict that can no longer cite evidence; it does not measure the honest
clock.

**Wall-clock basis:** idle site Path A ≈ **2–4 hours** sequential
(fundamental + A2 + grade/metrics), measured analog 47–48 min per full
reduced walk-forward plus W9-M 88 min truncated-with-contention as a
conservative cap. Optional A1/A4/A5 add one afternoon. **Not four days.**
2025 stays out of YAML and the regrade script. ADR 0014 thresholds are
constants (no separate refit). W9-M week-5 pickle is not a substitute for
the 2019–2024 predictions.parquet.

Operator still chooses. This memo does not write YAML, does not fit, and
does not change `/results`.
