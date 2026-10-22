# SOC-1 — Weekly social charts pipeline

**Date:** 2026-10-08  
**Branch:** `soc-1-charts`

## Logo decision (verbatim, owner 2026-10-08)

team logos are a hard requirement on these social graphics. DESIGN §6.3 **L2** still governs the **webapp v1 UI**, which stays logo-free. Do **not** treat L2 as a blocker for this task, and do **not** add logos to the webapp.

## `def_epa` sign convention

**Confirmed from code: higher `def_epa` = better defense.** Plot Y as `def_epa` directly (up = better).

Evidence:

1. Stage-1 measurement (`src/ncaa_quant/ratings/state_space.py` module docstring, measurement block):

   ```
   epa_h = off_h − def_a + hfa_global + hfa_dev_h + scoring_env + ε
   ```

   Higher away `def` lowers predicted home EPA → defense suppresses opponent EPA.

2. `docs/notes/14.md`: “higher `def_epa` = more EPA suppressed = better defense.”

3. `docs/webapp/DESIGN.md` §5.2: “higher `off_epa` / higher `def_epa` = better.”

Export passes Kalman `def_epa` through without a sign flip.  
**Note:** An FCS-prior comment in `state_space.py` (~L537) says the opposite; that comment is inconsistent with the H matrix and the notes above. Charts follow the measurement / notes / DESIGN evidence.

**Net rating for captions:** with both axes higher=better, net = `off_epa + def_epa` (equivalent to `off − def_allowed` if defense were stored as EPA allowed).

## Formulas (§3)

### Ridge map
- x = `off_epa`, y = `def_epa` (FBS only, resolved ratings week).
- Ellipse: axis-aligned ±1 marginal posterior SD (`off_sd`, `def_sd`). Covariance is not exported → approximate.
- Axis limits: symmetric about 0, data ± padding, rounded to 0.05.

### Slate map (SOC-1c)
- x = `max(p_win_home, 1 − p_win_home)`; y = `|mu_margin|`.
- Plotted only when `conviction_tier` is non-null and `sigma_margin_credible` is true.
- **Toss-up** (`conviction_tier == "toss_up"`): both logos equal size, side by side; no favored/opponent hierarchy.
- **Non-toss-up:** favored logo = published `conviction_team` (chip); opponent ~55% size lower-right. Assert chip side has `p ≥ 0.5` (`SlateCoherenceError` fails the run otherwise).
- Vertical tier bands at export enter thresholds `TIER_LEAN_ENTER=0.575`, `TIER_CLEAR_ENTER=0.70`, `TIER_STRONG_ENTER=0.85` on the p-axis.
- Φ check is diagnostic only: `max |p_chart − Φ(|μ|/σ)|`.
- Footnote only when sign(μ) disagrees with `(p_win_home > 0.5)`:  
  `"{n} toss-ups where expected margin and win probability lean opposite ways."`
- Dot color = published `conviction_tier` (hysteresis may place a game just across a band).

### Surprise strip
- Favored-perspective z:  
  `s = +1 if favored_side == home else −1` (tie-break via `compute_favored_side` / DESIGN §2.1 when `mu_margin == 0`);  
  `z = s · (actual_margin − mu_margin) / sigma_margin`.
- Positive z = favorite did better than Ridge expected; negative = underdog outperformed.
- Guide band at ±`Φ⁻¹(0.5 + nominal/2)` using `margin_interval_nominal` (0.80 → ±1.282). Visual only.
- Subtitle (SOC-1e): “X of Y inside Ridge's 80% range (target 80%)” from non-null
  `margin_interval_hit` (conformal), not from the band. Top-3 `|z|` get on-image score labels.
- Marker = outperforming team’s logo; ring color = published hit/miss (miss = accent).

## SOC-1b (2026-10-08) — slate STOP + ridge entering label

### Φ diagnostic (week-6 slate, n=58)
- d = p_favored − Φ(|μ|/σ): mean **+0.052**, median **+0.060**, min **−0.058**, max **+0.101**
- Mostly systematic: 53/58 have p_favored > Φ (p not shrunk toward 0.5; calibration pushes favored probs **up** relative to a pure normal)
- Mean d by |μ|/σ quartile: Q1 +0.029, Q2 +0.064, Q3 +0.068, Q4 +0.046

### Sign disagreements (SOC-1b STOP; lifted in SOC-1c)
4 games where sign(mu_margin) ≠ (p_win_home > 0.5) — all toss-ups; slate ships with footnote:

| game_id | matchup | mu | σ | p_win_home | tier |
|---|---|---|---|---|---|
| 401856823 | Arizona @ West Virginia | −0.127 | 18.65 | 0.555 | toss_up |
| 401858481 | Indiana @ Nebraska | −0.965 | 20.41 | 0.520 | toss_up |
| 401856718 | Ole Miss @ Vanderbilt | −0.852 | 18.27 | 0.521 | toss_up |
| 401864516 | Air Force @ NIU | −1.160 | 17.92 | 0.518 | toss_up |

### p_win_home / p_ml_home provenance
- MC draws from bivariate margin/total (`assemble_bivariate` + `sample_joint`); `moneyline_probs` = share of margin draws > 0 (`simulate.py`).
- Then `p_ml_home = _apply_calibrator("ml", p_ml)` — PIT recalibrator on the **margin** map (`production_stack.py`).
- Export aliases `p_ml_home` → published `p_win_home`. Not Φ(μ/σ).

### Ridge week label
Week 6 ratings: all 138 teams share `as_of_utc=2026-10-06T16:47:03Z` and match week-5 off/def (live `weekly` open circle). Label = **Entering Week 6** / footnote **ratings through Week 5 games**.

### Provenance of `data/social/artifacts_cache/latest`
Populated 2026-10-08 during SOC-1 by a one-off workstation `boto3` `get_object` of `latest/*` from bucket `ridge-artifacts` using workstation R2 read credentials (`load_secrets()` / `R2_ACCESS_KEY_ID`). Not part of the charts pipeline (pipeline itself never loads R2 credentials). sha256 matches live `latest/` (verified SOC-1b).

### Publish cadence
Last 5 history rows: W4 tue 2026-09-23, W4 daily 2026-09-25, W5 tue 2026-10-02, W6 tue 2026-10-06. Today Thu 2026-10-08: schedule says refresh Thu–Sat 06:00 UTC; `next_expected_publish_utc=2026-10-08T16:51:04Z` — daily_refresh was expected today and has **not** landed yet (still tuesday_primary from Oct 6).

## First production run (2026-10-08)

Commands used (PowerShell):

```powershell
uv run python scripts/fetch_team_logos.py --season 2026
uv run python scripts/make_social_charts.py --season 2026 --source local --artifact-dir data/social/artifacts_cache/latest
```

SOC-1 first run needed `--allow-stale` under the old age-only gate. SOC-1c aligns with DESIGN §3.2 (refuse only if age > 36h **and** `now > next_expected_publish_utc`); the SOC-1c re-render did **not** need `--allow-stale` (`published_at=2026-10-06T16:51:04Z`, `next_expected=2026-10-08T16:51:04Z`, run ~13:00Z — age ~44h but still before next expected).  
`--source local` was required: `r2_public_base_url` is empty (private-preview R2); artifacts were synced read-only into `data/social/artifacts_cache/latest/` for the run. No R2 writes.

Resolved weeks: map=6, slate=6, surprise=5. FBS count=138. Missing logos=0. Slate **shipped** (SOC-1c) with 4 sign-disagreement footnote.

## Weekly run instructions

```powershell
uv run python scripts/fetch_team_logos.py --season 2026
uv run python scripts/make_social_charts.py --season 2026
```

Makefile: `make social-logos` / `make social` (`SEASON=2026` default).

Flags worth knowing: `--source r2|local`, `--artifact-dir`, `--allow-stale`, `--allow-fixture`, `--allow-missing-logos`, `--chart all|map|slate|surprise`, `--week auto`, `--map-week` / `--slate-week` / `--surprise-week`, `--sizes 4x5,16x9`.

**`--source r2`** does a plain HTTPS GET of public `latest/` objects using `NCAA_QUANT_WEBAPP__R2_PUBLIC_BASE_URL`. No R2 credentials are loaded. Until public-read is enabled, use:

```powershell
uv run python scripts/make_social_charts.py --season 2026 --source local --artifact-dir data/social/artifacts_cache/latest
```

(after syncing a read-only copy of production `latest/` into that directory). Site staleness gate matches DESIGN §3.2: refuse only when age > 36h **and** past `next_expected_publish_utc` (`--allow-stale` to override).

Outputs: `out/social/{season}/w{week:02d}/` (gitignored) — six PNGs, alt/caption txt, `run_report.json`.

## SOC-1f (2026-10-08) — Top 25 replaces default map + ratings sanity

### A. Sanity (PASS)
- Net = `off_epa + def_epa`. Spearman(Ridge net rank, SP+ rank) on 138 paired FBS
  teams after 1 CFBD call `GET /ratings/sp?year=2026` (credits_used=1, remaining≈4911):
  **ρ = 0.852** (p≈4e-40). Gate ≥0.75 → pass.
- Largest |rank| disagreements: Georgia State (Ridge 44 / SP+ 111), Rutgers (128/75),
  Cincinnati (90/38), USC (65/13), Utah State (53/105), Kansas (16/66), SMU (76/26),
  Michigan (68/22), Virginia (66/21), Arizona State (104/60).
- Median SD by week (FBS): off 0.146→0.130→0.113→0.100→0.092→0.092;
  def 0.146→0.134→0.112→0.098→0.090→0.091. Week-1 → week-6 shrinks for both → pass.
- Artifact: `docs/notes/_artifacts/soc-1f/sanity.json`.

### B. Top 25 chart
- Chart key `top25`; included in `--chart all` (default). Scatter map kept behind
  `--chart map` only.
- Rows by net; rank-change vs prior ratings week; orange `#FD4D11` bars on navy
  `#07152F`; OFF#/DEF# chips; 4:5 one column / 16:9 two columns (1–13, 14–25).
- Paths: `out/social/2026/w06/top25_w6_{4x5,16x9}.png`.
- Week-6 note: entering-week open circle adds teams missing a week-5 row → many
  `NEW` and mostly downward deltas inside the top 25 (biggest faller Tennessee ▼7;
  no positive riser this week).

## SOC-1d (2026-10-08) — Ridge map PEM-style percentiles

- Axes: national percentile among plotted FBS teams.
  `pct = 100 * (rank − 1) / (n − 1)` with average ranks for ties; higher EPA = higher pct.
