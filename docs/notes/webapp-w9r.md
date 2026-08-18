# W9-R — restamp `/results` from 23-reval; regenerate fixtures

**Date:** 2026-08-17  
**Status:** Phase 1 DONE (restamp + fixture regen). Amendment 1 applied.
Phase 0.3 was a grading defect; **resolved by W9-G** (`docs/notes/webapp-w9g.md`).
Production `latest/` was **not** rewritten (R2 write forbidden; stays 1.1.0
until the first live publish).  
**Authority:** amended `docs/notes/23-reval.md` (post-`517926e`, plus
Amendment 1 §2 / verdict / notes); `docs/notes/webapp-w9v.md` (`157bc7d`);
`docs/notes/23-readout.md` (FINAL, not rewritten).

Phase 0 required all four items to clear before any literal is copied. Item
0.3 was a grading/reporting defect on the 2019 ATS intervals (and a second
regrade behavior that put invented `p_ats_home` into the 2019 ATS *rate*).
W9-G fixed both and re-derived the affected numbers. Phase 1 was not
started — restamp is still a separate step, now against the amended
`23-reval.md`.

Machine evidence: `docs/notes/_artifacts/webapp-w9a/w9r_phase0.json`.

---

## STOP

Phase 0 item 0.3 does not clear **for this restamp**. 2019 ATS naive (and
bootstrap) CIs were computed on a different sample than the published rate.
Snapshot CIs are aligned. Per the task: do not restamp unresolved metrics;
do not start a fixer regrade unprompted.

W9-G later did that regrade. This W9-R restamp remains unstarted until a
successor copies the amended `23-reval.md`.

STOP #2 (“defect affecting more than the 2019 regime”) does **not** trip
empirically — snapshot ATS CIs use the same denominator as the snapshot
rate. The defective *code* is shared (`attach_metric_cis`); the
contaminated *published numbers* are CFBD-2019 ATS CIs only (fundamental
and A2). That is still enough to refuse the restamp of `fund_ats_2019`
and of the 2019 ATS CIs copied into §3.

**Lifted by W9-G + Amendment 1.** The 2019 ATS / log-loss figures in
amended `23-reval.md` are the regrade. Source quality no longer blocks
Phase 1; Phase 1 is still a separate step and has not started.

---

## 0.1 — Matched-subset MAE and CRPS

Intersection of headline rows with finite `pred_margin` and
`realized_margin` on both champion 3
(`data/backtests/task23_fundamental_reduced_v2/full/predictions.parquet`)
and `task23_fundamental_reduced_v3` grade_v2. CRPS further requires finite
σ>0 on both.

```
champion3 vs v3
n_mae=4285  mae_old=14.5837  mae_new=14.5334  Δ=−0.0503
n_crps=4175 crps_old=10.1888 crps_new=10.0239  Δ=−0.1649
n_dropped=90  mean_|e|_old_on_dropped=27.4113
```

REGRADED_V2 v1 grade vs v3 on the same keys: **identical** MAE/CRPS
(14.5837 / 10.1888). Champion 3 and the readout μ agree on this
intersection.

The 90 dropped rows are exactly the ADR 0014 2019 w2–4 null-μ set
(`null_reason=no_credible_members`). Arithmetic that treated 14.85 vs 14.53
as a pure composition effect implied mean |e|≈30.1 on those rows; the
actual champion-3 |e| is **27.41**. The extra ~0.05 of the published MAE
drop is a real matched-sample movement, not the 90-row mix.

**Did point accuracy change once the sample is held fixed?** Yes, slightly:
MAE **14.58 → 14.53** (−0.05) and CRPS **10.19 → 10.02** (−0.16) on n=4285 /
4175. It did **not** change by the headline 14.85→14.53. Most of that
headline is the 90 high-error rows leaving. Copied into `23-reval.md` §6b.

---

## 0.2 — n reconciliation, all 13 metric ids

Headline n is 4376 on both vintages (2019 + 2021–2024; 2020 continuity
excluded). Old n below is REGRADED_V2 / `build_track_record`. New n is
W9A_REVAL / `metrics_summary.json`.

