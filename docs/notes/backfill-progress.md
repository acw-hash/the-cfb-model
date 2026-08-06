# Backfill progress (Task 23-FIX-BACKFILL)

One line per completed season after core datasets land.

```
season | dataset row counts | schema pass/fail | null-rate anomalies | credits/calls consumed | cumulative wall clock
```

---

## PRE-FLIGHT (2026-08-05)

### 1. Quota tier / window / reset

| Field | Value |
|---|---|
| Tier | **Free** (`patronLevel=0`) |
| Monthly limit | **1,000** calls/month |
| Remaining (post pre-flight) | **0** |
| Used | **1,000** |
| Window | **Monthly** (not hourly, not daily) |
| `resetAt` | **2026-09-01T00:00:00Z** |
| Header | `x-calllimit-remaining` only (no reset header on GETs) |
| `/info` | Does **not** consume quota; returns tier + `resetAt` |

**Verdict on the probe's "~200/window":** that was **leftover monthly budget**
(~213 remaining at probe start → burned ~205 → reserve trip), **not** a 200/day
or 200/hour tier. Non-recovery over 50+ minutes is expected — the window is
**calendar-month**, confirmed by `resetAt`.

### 2. Paid-tier purchase numbers (do not act — your call)

Cold-start estimate was ~1,380 GETs. **Actual remaining work is ~63 season-grain
partitions** (week-grain games/plays/advanced/lines already staged for 2014–2025).

| Tier | Cost | Calls/mo | Fits remaining ~63? | Fits cold 1,380? | Calendar if starting now |
|---|---:|---:|---|---|---|
| Free (current) | $0 | 1,000 | After 2026-09-01 reset, yes | No (1,000 < 1,380) | **Blocked until Sept 1** (0 remaining) |
| Academic (.edu) | $0 | 3,000 | Yes | Yes | After entitlement |
| **Tier 1** | **$1/mo** | **5,000** | **Yes** | **Yes** | **Afternoon** |
| Tier 2 | $5/mo | 30,000 | Yes | Yes | Afternoon |
| Tier 3+ | $10+/mo | 75k+ | Yes | Yes | Overkill for this backfill |

**Recommendation surface only:** Tier 1 ($1) turns a ~27-day free-tier wait into
an afternoon for the remaining ~63 GETs. Free reset on Sept 1 also covers the
remaining season-grain work (but not a full cold re-pull).

### 3. 2020 / 2021 / 2025 season-grain gap re-verify

| Check | Result |
|---|---|
| Staged week-grain 2020/2021/2025 | **Present** (games/plays/advanced/lines complete) |
| Live `GET /roster?year=2025` | **200**, ~8.9 MB payload — **data exists at CFBD** |
| Live `GET /player/portal?year=2021` | **200**, **1,770 rows** — **data exists at CFBD** |
| Staged season-grain those years | Still MISSING on disk |

**Conclusion:** matrix `MISSING` cells for 2020/2021/2025 roster-family were
**probe quota abort**, not source absence. Do **not** exclude those seasons from
season-grain backfill.

*(Gap re-verify consumed the last 2 monthly calls; remaining is now 0.)*

### Blocker

CFBD ingest cannot proceed until **Tier upgrade** or **2026-09-01 reset**.
Driver supports `--wait-for-quota` (sleep to `resetAt`) and `--exit-on-quota`.

---

## Season lines

*(appended below as seasons complete)*

2024 | advanced_box=3201 coaches=0 games=920 lines_historical=5520 plays=162726 portal=0 recruiting=0 returning_production=0 rosters=0 talent=0 teams=679 venues=0 | schema=fail | anomalies=plays.wp null~=1.0 (source) | calls~=0 | cumulative=0:00:28 | quality=fail (24 hard: mostly referential_games_venue_id — no venues partition); weather skipped (no venues)
2023 | advanced_box=2931 coaches=143 games=910 lines_historical=4651 plays=159011 portal=2502 recruiting=177 returning_production=131 rosters=22243 talent=239 teams=672 venues=844 | schema=fail | anomalies=plays.wp null~=1.0 (source); returning.defense_pct null~=1.0; recruiting.blue_chip_ratio null~=1.0 | calls~=0 | cumulative=0:00:29 | quality=fail (14 hard: drives pbp_drive_points_reconcile); weather already present (910 skipped)

### Quality findings (not schema edits)