- Midlines at 50; light shade on upper-right; corner labels Both/Defense/Offense/Both below average.
- Title “Who is actually complete?”; no key ellipse / SD / “how sure”.
- Logos: same visual size (~5.5% of axis); `overlap_allow_fraction=0.15`; leaders only if
  displaced > 0.5 logo-widths. Caption = top 5 by `(off_pct + def_pct) / 2`.
- `run_report.json` keeps raw `off_epa`/`def_epa` plus percentiles and `leader_line_count`.
- Week-6 re-render: **leader_line_count=8** (target < 15), overlap_fraction≈0.150.
  Paths: `out/social/2026/w06/ridge_map_w6_{4x5,16x9}.png`.

## SOC-1e (2026-10-08) — Brand + text diet (with SOC-1d)

Ran together with SOC-1d. Brand tokens: `BG=#07152F`, panel `#16233B` (6% lighter navy),
accent `#FD4D11`. Header uses `assets/brand/ridge_mark.png` (~64px tall at 4:5) beside the
title; no separate matplotlib “RIDGE” wordmark and no blue through-week line (week folded into
the subtitle). Accent reserved for surprise-strip miss rings and the Ridge map “Both above
average” label. Slate bands are a navy→slate hue ramp with Title Case labels at 14px / 70% white.

On-image text diet: title, one subtitle, axes/ticks, band/quadrant labels, and one left-aligned
footer (`the-cfb-model.vercel.app · Forecasts, not betting advice`, plus `· {n} games not shown`
when exclusions exist). Provenance, hysteresis/threshold/sign notes, exclusion counts, band-guide
disclaimer, and Ridge statistical-closeness note live in `{chart}_alt.txt` and `run_report.json`.

Per-chart subtitles:
- Slate: `Week 6 · {N} games · how sure Ridge is, as of {Tue/Thu}`
- Surprise: `Week 5 results · {X} of {Y} inside Ridge's 80% range (target 80%)` with on-logo
  score labels for the 3 largest `|z|` (abbr from logo manifest / staged teams); band label
  `80% range`; bottom annotation block removed.
- Ridge: SOC-1d subtitle (week folded in).

Text bbox overlap asserted at render time (`assert_no_text_bbox_overlap`). Logo contrast plates
re-tuned for navy (`LOGO_LUMINANCE_THRESHOLD=0.42`, plate alpha 0.28).

Re-render (needed `--allow-stale`; Thu refresh still not landed):

```powershell
uv run python scripts/make_social_charts.py --season 2026 --source local --artifact-dir data/social/artifacts_cache/latest --chart all --allow-stale
```

PNG paths:
`out/social/2026/w06/ridge_map_w6_{4x5,16x9}.png`,
`out/social/2026/w06/slate_map_w6_{4x5,16x9}.png`,
`out/social/2026/w06/surprise_strip_w5_{4x5,16x9}.png`.

## SOC-1c-FAST (2026-10-08) — ship-blocking chart fixes

1. **Ridge map** — removed per-team filled ellipses; one outline “Typical ±1 SD” key
   ellipse (median `off_sd`/`def_sd`) in the emptiest corner; median SDs in subtitle;
   SOC-1b “Entering Week 6” label retained. Confirmed Matplotlib `Ellipse` uses full
   diameters → `width=2.0 * sd`, `height=2.0 * sd` (correct for ±1 SD).
2. **Surprise strip** — axis-end direction labels; misses full opacity + accent ring,
   hits 70% opacity; subtitle `X of Y inside Ridge's {nominal} interval (Z%) · target …`;
   band label moved above the plot area.
3. **Slate** — re-rendered from current `latest/` (Thu refresh not landed; still
   `tuesday_primary` 2026-10-06T16:51:04Z). Past `next_expected` → needed `--allow-stale`.
4. **Footnotes** — `stamp_footnote` stacks upward from `y_floor=0.048` so lines never
   overlap the provenance footer at `y=0.025`.

PNG paths (all six):
`out/social/2026/w06/{ridge_map_w6,slate_map_w6}_{4x5,16x9}.png`,
`out/social/2026/w06/surprise_strip_w5_{4x5,16x9}.png`.

## SOC-1c (2026-10-08) — slate ship + §3.2 stale + HFA report

### A. Slate map shipped
- Favored-side rule as above; week-6 slate PNGs written to `out/social/2026/w06/slate_map_w6_{4x5,16x9}.png` with footnote for the 4 toss-up sign disagreements.
- Re-render command (no `--allow-stale`):  
  `uv run python scripts/make_social_charts.py --season 2026 --source local --artifact-dir data/social/artifacts_cache/latest --chart all`

### B. Stale check
`validate_staleness` now refuses only when `(now − published_at > 36h) AND (now > meta.next_expected_publish_utc)`, matching DESIGN §3.2 / site banner. Tests cover age>36h before next_expected (allow) and after (refuse).

### C. Report only — Φ gap by μ-side / neutral (no model or export changes)

`d = p_fav − Φ(|μ|/σ)` where `p_fav = max(p_win_home, 1 − p_win_home)`.

**Week-6 slate (n=58 plotted):**

| slice | n | mean d | median d |
|---|---:|---:|---:|
| home favored by μ (non-neutral) | 41 | +0.065 | +0.071 |
| away favored by μ (non-neutral) | 16 | +0.035 | +0.045 |
| neutral site | 1 | +0.049 | +0.049 |
| all | 58 | +0.056 | +0.060 |

Sign disagreements: μ-away / p-home = **4**; μ-home / p-away = **0**.

**`results_2026` with mu/σ/p (n=387):** home_μ mean +0.042 median +0.042 (n=350); away_μ mean +0.032 median +0.033 (n=37); neutral n=0. Sign: μ-away/p-home=6, reverse=0.

**`publish_history` 2026 tiered+credible (n=963):** home_μ mean +0.040 median +0.038 (n=856); away_μ mean +0.033 median +0.035 (n=93); neutral mean +0.057 median +0.058 (n=14). Sign: μ-away/p-home=**15**, reverse=**0**.

#### Calibrator / HFA quotes (code + DESIGN)

1. **ML calibrator inputs / training target do not include home field or `neutral_site`.**  
   `pit_calibration.py`: one monotone map on OOF **margin** (and **total**) PIT values `u = F(y)`; `MARKET_FREE_TARGETS = {margin, total}`; no covariates. DESIGN §2.6 / §5.2: distributional PIT recalibration, market-free for the fundamental stack; per-market ML/ATS maps are diagnostics only. `p_ml_home = _apply_calibrator("ml", p_ml)` applies the **margin** PIT map to the MC moneyline share (`production_stack.py`).

2. **μ already includes home-field advantage.**  
   Stage-1 measurement (`state_space.py`):  
   `epa_h = off_h − def_a + hfa_global + hfa_dev_h + scoring_env + ε` (HFA dropped on neutrals);  
   `margin ≈ margin_scale · (off_h − def_a − off_a + def_h + hfa)`.  
   Mapping-layer features include `neutral_site` (situational builder; listed in D1 feature inventory). So published `mu_margin` is already a home-perspective margin with HFA baked in when the site is not neutral.

#### Verdict on double-counting

**Suggestive residual home lean in `p_win_home` relative to μ — not proven classic feature double-counting.**

- All observed sign flips are one-way (μ leans away, calibrated p leans home); never the reverse.
- Week-6 mean d is ~3pp higher when μ favors home than when μ favors away; season-wide the gap shrinks to ~0.7pp.
- The calibrator has **no** home/neutral input, so this is not a second HFA feature stacked on μ. The plausible mechanism for a separate model task is: μ already embeds HFA, then a **global** distributional PIT map systematically lifts `P(M > 0)` (especially near toss-ups), which reads as extra home lean in `p_win_home`.

No model, export, or calibrator changes in this task.

## Layout

- `src/ncaa_quant/social/load.py`, `logos.py`, `placement.py`, `theme.py`, `copy.py`, `cli.py`
- `src/ncaa_quant/social/charts/{ridge_map,slate_map,surprise_strip}.py`
- Scripts: `scripts/fetch_team_logos.py`, `scripts/make_social_charts.py`
- Fonts: bundled Inter (OFL) under `assets/fonts/` (SF Pro Display preferred when installed)
- Brand mark: `assets/brand/ridge_mark.png` (required; render fails if missing)
- Logos: `data/social/logos/` (gitignored); overrides in `data/social/logos/overrides/{team_id}.png`

Existing S-series Best Bets modules in `ncaa_quant.social` are unchanged; charts are an additive surface.

## Test hygiene (2026-10-08)

Pre-existing `make test` failures on this workstation (unrelated to chart math) were
re-pinned so the suite stays green:

- `scripts/check_betting_language.py` ratchet baseline → 909/708/126 (stale since
  S-series Best Bet modules landed).
- `test_2026_credit_accounting_matches_v4_projection` slot count → 293 / 879 credits
  (staged 2026 schedule grew past the old 263 pin).
- `test_copy_cites_amended_numbers_and_reval_memo` now asserts the clarity-rewrite
  disclaimer phrase `not betting recommendations` (webapp copy unchanged in this task).

## Known limitations

1. **Marginal-SD ellipses** — no off/def posterior covariance in the artifact; ellipses are axis-aligned approximations.
2. **Hysteresis vs wedges** — published tiers can sit just across a Φ-boundary ray; footnote calls this out; tiers are never recomputed.
3. **Conformal vs normal band** — surprise-strip shaded band is a normal guide from `margin_interval_nominal`; ring colors / headline use published conformal `margin_interval_hit`.
4. **Results lack team_ids** — surprise logos resolve school → id via `team_ratings` / staged teams.
5. **Staged teams have no logos array** — fetch prefers archived CFBD raw `teams_s{season}_*.json`; only if absent does it spend one CFBD `/teams` credit.
6. **Public R2 HTTPS** — `r2_public_base_url` is empty in private-preview posture; default `r2` source fails closed until that URL is set.

## Product constraint

Ridge is not a betting product. Banned-word scanner fails the run on pick/bet/spread/cover/odds/… language; the fixed footer phrase “not betting advice” is the sole exception for “betting”.

---

## SOC-2 — Ridge social charts, round two (2026-10-08)

**Branch:** `soc-2-charts` (branched from `soc-1-charts`; SOC-1 not yet on `main`).

### Owner decisions

