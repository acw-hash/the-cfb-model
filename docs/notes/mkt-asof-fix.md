# TASK MKT-ASOF-FIX — Per-game feature as-of + grading mirror-check

**Date:** 2026-08-11  
**Scope:** Feature ladder as-of only (`market_lines.py` + `walkforward.resolve_lines_for_games`).  
**Forbidden (honored):** no tuning; no lockbox; no guard-band widening.

Artifacts: `docs/notes/_artifacts/mkt_asof_fix/`.

---

## STEP 1 — Root cause

**Hypothesis confirmed.** The feature ladder called
`resolve_lines_for_games(..., closing=False)` with a **fixed per-week**
`week_decision_as_of(season, CFBD_week)` and `bound = as_of` only. CFBD week
labels sit systematically ~1 ahead of the Labor-Day `week_of(kickoff)` calendar,
so for most games `kickoff < week_as_of`. Eligible snaps were then
`event_time < week_as_of`, which admits **post-kickoff** archives for that
`game_id` (the V2-BASELINE `feature_event_time_at_or_after_kickoff` leaks).

| season | n_games | n with kickoff &lt; week_as_of |
|---:|---:|---:|
| 2021 | 887 | 849 |
| 2022 | 896 | 854 |
| 2023 | 910 | 868 |
| 2024 | 920 | 874 |
| **2021–2024** | **3613** | **3445** |

Probe: `uv run python scripts/_asof_fix.py step1` →
`docs/notes/_artifacts/mkt_asof_fix/step1_root_cause.json`.

Capture-time semantics (5b-verify §2): staged `event_time` is the archive
**returned** stamp, not the requested decision instant — so a post-kickoff
returned row is a real information-set violation when admitted.

---

## STEP 2 — Fix

**Per-game feature as-of** (`feature_as_of_for_game` in
`src/ncaa_quant/features/market_lines.py`):

1. If harness `week_as_of < kickoff` → use `week_as_of` (Tuesday primary path).
2. Else → latest configured decision point **strictly before** kickoff
   (`tuesday_0600_et` / `saturday_0600_et` / `slot_close` for that CFBD week;
   typically `slot_close` = kick − 5 min when the week Tuesday is after kickoff).
3. No qualifying point → null + `is_missing` (never a later snap; never CFBD on
   the snapshot-backed feature path).

**Hard ladder constraints** in `_resolve_from_snapshots`:

- Features: `event_time <= feature_as_of` **and** `event_time < kickoff`.
- Closing (`closing=True`): `event_time < kickoff` (§2.7 last snap strictly
  before kickoff); CFBD close eval fill unchanged.

**Leaked-rows test:** `tests/unit/test_mkt_asof_fix.py` — the ten STOP
`(feature, game_id)` rows never resolve to the flagged post-kickoff
`snapshot_id`s; they resolve to an earlier pre-kick snap or null.

---

## STEP 3 — Grading ladder mirror-check

`closing=True` already enforced `event_time < kickoff`. Re-resolved all
2021–2024 staged games against Odds snapshots:

| season | n_games | n close at/after kickoff |
|---:|---:|---:|
| 2021 | 887 | **0** |
| 2022 | 896 | **0** |
| 2023 | 910 | **0** |
| 2024 | 920 | **0** |

**Zero statement:** no graded close currently resolves at/after kickoff under
the enforced constraint. **REGRADED_V2 / RERUN_V2 tables stand** (no regrade).

---

## STEP 4 — Re-audit + market-aware re-run

### Information-set / market-feature audit + prophecy

| check | result |
|---|---|
| Market-feature ladder audit (≥20 week-points) | **CLEAN** (leaks=0, feature-rows=4736) |
| Planted-prophecy over `mkt_*` | **passed** (4 features, 0 findings) |

Artifact: `docs/notes/_artifacts/mkt_asof_fix/market_feature_audit.json`
(also synced to `docs/notes/_artifacts/v2_baseline/market_feature_audit.json`
for the force-publish gate).

### `market_aware_reduced_v2` re-run

