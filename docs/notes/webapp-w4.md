# W4 — Game Detail page

**Date:** 2026-08-13  
**Status:** Complete  
**Authority:** `docs/webapp/DESIGN.md` §1, §2, §3, §4 (especially §4.3 chart spec), §5.2; `docs/notes/webapp-w2.md`; `docs/notes/webapp-w3.md`; `docs/notes/webapp-w1a.md` (W1A-FIX §2.3 exit-then-reassign)

---

## W4-0 — W2 primitive drift

W3 changed two W2 primitives. Original W2 notes were not rewritten; a **W3-AMENDED** section was appended to `docs/notes/webapp-w2.md`.

| Primitive | W3 change | Gallery after W3 |
|-----------|-----------|------------------|
| `IntervalBand` | μ is N1 (`--text-primary`); `[lo, hi]` is N2 (`--text-secondary`) | `/gallery` Interval bands section still shows `+4.1` heavier than `[-23.8, +33.4]`, plus the null-forecast “Forecast unavailable” demo |
| `GameRow` ≤640px | two-row compact grid (kickoff spanning; matchup + figures in column 2) | `/gallery` Game rows at 390px still render all four tiers, neutral, stale, and revised |

**Gallery evidence (2026-08-13, `GET /gallery` 200):** HTML contains every W2 demo heading and fixture label: Game rows, Tier chips, Interval bands, Badges & markers, Layout states, FIXTURE DATA, site-staleness copy, Strong/Clear/Lean/Toss-up, Revised, STALE, Forecast unavailable. Screenshots: `webapp/site/docs/screenshots/gallery-w4-0-light-desktop.png`, `gallery-w4-0-light-mobile.png`.

**§4.3 as written:** Neither change requires a spec amendment. The Game row pattern already names “N1 margin + N2 interval band inline”; the IntervalBand split implements that line. Interval band remains text-only `μ [lo, hi]`. The Game row ASCII is the ≥640px horizontal layout; the compact grid keeps the same elements, order, and type roles at mobile width.

No W2 primitive edits in W4. Axis labels wrap `<Figure>` in a positioned span rather than adding `style` to Figure.

---

## Built

| Path | Role |
|------|------|
| `src/app/game/[gameId]/page.tsx` | `/game/{game_id}` — SSR + ISR 6h, `generateStaticParams` from the slate |
| `src/app/game/[gameId]/not-found.tsx` | Unknown `game_id` — HTTP 404 inside root layout (banners intact) |
| `src/app/gallery/game-detail-states/page.tsx` | Dev-only doctored-clone gallery |
| `src/components/GameDetail/` | Matchup, forecast blocks, probabilities, revision, SVG trajectories, provenance |
| `src/lib/game-detail/` | PIT series collapse, SVG geometry, credibility, clones, provenance gloss |
| `src/components/ThisWeekSlate/` | Rows link to `/game/{game_id}` (unstyled `color: inherit`) |
| `tests/game-detail.test.ts` | PIT, gaps, two-band clone, σ-gating, forbidden copy |

---

## W4-1 — Route and data

**URL:** `/game/[gameId]` e.g. `/game/401628378`.

**Why this shape:** §5.2 names `/game/[gameId]`. `game_id` is the CFBD stable key (§1.2); it is already on every This Week row and does not change across refreshes.

**ISR:** `revalidate = 21600` on the game page, same 6h fallback as W3 / root layout. Loader is `loadArtifact()` for `week_predictions.json` + `team_ratings_2024.json`.

This Week rows wrap `GameRow` in `next/link` (`ThisWeekSlate`). Gallery isolated GameRow demos stay unlinked.

**Unknown `game_id`:** `notFound()` → segment `not-found.tsx`. HTTP 404, FIXTURE + staleness banners still from root layout, copy “Game not found” / “No published forecast matches this game.” Not a crash and not a redirect to `/`. Evidence: `game-detail-unknown.png`.

### Field map (every displayed field → named artifact field)

