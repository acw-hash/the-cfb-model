# Task 23-FIX-DATA — Pre-backfill CFBD / Odds / weather probe

Read-only probe before any 2014–2025 backfill. Scratch under `data/_probe/`
(gitignored). No writes to `data/staged/` or `data/features/`. No client/schema
edits. Drivers: `scripts/_probe_23_fix_data.py`,
`scripts/_fill_matrix_from_staged.py`, `scripts/_probe_23_fix_data_resume.py`.

---

## Verdict

**CONDITIONAL GO** for the 2014–2025 **core** CFBD backfill (games, plays,
drives, advanced box, lines, teams, venues).

**NO-GO / hold** on treating the season×dataset matrix as fully live-complete
for roster / returning / talent / recruiting / coaches / portal on **2020,
2021, 2025** — the CFBD rate budget on this key exhausted mid-probe (~200
calls; `x-ratelimit-remaining` fell to single digits and did not recover over
~50 minutes). Those cells are `MISSING` in the live sense; 2023 season-grain
was confirmed from already-staged partitions after the schema repair.

**Odds historical (Task 5B):** unit cost **measured = 30** (matches config).
Full 2021–2025 ladder = **54,090** credits — still above the **16,000**
ceiling. Do not purchase the baseline. Reduced one-season scopes fit.

**Do not exclude seasons from the CFBD core backfill** except apply §7.2 item 5
to **2020** (continuity only, not headline metrics). Usable training for
EPA-from-advanced-box starts **2014**. Play-level **WP is absent at CFBD
source** for every probed season — A5 / WP-GT is a source gap, not only a
staging gap.

---

## Part A — Season × dataset matrix

Flag legend: **GO** / **DEGRADED** / **MISSING**.

Sources: **live** = CFBD API this probe; **staged** = existing partitions
(schema-validated in memory only); season-grain gaps after budget abort.

| Dataset | 2014 | 2016 | 2019 | 2020 | 2021 | 2023 | 2025 |
|---|---|---|---|---|---|---|---|
| games | GO (868) | GO (873) | GO (888) | GO (568) | GO (887†) | GO (910†) | GO (934†) |
| plays | DEGRADED (158k) | DEGRADED (159k) | DEGRADED (160k) | DEGRADED (103k†) | DEGRADED (159k†) | DEGRADED (159k†) | DEGRADED (166k†) |
| advanced | GO (1698) | GO (1714) | GO (1774) | GO (1136†) | GO (1774†) | GO (2931†) | GO (3302†) |
| lines | GO (2303) | GO (2347) | GO (3506) | GO (2752†) | GO (4420†) | GO (4651†) | GO (5872†) |
| venues | GO (844) | GO | GO | GO | GO | GO | GO |
| roster | GO (16.2k) | GO (18.0k) | GO (18.8k) | MISSING‡ | MISSING‡ | GO (22.2k†) | MISSING‡ |
| returning | DEGRADED | DEGRADED | DEGRADED | MISSING‡ | MISSING‡ | DEGRADED† | MISSING‡ |
| talent | GO (0; era) | GO (237) | GO (231) | MISSING‡ | MISSING‡ | GO (239†) | MISSING‡ |
| recruiting | DEGRADED | DEGRADED | DEGRADED | MISSING‡ | MISSING‡ | DEGRADED† | MISSING‡ |
| portal | GO (0; era) | GO (0; era) | GO (0; era) | GO (0; era) | MISSING‡ | DEGRADED (2502†) | MISSING‡ |
| coaches | GO (132) | GO (134) | GO (134) | MISSING‡ | MISSING‡ | GO (143†) | MISSING‡ |

† Count/schema from staged partition (live week-grain incomplete after budget).
‡ Live season-grain not fetched — CFBD remaining &lt; reserve (10); not staged.

### Auth

- `CFBD_API_KEY` present; authenticated `fetch_teams(2023)` → 672 rows. Key never printed.
- Observed remaining started at **213**, ended at **≤9** after ~205 GETs; client
  reserve **10** correctly aborted further calls (`RateLimitBudgetError`).

### Game-count reconcile (Task 5 band ~800–900 FBS)

| Season | Games | Band | Note |
|---:|---:|---|---|
| 2014 | 868 | in band | |
| 2016 | 873 | in band | |
| 2019 | 888 | in band | |
| 2020 | 568 | **below** | COVID — see Part B |
| 2021 | 887 | in band | staged |
| 2023 | 910 | top of band | matches Task 5 (`docs/notes/05.md`); FBS-vs-FCS via `classification=fbs` |
| 2025 | 934 | slightly above | complete season; FBS+FCS hosts |

### Why plays are DEGRADED every season

- `wp` null rate **1.0** (source — see Part B).
- `epa` null rate **~0.22–0.25** (ppa present on scrimmage plays; never hits
  90% “complete” threshold).
- `success` null rate **1.0** (not in raw CFBD play keys sampled).
- Schema validate: **pass**.

### Other DEGRADED cells

- **returning:** `defense_pct` null rate **1.0** all verified seasons;
  `offense_pct` / `overall_pct` populated. Known CFBD shape (Task 5 notes).