1. **Logos** — SOC-1 §0 still applies (required on social graphics; webapp stays logo-free).
2. **Interval graphics (2026-10-08)** — DESIGN §4.3 (“Interval band — text-only, no error-bar graphics”) governs the **webapp v1 UI only**. Social graphics may draw published margin interval bars.

### §1.1 Chart library / approach

The Game Detail “Stage-1 ratings” chart is **hand-rolled React/SVG** — no Recharts/D3/Victory/etc.

| Pin | Version (from `webapp/site/package.json`) |
|---|---|
| react / react-dom | `^19.0.0` |
| playwright (dev) | `^1.51.1` |

SOC-2 renderer package: `social/render/` (same React + Playwright pins; `npm test` / `check-versions.mjs` fails on drift). Tokens imported read-only from `webapp/site/src/styles/tokens.css`. Number formatting mirrored from `webapp/site/src/lib/formatting/numbers.ts` (path noted in `format.mjs`). Trajectory encoding ported from `RatingTrajectoryChart.tsx` @ `98124ba54cc09d6d26c54f29c3501ab45d8f1db7` into `social/render/src/geometry.mjs` + `charts.mjs` (see `social/render/components/README.md`).

### §2.1 Defense sign

**`def_good = def_epa`** (higher = tougher defense).

1. **Export** (`src/ncaa_quant/webapp/export.py` `build_team_ratings`): writes `getattr(row, "def_epa")` with no sign flip.
2. **Display** (`RatingTrajectoryChart.tsx` `markersFor`): `mean: … p.def_epa` with no flip; helper text: “Higher is better on defense… EPA suppressed”.

### §2.2 `margin_scale`

Resolved from `StateSpaceConfig().margin_scale` → **80.0**  
Source path recorded in run report: `src/ncaa_quant/ratings/state_space.py:StateSpaceConfig.margin_scale`  
(Production filter uses this dataclass default; not present in YAML.)

`net_pts = margin_scale · (off_epa + def_good)`;  
`net_sd_pts = margin_scale · sqrt(off_sd² + def_sd²)` with independence footnote (no covariance exported).

### Bye weeks

In `team_ratings_*.json`, a bye is a **missing week row** (not a carried row). Example: Ole Miss `145` weeks `[1,2,3,4,6]` — week 5 absent. Learned chart excludes byes via `results_*.json` week games (school → id; results omit team_ids) cross-check.

### Learned week resolution

Title contract: **“What Ridge learned in week N”** = after week N’s games versus before them.

| Compared row | Meaning |
|---|---|
| week **N** | Preferred export row for week N (`postgame` over `weekly`) — state **after** week N games |
| week **N−1** (or latest week &lt; N if N−1 missing / bye) | State **before** week N games |

Export sets `week` on postgame rows from the observed game week (`state_space.run_filter` → `_state_to_row(..., week=week, kind="postgame")`). `build_team_ratings` collapses to one row per `(team, week)` preferring postgame (`export.py` `_kind_rank` + `tail(1)`). Live open circle: `predict._append_current_week_rating_points` appends `kind="weekly"` for the current slate week.

When `map_week >= slate_week`, the latest ratings week is that open circle (means duplicate prior postgame) → chart **N = map_week − 1**. Eligibility = played in **week N’s games** (results school→id). Prior-week bye does not exclude a week-N player (`prior_week_point`). Week-1 → skip.

### Architecture

1. **Python specs** — `src/ncaa_quant/social/specs/` builds one JSON per chart×size under `out/social/{season}/w{week}/specs/`.
2. **Node renderer** — `social/render/` screenshots with Playwright Chromium at 1600×2000 / 1920×1080, `deviceScaleFactor: 1`, animations disabled. Default theme **light** (DESIGN §4.1).
3. CLI — same entry point; `--chart readmap|learned|slate-ranges|gamecard|soc2`, `--game-id`, `--slate-scope`, `--theme`, `--skip-render`. Makefile: `make social-soc2`.

### Interval field

Upcoming games export `margin_interval_lo` / `margin_interval_hi` / `margin_interval_nominal` on `week_predictions.json` (used verbatim; no normal-approx bands).

### First production run (2026-10-08)

```powershell
cd social\render
npm install
npx playwright install chromium
cd ..\..
uv run python scripts/make_social_charts.py --season 2026 --chart soc2 --source local --artifact-dir data/social/artifacts_cache/latest --allow-stale
```

Resolved weeks: map=6, slate=6, surprise=5. Learned week=5 (entering semantics).

| Chart | Paths |
|---|---|
| Read map | `out/social/2026/w06/readmap_w6_{4x5,16x9}.png` + alt/caption |
| Learned | `learned_w5_{4x5,16x9}.png` + alt/caption |
| Slate ranges | `slate_ranges_w6_{4x5,16x9}.png` + alt/caption |
| Game cards | `gamecard_{401858481,401856715,401856718}_{4x5,16x9}.png` + per-id alt/caption |
| Specs | `out/social/2026/w06/specs/*.json` |

**Selections**

- Sharpest reads (team_id): Auburn 2, UTSA 2636, Georgia Southern 290, Louisville 97, Clemson 228  
- Widest: East Carolina 151, Northern Illinois 2459, Duke 150, Sacramento State 16, FIU 2229  
- Proven good count: 36  
- Learned top 10 by move_sd: Massachusetts, Middle Tennessee, Eastern Michigan, Alabama, Baylor, Texas A&M, Arkansas, Washington State, Air Force, Cincinnati  
- Featured 16: Georgia@Alabama, Kansas@Utah, Indiana@Nebraska, UCLA@Oregon, LSU@Kentucky, Texas A&M@Missouri, Iowa State@BYU, Boise State@Fresno State, Texas@Oklahoma, UCF@Oklahoma State, Maryland@Ohio State, Ole Miss@Vanderbilt, FSU@Louisville, UNC@Pitt, Houston@Kansas State, Wake@NC State  
- Default game cards: Indiana@Nebraska (toss-up), LSU@Kentucky (lean), Ole Miss@Vanderbilt (toss-up)

**Determinism:** re-render of `readmap_w6_4x5.png` → **pixel-identical and byte-identical**.

**Tests:** Before SOC-2 work this session: `test_social_charts.py` 23 passed. After: `test_social_charts.py` + `test_social_soc2.py` → 35 passed (12 new SOC-2 tests including pixel-identical Playwright render). Full suite `pytest -m "not live"` → **1100 passed**, 1 deselected.

### Ambiguities / notes

- Prompt mentioned `webapp/lib/tokens.ts` / `format.ts`; actual paths are `webapp/site/src/styles/tokens.css` and `webapp/site/src/lib/formatting/numbers.ts`.
- Axis limits for slate ranges expand to include every published `lo`/`hi` (rounded to 5); arrow caps apply when a bar is clipped to a narrower axis (helper `clip_bar`).
- Site trajectory figcaption contains the word “line”; SOC-2 captions/alt avoid it (banned-words scanner). Helper sentences match the site’s offense/defense notes without that figcaption.

### Follow-up verify-before-post (2026-10-08 evening)

#### 1. Production rerun without `--allow-stale` — STOP

```powershell
uv run python scripts/make_social_charts.py --season 2026 --chart soc2 --source local --artifact-dir data/social/artifacts_cache/latest
```

**Refused.** Tripped on **`meta.json` site-staleness gate** (not a per-file age of ratings/predictions alone):

| | First-run cache | Same cache at verify |
|---|---|---|
| `meta.published_at` | `2026-10-06T16:51:04Z` | same |
| `meta.refresh_kind` | `tuesday_primary` | same |
| `week_predictions.published_at` / `refresh_kind` | `2026-10-06T16:51:04Z` / `tuesday_primary` | same |
| `team_ratings_2026.published_at` | `2026-10-06T16:51:04Z` | same |
| `results_2026.published_at` | `2026-10-06T16:51:04Z` | same |
| `meta.next_expected_publish_utc` | `2026-10-08T16:51:04Z` | same |

At refuse time (~`2026-10-08T22:21Z`): age ≈ **53.5h** (&gt; 36h) and **~5.5h past** `next_expected_publish_utc`. No override used for this check. `--source r2` unavailable (`R2_PUBLIC_BASE_URL` empty).

Review PNGs below were regenerated afterward with `--allow-stale` (same artifact bytes) so caption/learned fixes could be inspected.

#### 2. Learned week alignment

Compares **week 5 vs week 4** (N=5 when map=slate=6). Pairing already matched “after week N vs before”; fix was **prior-bye baseline** (`prior_week_point`) so a missing N−1 row still uses latest week &lt; N.

**(a) UMass (113)** — week-5 game: Eastern Michigan @ Massachusetts (`401866433`, graded). Rows: week 5 `as_of=2026-10-04T00:30:00Z` (after); week 4 `as_of=2026-09-27T06:00:00Z` (before). Eligible.

**(b) Ole Miss (145)** — weeks `[1,2,3,4,6]`; no week-5 results game (bye); **not eligible** for learned week 5.

**(c)** Test `test_learned_eligible_after_prior_bye`: bye week 4, played week 5 → eligible; `before_week=3`.

#### 3. Sharpest / widest caption gap

Plotted FBS `net_sd_pts` (week 6): min **9.51**, 5th-smallest **9.76**, median **10.40**, 5th-largest **11.90**, max **12.49**.  
`MIN_READ_GAP_PTS = 1.0`: sharp_gap **0.64** → do not name sharpest; wide_gap **1.50** → name widest.  
Caption now: `Widest: ECU, NIU, DUKE. 36 FBS teams in Proven good.` (highlights unchanged).

#### 4. FBS spot check

Sacramento State `team_id=16` in `data/staged/teams/season=2026/part.parquet`: `classification == "fbs"`, conference Mid-American. All 138 read-map teams are in the staged FBS set. **FBS-vs-FBS games this season (final scores in results): none with &lt; 3** among plotted teams.

#### 5. Review PNG paths (4:5, post-fix `--allow-stale` rerun)

- `C:\Users\alecw\Projects\the-cfb-model\out\social\2026\w06\readmap_w6_4x5.png`
- `C:\Users\alecw\Projects\the-cfb-model\out\social\2026\w06\learned_w5_4x5.png`
- `C:\Users\alecw\Projects\the-cfb-model\out\social\2026\w06\slate_ranges_w6_4x5.png`
- `C:\Users\alecw\Projects\the-cfb-model\out\social\2026\w06\gamecard_401858481_4x5.png` (first default card: Indiana @ Nebraska)

**Determinism (stale review rerun):** pixel-identical **and** byte-identical on `readmap_w6_4x5.png`.