- **2024:** almost all games weeks fail `referential_games_venue_id` because `venues` season partition is missing. Clears once venues season-grain is fetched. Secondary: a few `play_sequence_monotone_within_drive` and `completeness_advanced_box_vs_games` (1 game) — reported, not loosened.
- **2023:** drives-only `pbp_drive_points_reconcile` across most weeks (drive points vs final disagree >8). Core games/plays/advanced mostly pass. Known-class source/staging tension — finding only.

### Filter pass (Task 14 inputs: games + advanced)

Ran on staged **2014–2025** contiguous range (season-grain roster family still incomplete):

| Metric | Value |
|---|---|
| Games | 10,372 |
| Observations | 10,316 |
| Wall clock | **2.89 s** |
| Innovation health | mean_z=0.007, var_z=0.54, n=41,264, misspecified=false |
| Artifact | `data/tmp/backfill_23_filter/summary.json` |

### Resume commands (after Tier 1 upgrade or 2026-09-01)

```bash
# Sleep to resetAt, then continue season-grain in value order
uv run python scripts/backfill_23_cfbd.py --wait-for-quota

# Or exit fast if still empty (purchase check)
uv run python scripts/backfill_23_cfbd.py --exit-on-quota --preflight-only
```

Checkpoint: `data/tmp/backfill_23_checkpoint.json` (64 units already marked).
2024 | advanced_box=3201 coaches=152 games=920 lines_historical=5520 plays=162726 portal=3378 recruiting=194 returning_production=133 rosters=22687 talent=134 teams=679 venues=844 | schema=fail | anomalies=plays.wp null~=1.0 (source); returning.defense_pct null~=1.0; recruiting.blue_chip_ratio null~=1.0 | calls~=7 | cumulative=0:00:34 | quality=fail; weather rows_written=40 skipped=0 gaps=880
2025 | advanced_box=3302 coaches=161 games=934 lines_historical=5872 plays=166057 portal=4499 recruiting=230 returning_production=134 rosters=29907 talent=134 teams=681 venues=844 | schema=fail | anomalies=plays.wp null~=1.0 (source); returning.defense_pct null~=1.0; recruiting.blue_chip_ratio null~=1.0 | calls~=7 | cumulative=0:01:04 | quality=fail; weather BLOCKED missing coords: Missing lat/lon for FBS-hosting venue(s); add to configs/venues_overrides.yaml:
  - venue_id=5455 name='Ford Center At The Star' city='Frisco' state='TX'