- **recruiting:** `blue_chip_ratio` null rate **1.0**; rank/points present.
- **portal 2023:** `rating` null rate **~0.64** (many unrated transfers);
  schema pass; negatives on `portal_net_rating` accepted (min −10.68).

---

## Part B — Era boundaries

### 5. EPA / WP first complete season (threshold = 90% non-null)

| Field | First complete season | Evidence |
|---|---|---|
| Advanced `offense_epa` | **2014** | 100% non-null from 2014 onward |
| Advanced `defense_epa` | **2014** | 100% non-null from 2014 onward |
| Play `epa` (ppa) | **none at 90%** | ~77% non-null every season — usable, not “complete” |
| Play `wp` | **none** | 0% non-null every probed season |

§3.1 “PBP quality good 2014+” holds for **advanced-box EPA** and play **ppa**.
It does **not** hold for play-level win probability. Training that needs play
EPA can start 2014 with the ~23% nulls; anything requiring WP cannot.

### 6. Garbage-time inputs — source vs staging

Raw CFBD `/plays` sample keys (2019 week 1): `ppa`, `period`, `offenseScore`,
`defenseScore`, `clock`, … — **no** `homeWinProb` / `wp` / `winProbability`.

| Input | At CFBD source? | On staged `plays`? |
|---|---|---|
| Win probability | **No** (all probed seasons) | column exists, always null |
| Period | Yes | Yes |
| Score differential | Yes (`offenseScore`/`defenseScore`) | **Dropped** by `PlaysSchema` |
| Time remaining / clock | Yes (`clock`) | **Dropped** by `PlaysSchema` |

**Conclusion:** A5 was NOT RUN because staged plays have no effective
`garbage_time` flags. That is **both**:

1. a **source gap** for the primary WP rule (`wp_before` never present), and
2. a **staging gap** for the Connelly fallback (scores/clock not staged;
   flags never materialised).

Connelly GT is only recoverable via `plays_from_cfbd_raw_json` on raw archives.

### 7. Season 2020 shape

| Metric | Value |
|---|---|
| FBS games | **568** (completed 568) |
| Weeks | 1–16 (no week 0) |
| Conference-game rate | **~78.9%** |
| Plays (full staged) | 102,809 |

Materially fewer games than 800–900; heavily conference-scheduled. Aligns with
§7.2 item 5: include for Stage-1 continuity, exclude from mapping loss and
headline metrics — applied knowingly, not by assumption.

### 8. Portal era (§3.4)

- Pre-2021 (2014/2016/2019/2020): **0 rows**; `portal_net_rating` → **NaN**,
  never zero (`never_zero_pre_2021: true`).
- 2021 / 2025: live portal fetch **not completed** (rate budget) — matrix
  MISSING; do not invent zeros.
- 2023 staged: 2502 rows; sampled `portal_net_rating` includes **negatives**
  (19/32 finite nets negative; min −10.68). Repaired schema path accepts them.

### 9. Roster / returning

- Live **GO** for roster schema on 2014/2016/2019 including **negative
  `athlete_id`** counts (e.g. 6779 in 2014) — opaque-id repair holds.
- Returning validates with negatives allowed; **`defense_pct` unusable**
  (always null) for every verified season — use offense/overall +
  null-with-indicator for defense.
- 2020/2021/2025 roster+returning: **not live-confirmed** this probe (budget).
  2023 confirmed via staged (22,243 roster / 131 returning).

---

## Part C — Odds API

### Credits spent this task

| Call | Credits |
|---|---:|
| `GET /v4/sports` (quota probe) | **0** (`x-requests-last=0`) |
| One `fetch_historical_odds` | **30** (`x-requests-last=30`) |
| **Total** | **30 / 50 cap** |

### 10. Quota reconciliation

| Figure | Role | Source |
|---:|---|---|
| **20,000** | Monthly plan quota | DESIGN §3.2 / TASKS.md only — **not in config** |
| **16,000** | Historical spend **ceiling** | `configs/data.yaml` → `odds_historical_credit_ceiling` |
| Live `x-requests-remaining` | **19,973** (pre-historical) | sports-list headers |
| Live `x-requests-used` | **27** (pre-historical) | sports-list headers |
| Notes “24 used” | **STALE** | `docs/notes/23.md` — live was 27 before this probe’s historical call; **57** after (+30) |

**Wrong number:** treating **16,000** as the monthly budget. It is a
configured **ceiling guard** for historical backfill. The plan is **20,000**/mo
in docs only; every spend guard that reads config enforces **16k**, not 20k.
That affects every other Odds spend gate in the project.

`OddsAPIClient` has **no** sports-list helper — probe used raw `httpx` against
`/v4/sports` without editing the client (noted as a small API-surface gap).

### 11–12. Historical entitlement + measured unit cost

- Historical call **succeeded** (plan includes historical). Task 5B is not
  blocked by tier entitlement.
- Requested `2024-10-12T16:00:00Z` → envelope `timestamp=2024-10-12T15:55:38Z`
  with `previous`/`next` 5-minute neighbors.