| id | old n | new n | delta | reason |
|---|---:|---:|---:|---|
| `fund_ats_snapshots` | 3496 | 3496 | 0 | Same decided snapshot sample. Finite line 3596; 100 pushes/undecided excluded from both. |
| `fund_ats_2019` | 743 | 657 | **−86** | 2019 headline still 763. Finite close 757, decided y 743, **14 pushes**, both vintages. New: 86 of those 743 have finite y and **NaN `p_ats_home`** (the 90 ADR 0014 null-μ rows minus 4 of the 14 pushes). 743−86=657. |
| `fund_ou_snapshots` | 3136 | 3136 | 0 | Same. Finite total_close 3168; 27 pushes; 5 y-finite / p-NaN leftover both sides. |
| `fund_ou_2019` | 747 | 551 | **−196** | Decided OU y still 747 / 10 pushes. New: **196** rows with finite y and NaN `p_ou_over`. That is the 90 null-μ rows plus the 110 μ-but-no-σ 2019 w2–4 rows (p_ou is nulled at fit; regrade does **not** refresh OU). 90+110=200; 4 of those 200 lack finite OU y → 196. 747−196=551. |
| `mae_margin_fund` | 4375 | 4285 | **−90** | 90 `no_credible_members` (null μ). 4376 headline − 90 = 4286 scored; one scored row lacks realized margin → 4285. |
| `mae_margin_a2` | 4375 | 4290 | **−85** | Same OOD family; A2 scored five of those rows under frozen ratings (`n_ungradable=85`). |
| `crps_margin_fund` | 4375 | 4175 | **−200** | 90 null μ **and** 110 finite-μ / missing-σ (2019 w2=41, w3=34, w4=35). 90+110=200. 4375−200=4175. |
| `crps_margin_a2` | 4375 | 4175 | **−200** | 85 null μ + 115 μ-without-σ, same three 2019 weeks. |
| `ats_logloss_band` | — | — | — | Band, not a count. |
| `scorecard_clv` | — | — | — | UNMEASURABLE both vintages. |
| `scorecard_fund_ats` | — | — | — | Label + notes; n lives on the ATS rows. |
| `scorecard_fund_ou` | — | — | — | Label + notes. |
| `scorecard_logloss` | — | — | — | Label + notes. |

### CRPS 4375 → 4175 (−200) vs MAE −90: the extra 110

**Mechanism: missing σ, not the webapp helper by name.** MAE requires finite
μ+y. CRPS additionally requires `np.isfinite(sig) & (sig > 0)`:

```1351:1354:src/ncaa_quant/evaluation/metrics.py
            if sigma_col in cont.columns and cont[sigma_col].notna().any():
                sig = cont[sigma_col].to_numpy(dtype=float)
                ok = np.isfinite(sig) & (sig > 0)
```

All 110 sit in ADR 0014’s 2019 w2–4 blocks, with `sigma_m_is_missing=True`,
finite μ, **NaN `p_ats_home` / `p_ou_over` on the fit parquet**, and
`null_reason` empty (so they are *scored* for μ, not in the 90
`n_ungradable`). Fit-time path is ADR 0014 keep-μ / drop-σ:

```1398:1416:src/ncaa_quant/evaluation/production_stack.py
        if not np.any(ok_m):
            # σ may be missing after refusing a constant floor, but μ can still be
            # an honest point prediction — do not erase it (ADR 0014).
            if np.any(np.isfinite(mu_m)):
                out["sigma_m"] = np.nan
                ...
                for col in (
                    "p_ml_home_raw",
                    "p_ats_home_raw",
                    "p_ou_over_raw",
                    "p_ml_home",
                    "p_ats_home",
                    "p_ou_over",
                ):
                    out[col] = np.nan
```

That is the same credibility family as `sigma_margin_credible` in
`export.py` (missing-σ / null_reason ⇒ not credible), but CRPS n is gated
by the metrics `sig > 0` test above, not by the webapp helper.

**Why ATS only dropped 86 of those 200, while OU dropped 196.** Regrade
refreshes **ATS** probabilities and, when σ is missing, **invents** a
hard-edge p from the sign of μ+S:

```87:95:scripts/_ats_regrade.py
def _p_ats_gaussian(mu, sigma, spread):
    ...
    # Missing σ: hard edge from μ alone (sign of μ+S).
    fallback = np.isfinite(mu) & np.isfinite(spread) & (~ok)
    out[fallback] = np.where(edge > 0, 0.999, np.where(edge < 0, 0.001, 0.5))
```

So the 110 μ-without-σ rows **re-enter 2019 ATS** with p∈{0.999,0.001,0.5}.
The 90 null-μ rows cannot. OU is not refreshed, so both groups stay out of
2019 OU. Fit parquet: those 110 have `p_ats_home` NaN; grade parquet: 109/110
have finite invented p (one lacks a finite spread).

Snapshot n for ATS/OU is unchanged because every extra missing-σ row is
2019 w2–4.

---

## 0.3 — 2019 ATS estimator versus its interval

Naive 95% Wald intervals **do** center on *their* point estimate. The 2019
rows look off-center because the **interval’s point is not the published
rate**.

Pushes are **not** the split. 2019 has 14 finite-line pushes/undecided.
`ats_home_outcomes` marks them NaN. `binary_accuracy` drops them.
`attach_metric_cis` also drops them (`hits[~np.isfinite(y_ats)] = nan`).
Same 14, both sides.

The split is **NaN `p_ats_home` with finite decided y**:

```1254:1257:src/ncaa_quant/evaluation/metrics.py
        hits = ((p >= 0.5).astype(float) == y_ats).astype(float)
        hits[~np.isfinite(y_ats)] = np.nan
        mask = np.isfinite(hits)
```

`np.nan >= 0.5` is False, so a missing probability is scored as an **away
pick**. `binary_accuracy` / `_regime_ats` instead require
`isfinite(p) & isfinite(y)` (`metrics.py` 361, `_ats_regrade.py` 210).

Fundamental 2019 (paste):

```
n_sub=763
n_rate (finite p and y)=657   rate=47.793%
n_ci  (attach_metric_cis)=743  ci_sample_rate=49.125%
n_push=14
n_nan_p_finite_y=86
naive CI n=743  point=0.49125  [0.45530, 0.52720]  mid=0.49125
bootstrap      point=0.49125  [0.46938, 0.51124]
denominators_equal=false
```

86 = 90 null-μ minus 4 pushes inside that 90. 657+86=743, which is also
REGRADED_V2’s 2019 ATS n. The published pair **47.8% in [45.5%, 52.7%]** is
rate-on-657 pasted next to a Wald interval for **49.13% on 743**. The
memo’s 49.1% “center” is exactly that CI-sample rate.

Snapshots (same code, no NaN p):

```
n_rate=3496  n_ci=3496  rate=ci_sample=48.942%
n_nan_p_finite_y=0
naive [0.47285, 0.50599] mid=0.48942
denominators_equal=true
```

That is why snapshots look centered and 2019 does not. `_regime_ou`
filters `isfinite(y) & isfinite(p)` *before* the bootstrap, so 2019 OU CIs
are aligned with their rates.

### A2 plausibility trip

A2 2019:

```
n_rate=662  rate=43.807%
n_ci=743    ci_sample_rate=44.818%
n_nan_p_finite_y=81
band on n=662 (z=3): [44.17%, 55.83%]  rate_inside=false
band on n=743:       [44.50%, 55.50%]  ci_sample_rate_inside=true
```

The **guard** (`assert_prediction_ats_plausible`) uses the finite-p mask —
same denominator as the published 43.81%, not the CI sample. On that mask
it is internally consistent and fires: 43.81% < 44.17% by 0.36 pp.

It did **not** fire because A2 is “expected to be worse” as a slogan. It
fired because the finite-p 2019 rate is outside the fair-coin band for its
own n. Two caveats that keep this from being a license to restamp:

1. **The published 2019 CI is not an interval for that rate.** 43.8% is
   shown with [41.2%, 48.4%], whose center (44.8%) is the *other* sample.
   If the CI sample’s 44.82% were the rate, the guard would **not** trip
   (`ci_sample_rate_inside=true` on n=743). The 0.36 pp miss is smaller
   than the 1.0–1.3 pp rate/CI discrepancy, which is why the task
   forbade dismissing the trip until this was resolved.
2. **The rate itself includes 110 (fund) / 115 (A2) 2019 w2–4 rows whose
   `p_ats_home` was invented by the regrade fallback**, after the fit
   stored them as missing. That is a second grading inconsistency, in the
   *estimator*, not only in the interval. Whether 43.81% without those
   rows sits inside the band is a regrade question and was not started.

The guard caught a real publishing hazard (rate vs interval disagree; OOD
rows with invented hard-edge p). It is not “A2 is allowed to be terrible.”
Fixing either issue is a **regrade, not a refit**, not started here.

### Resolution (W9-G)

Authorized as W9-G (`docs/notes/webapp-w9g.md`). Both defects removed:
`attach_metric_cis` now matches `binary_accuracy`; `_p_ats_gaussian` no
longer invents p from missing σ. Fundamental 2019 ATS **49.9% n=553**
(rate n == CI n); snapshots **unchanged** 48.9% n=3496. Log-loss 1.350 →
0.779 on the remaining 553 (104 invented rows entered the 657; 66 wrong;
contribution 455.95). Remaining 2019 LL is **not** ≈ 0.93, so the band is
restated **0.78–0.93**, not collapsed. A2 2019 **45.6% n=553** is inside
[43.62%, 56.38%]; the guard does **not** fire. MAE/CRPS/OU byte-identical.
`23-reval.md` amended in place. Phase 1 still not started.

---

## 0.4 — Regrade provenance

W9-A `grade.log` reused the fundamental grade parquet. Identity of that
file:

```
input parquet:
  data/backtests/task23_fundamental_reduced_v3/full/predictions.parquet
  mtime_utc=2026-08-17T20:41:46.173747778+00:00
  size=1325254
  run_id=task23_fundamental_reduced_v3
  model_version=production-v0_reduced_v3
  n=4944
  N_2025=0

grade parquet (reused):
  data/backtests/task23_fundamental_reduced_v3/full/grade_v2/predictions.parquet
  mtime_utc=2026-08-17T22:18:19.381660461+00:00
  size=1314531
  run_id=task23_fundamental_reduced_v3
  model_version=production-v0_reduced_v3
  grade_version=v2
  n=4944
  max |Δμ| vs input = 0.0 (4944/4944)
  max |Δμ| vs champion 3 = 71.72; 4854/4944 disagree
```

Fit `created_at` 2026-08-17T20:41:46Z matches the input mtime. Grade mtime
is 22:18Z, after A2 fit end (22:11:32Z) and before the completed W9-A A2
grade write (22:23:55Z) — consistent with “first shell wrote fundamental
grade_v2 then aborted; `_w9a_grade.py` reused it.” No `grade_manifest.json`
was written (that file is emitted by `_ats_regrade.py`, not `_w9a_grade.py`).

This is **not** a champion-3 artifact (`run_id` /
`production-v0_reduced_v2`). μ identity with the v3 walk-forward is exact.

A2 grade (for 0.3): same pattern,
`run_id=task23_a2_reduced_v2` / `production-v0_a2_reduced_v2`,
mtime 2026-08-17T22:23:55Z, input mtime 22:11:32Z.

---

## Amendment 1 (pre-Phase 1)

Applied 2026-08-17. Does not start the restamp. Constraints for Phase 1:

1. **Source.** Every restamp literal is the amended `23-reval.md` (post-
   `517926e` plus this amendment), not the W9-A first pass.
   `fund_ats_2019` = 49.9% [46.9%, 52.3%] n=553;
   `ats_logloss_band` = 0.78–0.93 vs 0.693;
   `scorecard_fund_ats` = MISSED (48.9% / 49.9%);
   `scorecard_logloss` = MISSED 0.78–0.93 vs 0.693.
   First-pass 47.8% n=657 / 0.93–1.35 as *current* published figures is a
   STOP.
