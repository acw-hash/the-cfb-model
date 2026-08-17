# TASK 23-REVAL — Honest-Tuesday revalidation (W9-A)

**Date:** 2026-08-17  
**Status:** DECISION — documentation only. No site restamp in this task.  
**ensemble_scope:** `REDUCED_PER_ADR_0013` (not §5.2-complete; ADR 0013).  
**ADR 0014:** member-credibility contract in force (code constants, not re-fit).  
**Vintage:** **W9A_REVAL** / `FEATURE_TIME=TUESDAY_DECISION` — current-code
walk-forward on the kickoff-aligned Tuesday clock (`c6404fc`), graded on
already-fixed closes. This is **not** REGRADED_V2 of champion 3.  
**Why this run exists.** W9-V (`docs/notes/webapp-w9v.md`) showed champion 3's
2024 week-5 `as_of` is **2024-10-01T10:00Z**, after all 56 kickoffs, while the
site labels those rows `FEATURE_TIME=TUESDAY_DECISION`. Week-align is a defect
fix; the published `/results` table measured a leaky information set. W9-M's
registry pickle was a truncated 2019–2024w5 fit. This memo is the full
2019–2024 current-code walk-forward that is both the revalidation and the
deployable champion.

**This memo does not rewrite `docs/notes/23-readout.md`.** That file is FINAL
and remains the record of what the site claimed. A successor task restamps
`build_track_record` from the tables below. Copy them verbatim.

Sources: DESIGN §1.6 / §13 / §16; `docs/notes/webapp-w9v.md`;
`docs/notes/week-align-fix.md`; `docs/notes/mkt-asof-fix.md`; ADR 0005, 0013,
0014; artifacts `docs/notes/_artifacts/webapp-w9a/metrics_summary.json`.

---

## Run identity (paste)

### Fundamental (`task23_fundamental_reduced_v3` / `full`)

```
git_sha=157bc7d34e64462fb00a646d449bccdbf5bd15fe
git_dirty=false
created_at=2026-08-17T20:41:46Z
wall_clock_sec=2408.849
label=W9A-PATH-A;FEATURE_TIME=TUESDAY_DECISION;ensemble_scope=REDUCED_PER_ADR_0013;ADR_0014
seasons_executed=[2019, 2020, 2021, 2022, 2023, 2024]
model_version=production-v0_reduced_v3
quality_gate: passed=true failures=[] n_scored=4286 n_ungradable=90 n_null_mu=0
              zero_mu_rate=0.0 absent_blocks=[[2019,1]]
              null_reason_counts={no_credible_members: 90}
fit_pid=37468 start=2026-08-17T20:01:36Z end=2026-08-17T20:41:48Z
```

90 ungradable rows are ADR 0014 2019 weeks 2–4 (`no_credible_members`). n_null_μ=0.
YAML at fit time was untracked (`git status --untracked-files=no` clean) and is
committed in the same follow-up commit as this memo.

### A2 (`task23_a2_reduced_v2` / `A2_frozen_after_week_1`)

```
git_sha=157bc7d34e64462fb00a646d449bccdbf5bd15fe
git_dirty=false
created_at=2026-08-17T22:11:33Z
wall_clock_sec=5286.791
label=W9A-PATH-A;A2;FEATURE_TIME=TUESDAY_DECISION;ensemble_scope=REDUCED_PER_ADR_0013;ADR_0014
seasons_executed=[2019, 2020, 2021, 2022, 2023, 2024]
model_version=production-v0_a2_reduced_v2
quality_gate: passed=true failures=[] n_scored=4291 n_ungradable=85 n_null_μ=0
              zero_mu_rate=0.0 absent_blocks=[[2019,1]]
              null_reason_counts={no_credible_members: 85}
fit_pid=19860 start=2026-08-17T20:42:45Z end=2026-08-17T22:11:35Z
```

85 ungradable rows are the same 2019 w2–4 OOD family (five of those rows scored
under frozen ratings). Not a new failure mode.

Lockbox: `N_2025 = 0` in both `predictions.parquet` (n=4944, seasons 2019–2024).

---

## 1. THESIS (A2) — two clauses, not one

**Clause A — the rating engine learns in-season (point prediction).**  
Freezing Stage-1 after Week 1 (A2) vs continual updates (fundamental),
**W9A_REVAL / REDUCED**, all-season finite-μ basis:

