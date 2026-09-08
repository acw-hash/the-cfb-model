# S18 — Week-1 error structure

**Date:** 2026-09-08  
**Mode:** READ-ONLY  
**Sources:** live R2 `latest/results_2026.json`; staged `games`/`teams` season=2026;
`data/artifacts/state_space/filter_history.parquet` for Kalman as-of.

Signed error = `mu_margin − actual_margin` (home−away).

Classification: staged `teams.classification` (`fbs` / `fcs`). Every non-FBS
side in the 99 is `fcs` (no ii/iii in graded slate).

## 1. MAE / median AE by FBS–FCS

| bucket | n | MAE | median AE |
|--------|--:|-----|----------:|
| both FBS | 51 | 16.780860320826005 | 15.403863196523453 |
| any FCS opponent | 48 | 15.760016886744845 | 14.43632047513606 |
| overall | 99 | 16.285905928544235 | 14.629333172796258 |

## 2. Interval hit rate (same buckets)

| bucket | hits / n with interval | hit rate |
|--------|-----------------------:|----------|
| both FBS | 41 / 51 | 0.803921568627451 |
| any FCS | 24 / 33 | 0.7272727272727273 |
| overall | 65 / 84 | 0.7738095238095238 |

(15 graded rows have null margin intervals — all in the any-FCS bucket.)

## 3. Top 10 absolute errors

| rank | home (class) | away (class) | actual | mu_margin | abs err |
|-----:|--------------|--------------|-------:|----------:|--------:|
| 1 | Rutgers (fbs) | Massachusetts (fbs) | −16 | 41.69856667964856 | 57.69856667964856 |
| 2 | Utah State (fbs) | Idaho State (fcs) | −12 | 40.29333107549373 | 52.29333107549373 |
| 3 | LSU (fbs) | Clemson (fbs) | 41 | −4.551763060713326 | 45.55176306071333 |
| 4 | Bowling Green (fbs) | Tarleton State (fcs) | −7 | 32.7303623360314 | 39.7303623360314 |
| 5 | South Carolina (fbs) | Kent State (fbs) | 57 | 18.0099577575607 | 38.9900422424393 |
| 6 | Louisiana Tech (fbs) | Northwestern State (fcs) | 74 | 37.35058453596463 | 36.64941546403537 |
| 7 | Nevada (fbs) | Western Kentucky (fbs) | 35 | 0.050909968950003825 | 34.94909003105 |
| 8 | Mississippi State (fbs) | UL Monroe (fbs) | 49 | 14.493199181734326 | 34.506800818265674 |
| 9 | Charlotte (fbs) | The Citadel (fcs) | −2 | 31.828524947262764 | 33.828524947262764 |
| 10 | Texas (fbs) | Texas State (fbs) | 52 | 18.537007285187983 | 33.46299271481202 |

## 4. Signed mean error (bias)

| bucket | mean(`mu − actual`) |
|--------|--------------------:|
| overall | −1.3498060244232966 |
| both FBS | −4.851493760526906 |
| any FCS | 2.37073719518679 |

Near zero overall → approximately symmetric in the home−away frame (slight
home-margin underforecast). Not a large favorite-side bias:
mean favorite-margin error (`sign`-aligned) = −0.4254386255113155 overall.

## 5. Kalman at graded_from (`as_of=2026-08-27T11:33:22Z`)

Source: last `kind=postgame` row in `filter_history` with `event_time < as_of`.
Prior 2026 games: staged completed games with `event_time < as_of` → **0** for
all four (earliest 2026 `event_time` = 2026-08-29T21:00:00Z).

| team | team_id | class | n prior 2026 obs | off_epa | def_epa | st_value | pace | last event_time |
|------|--------:|-------|-----------------:|--------:|--------:|---------:|-----:|-----------------|
| Rutgers | 164 | fbs | 0 | 0.07563493261113724 | −0.03488332004132936 | 0.0 | −0.11962824166128522 | 2025-11-30T00:00:00Z (2025 w14) |
| Massachusetts | 113 | fbs | 0 | −0.2125865754949425 | −0.32619044249018536 | 0.0 | −0.44620316268965204 | 2025-11-26T01:00:00Z (2025 w14) |
| Utah State | 328 | fbs | 0 | −0.013092777071743443 | −0.049144270697096065 | 0.0 | −0.062438322658080386 | 2025-12-22T22:30:00Z (hist week=1) |
| Idaho State | 304 | fcs | 0 | ABSENT from `filter_history` (FCS pooled; no team row) | | | | |

## 6. Early kickoffs vs later

All 99 share the same `graded_from`: `2026-08-27T11:33:22Z` / `daily_refresh`.

Kickoff UTC date counts: Aug 29:7, Aug 30:1, Sep 3:6, Sep 4:8, Sep 5:60,
Sep 6:16, Sep 7:1. **n=91 on Sept 5 is not in the artifact** (only 60 UTC /
68 ET).

| bucket | n | MAE | median AE |
|--------|--:|-----|----------:|
| Aug 29–30 (UTC or ET) | 8 | 10.240597206311854 | 7.670048923039957 |
| Sept 5 UTC only | 60 | 17.80564888809535 | 15.225553854120378 |
| Sept 5 ET | 68 | 17.764567871252286 | 15.442350689309192 |
| complement of Aug 29–30 (the other 91) | 91 | 16.81736164038884 | 14.719920630730606 |