2. **Memo first.** §2 no longer says “all ≫ 0.693”. The accurate
   comparison is fundamental **0.78–0.93 vs 0.693**. Verdict
   `plain_language` cites snapshot ATS 48.9% [47.5%, 50.5%]; 2019 49.9%
   [46.9%, 52.3%]; log-loss 0.78–0.93 vs 0.693. Label unchanged:
   **NOT CURRENTLY FIT TO BET**. Phase 1 copies that paragraph; it does
   not invent a second one.
3. **Metric notes** (verbatim in `23-reval.md` under the 13-id table).
   One plain sentence each, no history narration. 2019 weeks 2–4 are a
   partially degraded cohort per ADR 0014 (stated once).
   - `mae_margin_fund` / `crps_margin_fund`: 90 rows (2019 weeks 2–4)
     carry no credible ensemble member and are not scored; on the matched
     sample point accuracy is essentially unchanged.
   - `fund_ats_2019`: sample excludes rows where the model recorded no
     ATS probability; no probability is imputed.
   - `fund_ou_2019`, `crps_*`: same basis (rows without a recorded
     probability or a usable σ are not scored; nothing is imputed).
4. **Literal-equality tests** assert against those amended memo values.
   A test that compares a restamp field to a W9-A first-pass number is a
   STOP.

## Phase 1

Restamped `/results` from amended `23-reval.md` (post-`517926e` + Amendment 1).
No refit, no regrade, no registry change, no R2 write. 23-readout.md untouched.

### Restamp diff (13 ids)

Old = committed `build_track_record` / REGRADED_V2. New = amended memo.

| id | old | new |
|---|---|---|
| `fund_ats_snapshots` | 50.7% [48.7%, 52.7%] n=3496 REGRADED_V2 | 48.9% [47.5%, 50.5%] n=3496 W9G_REGRADE |
| `fund_ats_2019` | 51.3% [48.3%, 54.3%] n=743 | 49.9% [46.9%, 52.3%] n=553 W9G_REGRADE |
| `fund_ou_snapshots` | 52.3% [49.7%, 54.8%] n=3136 | 51.5% [49.7%, 53.5%] n=3136 W9A_REVAL |
| `fund_ou_2019` | 50.9% [46.6%, 55.4%] n=747 | 51.4% [46.5%, 55.3%] n=551 W9A_REVAL |
| `mae_margin_fund` | 14.85 n=4375 | 14.53 n=4285 W9A_REVAL |
| `mae_margin_a2` | 16.45 n=4375 | 15.51 n=4290 W9A_REVAL |
| `crps_margin_fund` | 10.68 n=4375 | 10.02 n=4175 W9A_REVAL |
| `crps_margin_a2` | 11.87 n=4375 | 10.75 n=4175 W9A_REVAL |
| `ats_logloss_band` | 0.82–1.04 vs 0.693 | 0.78–0.93 vs 0.693 |
| `scorecard_clv` | UNMEASURABLE | UNMEASURABLE |
| `scorecard_fund_ats` | MISSED (50.7% / 51.3%) | MISSED (48.9% / 49.9%) |
| `scorecard_fund_ou` | MISSED / uninterpretable | MISSED / uninterpretable |
| `scorecard_logloss` | MISSED 0.82–1.04 vs 0.693 | MISSED 0.78–0.93 vs 0.693 |

`vintage_labels` = `["W9A_REVAL"]`. `ensemble_scope_label` unchanged
(`REDUCED_PER_ADR_0013`). `source_memo` = `docs/notes/23-reval.md`.
Verdict **label** unchanged: `NOT CURRENTLY FIT TO BET`.
`plain_language` cites snapshot ATS 48.9% [47.5%, 50.5%]; 2019 49.9%
[46.9%, 52.3%]; log-loss 0.78–0.93 vs 0.693; CLV and honest OU unmeasurable.

W9-A first-pass 47.8% n=657 / 0.93–1.35 is not a current published value
in `export.py`, `copy.ts`, or the regenerated fixtures.

### Fixture provenance