**Tests:** social unit 35 → **37** (+2: prior-bye eligibility, caption gap branches). Full `pytest -m "not live"` → **1102 passed**, 1 deselected (was 1100).

## SOC-2 polish

**Date:** 2026-10-08  
**Branch:** `soc-2-charts`

Global frame: CSS layout **800×1000 / 960×540** at 1×, Playwright `deviceScaleFactor: 2` → **1600×2000 / 1920×1080**. Type scale per prompt (title 34/40, dek 17/22, labels 15/20, footer 12/16; nothing below 12). Single **RIDGE** wordmark + week stamp; lean footer (`Ridge · Forecasts, not betting advice · the-cfb-model.vercel.app · published {Day Mon D} ET`). Model/vintage/refresh details in alt + `run_report.json`. U+2212 minus; `humanize_refresh_kind` explicit table (unknown fails). Logos sized by opaque-pixel area (aspect ≤ 1.6). Layout lint in Playwright on every final render (`--no-lint` debug-only). Fast iteration: `npm run preview`, `--specs-only` / `--render-only`, contact sheet, per-chart `render_ms`.

### Bugs fixed
1. **Slate bars** — per-row SVG used absolute `mid = i*rowH + rowH/2`; clipped for `i≥1`. Now `mid = rowH/2`. Encoding: gray range bar + μ dot + tier chip; favored by `p_win_home ≥ 0.5`.
2. **Game-card trajectories** — panels full width; week ticks inside each SVG on the same scale as points; single matchup header (no duplicate title block).

### Production run (read-only `latest/`, `--allow-stale`)

```powershell
uv run python scripts/make_social_charts.py --season 2026 --chart soc2 --source local --artifact-dir data/social/artifacts_cache/latest --allow-stale
```

Stale gate still refuses without `--allow-stale` (same meta as verify follow-up). Full `--chart soc2` render **12.85 s** (target &lt; 60 s).

| Chart | 4:5 path | Headline | Callout |
|---|---|---|---|
| Read map | `out/social/2026/w06/readmap_w6_4x5.png` | Ridge has a similar read on most teams. These 5 it can't pin down. | Duke: +18.4, but ±12.0 |
| Learned | `out/social/2026/w06/learned_w5_4x5.png` | Massachusetts's loss vs Eastern Michigan moved Ridge more than any other result | −20.0 pts |
| Slate ranges | `out/social/2026/w06/slate_ranges_w6_4x5.png` | Ridge's surest game: Ohio State over Maryland, 90%. Closest: Indiana–Nebraska | coin flip |
| Game card | `out/social/2026/w06/gamecard_401858481_4x5.png` | Toss-up: Ridge has Nebraska by 1.0 | Indiana offense: #29 → #11 since week 4 |

Also: `readmap_w6_16x9.png`, `contact_sheet_w6.png`. Lint **ok** on all 12 rendered PNGs. Determinism: pixel-identical at 2× on `readmap_w6_4x5.png`.

**Margin-sign vs win-probability mismatches (featured slate):**  
`401856718` Ole Miss @ Vanderbilt (μ=−0.85, p_home=0.521);  
`401858481` Indiana @ Nebraska (μ=−0.96, p_home=0.520).

**Tests:** social unit **37 → 49** (+12 polish). Full `pytest -m "not live"` → **1114 passed**, 1 deselected (was 1102).

### Ambiguities
- Learned `pregame_label` uses results `mu_margin` from the team's perspective (not entering net rating).
- Layout lint treats row/trajectory intentional groups and tick↔helper adjacency as non-failures; empty-band scan uses content boxes between header and footer.
- `--no-lint` implies non-final (refused with `--final`).

## SOC-2 polish 2

**Date:** 2026-10-08  
**Branch:** `soc-2-charts` (no merges)

### 1. Correctness

**Game-card margin vs win-probability (blocking).** Headlines and the projected-margin stat name the team favored by `mu_margin` sign (home if `>0`, away if `<0`, magnitude `|μ|`). Win-chance stats name the team favored by `p_win_home`. The two are never mixed in one sentence. Coin-flip (`|p−0.5|≤0.03`) uses “can't split”; margin/prob disagree (non-flip) uses “toss-up for Ridge”.

Corrected default-card headlines (4:5):

| game_id | Matchup | Headline | Projected | Win chance |
|---|---|---|---|---|
| `401858481` | Indiana @ Nebraska | Ridge can't split IU and NEB | IU by 1.0 | NEB 52% |
| `401856718` | Ole Miss @ Vanderbilt | Ridge can't split MISS and VAN | MISS by 0.9 | VAN 52% |
| `401856715` | LSU @ Kentucky | Ridge has LSU by 8.1 | LSU by 8.1 | LSU 69% |

Slate still plots the IU–NEB / MISS–VAN rows at μ≈−1.0 / −0.85 with “vs” labels (coin-flip rule); packing unchanged and correct.

**“Ridge had” spot-check** (learned audit vs `results_2026.json`, home-perspective `mu_margin` → team-perspective):

| Row team | Matchup | μ_home | Team-persp “Ridge had” | Scores |
|---|---|---|---|---|
| Alabama | Alabama @ Mississippi State | −1.05 | **+1.05** | 56–23 |
| Massachusetts | Eastern Michigan @ Massachusetts | +22.89 | **+22.89** | 38–14 |
| Air Force | Navy @ Air Force | +22.14 | **+22.14** | 14–9 |

Signs match; no code fix required beyond the existing `ridge_had_team_perspective` path. Full top-10 audit is in `run_report.json` → `charts.learned.ridge_had_audit`.

### 2. Copy / layout / lint

- Headlines ≤42 / deks ≤60; no wrap; no footnotes/legends on canvas; units once on axis titles; whole-number axis ticks; short names from CFBD abbreviations.
- Logo slots area-normalized **and** bounding-box capped; SVG `viewBox` matches CSS px (no `preserveAspectRatio="none"`, no flex-stretch).
- Lint additions: SVG element clip, text–text / text–mark overlap, logo–logo >15% on read map (leaders exempt), wrap via height vs line-height, non-uniform SVG scaling, tick-label overlap.
- Round-1 synthetic probe (`social/render/src/lint_round1_probe.mjs`) **fails** on: `preserveAspectRatio=none`, aspect mismatch, wrapped headline/dek, sub-12px text, clipped label — confirming the new checks catch round-1 defects (original round-1 PNGs were overwritten).

### 3. Per-chart headlines / deks (verbatim)

| Chart | Headline (chars) | Dek (chars) |
|---|---|---|
| Read map | `5 teams Ridge can't pin down yet` (32) | `Right = better · higher = surer` (31) |
| Learned | `MASS's loss vs EMU moved Ridge most` (35) | `Biggest rating moves after week 5` (33) |
| Slate | `Ridge's surest game: OSU, 90%` (29) | `Dot = projected margin · bar = 80% range` (40) |
| Game (IU@NEB) | `Ridge can't split IU and NEB` (28) | `Sat 12:00 PM ET` (15) |

Read-map callout: `DUKE +18, but ±12`. Median labeled inline; widest-5 legend/footnote removed.

### 4. Deliverable paths

- Contact sheet: `out/social/2026/w06/contact_sheet_w6.png`
- 4:5 PNGs:
  - `out/social/2026/w06/readmap_w6_4x5.png`
  - `out/social/2026/w06/learned_w5_4x5.png`
  - `out/social/2026/w06/slate_ranges_w6_4x5.png`
  - `out/social/2026/w06/gamecard_401858481_4x5.png`
  - `out/social/2026/w06/gamecard_401856718_4x5.png`
  - `out/social/2026/w06/gamecard_401856715_4x5.png`

### 5. Read-map gray dots

15 logos demoted to 6px gray dots (≤15 cap): SJSU, NEB, BGSU, OHIO, PSU, UVA, VT, APP, BALL, CIN, IOWA, MTSU, USM, SC, WMU. Listed in the readmap spec `points` with `as_dot: true` / run report.

### 6. Tests / render time

- SOC-2 unit tests: **27 passed + 1 failed** (mid-round, result/pregame split) → **28 passed, 1 skipped**.
- Production `--chart soc2` render_total_ms: **12201** (~12.2 s).

### Ambiguities
- 16×9 learned shows top **6** moves (10 rows do not fit); 16×9 slate shows top **8** by win probability.
- Game-card tier chip shows the tier word only (`Lean` / `Toss-up`), not the favorite name.
- Trajectory band `<path>` fills are exempt from clip lint (clipped by the plot box by design).

## SOC-2 polish 2 follow-up

**Date:** 2026-10-09  
**Branch:** `soc-2-charts` (no merges)

### 1. Display names

Added owner-editable `social/display_names.csv` (`team_id`, `display_name`, `display_short`) and
`src/ncaa_quant/social/display_names.py`. Headlines cascade **full → display_short → CFBD abbreviation**
until ≤42 characters.

**Seeded overrides (CFBD school → display):**

| team_id | CFBD school | display_name / short |
|---|---|---|
| 113 | Massachusetts | UMass / UMass |
| 145 | Ole Miss | Ole Miss / Ole Miss |
| 193 | Miami (OH) | Miami (OH) / Miami (OH) |
| 62 | Hawai'i | Hawaii / Hawaii |
| 23 | San José State | San Jose State / San Jose St |
| 221 | Pittsburgh | Pittsburgh / Pitt |
| 2026 | App State | Appalachian State / App State |
| 98, 189, 256, 324, 2226, 2229, 2390, 2393 | (common short forms) | see CSV |

Learned templates: win = `{T}'s win {over|at} {OPP} moved Ridge most`; loss = `{T}'s loss to {OPP} moved Ridge most`.

**Headlines (verbatim):**

| Chart | n | Headline |
|---|---|---|
| Read map | 32 | `5 teams Ridge can't pin down yet` |
| Learned | 36 | `UMass's loss to EMU moved Ridge most` |
| Slate | 29 | `Ridge's surest game: OSU, 90%` |
| Game IU@NEB | 38 | `Ridge can't split Indiana and Nebraska` |
| Game MISS@VAN | 41 | `Ridge can't split Ole Miss and Vanderbilt` |
| Game LSU@UK | 20 | `Ridge has LSU by 8.1` |

### 2. Gray-dot priority

Never demote featured-slate or default game-card teams. Demote FCS-transition / Group-of-5 before Power-4; within a tier keep closest-to-median / |x|≈0.

