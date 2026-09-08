# S16 — Week-1 results readout (public copy)

**Date:** 2026-09-08  
**Source:** live R2 `latest/results_2026.json` (GET; not a local rebuild)  
**Artifact:** `schema_version=1.3.0`, `season=2026`, `published_at=2026-09-08T15:34:11Z`, `fixture` ABSENT

Read-only. Numbers below are unrounded. No interpretation.

## 1. Graded count / week

| Metric | Value |
|--------|-------|
| n graded | 99 |
| week | all 99 have `week=1` |

## 2. Margin interval hit rate

| Metric | Value |
|--------|-------|
| n hits | 65 |
| n with non-null interval (`lo` and `hi` both non-null) | 84 |
| hit rate | 0.7738095238095238 |
| nominal (`margin_interval_nominal`) | 0.8 (84/84) |

## 3. Total interval

`total_interval_lo` / `total_interval_hi` / `total_interval_nominal` / `total_interval_hit` keys PRESENT on graded rows; all 99 values null.

| Metric | Value |
|--------|-------|
| n with non-null total interval | 0 |
| hit rate | n/a (denominator 0) |
| nominal | all null |

## 4. Margin point error (`mu_margin` vs `actual_margin`)

Across all 99 graded (all have both fields non-null):

| Metric | Value |
|--------|-------|
| MAE | 16.285905928544235 |
| median absolute error | 14.629333172796258 |

## 5. Conviction

### `conviction_label` distribution (99)

| n | labels |
|--:|--------|
| 2 | Clear lean Eastern Michigan; Strong lean North Dakota State; Toss-up |
| 1 | 93 other distinct labels |

n distinct labels = 96. No null labels.

### Interval-hit rate by `conviction_tier`

| tier | n | n with margin interval | hits | hit rate |
|------|--:|-----------------------:|-----:|----------|
| strong_lean | 74 | 59 | 45 | 0.7627118644067796 |
| clear_lean | 16 | 16 | 13 | 0.8125 |
| lean | 7 | 7 | 6 | 0.8571428571428571 |
| toss_up | 2 | 2 | 1 | 0.5 |

## 6. Honest-absence rows

| Metric | Value |
|--------|-------|
| n with null `mu_margin` or `sigma_margin` | 0 |
| `null_reason` field | ABSENT (not a key on graded rows) |

## 7. Three largest absolute margin misses

| home | away | actual_margin | mu_margin | interval covered (`margin_interval_hit`) |
|------|------|--------------:|----------:|------------------------------------------|
| Rutgers | Massachusetts | -16 | 41.69856667964856 | False |
| Utah State | Idaho State | -12 | 40.29333107549373 | None |
| LSU | Clemson | 41 | -4.551763060713326 | False |

## 8. Three most accurate

| home | away | actual_margin | mu_margin | interval covered (`margin_interval_hit`) |
|------|------|--------------:|----------:|------------------------------------------|
| Rice | Houston Christian | 28 | 27.90400829813326 | True |
| Washington | Washington State | 14 | 14.221514759360485 | True |
| Kansas | Long Island University | 45 | 45.280792968471715 | None |

## 9. `p_win_home` Brier

| Metric | Value |
|--------|-------|
| n with non-null `p_win_home` | 99 |
| Brier (mean `(p_win_home - p_win_home_realized)^2`) | 0.09818062383087345 |