```
input parquet:
  data/registry/artifacts/v2/week_predictions.parquet
  mtime_utc=2026-08-17T20:41:49.102940+00:00
  size=53523
  run_id=task23_fundamental_reduced_v3
  model_version=production-v0_reduced_v3
  n=56
  as_of=2024-09-24T10:00:00Z
  registry champion_version=2

equivalent week parquet (byte-size match, earlier mtime):
  data/backtests/task23_fundamental_reduced_v3/full/weeks/season=2024_week=5.parquet
  mtime_utc=2026-08-17T20:41:46.028408+00:00
  size=53523
```

`generate_fixture_week_artifacts` now defaults to the v2 registry parquet
and stamps `published_at` = `2024-09-24T10:00:00Z` (the parquet `as_of`).
`week_predictions.json` schema 1.2.0; withdrawn keys absent.
`track_record.json` schema 1.2.0 (was 1.1.0).
`results_2024.json` regenerated against the new bands.
`week_predictions.legacy-1.1.0.json` unchanged.
`team_ratings_2024.json` not regenerated (filter_history is not this champion).
`meta.json` restamped to the same as_of / vintage / champion 2 so This Week
header matches the week object.

Interval hits (per-game only; no aggregate published):

```
old margin: true=48 false=7 null=1
new margin: true=48 false=7 null=1
flips: 401636883, 401641018  (2 games; offsetting)
total_interval_hit: all null both vintages
```

Same 48/55 rate. Two games flipped and cancelled. Not a band-width defect.

Game `401628373`: `published_at=2024-09-24T10:00:00Z` <
`kickoff_utc=2024-09-28T19:30:00Z`. ProvenanceStrip: `W9A_REVAL` /
`REDUCED_PER_ADR_0013` / `FEATURE_TIME=TUESDAY_DECISION` — the Tuesday
decision clock is now the actual feature `as_of`.

### Tests / build

```
make test
========= 887 passed, 1 deselected, 32 warnings in 295.66s (0:04:55) ==========
Required test coverage of 80% reached. Total coverage: 80.14%

cd webapp/site && npm test
 Test Files  21 passed (21)
      Tests  129 passed (129)

npm run build — routes present: / , /about , /results , /game/[gameId]
```

W9-P numeric oracle no longer compares produced μ to the public fixture
(that fixture is now v3 / honest Tuesday). It compares to the champion-3
parquet `predict_fn` still loads. Same 56 CFBD ids.

### Grep gate (W0 union; not adjusted)

```
rg -n -i --pcre2 "best bet|yes bet|\bplay\b|edge vs market|\bunits\b|lock it in|must bet|recommended bet" webapp/site/src/lib/results/copy.ts webapp/site/src/components/Results/ScopeSection.tsx
union_copy_exit=1

rg -n -i --pcre2 "best bet|yes bet|edge vs market|lock it in|must bet|recommended bet" webapp/site/src
w8d_src_exit=1
```

Empty stdout. Ripgrep exit 1 = no matches. Gate did **not** flag the
restamped `plain_language` / `VERDICT_LAY_SUMMARY`. W0 union list still
unreconciled (W9-1).

### Production

R2 write / revalidate POST forbidden. `latest/` stays 1.1.0. Local
fixtures and this commit are the restamp; production HTML will not show
48.9 / 49.9 / 14.53 until a later sanctioned fixture push.

```
curl.exe -s https://the-cfb-model.vercel.app/results | rg -o "48\.9|49\.9|14\.53"
(empty)

curl.exe -s https://the-cfb-model.vercel.app/results | rg -c "50\.7|51\.3|14\.85|47\.8|1\.35"
1

curl.exe -s https://the-cfb-model.vercel.app/results | rg -o "NOT CURRENTLY FIT TO BET"
NOT CURRENTLY FIT TO BET
NOT CURRENTLY FIT TO BET

curl.exe -s https://the-cfb-model.vercel.app/ | rg -o "FIXTURE"
FIXTURE
FIXTURE
```

New figures absent; old REGRADED_V2 substring still present (count 1);
verdict intact; FIXTURE banner still up. Matches “do not write R2.”

No hyperparameters, ADR 0014 thresholds, or CQR were changed.
23-readout.md untouched.