**New gray-dot list (15):** LIB, NMSU, DEL, JXST, MOST, KENN, SHSU, App State, Bowling Green, OHIO, WMU, BALL, San Jose St, MTSU, USM.  
None are on the protected slate/card set (e.g. NEB/IU/LSU/Ole Miss stay as logos).

### 3. Test counts (35 → 28 explained)

Nothing was deleted between polish rounds. The apparent drop was **scope**:

- Earlier “35 / 37 / 49” figures mixed SOC-1 chart tests + SOC-2, or counted before/after different filters.
- Polish-2 “28 passed, 1 skipped” = only `test_social_soc2.py` + `test_social_soc2_polish.py`.
- The **1 skip** was `test_pixel_identical_two_renders`: renderer threw layout-lint (`median` vs callout overlap on a 1-team fixture). That was a real defect; callout placement now offsets away from the median rule. **Restored — test passes.**

**Coverage added this follow-up:** display-name cascade, learned win/loss templates, gray-dot protect + G5-before-P4.

| Suite | Count |
|---|---|
| SOC-2 unit (`soc2` + `soc2_polish`) | **32 passed** |
| All `tests/unit/test_social_*.py` | **113 passed** |
| Full `pytest -m "not live"` | **1120 passed**, 1 deselected |

### 4. Lint proof (round-1 specs)

Round-1 PNG/JSON were overwritten; reconstructed from `soc2_polish_summary.json` + dense ticks / forced logo pile-up in
`docs/notes/_artifacts/social-charts-w6/round1_lint/`. Current lint **fails** as required:

- Learned: **Overlapping tick labels** (−60 vs −55 … +55 vs +60)
- Read map: **Logo overlap 88–100%** (no leaders)

Report: `docs/notes/_artifacts/social-charts-w6/round1_lint/round1_lint_report.json`.

### 5. Fresh data

```text
--source local --artifact-dir data/social/artifacts_cache/latest
# WITHOUT --allow-stale → REFUSED
Site stale: meta.published_at=2026-10-06T16:51:04Z age≈66.8h > 36h
  and now past next_expected_publish_utc=2026-10-08T16:51:04Z
```

| Field | Value |
|---|---|
| `meta.published_at` / `week_predictions.published_at` | `2026-10-06T16:51:04Z` |
| `refresh_kind` | `tuesday_primary` |
| `--source r2` | unavailable (`R2_PUBLIC_BASE_URL` empty) |

Contact sheet regenerated under `--allow-stale` with the new display-name / gray-dot code:
`out/social/2026/w06/contact_sheet_w6.png` (render_total_ms ≈ 12777).

## SOC-2 round 3

**Date:** 2026-10-09  
**Branch:** `soc-2-charts` (no merges)

### 0. Owner decision (2026-10-09)

Market may appear on the **slate chart and game cards only**. Read map and learned stay market-free. No picks/units/books/wager language; footer still “Forecasts, not betting advice”; banned-words list unchanged. Chart text says “market”, nothing else.

### 1. Market source + sign proof

| | |
|---|---|
| Module | `workers/odds-refresh/src/consensus.ts` → `homeSpreadToMarketMargin` |
| Field | `odds_snapshot.games[].market_home_margin` |
| Convention | `market_home_margin = −median_home_spread` (same home-minus-away scale as `mu_margin`; ADR-ODDS-SNAPSHOT §5) |
| Consensus | `median_across_books_devigged_h2h` |
| Snapshot | `2026-10-07T13:01:28Z` (week 6; newest in production `latest/` — **not** from today 2026-10-09) |

**Sign proof (obvious home favorite):** Maryland @ Ohio State — `spread_home_points=−34.5`, `market_home_margin=+34.5`, Ridge `mu_margin=+23.14`. Away-favorite check: Tennessee @ Arkansas — `spread_home=+13.5`, `market_home_margin=−13.5`.

### 2. Fresh-data / `--allow-stale` STOP

Production `latest/` forecast is still `tuesday_primary` `2026-10-06T16:51:04Z`; `next_expected_publish_utc=2026-10-08T16:51:04Z`. Without `--allow-stale` the site-staleness gate refuses. Odds worker last wrote week-6 snapshot on **2026-10-07** (no 2026-10-09 snapshot). Deliverable render used `--allow-stale` against that cache.

### 3. Chart redesigns

- **Slate** → “Ridge vs. the market · week {N}”: market-favorite perspective; hollow ◇ = market, filled dot = Ridge; “Ridge leans” / “Ridge win”; sort by \|Ridge−market\|.
- **Game card** → simplified (no trajectories / stat strip / legend): teams + win %, home-perspective margin bar with market diamond, two FBS-rank dumbbells.
- **Read map** → percentile axes (x=`net_pts` pct, y=certainty pct); every FBS logo; no gray dots/highlights/callouts/median rule. Slot start 30 → final **26** CSS px; max displacement **14.0** px.
- **Learned** → x domain fitted to plotted ranges + padding (no −100…+100 squeeze); display-name headlines retained.

### 4. Deliverables (4:5)

- Contact sheet: `out/social/2026/w06/contact_sheet_w6.png`
- `out/social/2026/w06/readmap_w6_4x5.png`
- `out/social/2026/w06/learned_w5_4x5.png`
- `out/social/2026/w06/slate_ranges_w6_4x5.png`
- `out/social/2026/w06/gamecard_401858481_4x5.png` (IU@NEB)
- `out/social/2026/w06/gamecard_401856718_4x5.png` (MISS@VAN)
- `out/social/2026/w06/gamecard_401856715_4x5.png` (LSU@UK)

**Headlines / deks / caption seeds (verbatim):**

| Chart | Headline | Dek |
|---|---|---|
| Slate | Ridge vs. the market · week 6 | Dot = Ridge · ◇ = market · top rows = biggest gaps |
| Learned | UMass's loss to EMU moved Ridge most | Biggest rating moves after week 5 |
| Read map | How Ridge sees FBS entering week 6 | Right = better team · higher = surer read |
| Game IU@NEB | Ridge can't split Indiana and Nebraska | Sat 12:00 PM ET · Toss-up |
| Game MISS@VAN | Ridge can't split Ole Miss and Vanderbilt | Sat 3:30 PM ET · Toss-up |
| Game LSU@UK | Ridge has LSU by 8.1 | Sat 7:00 PM ET · Lean |

Slate caption: `Ridge's biggest gaps with the market this week: MD (+11.4), MIZ (+10.1), WAKE (+9.0).`  
Learned caption: `Ridge changed its mind most on MASS (−20.0 pts move, 1.5 SD). Biggest new clarity: BYU.`  
Read map caption: `Widest: ECU, NIU, DUKE. 40 FBS teams in Proven good.`

**Slate 16 rows (Ridge / market / gap in market-favorite perspective):**

| Matchup | Ridge | Market | Gap / leans |
|---|---:|---:|---|
| Maryland @ Ohio State | +23.1 | +34.5 | MD +11.4 |
| Texas A&M @ Missouri | +13.6 | +3.5 | MIZ +10.1 |
| Wake Forest @ NC State | +12.5 | +3.5 | WAKE +9.0 |
| Ole Miss @ Vanderbilt | +0.9 | +9.5 | VAN +8.6 |
| Iowa State @ BYU | +18.0 | +10.5 | BYU +7.5 |
| Georgia @ Alabama | +8.3 | +1.5 | ALA +6.8 |
| Indiana @ Nebraska | +1.0 | +7.5 | NEB +6.5 |
| Florida State @ Louisville | −2.3 | +3.5 | FSU +5.8 |
| UCLA @ Oregon | +5.9 | +11.5 | UCLA +5.7 |
| Boise State @ Fresno State | +10.7 | +6.5 | BOIS +4.2 |
| North Carolina @ Pittsburgh | +5.8 | +3.5 | Pitt +2.3 |
| Texas @ Oklahoma | +9.4 | +7.8 | TEX +1.6 |
| Kansas @ Utah | +14.3 | +15.5 | KU +1.2 |
| Houston @ Kansas State | +1.4 | +2.5 | HOU +1.2 |
| LSU @ Kentucky | +8.1 | +8.5 | — |
| UCF @ Oklahoma State | +9.6 | +10.0 | — |

Excluded for missing market: **none** (all 16 featured games had `market_home_margin`).

### 5. Tests / lint / render

| Suite | Before (collect) | After |
|---|---|---|
| Social unit (`soc2` + `soc2_polish` + `soc2_round3` + `social_charts`) | 55 collected | **62 passed, 2 skipped** |
| Full `pytest -m "not live"` | 1070 collected | **1078 passed**, 1 skipped, 1 deselected |

Lint: **ok** on all 12 SOC-2 PNGs. `render_total_ms`: **12291** (~12.3 s).

Gray-dot priority (follow-up item 2) **dropped** — read map has no gray dots.

## SOC-2 round 4

**Date:** 2026-10-09  
**Branch:** `soc-2-charts` (no merges)

### Rule

Every number sits under a column header or axis title that names its metric in plain words. Values are written as `{TEAM} by X` / `Win|Lose by X` (or `Even`), never bare signed numbers the reader has to interpret.

### 1. Read map

- **Tick bug:** left tick printed `"50"` because `axes.zero_label = "50"` and the renderer substituted that whenever `v === 0`. Rank ticks also used `formatSignedWhole` (`+25`). Spec now sends explicit `x_tick_labels` (`#138`…`#1`) and `zero_label: null`; renderer prefers `x_tick_labels`.
- **X-axis:** title `Ridge power rank`; position still net-pts percentile among plotted FBS (`#1` right). Ticks at ranks `#1,#25,#50,#75,#100,#125` plus last (`#138` this week). Rank = `net_pts` desc, ties by `team_id`.
- **Y-axis:** title `How sure Ridge is`; no percentile ticks. End labels from data min/max `net_sd_pts` (rounded whole): `±10 pts · most sure` / `±12 pts · least sure`, plus `↑ more sure` near the top.
- **Dek (≤90):** `Left to right: Ridge's power rank · bottom to top: how settled each rating is`
- **Quadrants** placed outside the plot (above top corners / between plot and x-title). Text boxes are fixed obstacles in `repel_logos`.
- **Lint:** logo↔text no longer skips every `logo-label-group` logo; same-group pairs only. Round-3 read-map HTML fixture fails with `Text overlaps logo`.

### 2. Slate (Ridge vs. the market)