| UI element | Artifact field |
|------------|----------------|
| Matchup header | `away_team`, `home_team`, `neutral_site` |
| Kickoff | `kickoff_utc` |
| Margin μ | `mu_margin` |
| Margin σ | `sigma_margin` |
| Margin interval | `margin_interval_lo`, `margin_interval_hi` |
| Margin coverage | `margin_interval_nominal` |
| Total μ | `mu_total` |
| Total σ | `sigma_total` |
| Total interval | `total_interval_lo`, `total_interval_hi`, `total_interval_nominal` |
| Home win | `p_win_home` if `p_win_home_credible` (and `sigma_margin_credible`) |
| Cover (model ref) | `p_cover_home` if `p_cover_home_credible` |
| Over (model ref) | `p_over` if `p_over_credible` |
| Honest absence string | `renderNotComputed()` = “not computed”; `null_reason` when set |
| Forecast unavailable | `mu_margin` null + `null_reason` |
| Tier chip | `conviction_label` verbatim; `conviction_tier` only for chip style |
| Revised marker | `tier_revised_since_primary`, `conviction_tier`, `tier_primary` |
| Tuesday primary line | `tier_primary` via `TIER_GROUP_LABEL` (category name, not a composed label) |
| Vintage | `vintage_label` |
| Ensemble | `ensemble_scope_label` |
| Feature time | `feature_time_label` |
| Published stamp | `published_at` |
| Refresh kind | `refresh_kind` (§1.2 enum display labels) |
| STALE badge | `stale_stamp`, `stale_sources[]` |
| Rating series | `team_ratings_<season>.teams[home_team_id].weeks`, same for away — `week`, `as_of_utc`, `off_epa`, `def_epa`, `off_sd`, `def_sd` |
| Chart caption numbers | same rating fields after PIT collapse; formatted by `formatEpa` |

Nothing on screen is a client-invented number. Pace is read from the artifact but **not plotted** (see W4-3).

---

## W4-2 — Uncertainty presentation

**Margin (primary):** N1 μ, N2 `[lo, hi]`, N2 `σ`, C2 “{percent} nominal coverage”. Fixture week-5 rows carry `margin_interval_nominal = 0.8` → **80% nominal coverage**. A band whose coverage is unlabeled is not rendered.

**Total (secondary):** N2 μ (unsigned, §4.2), σ when present. v1 export: `total_interval_*` are **null on all 56 games**. The Total section is still shown; the interval reads “Interval not computed — Conformal/quantile bounds were not emitted for totals in this export.” (§1.8 meaning of null interval fields). Not omitted silently.

**Probabilities:** rendered only when the per-field `*_credible` flag is true **and** `sigma_margin_credible` is true (§1.8 σ-gating is authoritative). Otherwise the §1.8 string **“not computed”** plus `null_reason` when set. No bars, no 0% fallback. Liberty @ App State (`401640992`) is a live fixture case: cover/over null and not credible; home win still shown.

**How the layout says a point is not a certainty (no marketing copy):**

- The headline number is N1 / `--text-primary`. The interval sits beside it as N2 / `--text-secondary`, so the band is the quieter, honest object.
- Coverage is a C2 line under the figures (“80% nominal coverage”), not a slogan.
- Total is the same pattern at secondary billing (N2 μ, secondary color).
- On the chart, posterior mean is a 1.5px stroke; ±1 SD is `fill-opacity: 0.1` behind the line.

---

## W4-3 — Rating trajectories

**Source:** `team_ratings_2024.json`. Per team, per week: `off_epa`, `def_epa`, `pace`, `off_sd`, `def_sd`.

**Dimensions:** offense and defense small multiples, both teams on shared X (week 1…current) and a shared Y domain (mean ± 1 SD across both teams and both EPA dimensions).

**Pace excluded.** §4.3 names Y as `off_epa` / `def_epa` only. Pace is a different quantity (even though the dump is mean-centered). Two dimensions is the spec minimum; adding pace would mix scales and add series clutter against a calm chart.

**Band treatment:** taken from §4.3, not invented: “band: ±1 posterior SD (`off_sd`, `def_sd`)” and “light fill between mean ± SD; no extraneous gridlines.” One quiet y=0 hairline (league mean) is the only reference stroke.