2022 | advanced_box=2901 coaches=147 games=896 lines_historical=5169 plays=160327 portal=2273 recruiting=184 returning_production=130 rosters=30194 talent=232 teams=672 venues=844 | schema=fail | anomalies=plays.wp null~=1.0 (source); returning.defense_pct null~=1.0; recruiting.blue_chip_ratio null~=1.0 | calls~=7 | cumulative=0:01:35 | quality=fail; weather rows_written=43 skipped=0 gaps=853
2021 | advanced_box=1774 coaches=152 games=887 lines_historical=4420 plays=158634 portal=1770 recruiting=191 returning_production=128 rosters=18698 talent=223 teams=670 venues=844 | schema=fail | anomalies=plays.wp null~=1.0 (source); returning.defense_pct null~=1.0; recruiting.blue_chip_ratio null~=1.0 | calls~=7 | cumulative=0:02:06 | quality=fail; weather rows_written=45 skipped=0 gaps=842
2019 | advanced_box=1774 coaches=134 games=888 lines_historical=3506 plays=159915 portal=0 recruiting=223 returning_production=130 rosters=18794 talent=231 teams=682 venues=844 | schema=fail | anomalies=plays.wp null~=1.0 (source); returning.defense_pct null~=1.0; recruiting.blue_chip_ratio null~=1.0 | calls~=2 | cumulative=0:02:34 | quality=fail; weather rows_written=34 skipped=0 gaps=854
2023 | advanced_box=2931 coaches=143 games=910 lines_historical=4651 plays=159011 portal=2502 recruiting=177 returning_production=131 rosters=22243 talent=239 teams=672 venues=844 | schema=fail | anomalies=plays.wp null~=1.0 (source); returning.defense_pct null~=1.0; recruiting.blue_chip_ratio null~=1.0 | calls~=0 | cumulative=0:03:01 | quality=fail; weather rows_written=0 skipped=910 gaps=0
FINDING | SCHEMA_FAIL 2018/recruiting: {
    "DATA": {
        "DATAFRAME_CHECK": [
            {
                "schema": "RecruitingSchema",
                "column": "points",
                "check": "greater_than_or_equal_to(0.0)",
                "error": "Column 'points' failed element-wise validator
2018 | advanced_box=1762 coaches=139 games=884 lines_historical=3034 plays=160512 portal=0 recruiting=0 returning_production=130 rosters=17940 talent=237 teams=687 venues=844 | schema=fail | anomalies=plays.wp null~=1.0 (source); returning.defense_pct null~=1.0 | calls~=4 | cumulative=0:00:30 | quality=fail; weather BLOCKED missing coords: Missing timezone for FBS-hosting venue(s); add to configs/venues_overrides.yaml:
2017 | advanced_box=1738 coaches=139 games=874 lines_historical=2400 plays=158574 portal=0 recruiting=233 returning_production=128 rosters=17907 talent=157 teams=687 venues=844 | schema=fail | anomalies=plays.wp null~=1.0 (source); returning.defense_pct null~=1.0; recruiting.blue_chip_ratio null~=1.0 | calls~=8 | cumulative=0:01:01 | quality=fail; weather BLOCKED missing coords: Missing timezone for FBS-hosting venue(s); add to configs/venues_overrides.yaml:
FINDING | SCHEMA_FAIL 2016/recruiting: {
    "DATA": {
        "DATAFRAME_CHECK": [
            {
                "schema": "RecruitingSchema",
                "column": "points",
                "check": "greater_than_or_equal_to(0.0)",
                "error": "Column 'points' failed element-wise validator
2016 | advanced_box=1714 coaches=134 games=873 lines_historical=2347 plays=158518 portal=0 recruiting=0 returning_production=128 rosters=18017 talent=237 teams=687 venues=844 | schema=fail | anomalies=plays.wp null~=1.0 (source); returning.defense_pct null~=1.0 | calls~=8 | cumulative=0:01:31 | quality=fail; weather BLOCKED missing coords: Missing timezone for FBS-hosting venue(s); add to configs/venues_overrides.yaml:
FINDING | SCHEMA_FAIL 2015/recruiting: {
    "DATA": {
        "DATAFRAME_CHECK": [
            {
                "schema": "RecruitingSchema",
                "column": "points",
                "check": "greater_than_or_equal_to(0.0)",
                "error": "Column 'points' failed element-wise validator
2015 | advanced_box=1726 coaches=132 games=870 lines_historical=2320 plays=160180 portal=0 recruiting=0 returning_production=127 rosters=16393 talent=231 teams=688 venues=844 | schema=fail | anomalies=plays.wp null~=1.0 (source); returning.defense_pct null~=1.0 | calls~=8 | cumulative=0:02:01 | quality=fail; weather BLOCKED missing coords: Missing timezone for FBS-hosting venue(s); add to configs/venues_overrides.yaml:
FINDING | SCHEMA_FAIL 2014/recruiting: {
    "DATA": {
        "DATAFRAME_CHECK": [
            {
                "schema": "RecruitingSchema",
                "column": "points",
                "check": "greater_than_or_equal_to(0.0)",
                "error": "Column 'points' failed element-wise validator
2014 | advanced_box=1698 coaches=132 games=868 lines_historical=2303 plays=158315 portal=0 recruiting=0 returning_production=125 rosters=16178 talent=0 teams=689 venues=844 | schema=fail | anomalies=plays.wp null~=1.0 (source); returning.defense_pct null~=1.0 | calls~=6 | cumulative=0:02:31 | quality=fail; weather BLOCKED missing coords: Missing lat/lon for FBS-hosting venue(s); add to configs/venues_overrides.yaml:
2020 | advanced_box=1136 coaches=137 games=568 lines_historical=2752 plays=102809 portal=0 recruiting=206 returning_production=130 rosters=16458 talent=218 teams=671 venues=844 | schema=pass | anomalies=plays.wp null~=1.0 (source); returning.defense_pct null~=1.0; recruiting.blue_chip_ratio null~=1.0 | calls~=8 | cumulative=0:06:49 | quality=pass; weather rows_written=568 skipped=0 gaps=0
### Tier 1 completion (2026-08-05)

- Tier confirmed: Tier 1, 5000/mo; used 77; remaining 4923.
- Season-grain landed for all 2014-2025 except recruiting schema-blocked years (2014,2015,2016,2018: points=-0.04).
- 2020 was missing from value-order list initially; added and completed (quality PASS 65/65; weather 568/568).
- Filter re-run 2014-2025: 10316 obs, 2.61s, innovations healthy.
- See docs/notes/backfill.md for coverage matrix and STOP items.