> **FEATURE_TIME=SLOT_CLOSE_KICK_MINUS_5M** — measures near-close information
> ceiling, not decision-time edge; **not comparable to Tuesday-decision runs**;
> CLV at this feature time is degenerate by construction.
> (CFBD week labels were still Labor-Day-misaligned; MKT-ASOF-FIX fell back to
> `slot_close` ≈ kick−5min for most snapshot games. Superseded for decision-time
> comparisons by WEEK-ALIGN-FIX / `FEATURE_TIME=TUESDAY_DECISION` in
> `docs/notes/week-align-fix.md`.)

- Config: `task23_market_aware_full_reduced_v2`
- Label: `mkt-asof-fix;ensemble_scope=REDUCED_PER_ADR_0013;audit=CLEAN`
- Wall clock: ~2934 s (~48.9 min)
- **Guard disposition: INSIDE_BAND — published** (no ADR 0014 force path)
- Archived predictions:
  `docs/notes/_artifacts/week_align_fix/kick5min_predictions.parquet`
  (and `data/backtests/task23_market_aware_reduced_v2_slot_close/full/`)

| Regime | ATS | n | LL (model) | MAE margin | CRPS margin | 95% bootstrap CI | band |
|---|---:|---:|---:|---:|---:|---|---|
| cfbd_2019 | 47.5% | 743 | 0.961 | 15.11 | 11.08 | [44.5%, 50.8%] | [44.5%, 55.5%] |
| snapshots_2021_2024 | **52.25%** | 3491 | 1.062 | 11.72 | 8.50 | [50.9%, 53.7%] | [47.46%, 52.54%] |

Snapshot ATS sits just under the fair-coin upper edge (52.54%). Original path
`data/backtests/task23_market_aware_reduced_v2/full/predictions.parquet` was the
publish target at the time; kick−5min bytes are preserved under the archive paths
above.

### Within-vintage A3 vs market-aware (first honest comparison)

Both RERUN_V2 codepath; A3 = market features off; market-aware = fixed ladder
at **FEATURE_TIME=SLOT_CLOSE_KICK_MINUS_5M** (not Tuesday decision).

| Regime | market-aware ATS | A3 ATS | Δ (aware − A3) | aware n | A3 n |
|---|---:|---:|---:|---:|---:|
| cfbd_2019 | 47.5% | 50.7% | **−3.23 pp** | 743 | 743 |
| snapshots_2021_2024 | 52.25% | 52.22% | **+0.03 pp** | 3491 | 3491 |

On the snapshot regime that matters for Odds-backed features, market-aware ≈ A3
after the leak fix (+0.03 pp) **at near-close feature time**. The prior
unpublished exception rate (52.71%) was a contaminated/leaking feature path —
not a graded table. For decision-time edge see WEEK-ALIGN-FIX.

---

## Ambiguities / decisions

1. **Fallback when week Tuesday is after kickoff.** Spec asked for “latest
   configured decision point strictly before kickoff.” With
   `odds_historical_decision_points` including `slot_close`, that is usually
   kick−5min for the 3445 misaligned games. Tuesday semantics are preserved
   when `week_as_of < kickoff`.
2. **Audit attribution.** `_snapshot_event_time_for_row` in
   `scripts/_v2_baseline.py` no longer invents an `event_time` when the ladder
   returns null `source_row_id` (would false-flag post-kickoff rows after the
   fix). Required for an honest CLEAN re-audit.
3. **CFBD week vs Labor-Day week** was harness-level calendar debt (noted in
   V2-BASELINE); this task only capped the market ladder. **Repaired in
   WEEK-ALIGN-FIX** (`docs/notes/week-align-fix.md`) — decision-time
   comparisons must use `FEATURE_TIME=TUESDAY_DECISION`, not the kick−5min table
   above.

---

## Acceptance

- [x] Step 1 count: **3445 / 3613** (season, game) with kickoff before week as_of
- [x] Leaked-rows test passing
- [x] Step 3 per-season zeros + v2 tables stand
- [x] Clean audit table + prophecy
- [x] Market-aware v2 numbers + guard **INSIDE_BAND_PUBLISHED**
- [x] `make lint typecheck test`