**PIT + collapse:** the fixture dump repeats weeks and includes post-season `as_of_utc` labeled as week 1. `seriesForTeam` keeps the latest snapshot with `as_of_utc ≤ game.published_at` and `week ≤ game.week`. Week 5 snapshots dated after the Tuesday primary are dropped, so the open-circle “current week” mark appears only when a PIT-valid week-N point exists. Duplicate identical week rows collapse to one point.

**Gaps:** missing weeks (byes, or a doctored drop) split the path into new `M` segments. Never interpolated. Doctored clone removes week 3 for Arkansas and Texas A&M: `game-detail-gapped-ratings.png` shows 1–2 connected, week 3 empty, week 4 isolated dots.

Ohio State in the fixture has no week 3 (bye); the suppressed-sigma page shows that honest hole as well.

**Rendering:** inline SVG, no canvas, no charting library. Deterministic path strings (`n.toFixed(2)` in `lib/game-detail/geometry.ts`, not in UI). Axis labels are `<Figure variant="c2">` with tabular numerals. Height 200px / 280px per §4.3.

**Text alternative:** visible `figcaption` with current ratings and direction of travel. SVG is `aria-hidden`; if SVG fails, the caption remains.

**390px:** chart is `width: 100%` in the 44rem column with `overflow-x: hidden` on the page. It holds without horizontal scroll and without a tick-reduction. No reduction applied.

---

## W4-4 — Revision history and provenance

**Revised marker** when `tier_revised_since_primary` and the current tier is not suppressed. Tooltip is the W2/§2.5 sentence naming `{tier_primary} → {conviction_tier}`.

**Tuesday primary** is also on the page as a C2 line: `Tuesday primary: {TIER_GROUP_LABEL[tier_primary]}` — category name only, not a composed `conviction_label`.

**Two-band honesty (W1A-FIX §2.3):** hysteresis may reassign `strong_lean → lean` directly. The two-band clone keeps the fixture-verbatim Lean chip and sets `tier_primary=strong_lean`. Display: **Lean {Team}** + outline **Revised** + “Tuesday primary: Strong lean”. There is no Clear-lean chip, no step ladder, and no copy that implies a slide through the skipped band. Screenshot: `game-detail-two-band.png`.

**Provenance (all three, on-page gloss):**

| Label | Field | Plain language on the page |
|-------|-------|----------------------------|
| Vintage | `vintage_label` | Which graded training run produced these numbers. |
| Ensemble | `ensemble_scope_label` | Which models were combined. Reduced means a smaller set than the full experimental ensemble. |
| Feature time | `feature_time_label` | When inputs were frozen. Tuesday decision means later information is not in this forecast. |

Plus `PublishedAtStamp`, `refresh_kind`, and `STALE(source, age)` from `stale_stamp` when set.

---

## W4-5 — States (doctored clones; fixtures untouched)

| State | How | Evidence |
|-------|-----|----------|
| Suppressed sigma | Clone: `sigma_margin_credible=false`, probabilities/tier null, `null_reason=cold_start_insufficient`; μ kept | `game-detail-suppressed.png` — “not computed — cold start insufficient”; “Tier not shown” |
| Stale game | `stale_stamp=STALE(odds, 4.0h)` (age 4h, under the 6h tier gate) | `game-detail-stale.png` |
| Revised two-band | `tier_primary=strong_lean` on a fixture lean row | `game-detail-two-band.png` |
| Missing mid-season rating week | In-memory ratings clone, week 3 removed for both teams | `game-detail-gapped-ratings.png` |
| Null total interval | v1 export is already all-null; clone labeled | live game pages + `game-detail-null-total.png` |
| Unknown `game_id` | `/game/not-a-real-id` | `game-detail-unknown.png` (404, banners intact) |

Clones live in `src/lib/game-detail/demo-states.ts`. Committed JSON fixtures were not written.

---

## W4-6 — Visual evidence