| Metric | Continual (fund) | A2 frozen | Δ |
|---|---:|---:|---:|
| MAE margin | 14.53 (n=4285) | 15.51 (n=4290) | **+0.97** |
| CRPS margin | 10.02 (n=4175) | 10.75 (n=4175) | **+0.73** |

Weekly MAE curve (fundamental, REDUCED): Week 4 → 15.31, Week 10 → 13.24
(Δ −2.07). Continual Stage-1 updates remain a real MAE/CRPS gain.

**n caveat vs 23-readout.** REGRADED_V2 MAE used n=4375 (n_scored=4376). This
run scores n=4286 and drops 90 ADR 0014 2019 w2–4 OOD rows from the MAE sample.
Do not treat 14.53 vs 14.85 as a same-n comparison.

**Clause B — how much of that learning the close already prices (sides).**  
ATS, **W9A_REVAL / REDUCED**, regimes never pooled:

| Regime | Fund ATS | A2 ATS | Δ (pp) | n (fund / A2) |
|---|---:|---:|---:|---|
| CFBD 2019 | 47.8% | 43.8% | **−4.0** | 657 / 662 |
| Snapshots 2021–24 | 48.9% | 50.9% | **+2.0** | 3496 / 3496 |

MAE/CRPS learning is still not an ATS edge vs the close. Snapshot ATS straddles
50%. 2019 frozen-ratings ATS is worse (small-n, single CFBD-close regime) and
must not be pooled with snapshots.

A2 2019 regrade ATS 43.81% (n=662) sits 0.36 pp below the ATS plausibility
band [44.17%, 55.83%]. The **in-run** A2 walk-forward published (guard passed
on harness closes). The trip is on the post-hoc fixed-close regrade of a stack
that is *supposed* to be worse. Recorded, not fatal, not a license to retune.

---

## 2. MARKET (probabilistic)

**Log-loss vs fair-coin market baseline 0.693** (ATS @ −110/−110 → fair 0.5),
**W9A_REVAL / REDUCED**:

| Run | Vintage | 2019 LL | Snapshots LL |
|---|---|---:|---:|
| fundamental | W9A_REVAL | 1.350 | 0.931 |
| A2 | W9A_REVAL | 1.435 | 0.935 |

The miss is universal on this pair: model ATS log-loss ≈ **0.93–1.44**, all ≫
0.693. Core fundamental band **0.93–1.35**.

**CRPS vs de-vigged market baseline:** **NOT COMPUTED** (same gap as
23-readout §2).

A1 / A3 / A4 / A5 / A6 / market-aware were **not** re-run. They are not
`/results` rows. Do not backfill them from REGRADED_V2 / RERUN_V2.

---

## 3. SIDES / TOTALS

### ATS vs close (REDUCED; regimes never pooled)

| Run | Vintage | Regime | ATS | n | 95% bootstrap CI | 95% naive CI |
|---|---|---|---:|---:|---|---|
| fundamental | W9A_REVAL | 2019 | 47.8% | 657 | [46.9%, 51.1%] | [45.5%, 52.7%] |
| fundamental | W9A_REVAL | snapshots | 48.9% | 3496 | [47.5%, 50.5%] | [47.3%, 50.6%] |
| A2 | W9A_REVAL | 2019 | 43.8% | 662 | [41.7%, 48.2%] | [41.2%, 48.4%] |
| A2 | W9A_REVAL | snapshots | 50.9% | 3496 | [49.8%, 52.2%] | [49.2%, 52.5%] |

**Does any ATS CI exclude 50%?**  
- **Fundamental both regimes:** no — bootstrap CIs include 50%.  
- **A2 2019:** bootstrap [41.7%, 48.2%] excludes 50% **low** (frozen ratings;
  expected worse).  
- **A2 snapshots:** no.

Neither fundamental regime's CI clears a clean §1.6 ≥51.5% claim. Snapshot
headline moved from REGRADED_V2 50.7% toward 48.9% — the leak-removal landing
on sides.

### OU vs close (REDUCED)