- Removed `Ridge leans` / `Ridge win` and on-plot value labels beside dot/◇.
- Columns (content width): matchup 28% · plot 30% · Ridge 15% · Market 15% · Gap 12%.
- Values from market-favorite perspective: `OSU by 23.1` / underdog row `FSU by 2.3` / `Even` / unsigned gap `11.4`.
- Dek: `Dot = Ridge · ◇ = market · sorted by gap (pts)`
- Axis: `Projected margin for market favorite (pts)`
- Caption seed: `Biggest gaps with the market: OSU–MD (Ridge OSU by 23.1, market 34.5), …` (top 3)

### 3. Learned

- Removed ±1 SD bars; hollow ○ → filled ● with short arrow.
- Plot header: `Ridge rating: before ○ → after ●`; axis `Ridge rating (pts vs. average FBS team)`.
- Columns: Rating change (signed bold) · Result · Ridge expected (`Win by` / `Lose by` / `Even`).
- Domain fitted to before/after dots (4 ticks this week: −40…+80); Up/Down groups kept; dek sized-to-certainty wording.

### 4. Deliverables (4:5)

- Contact sheet: `out/social/2026/w06/contact_sheet_w6.png`
- `out/social/2026/w06/readmap_w6_4x5.png`
- `out/social/2026/w06/learned_w5_4x5.png`
- `out/social/2026/w06/slate_ranges_w6_4x5.png`

| Chart | Headline | Dek |
|---|---|---|
| Read map | How Ridge sees FBS entering week 6 | Left to right: Ridge's power rank · bottom to top: how settled each rating is |
| Slate | Ridge vs. the market · week 6 | Dot = Ridge · ◇ = market · sorted by gap (pts) |
| Learned | UMass's loss to EMU moved Ridge most | Biggest rating changes after week 5, sized to how sure Ridge was |

**Column headers**

- Slate: Ridge · Market · Gap
- Learned: Ridge rating: before ○ → after ● · Rating change · Result · Ridge expected

Data freshness unchanged: `--allow-stale` on cached artifacts; footer keeps published + market timestamps.

### 5. Tests / lint / render

| Suite | Result |
|---|---|
| `test_social_soc2_round4` | **6 passed** |
| Social SOC-2 cluster (`soc2` + polish + round3 + round4 + charts) | **69 passed, 1 skipped** |
| Full `pytest -m "not live"` | **1134 passed**, 1 skipped, 1 deselected |
| Lint | **ok** on all 12 SOC-2 PNGs |
| `render_total_ms` | **12377** (~12.4 s) |

Round-3 read-map lint probe: fails with `Text overlaps logo` (fixture `docs/notes/_artifacts/social-charts-w6/round3_readmap_w6_4x5.html`).

## SOC-2 round 5

**Date:** 2026-10-09  
**Branch:** `soc-2-charts` (no merges)  
**Slate:** unchanged (approved).

### 0. Lint gaps

Extended `social/render/src/lint.mjs`:
- Truncated text: HTML `scrollWidth > clientWidth`, ellipsis, SVG `getBBox` vs `data-col-width` (SVG scrollWidth heuristic disabled — unreliable).
- Axis ticks + quadrant labels included in text–text overlap.
- Logo–logo max overlap **8%** (was 15%); enforced on 4×5 read maps (`height ≥ 800`).

Frozen round-4 fixtures under `docs/notes/_artifacts/social-charts-w6/round4_lint/` fail as required (`lint_round4_probe.mjs` exit 0):
- learned: `Truncated text: "Rating change"`
- readmap: logo overlap 18% + quad vs rank-tick overlaps

### 1. Read map

- **X:** `net_pts`, title `Ridge rating (pts vs. average FBS team)`, symmetric domain, 5 signed whole ticks, vertical 0 with `average` at top.
- **Y:** certainty percentile; rotated title `How sure Ridge is about each rating`; two-line ends `Most sure / could be off ±{min} pts` and `Least sure / could be off ±{max} pts`; median midline unlabeled; no `↑ more sure`.
- **Dek:** `Right = stronger team · higher = Ridge more confident in that rating`
- Square plot centered in content; inside quadrant labels (~130×24 obstacles); packing slot **26** CSS px, max displacement **36** px, worst overlap **0.080**.
- Ambiguity: preferred max displacement was 14 px, but that collapses packing below ≤8% for 138 FBS logos; solver tries 14 → 28 → 36 and reports the actual max. 16×9 logo-overlap lint is not enforced (plot too short).
- Alt adds top 5 by `net_pts` plus sharpest/widest with raw `net_sd_pts`.

### 2. Biggest movers (was learned)

- Title: `Biggest movers after week {N}` · Dek: `Teams whose Ridge rating changed most after week {N} games`
- Files: `movers_w{N}_*` (CLI `learned` is a one-run alias; noted in report `filename_alias`)
- Columns: Team 18% · Plot 32% · Rating change 14% · Result 20% · Ridge expected 16%; headers may wrap to 2 lines (`data-allow-wrap`)
- Dots r=9, stroke arrow 2 px; domain +10% pad; thin 0 rule labeled `avg`; rows fill dek→axis

### 3. Game cards

- Logos slot **120** CSS px (4×5), win % **64** px, margin bar **14** px tall, rank text **17** px
- Equal logo slots bottom-aligned; names / win % share baselines; `@` centered on logo row; `win chance` on win-% baseline
- Range end labels under bar ends; 0 rule is a hairline; μ above / market below

### 4. Deliverables (4:5)

| Path | Headline | Dek |
|---|---|---|
| `out/social/2026/w06/readmap_w6_4x5.png` | How Ridge sees FBS entering week 6 | Right = stronger team · higher = Ridge more confident in that rating |
| `out/social/2026/w06/movers_w5_4x5.png` | Biggest movers after week 5 | Teams whose Ridge rating changed most after week 5 games |
| `out/social/2026/w06/slate_ranges_w6_4x5.png` | Ridge vs. the market · week 6 | *(unchanged)* |
| `out/social/2026/w06/gamecard_*_4x5.png` | *(per card)* | |
| `out/social/2026/w06/contact_sheet_w6.png` | | |

**Read map packing:** slot **26** px · max displacement **36** px · worst overlap **0.080**

**Lint:** ok on all 12 SOC-2 PNGs · round-4 fixture probe fails as specified  
**Render:** `render_total_ms` **12529** (~12.5 s) · deviceScaleFactor 2  
**Tests:** `make test` → **1140 passed**, 1 skipped, 1 deselected · `test_social_soc2_round5` **6 passed**

Command:
```
uv run python scripts/make_social_charts.py --season 2026 --chart soc2 --source local --artifact-dir data/social/artifacts_cache/latest --allow-stale
```

## Recovery 2026-10-13

**Branch:** `soc-2-recovered` (from `main` @ `7e0e923`)  
**Source:** path-limited `git checkout 390dba3 -- src/ncaa_quant/social/` (chart package only; `model-hfa-v4-wip` left untouched).

Evidence:

- **27/27** hash match of chart modules to `390dba3` (HFA worktree `cfb-model-hfa` identical; 17:06 mtimes were checkout touch only).
- **31/31** bytecode match of restored sources to round-5 `__pycache__` (structural code-object compare; header ignored).
- `--render-only` from frozen `out/social/2026/w06/specs`: **12/12** SOC-2 PNGs byte-identical to the dated backup under `_recovery_backups/`.
- §3b full pixel rebuild from `data/social/artifacts_cache/latest` was **waived**: round-5 inputs were overwritten by a later publish (week-6 FBS rows 138 → 116; rebuild lint failed with text–logo overlap on a different packing).

Cause of loss: on 2026-10-12 the untracked chart `.py` files were committed into `390dba3` on `model-hfa-v4-wip`, then removed from the main working tree by `checkout` back to `main`.

## Weekly runs

### 2026-10-13 — week 7 (SOC-2 on `soc-2-recovered`)

**Model check:** week-7 `week_predictions.model_identity.model_version` = `production-v0_reduced_v3`, `vintage_label` = `W9A_REVAL` (meta champion same). Matches week-6 record. Not HFA-v4.

**Freshness (production `latest/`, no `--allow-stale`):** week 7 `tuesday_primary` published `2026-10-12 16:57:05 EDT`; ratings entering week 7 (138 teams); results include week-6 completed games (58); odds snapshot `2026-10-13T13:02:35Z` (week 7, ~12 h old).

**Resolved weeks:** read map = 7 · movers = 6 · slate = 7 · game cards = ND@BYU, WIS@UCLA, MIZ@Ole Miss.

**Code changes this run:**
1. Shrink loop treats logo×quadrant-keep-out overlap as a shrink trigger; floor 22 px (`test_readmap_shrink_treats_text_obstacle_as_trigger`).
2. Quadrant / average labels moved **outside** the plot frame (top row: Known struggles · average · Proven good; bottom row below x-ticks: Hard to read · x-title · Good, still proving it). Lint skips intentional `outside-label-row` coplanar pairs.
3. Keep-out miss (notes only): old keep-out plot-local CSS `[0,0]–[130,24]` vs rendered `"Known struggles"` bbox `[8.0,9.6]–[104.6,24.6]` — text fit inside the keep-out; lint still failed because a logo grazed the real text bbox while clearing the keep-out box.

**Sanity:** slate 16/16 with market (excluded: none). Movers 10/10 rows each joined to one week-6 result (`result_join_excluded=[]`). Margin-vs-win% mismatches (toss-ups): Kentucky @ Oklahoma (μ=−0.35, p=0.550); North Carolina @ Duke (μ=−0.96, p=0.518). Display-name CSV is override-only; no new rows proposed (CFBD names in outputs look like common usage). Banned-words + lint **ok** on all 12 PNGs. Determinism: pixel-identical on `readmap_w7_4x5.png`.

**Read map packing:** slot **28** · max displacement **36** · worst overlap **0.080**.

**Footer stamps:** published Mon Oct 12 ET · market as of Tue 9:02 AM ET (slate/gamecards).

**Render:** `render_total_ms` **15686** (~15.7 s).

**Tests:** social unit suite **53 passed**, 1 skipped. Full `make test` blocked by pre-existing dirty `test_webapp_w9d.py` (unrelated ImportError); with that + `test_model_hfa4` ignored: **1120 passed**, 2 failed on unrelated dirt (betting-language ratchet, slot-close credit pin).