| File | What |
|------|------|
| `game-detail-390-light.png` | Game detail, 390px, light |
| `game-detail-390-dark.png` | Game detail, 390px, dark |
| `game-detail-desktop-light.png` | Game detail, 1280px, light |
| `trajectory-chart-390.png` | Chart alone, 390px |
| `trajectory-chart-desktop.png` | Chart alone, desktop |
| `game-detail-two-band.png` | Two-band revision clone |
| `game-detail-suppressed.png` | Suppressed-sigma clone |
| `game-detail-stale.png` | Stale stamp |
| `game-detail-gapped-ratings.png` | Week-3 gap |
| `game-detail-null-total.png` | Null total interval |
| `game-detail-unknown.png` | Unknown game_id |
| `gallery-w4-0-light-desktop.png` / `-mobile.png` | W4-0 gallery still mounts W2 primitives |

Capture script: `webapp/site/tests/capture-game-detail-screenshots.mjs` (Playwright ephemeral, not a package.json dependency). Capture run: **zero console errors** on `/game/401628378`, `/gallery`, `/gallery/game-detail-states` (the unknown-id 404 is the not-found state, not an app error).

### §4.4 Anti-pattern checklist

| Item | Verdict | Notes |
|------|---------|-------|
| no default-shadcn aesthetic | **PASS** | Hand-rolled CSS modules; no component library |
| no purple-gradient heroes | **PASS** | Flat `--bg-primary`; orange is fixture/stale banners only |
| no emoji cards | **PASS** | No emoji in page copy or markup |
| no wall-of-widgets | **PASS** | Vertical stack: matchup → margin → total → probs → tier → chart → provenance |
| no gratuitous glassmorphism | **PASS** | Opaque surfaces, no blur |
| no filler marketing copy | **PASS** | Artifact labels and §1.8 absence strings only |

### Positive evidence (required)

- **The uncertainty band reads as quieter than the point forecast.** `game-detail-390-light.png` shows N1 `+15.3` in primary next to N2 `[-0.7, +55.8]` in secondary, with “80% nominal coverage” as C2 under the figures — not as a badge or slogan.
- **The trajectory chart reads as calm, not as a BI chart.** `trajectory-chart-390.png` is two small multiples, solid vs dashed for the two teams (no team colors), one hairline at 0, no grid, no legend box, no chrome.
- **The sd bands are legible without obscuring the lines.** Same file: `fill-opacity: 0.1` fills sit behind 1.5px mean strokes; both teams remain readable where bands overlap.
- **Provenance labels are present without dominating the page.** `game-detail-390-light.png` keeps vintage / ensemble / feature time as C2 blocks under the chart, after the forecast — tertiary type, not a hero strip.
- **The two-band revision reads as one honest change, not a slide.** `game-detail-two-band.png` shows the current Lean chip, a quiet Revised outline, and “Tuesday primary: Strong lean”. No Clear-lean chip and no Strong → Clear → Lean sequence.

---

## Decisions / ambiguities

1. **Pace omitted** — defended above; §4.3 does not put it on the chart.
2. **§4.3 band treatment exists** — ±1 SD light fill; W4 did not invent a treatment and did not STOP.
3. **Probabilities are text, not bars.** §1.8 says “no probability bar” on refusal; drawing meters on the credible case would add widget chrome. Percents in N2 cannot be misread as a 0% bar when suppressed.
4. **Tuesday primary uses `TIER_GROUP_LABEL`**, not a composed “Strong lean {Team}”, because the artifact does not store Tuesday’s `conviction_label`.
5. **Cover / over copy** uses the spec’s “model ref” wording. No pick, bet, edge, line, or market string in Game Detail markup (tested).
6. **Ratings fixture duplicates and future `as_of`:** collapsed and PIT-filtered rather than plotted raw. Fixtures were not modified.
7. **FCS opponents** (Holy Cross, UT Martin, Wagner) have no ratings entry; the caption states that school’s ratings are not in this publish.

---

## Acceptance commands

```
$ cd webapp/site
$ npm run typecheck   # pass
$ npm run test        # vitest — 57 passed
$ npm run lint        # eslint + prettier — pass
$ npm run build       # next build — pass; /game/[gameId] SSG + revalidate 6h
```

`GET /game/401628378` and `GET /gallery/game-detail-states`: zero browser console errors on the capture run.

---

*End of W4 task notes.*