| Run | Regime | OU | n | 95% bootstrap CI | 95% naive CI |
|---|---|---:|---:|---|---|
| fundamental | CFBD 2019 | 51.4% | 551 | [46.5%, 55.3%] | [47.2%, 55.5%] |
| fundamental | Snapshots 2021–24 | 51.5% | 3136 | [49.7%, 53.5%] | [49.7%, 53.2%] |
| A2 | CFBD 2019 | 50.5% | 551 | [45.7%, 55.3%] | [46.3%, 54.6%] |
| A2 | Snapshots 2021–24 | 52.3% | 3136 | [51.0%, 53.6%] | [50.5%, 54.0%] |

**Possessions-null caveat (unchanged):** no `is_missing` indicator; values are
NaN; drives staged for 2023 only. OU remains measured without the §4.5 key
totals feature on almost all rows.

---

## 4. MEASUREMENT GAPS — only remaining §1.6 instruments unmeasurable

| Instrument | Status | Successor |
|---|---|---|
| **CLV** (primary §1.6) | **NOT COMPUTED** — runner has no `bets.parquet` / settle path | **CLV/bets runner seam** |
| **Possessions / honest OU** | Structurally null outside 2023 (drives never backfilled) | **Drives backfill 2014–2025** |

---

## 5. §1.6 SCORECARD (REDUCED, W9A_REVAL)

| Criterion | Result | Number vs target | Vintage |
|---|---|---|---|
| Mean same-book CLV > 0, 95% CI excludes 0, n≥300 | **UNMEASURABLE** | NOT COMPUTED (no bets/settle path) | — |
| Fundamental ATS ≥ 51.5% | **MISSED** | Snapshots **48.9%** [47.5%, 50.5%] (n=3496); 2019 **47.8%** [46.9%, 51.1%] (n=657) — neither CI clears 51.5% | W9A_REVAL |
| Fundamental OU ≥ 51.5% | **MISSED / uninterpretable** | Snapshots **51.5%** [49.7%, 53.5%] (CI includes 51.5%); 2019 **51.4%** — possessions structurally null outside partial 2023 | W9A_REVAL |
| Brier / log-loss ≤ market baseline | **MISSED** | ATS LL **0.93–1.35** (fundamental) vs market **0.693** | W9A_REVAL |
| Calibration slope ∈ [0.9, 1.1] | **UNMEASURABLE this session** | Not the restamp target | — |
| Process: zero leakage / pipeline | **Carried, with honest clock** | Week-align is now the harness; champion 3 Labor-Day clock is not this run | — |
| Full §5.2 ensemble | **MISSED by definition** | REDUCED_PER_ADR_0013 | ADR 0013 |

No hyperparameters tuned against these numbers. Lockbox 2025 excluded.

---

## 6. FINDINGS LEDGER (this revalidation only)

A1 / A4 / A5 / A3 / A6 were **not** re-run. 23-readout §6a–§6e remain
historical of REGRADED_V2 / RERUN_V2. They are not `/results` restamp inputs.

### 6a. Clause A still holds on the honest clock

Fundamental MAE 14.53 vs A2 15.51 (Δ +0.97); CRPS 10.02 vs 10.75 (Δ +0.73).
Weekly MAE 15.31 → 13.24 (Δ −2.07). In-season rating updates still buy point
accuracy.

### 6b. MAE/CRPS vs 14.85 / 10.68 — do not over-read an improvement

The pre-run expectation (W9-V / this task brief) was that removing a seven-day
information leak would **worsen** point accuracy. Observed all-season MAE/CRPS
are **lower** than REGRADED_V2. Two facts block reading that as a modeling win:

1. **Sample change.** 90 ADR 0014 2019 w2–4 rows left the MAE sample (4375 →
   4285). Those rows were scored under champion 3.
2. **Week-align rewires every Tuesday**, not only 2024 week 5. Training as_ofs,
   Kalman `prior_as_of`, and retrain banks all move.

Snapshot ATS on **matched n=3496** moved 50.7% → 48.9%. That is the cleaner
leak-removal read on sides. No tuning. No member re-selection.

### 6c. Week-5 causal prefix vs W9-M

This run's 2024 week-5 rows match W9-M
`data/registry/artifacts/v1/week_predictions.parquet` (backed up at
`data/registry_w9m_truncated/`) at **0.0** on `mu_margin` / `sigma_margin` /
`p_ml_home`, 56/56. The truncated W9-M walk-forward is a PIT prefix of this
full run.

---

## 7. VERDICT AND SEQUENCE

### Verdict (one recommendation)

**NOT CURRENTLY FIT TO BET.**