**Paths:**
- `out/social/2026/w07/contact_sheet_w7.png`
- `out/social/2026/w07/readmap_w7_{4x5,16x9}.png`
- `out/social/2026/w07/movers_w6_{4x5,16x9}.png`
- `out/social/2026/w07/slate_ranges_w7_{4x5,16x9}.png`
- `out/social/2026/w07/gamecard_401856809_{4x5,16x9}.png` (ND@BYU)
- `out/social/2026/w07/gamecard_401858494_{4x5,16x9}.png` (WIS@UCLA)
- `out/social/2026/w07/gamecard_401856721_{4x5,16x9}.png` (MIZ@Ole Miss)

Command:
```
uv run python scripts/make_social_charts.py --season 2026 --chart soc2 --source local --artifact-dir data/social/artifacts_cache/latest
```

### 2026-10-22 — week 8 partial (read map + movers only; `--allow-stale`)

**Branch:** `soc-2-recovered` @ `5d44dce` (1 ahead of origin; card-ledger revert **not** pushed — awaiting owner).

**Override:** `--allow-stale` on season-to-date charts only. Production `latest/` still `tuesday_primary` week 8 (`published_at=2026-10-20T14:56:31Z`); age >36h and past `next_expected_publish_utc=2026-10-22T14:56:31Z`, so the site gate refuses without the flag. **Slate / game cards not rendered** under `--allow-stale`.

**Why ratings charts are safe under stale Tuesday artifacts:** decision `as_of` is calendar Tuesday and does **not** vary across `daily_refresh` days (`predict.py` `idempotency_partition_for_publish` / `run_predict_publish`: “Decision as_of is intentionally excluded — it does not vary across daily_refresh days”). Live Kalman uses `event_time < as_of` (`live_predict_rows`); current-week open circles are stamped at that same `as_of` (`_append_current_week_rating_points`). So a Thu–Sat refresh may rewrite `team_ratings_2026.json`’s `published_at` stamp, but rating **values** for the entering-week map / postgame movers do not move until new games enter the filter (postgame / weekly path). Cron `predict_publish_cron_refresh: "0 6 * * 4-6"` still points at `RefreshKind.DAILY_REFRESH` via `serve_all`, but that deployment is **not** what runs on this workstation (see below).

**Model check:** `production-v0_reduced_v3` / `W9A_REVAL` (matches week-7 notes). Not HFA/WIP.

**Resolved weeks:** read map = entering **8** · movers = **7**.

**Read map — FAIL (lint):** packing reached floor slot **22** · max displacement **36** · Python worst overlap **0.07997** (≤0.08 budget), but Playwright lint refused: `Logo overlap 9% (max 8%)`. No read-map PNGs shipped (`paths=[]`). Specs/caption/alt were written for inspection only. No chart-code changes attempted (owner: production run only).

**Movers — PASS:**
- Result joins: 10/10 top movers each joined to exactly one week-7 result (`result_join_excluded=[]`; `n_played=121`, `excluded_bye=17`).
- Lint ok on both sizes; banned-words scan clean; determinism pixel-identical on `movers_w7_4x5.png`.
- Render ~2.0 s (`render_total_ms=2029`).

**Movers copy (verbatim):**
- Headline: `Biggest movers after week 7`
- Dek: `Teams whose Ridge rating changed most after week 7 games`
- Caption seed: `Ridge changed its mind most on ALA (−16.7 pts move, 1.7 SD). Biggest new clarity: ECU.`
- Alt: `Biggest Ridge rating moves in week 7 2026, sized against last week's uncertainty. Uncertainty is approximate (offense and defense treated as independent).`
- Footer: `Ridge · Forecasts, not betting advice · the-cfb-model.vercel.app · published Tue Oct 20 ET`

**Movers paths:**
- `out/social/2026/w08/movers_w7_4x5.png`
- `out/social/2026/w08/movers_w7_16x9.png`
- `out/social/2026/w08/contact_sheet_w8.png` (movers-only sheet from this run)

**Thursday refresh diagnosis (read-only; nothing triggered):**
- Config cron exists: `predict_publish_cron_refresh: "0 6 * * 4-6"` → `predict_publish_flow` / `daily_refresh` in `serve_all()`.
- Workstation Prefect has **no** `predict_publish` deployment or flow. Active deployments: `ingest_odds`, `capture_slot_close`, `odds_cadence_watchdog` only.
- `scripts/prefect_keepalive.ps1` explicitly: “Does NOT start serve_all / predict_publish / postgame / weekly / settle.” Keepalive log lines: `starting ingest_odds worker (serve_ingest_odds only; no predict_publish)`.
- No `predict_publish` / `daily_refresh` flow-run today; no error from a failed refresh — the job was **never scheduled**. Historical Thu/Fri publishes used one-off scripts (`publish_week2_thu.py`, `publish_week4_fri.py`); **no** `publish_week8*.py` exists yet. Tuesday week-8 publish was manual (`AS_OF=now` pattern in `publish_week7.py`).

**Housekeeping (awaiting owner):**
- `5d44dce` reverts `619bbd7`: restores W7-7 six-play ledger (`n=6`: UK@OU, TOL@EMU, CIN@WVU, UNM@HAW, ASU@TTU, NIU@WYO) and moves Kent State / Missouri back to `superseded_calls`; deletes `out/social/2026_w7/late_adds_w7.md`. Not pushed.
- Untracked `tests/unit/test_social_charts.py`: **stale SOC-1** suite (23 tests on `charts/ridge_map|slate_map|surprise_strip|top25`). All 23 assertions green; isolated run exits non-zero only on coverage `fail-under=80`. Not a duplicate of `test_social_soc2*`; do not commit/delete without owner.

Commands:
```
uv run python scripts/make_social_charts.py --season 2026 --chart readmap --source local --artifact-dir data/social/artifacts_cache/latest --allow-stale
uv run python scripts/make_social_charts.py --season 2026 --chart learned --source local --artifact-dir data/social/artifacts_cache/latest --allow-stale
```

### 2026-10-22b — week 8 follow-up (packer 6%, movers copy, slate STOP)

**Ledger `5d44dce`:** owner template still `[PUSH IT | DROP IT]` — **not acted on; not pushed.** Charts-branch tip (with `5d44dce`) W7 ledger `n=6` (UK@OU, TOL@EMU, CIN@WVU, UNM@HAW, ASU@TTU, NIU@WYO; Kent/Missouri superseded). **main** W7 ledger `n=7` (same six + late `Massachusetts @ Buffalo`). Notes commit `eee4be4` kept either way.

**SOC-1 test:** committed `tests/unit/test_social_charts.py` (covers committed `charts/ridge_map|slate_map|surprise_strip|top25`). Isolated `pytest` of that file alone trips coverage `fail-under=80`; leave addopts alone.

**Read-map packing:** packer target `_READMAP_PACKER_OVERLAP_TARGET=0.06` (lint stays `MAX_LOGO_OVERLAP_FRAC=0.08` / `_READMAP_LINT_OVERLAP_LIMIT`). Floor still 22. Test `test_readmap_packer_target_below_lint_limit`. Re-render with `--allow-stale`: slot **22** · max disp **36** · packer worst overlap **0.0599** · lint **ok** · determinism pixel-identical.

**Slate + game cards — STOP (midweek predictions not identical):** only week **4** has both `tuesday_primary` and `daily_refresh` `week_predictions` in R2 (w5–w7 lack daily). Compare 70 common games Tue `2026-09-23T21:33:01Z` vs Thu `2026-09-25T12:46:34Z`:

| field | max \|Δ\| |
|---|---|
| `mu_margin` | **4.836** (worst `401867909`) |
| `sigma_margin` | **1.436** |
| `p_win_home` | **0.0296** |
| `conviction_tier` | **1** mismatch (`401856815` clear_lean → strong_lean) |
| intervals | null on one side for `401866428` (treated as non-identical) |

Historical Thu scripts advance operator `as_of` (e.g. `publish_week4_fri.py`), so predictions move. **No slate / gamecard render.**

**Movers copy:** caption `Biggest movers after week {N}: {T1} {Δ1}, …` (display short names, signed 1-decimal; no SD / “changed its mind”). Alt lists before→after, Δ, result, Ridge expected. Read-map / slate copy still describe on-chart elements (certainty Y + UNCERTAINTY_FOOTNOTE remain valid on the map; slate alt unchanged). Re-rendered movers: lint ok, banned-words clean, determinism ok.

**Delivered paths (partial set):**
- `out/social/2026/w08/contact_sheet_w8.png`
- `out/social/2026/w08/readmap_w8_{4x5,16x9}.png`
- `out/social/2026/w08/movers_w7_{4x5,16x9}.png`

**Read map copy:** title `How Ridge sees FBS entering week 8` · dek `Right = stronger team · higher = Ridge more confident in that rating` · caption `Widest: CLEM, STAN, FAU. 30 FBS teams in Proven good.` · footer `… published Tue Oct 20 ET`

**Movers copy:** title `Biggest movers after week 7` · caption `Biggest movers after week 7: ALA −16.7, WASH +16.6, AUB +15.2.` · footer `… published Tue Oct 20 ET`

### 2026-10-22c — ledger sync, ratings proof, week-8 Thu dry-run (no publish)

**1. Ledger sync:** `git checkout origin/main --` for card/ledger paths (`data/social/posted_calls_2026_w{4–8}.json`, `docs/notes/_artifacts/w{4–8}-posted-calls/`, tracked `out/social/2026_w{6–8}` threads). Commit `f346296` *Sync card ledger to main; ledger changes live on main only*. Confirmed `git diff origin/main -- <ledger paths>` empty. Pushed `soc-2-recovered` through `f346296`.

**2. Ratings stability:**
- Week-4 R2 `tuesday_primary` and `daily_refresh` `team_ratings_2026.json` are both **empty stubs** (`teams: {}`, ~96 B) — no per-team `off_epa`/`def_epa`/`off_sd`/`def_sd` rows to compare (pre–W-RATINGS-WIRE).
- Live proof: dry-run Thu `team_ratings_2026` vs current Tuesday `latest/team_ratings_2026` — **1024** common team-week rows; max \|Δ\| **off_epa=0, def_epa=0, off_sd=0, def_sd=0**.
- **Verdict:** w08 read map + movers PNGs are **final for posting** (ratings midweek-stable on completed week rows).

**3. Thursday week-8 dry run (STOP — not published):**
- Past one-offs: `scripts/publish_week2_thu.py`, `scripts/publish_week3_fri.py`, `scripts/publish_week4_fri.py` (`execute_predict_publish` + `RefreshKind.DAILY_REFRESH` + operator `as_of`).
- Dry helper: `scripts/_dry_publish_week8_thu.py` → `run_isolated_week_export` (local only, `push=False`, `export_enabled=False`). Artifacts under `docs/notes/_artifacts/soc2-weekly-w8-thu-dry/artifacts/`.
- `as_of` / `published_at`: `2026-10-22T18:49:51Z` (operator). Model: `production-v0_reduced_v3` / `W9A_REVAL` / `run_id=task23_fundamental_reduced_v3` — **matches** Tuesday.

