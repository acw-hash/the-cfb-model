# 23-READOUT-ADDENDUM STOP — 2019 feature-source violation

**Disposition:** STOP. Do not close the measurement campaign. Do not record
2019 market-aware ATS 45.63% [42.9%, 48.6%] vs fundamental 51.3% as
NaN-aware-split noise.

**Vintage / scope / feature time:** RERUN_V2_WEEK_ALIGN /
`REDUCED_PER_ADR_0013` / `FEATURE_TIME=TUESDAY_DECISION`  
**Run:** `task23_market_aware_reduced_v2_tue`  
**Check:** `scripts/_readout_addendum_check.py` →
`docs/notes/_artifacts/readout_addendum/2019_mkt_equivalence.json`

## Named violation

On every 2019 prediction row the Tuesday-decision market-aware stack is
`market_feature_source=snapshots`. Odds snapshots for 2019: **0 rows**.
Reconstructed `mkt_*` via `ProductionFeatureProvider._resolve_market_lines`:

| check | n |
|---|---:|
| 2019 prediction rows | 763 |
| `mkt_*` null + `mkt_is_missing` | **6** |
| non-null `mkt_*` (violations) | **757** |
| `line_source=cfbd_close` | **757** |
| `market_provenance` labeled `snapshots` | **757** |

Sample: `game_id=401110773` week=2 `mkt_spread=-54.75` `mkt_total=65.0`
`mkt_n_books=4` `mkt_is_missing=0` `line_source=cfbd_close`
`market_provenance=snapshots`.

## Mechanism

1. `resolve_lines_for_games(..., closing=False)` uses CFBD open/else **close**
   for `season < 2021` even when the stack is snapshots-source.
2. 2019 CFBD opens are mostly absent → features resolve to **`cfbd_close`**
   at Tuesday decision time (close-as-feature; not a decision-time open).
3. `ProductionFeatureProvider._resolve_market_lines` relabels any non-null
   `line_source` as `market_provenance="snapshots"`.

This is a 2019 feature-source violation (and a PIT defect: Tuesday features
carry the CFBD close). It is **not** fit-path variance from NaN-aware splits.

## Blast radius

- Tuesday-decision market-aware **2019 ATS / LL / MAE** (45.63% / 0.835 /
  14.59) — contaminated by CFBD-close features. Snapshot 2021–2024 table
  is out of this blast radius (Odds-backed).
- A3 (market off) and fundamental omit `mkt_*` — out of blast radius.
- A6 (`cfbd_open_close`) is the ablation that is *allowed* to use CFBD as
  features; this run is not A6.
- Planned addendum noise claim for 45.63% vs 51.3% (n=743) is **refused**.

## Fix scope (separate session)

Do **not** fix here. Snapshot-source `mkt_*` for 2019 must be null +
`is_missing` (never CFBD; never relabel close as snapshots). Re-run the
check, then resume campaign close.

Guard band: not widened. Lockbox: not read.