Point-prediction machinery remains **credible** (weekly MAE curve still
declines through mid-season, MAE/CRPS sane, A2 Clause A confirms in-season
learning) but **no edge vs the close is demonstrated** (fundamental snapshot
ATS 48.9% [47.5%, 50.5%]; 2019 47.8% [46.9%, 51.1%]; log-loss loses to 0.693;
CLV unmeasurable) and **two §1.6 instruments remain unmeasurable** (CLV; honest
OU via possessions).

The honest Tuesday clock did **not** move this label off `NOT CURRENTLY FIT TO
BET`. Snapshot ATS receded from 50.7% toward 49%, which strengthens rather than
weakens the no-edge clause. MAE/CRPS looking better than 14.85/10.68 is not a
betting argument (see §6b).

### `/results` restamp (not this task)

A successor task copies §1 / §3 / §5 numbers into `build_track_record` and
`copy.ts`. Until then the site still cites 23-readout / REGRADED_V2.

### Successor sequence (unchanged dependency order)

1. **CLV/bets runner seam.**
2. **Drives backfill 2014–2025** (flag only).
3. **Membership build per ADR 0013**, behind ADR 0014.
4. Plan-estimator recalibration; determinism re-run — optional hygiene.

No hyperparameters, member selection, ensemble weights, ADR 0014 thresholds, or
CQR configuration were changed to chase these numbers.

---

## Weekly MAE curve (fundamental, headline, margin)

| week | n | MAE |
|---:|---:|---:|
| 1 | 545 | 15.71 |
| 2 | 401 | 17.77 |
| 3 | 351 | 17.36 |
| 4 | 322 | 15.31 |
| 5 | 294 | 13.72 |
| 6 | 256 | 12.77 |
| 7 | 265 | 13.28 |
| 8 | 282 | 13.21 |
| 9 | 267 | 13.73 |
| 10 | 283 | 13.24 |
| 11 | 291 | 13.88 |
| 12 | 309 | 13.57 |
| 13 | 322 | 13.22 |
| 14 | 164 | 13.86 |
| 15 | 23 | 15.97 |
| 16 | 1 | 19.23 |

---

## Old vs new — 13 `EXPECTED_METRIC_IDS`

Old = 23-readout / REGRADED_V2 / champion 3 (`build_track_record` literals).
New = this memo / W9A_REVAL. Site still shows **old** until a restamp task.

| id | old | new |
|---|---|---|
| `fund_ats_snapshots` | 50.7% [48.7%, 52.7%] n=3496 REGRADED_V2 | 48.9% [47.5%, 50.5%] n=3496 W9A_REVAL |
| `fund_ats_2019` | 51.3% [48.3%, 54.3%] n=743 | 47.8% [46.9%, 51.1%] n=657 |
| `fund_ou_snapshots` | 52.3% [49.7%, 54.8%] n=3136 | 51.5% [49.7%, 53.5%] n=3136 |
| `fund_ou_2019` | 50.9% [46.6%, 55.4%] n=747 | 51.4% [46.5%, 55.3%] n=551 |
| `mae_margin_fund` | 14.85 n=4375 | 14.53 n=4285 |
| `mae_margin_a2` | 16.45 n=4375 | 15.51 n=4290 |
| `crps_margin_fund` | 10.68 n=4375 | 10.02 n=4175 |
| `crps_margin_a2` | 11.87 n=4375 | 10.75 n=4175 |
| `ats_logloss_band` | 0.82–1.04 vs 0.693 | 0.93–1.35 vs 0.693 (fundamental) |
| `scorecard_clv` | UNMEASURABLE | UNMEASURABLE |
| `scorecard_fund_ats` | MISSED (50.7% / 51.3%) | MISSED (48.9% / 47.8%) |
| `scorecard_fund_ou` | MISSED / uninterpretable | MISSED / uninterpretable |
| `scorecard_logloss` | MISSED 0.82–1.04 vs 0.693 | MISSED 0.93–1.35 vs 0.693 |

**Champion / registry.** Serialized from this fundamental run; promoted
`force=False`, `manual_approve=True`, registry **v2** champion. W9-M truncated
v1 preserved at `data/registry_w9m_truncated/` and archived in-place as v1.

**Standing labels.** REDUCED_PER_ADR_0013; regimes never pooled; lockbox 2025
excluded; no hyperparameter / threshold / feature fix against these numbers.