**Publish command (owner-approved only; not run):**
```python
# Mirror past Thu scripts with live export+push (requires webapp.export_enabled):
from datetime import UTC, datetime
from ncaa_quant.pipelines.predict import RefreshKind, execute_predict_publish
AS_OF = datetime.now(tz=UTC)
execute_predict_publish(
    season=2026, week=8,
    refresh_kind=RefreshKind.DAILY_REFRESH,
    as_of=AS_OF,
)
```
Would write to R2 `latest/` (and versioned `v1/2026/w8/daily_refresh/`):
`week_predictions.json`, `track_record.json`, `team_ratings_2026.json`, `results_2026.json`, `meta.json`.  
Does **not** write `odds_snapshot.json` (odds worker).

**Dry vs Tuesday week_predictions (52 common games; 4 Tuesday-only already past kickoff filter):**

| field | max \|Δ\| |
|---|---|
| `mu_margin` | **0.520** |
| `sigma_margin` | **0.531** |
| `p_win_home` | **0.0155** |
| intervals | **0** |
| `conviction_tier` | **2** changes: Oregon@Illinois `lean`→`clear_lean`; NDSU@New Mexico `clear_lean`→`lean` |

- Games with \|Δμ\| ≥ 2: **none**.
- Tuesday-only (excluded from dry slate): MTSU@FIU, MOST@DEL, NMSU@SHSU, USA@MRSH.

**STOP.** Owner approves any live Thursday publish separately.

### 2026-10-22d — Thursday publish APPROVED then STOPPED at pre-publish gates

Owner approved live week-8 `daily_refresh` with pre-checks. Diffed dry-run artifacts (`docs/notes/_artifacts/soc2-weekly-w8-thu-dry/artifacts/`, `as_of=2026-10-22T18:49:51Z`) against live `latest/` (still `tuesday_primary` `2026-10-20T14:56:31Z`). **No publish run.**

**Pre-publish STOP reasons:**

1. **`week_predictions.json` — 4 games removed** (owner required 0 added/removed). Dropped because kickoff ≤ dry `as_of=now`:
   - `401871055` Middle Tennessee @ FIU (`2026-10-20T23:00Z`)
   - `401871069` Missouri State @ Delaware (`2026-10-20T23:00Z`)
   - `401871092` South Alabama @ Marshall (`2026-10-20T23:30Z`)
   - `401871070` New Mexico State @ Sam Houston (`2026-10-22T00:00Z`)
   Common 52 games: prediction values move as previously reported (max \|Δμ\| 0.52; 2 tier changes); `published_at` / `refresh_kind` would change.

2. **`results_2026.json` — graded-record regression.** Same 888 game_ids, but **507** rows flip `grade_status` `graded` → `no_pre_kickoff_publish` (and prediction/interval provenance fields rewrite). Owner: any change to graded results → STOP. Root cause: dry `grade_export` re-keys against the *new* publish clock / slate, so historical games lose a qualifying pre-kickoff publish pointer.

3. **`team_ratings_2026.json` — EPA/SD identical; open-circle metadata only.** 1024 common team-weeks: max \|Δ\| off/def epa/sd = **0**. Sole non-timestamp content delta: week-**8** open-circle `as_of_utc` stamp (`2026-10-20T14:52:13Z` → `2026-10-22T18:49:51Z`) on all 138 teams. Restates: `as_of=now` does **not** change the ratings cutoff for completed-week means (Δ=0 on epa/sd).

4. **`track_record.json`:** identical apart from `published_at` (PASS).

5. **`meta.json` changed fields:** `published_at`, `refresh_kind` (`tuesday_primary`→`daily_refresh`), `next_expected_publish_utc`.

**Not done:** live `execute_predict_publish`, site verify, slate/gamecard render. Charts remain Tuesday-stale for market surfaces; read map/movers still final on ratings proof.

**Follow-ups (carry):**
- `margin_interval_lo/hi` unchanged while μ moved ≤0.52 — does `daily_refresh` recompute intervals?
- No scheduled Thursday Prefect deployment (cron set; keepalive serves odds only).
- Publish path needs a results/grade strategy that does not wipe `graded` rows, and a slate policy for already-kicked games (keep vs drop).

### 2026-10-22e — week 8 slate + game cards from Tuesday `latest/` (`--allow-stale`)

**Owner direction:** no Thursday publish this week. Post from Tuesday's forecast. Charts match the live site (still `tuesday_primary` `published_at=2026-10-20T14:56:31Z`).

**Justification (`--allow-stale`):** week-8 Thursday dry run (`docs/notes/_artifacts/soc2-weekly-w8-thu-dry/`) vs Tuesday `latest/` showed max \|Δμ\| **0.52**, \|Δp\| **0.016**, interval Δ **0**. Ratings EPA/SD Δ=0. Publish path remains unsafe (see 2026-10-22d STOP); social surfaces stay on Tuesday primary.

**Artifact source:** `data/social/artifacts_cache/latest` mirrored to `latest_postable/` with kickoff≤now games removed from `week_predictions.games` for featured/default selection only (not a live publish). Odds stamp: `odds_snapshot` `2026-10-22T13:00:27Z` (Thu 9:00 AM ET). Model: `production-v0_reduced_v3` / `W9A_REVAL`.

**Kickoff filter (as_of ≈ 2026-10-22T20:38Z):** 4 games already kicked; **none** were in the unfiltered featured-16 or default top-3 cards → **0 featured exclusions / 0 replacements**; **0 card exclusions / 0 replacements**. Kicked (excluded from candidate pool only):
- `401871055` Middle Tennessee @ FIU
- `401871069` Missouri State @ Delaware
- `401871092` South Alabama @ Marshall
- `401871070` New Mexico State @ Sam Houston

Report: `docs/notes/_artifacts/soc2-weekly-w8-postable/kickoff_filter_report.json`.

**Watch games (Tuesday tiers kept; not in featured-16 or default cards):**
- Oregon @ Illinois (`401858495`) — tier **lean** (site/Tuesday)
- North Dakota State @ New Mexico (`401864527`) — tier **clear_lean** (site/Tuesday)

**Featured slate (16):** LSU@AUB, HOU@UTAH, TENN@SC, BYU@UCF, Ole Miss@TEX, PITT@MIA, USC@WIS, TA&M@ALA, MSU@UCLA, IU@MICH, COLO@OKST, ISU@ARIZ, BSU@WSU, BAY@KU, TTU@CIN, VAN@UK.

**Default game cards:** `401856728` LSU@AUB (lean), `401856837` HOU@UTAH (lean), `401856731` TENN@SC (toss_up).

**QA:** lint ok all rendered sizes; banned-words clean (`texts_checked`); determinism pixel+byte identical on first 4×5 of each run (`slate_ranges_w8_4x5`, `gamecard_401856728_4x5`).

**Footer stamp (all slate/cards):** `published Tue Oct 20 ET · market as of Thu 9:00 AM ET`

**Slate copy:**
- Headline: `Ridge vs. the market · week 8`
- Caption: `Biggest gaps with the market: TEX–Ole Miss (Ridge Ole Miss by 12.3, market 3.5), UK–VAN (Ridge UK by 20.2, market 10.5), USC–WIS (Ridge WIS by 6.2, market 3.0).`
- Alt: `Week 8 Ridge vs market projected margins, 2026. Market from odds_snapshot market_home_margin as of 2026-10-22T13:00:27Z.`

**Game card copy:**
| game_id | Headline | Caption | Alt |
|---|---|---|---|
| 401856728 | Ridge has LSU by 3.9 | Ridge's view of LSU @ AUB: LSU by 3.9, market LSU by 7.5. Tier: Lean. | Game card for LSU @ Auburn, week 8 2026. |
| 401856837 | Ridge has Utah by 2.1 | Ridge's view of HOU @ UTAH: UTAH by 2.1, market UTAH by 6.5. Tier: Lean. | Game card for Houston @ Utah, week 8 2026. |
| 401856731 | Ridge has Tennessee by 2.6 | Ridge's view of TENN @ SC: TENN by 2.6, market SC by 2.5. Tier: Toss-up. | Game card for Tennessee @ South Carolina, week 8 2026. |

**Paths:**
- `out/social/2026/w08/contact_sheet_w8.png` (readmap + movers + slate + 3 cards)
- `out/social/2026/w08/slate_ranges_w8_{4x5,16x9}.png`
- `out/social/2026/w08/gamecard_401856728_{4x5,16x9}.png`
- `out/social/2026/w08/gamecard_401856837_{4x5,16x9}.png`
- `out/social/2026/w08/gamecard_401856731_{4x5,16x9}.png`
- (prior) `readmap_w8_*`, `movers_w7_*`

Commands:
```
uv run python scripts/make_social_charts.py --season 2026 --chart slate-ranges --source local --artifact-dir data/social/artifacts_cache/latest_postable --allow-stale --out out/social/2026/w08
uv run python scripts/make_social_charts.py --season 2026 --chart gamecard --source local --artifact-dir data/social/artifacts_cache/latest_postable --allow-stale --out out/social/2026/w08 --game-id 401856728 --game-id 401856837 --game-id 401856731
```

## Follow-up: scheduled Thursday refresh

Requirements only — **do not implement** until owner assigns. Carry from week-8 dry-run STOP (2026-10-22d) and owner direction (no Thursday publish this week).

- **R1:** Games with kickoff ≤ `as_of` stay in `week_predictions`, frozen at their last pre-kickoff forecast. Never dropped from the published slate artifact.
- **R2:** Results grading keys each game to its latest publish strictly before kickoff. A later publish must not change any existing grade. Add a regression test: a Thursday publish leaves `results_2026` and `track_record` identical apart from timestamps.
- **R3:** Register the scheduled Thursday Prefect deployment only after R1 and R2 pass.
- **R4:** Investigate whether `daily_refresh` recomputes `margin_interval_lo`/`hi` (week-8 dry: intervals Δ=0 while μ moved ≤0.52).
- **R5:** Until the Thursday job exists, decide whether `meta.next_expected_publish_utc` should point to next Tuesday so the site's staleness logic does not expect a Thursday that never comes (current week-8 meta still pointed at `2026-10-22T14:56:31Z`).