- **Measured unit cost = 30** (= config `odds_historical_credits_per_call`).

### 13. 5B ladder (measured 30 × request count)

Estimator agrees with measured unit cost × call count. Baseline **1803 × 30 =
54,090** — matches notes; **not an estimator bug**.

| Scope | Requests | Credits @30 | Fits 16k ceiling? | Fits 20k plan? |
|---|---:|---:|---|---|
| 2021–2025, all DPs (baseline) | 1803 | **54,090** | No | No |
| 2024, `slot_close` only | 369 | **11,070** | Yes | Yes |
| 2024–2025, `slot_close` only | 750 | **22,500** | No | No |
| 2024–2025, tuesday + slot_close | 782 | **23,460** | No | No |

**Book count does not reduce credits** (meter = `10 × markets × regions`).
“One book” scopes cost the same as all books under the current estimator.

Rungs that fit remaining headroom under the **16k ceiling** (after live
reserve): one season `slot_close` (~11k). Prefer that for bet-layer validation.

### 14. Historical payload vs bet layer

Suitable: snapshot timestamp ≠ request time; prev/next navigation; 4 books in
sample; both sides’ American prices on markets. **Not** close-only. Can support
bet-time ≠ close for CLV if backfilled at decision points.

### 15. Live 2026 capture (Task 4)

| Metric | Value |
|---|---|
| Rows | **11,322** (`snapshot_source=live`) |
| `captured_at` range | 2026-08-04 15:51Z → 2026-08-05 16:00Z |
| Unique capture minutes | 7 |

Capture is running; history is still only ~1 day deep.

---

## Part D — Weather / venues

- Open-Meteo archive `https://archive-api.open-meteo.com/v1/archive` reachable;
  2014-09-06 hourly probe at Michigan Stadium coords → 24 hours temp/wind/precip.
- Venue lat/lon: CFBD `/venues` (844 rows) + staged venues. Missing coords in
  probed game sets: **venue_id 4737** (2014), **venue_id 5455** (2025). All other
  probed-season game venues had coordinates.

---

## Part E — Client load behavior / wall clock

### Retry / idempotency

Re-ran (pass):

- `tests/unit/test_cfbd.py::test_retry_on_500_then_success`
- `tests/unit/test_cfbd.py::test_resumability_skips_completed_partition`

Finding (no client edit): **429 is retryable** in `_is_retryable` but there is
**no dedicated 429→success unit test** (only 500). Budget guard stops before
call when remaining &lt; reserve — observed live.

### Full 2014–2025 CFBD backfill wall clock

| Estimate | Value |
|---|---|
| Approx GETs (6 week-grain × ~18 weeks × 12 seasons + season-grain) | **~1,380** |
| Config QPS | 2.0 → ~0.2 h pure throttle time |
| **Observed quota window** | ~200 calls then remaining &lt; reserve 10 |
| Windows needed @200/window | **~7** |
| Calendar time | **~7 hours if hourly reset; appears closer to daily on this key** (remaining did not recover in 50+ minutes) |

**Plan the backfill as quota-window-bound**, not 2 QPS wall-clock. Resume/
idempotent partitions make multi-window runs safe. Consider CFBD Patreon tier
if daily ~200 is the real ceiling.

---

## GO / NO-GO summary

| Decision | Call |
|---|---|
| Core CFBD backfill 2014–2025 (games/plays/advanced/lines/…) | **GO** — multi-window; resume on partitions |
| 2020 in headline metrics / mapping loss | **NO** — continuity only (§7.2 item 5) |
| Rely on play WP / A5 WP-GT without raw Connelly path | **NO** — source has no WP |
| Season-grain backfill (roster/returning/…) | **GO when quota recovers** — verified path on 2014/16/19 (+ staged 2023); expect `defense_pct` / `blue_chip_ratio` null-with-indicator |
| Odds historical baseline 54,090 | **NO-GO** under 16k ceiling |
| Odds reduced (e.g. 2024 slot_close ≈ 11,070) | **GO under ceiling** — your spend call |
| Probe matrix fully live for 2020/2021/2025 season-grain | **INCOMPLETE** — rate budget; re-probe after reset |

---

## Acceptance checklist

- [x] Season × dataset GO/DEGRADED/MISSING matrix
- [x] First complete EPA/WP / advanced-box season per field
- [x] GT inputs: source vs staging statement
- [x] Quota reconciliation + wrong-number source (16k ceiling vs 20k plan; “24 used” stale)
- [x] Measured Odds unit cost = 30; ladder recomputed
- [x] Historical payload has bet-time snapshot ≠ close
- [x] CFBD backfill wall-clock / quota-window estimate
- [x] Odds credits spent: **30 / 50**
- [x] `make lint typecheck test` — passed (ruff, mypy 84 files, pytest)

Artifacts: `data/_probe/cfbd_matrix.json`, `odds_probe.json`,
`weather_venues_probe.json`, `venues_coverage_refresh.json`,
`wall_clock_estimate.json`, `probe_summary.json`.
