# W3 — This Week page

**Date:** 2026-08-13  
**Status:** Complete  
**Authority:** `docs/webapp/DESIGN.md` §1, §2, §3, §4, §5.1; `docs/notes/webapp-w2.md`

W2 §4 visual acceptance was operator-reviewed, not externally verified. W3 screenshots are the first external check of GameRow at real 56-game slate density.

---

## W3-0 — Gitignore anchoring

### `git status --ignored webapp/site/` (before)

```
On branch main
Your branch is ahead of 'origin/main' by 4 commits.

Ignored files:
  (use "git add -f <file>..." to include in what will be committed)
	webapp/site/.next/
	webapp/site/node_modules/
	webapp/site/tsconfig.tsbuildinfo

nothing to commit, working tree clean
```

Tracked `webapp/site/src/lib/**` files were already in the index (W2 added them with `-f` or before the collision was noticed). **New** files under that tree were ignored:

```
.gitignore:13:lib/	webapp/site/src/lib/this-week/sort.ts
.gitignore:13:lib/	webapp/site/src/lib/new-w3-file.ts
.gitignore:9:dist/	webapp/site/dist/bundle.js
.gitignore:7:build/	webapp/site/build/index.js
webapp/site/.gitignore:3:out/	webapp/site/out/index.html
```

Unanchored scaffold collisions reported and fixed:

| Rule | Was | Now |
|------|-----|-----|
| `lib/` | matched any `lib/` path segment | `/lib/` (repo-root Python packaging only) |
| `lib64/` | same | `/lib64/` |
| `dist/` | matched `webapp/site/dist/` | `/dist/` |
| `build/` | matched `webapp/site/build/` | `/build/` |
| `out/` | already scoped in `webapp/site/.gitignore` | unchanged; site also gained `build/` and `dist/` |

### Nested-lib staging without `-f` (after)

```
git check-ignore -v webapp/site/src/lib/w3-0-probe.txt
(not ignored — check-ignore exit 1)

git add webapp/site/src/lib/w3-0-probe.txt
A  webapp/site/src/lib/w3-0-probe.txt
```

Probe file was unstaged and deleted; no content landed from this step. `this-week/*.ts` later staged normally.

---

## Built

| Path | Role |
|------|------|
| `src/app/page.tsx` | `/` This Week — server-rendered from W2 loader |
| `src/app/layout.tsx` | `revalidate = 21600` ISR fallback |
| `src/app/this-week.module.css` | 44rem measure |
| `src/app/gallery/this-week-states/page.tsx` | Dev-only doctored-clone gallery (404 in production) |
| `src/components/ThisWeekHeader/` | Season/week, PublishedAtStamp, refresh_kind |
| `src/components/ThisWeekSlate/` | Client sort/group over already-loaded games |
| `src/components/SortControl/` | Visible ordering control |
| `src/components/SlateGroupHeader/` | Day / tier group rule |
| `src/components/OffseasonState/` | Offseason + pre-first-publish copy |
| `src/lib/this-week/sort.ts` | Comparators, grouping, tie-breaks |
| `src/lib/this-week/refresh-kind.ts` | §1.2 enum display labels |
| `src/lib/this-week/demo-states.ts` | In-memory clones; fixtures never written |

### W2 component tweaks required by W3 visual acceptance

Sanctioned list was “new page-level components only.” Two existing primitives had to move so §4.3 / W3-4 / W3-5 could pass at slate density (W2 gallery never showed 56 rows):

1. **`IntervalBand`** — μ is now N1 (`--text-primary`); `[lo, hi]` is N2 (`--text-secondary`). W2 rendered the whole `μ [lo, hi]` as one N2 string, so the band could not be quieter than the margin it accompanies.
2. **`GameRow.module.css` (≤640px)** — two-row compact grid (kickoff spanning, matchup + figures) instead of a three-row stack. 56 stacked cards would not read as a scores app at 390px.

Tokens, fixtures, schemas, and DESIGN.md were not modified.

---

## W3-1 — Page, data, ISR

**ISR interval:** `revalidate = 21600` (6 hours) on the root layout and the This Week page.

**Why it matches the publish schedule:** §3 names on-demand revalidation after every R2 push as primary, and `revalidate: 21600` on layout as the time-based fallback. Primary publish is Tue 06:00 UTC; refresh is Thu–Sat 06:00 UTC. Six hours is shorter than the gap between those slots and long enough that a missed webhook still loads the next generation without polling R2 every request.

Route `/` is server-rendered from `meta.json` + `week_predictions.json` via `loadArtifact()`. Sort toggle is client-side over that payload (`history.replaceState`, no `router.push`, no refetch).

### Field map (every displayed field → named artifact field)

| UI element | Artifact field |
|------------|----------------|
| Page title “Week {n}” | `meta.week` |
| Season kicker | `meta.season` |
| Published timestamp | `meta.published_at` |
| Refresh kind | `meta.refresh_kind` (labels from §1.2 enum names) |
| FIXTURE banner | `meta.fixture` (layout, W2) |
| Site staleness banner | `meta.published_at`, `meta.next_expected_publish_utc` (layout, W2) |
| Game list | `week_predictions.games[]` |
| Kickoff | `kickoff_utc` |
| Day group header | calendar day of `kickoff_utc` (visitor TZ) |
| Matchup | `away_team`, `home_team`, `neutral_site` |
| Headline margin | `mu_margin` |
| Interval bounds | `margin_interval_lo`, `margin_interval_hi` |
| Tier chip | `conviction_label` (hidden when null); `conviction_tier` only selects chip style |
| Conviction group header | `conviction_tier` category name, not composed per-game copy |
| Within-tier order | `conviction_basis.p_favored` (artifact only; never recomputed) |
| Revised marker | `tier_revised_since_primary`, `tier_primary`, `conviction_tier` |
| Per-game stale | `stale_stamp` when `is_stale` |
| Forecast unavailable | `mu_margin` null + `null_reason` |
| Offseason copy | empty `week_predictions.games[]` |

Nothing on screen is a client-invented number. `conviction_label` is rendered verbatim.

---

## W3-2 — Sort and group

Both orderings shipped.

**Default: BY KICKOFF**, grouped by visitor-local calendar day of `kickoff_utc`. This Week’s job is the week’s slate, in the order a scores app is read — who plays when. The spec’s Apple Sports benchmark and the task’s own gloss (“grouped by day, scores-app default reading”) agree. BY CONVICTION is the analytical overlay: useful, one click away, not the first frame. The Order control always shows the active option; `?order=conviction` is an optional visible URL indicator written with `replaceState` (reload without the param returns to kickoff; nothing is stored in `localStorage`).

### Tie-break (tested)

| Order | Primary | Tie-break |
|-------|---------|-----------|
| Kickoff | `kickoff_utc` ascending | `game_id` lexicographic |
| Conviction | tier rank strong → clear → lean → toss-up → null | `conviction_basis.p_favored` descending (null last), then `game_id` lexicographic |

Empty groups are omitted. A one-game Strong lean group is a correct outcome.

---

## W3-3 — States

Fixtures on disk were not written. Clones live in `src/lib/this-week/demo-states.ts` and `/gallery/this-week-states` (dev-only).

| State | How demonstrated | Evidence |
|-------|------------------|----------|
| **Sparse top tier** | Real week-5 fixture: 1 `strong_lean` in 56 | `this-week-conviction-sparse.png`; test |
| **Empty top tiers** | Clone = fixture **subset** with strong_lean and clear_lean rows removed. No field values invented. | `this-week-empty-top-tiers.png`; test |
| **Suppressed tiers** | Clone: `sigma_margin_credible=false`, conviction_* null, `null_reason=cold_start_insufficient`; `mu_margin` kept | `this-week-stale-revised.png` row 3 (no chip); test |
| **Stale games** | Clone: `stale_stamp="STALE(odds, 4.0h)"` (age 4.0h, under §2.4 6h gate, so the chip remains) | `this-week-stale-revised.png` row 1 |
| **Revised** | Clone of a fixture **lean** row: `tier_primary=strong_lean`, `tier_revised_since_primary=true`. Current numbers and `conviction_label` stay fixture-verbatim. | `this-week-stale-revised.png` row 2 |
| **Offseason** | Clone: `games: []` | `this-week-offseason.png` — “Season complete — view Results.” not an empty list |

§5 also names “No FBS games scheduled this week.” W3-3.6 maps a meta-pointed week with zero games to the offseason copy; that discriminator is used here. Results href deferred to W5 (copy is verbatim, no dead link).

### Two-band revision display honesty (strong_lean → lean)

Hysteresis exit-then-reassign (W1A-FIX §2.3) can jump two bands. The row shows **only the current** `conviction_label` (here a fixture-verbatim Lean chip) plus the quiet Revised marker. The marker tooltip names both endpoints (`strong_lean → lean`). There is no intermediate Clear-lean chip, no step ladder, and no copy that implies a gradual descent.

---

## W3-4 — Density and mobile

**390px:** two-row GameRow, quiet `--bg-secondary` day/tier rules, sticky header with prominent PublishedAtStamp. Full-slate captures: `this-week-full-390-light.png`, `this-week-full-390-dark.png`.

**Desktop measure:** `max-width: 44rem` (704px), centered. §4.3 is scores-app density, not a full-bleed table: a 1280px viewport must not stretch kickoff / matchup / figures across the window. 44rem holds the three-column row without a cavernous matchup gap. Capture: `this-week-full-desktop-light.png`.

---

## W3-5 — Visual evidence

| File | What |
|------|------|
| `webapp/site/docs/screenshots/this-week-full-390-light.png` | Full 56-game slate, 390px, light |
| `webapp/site/docs/screenshots/this-week-full-390-dark.png` | Full 56-game slate, 390px, dark |
| `webapp/site/docs/screenshots/this-week-full-desktop-light.png` | Full slate, 1280px, light, BY KICKOFF |
| `webapp/site/docs/screenshots/this-week-conviction-sparse.png` | BY CONVICTION, single-item Strong lean group |
| `webapp/site/docs/screenshots/this-week-empty-top-tiers.png` | Doctored clone — list opens at Lean |
| `webapp/site/docs/screenshots/this-week-stale-revised.png` | Stale + Revised + suppressed pair/triple |
| `webapp/site/docs/screenshots/this-week-offseason.png` | Zero-game offseason copy |

Screenshot path follows the W2 convention (`webapp/site/docs/screenshots/`). Capture script: `webapp/site/tests/capture-this-week-screenshots.mjs` (Playwright ephemeral, not a package.json dependency).

### §4.4 Anti-pattern checklist

| Item | Verdict | Notes |
|------|---------|-------|
| no default-shadcn aesthetic | **PASS** | Hand-rolled CSS modules; no component library |
| no purple-gradient heroes | **PASS** | Flat `--bg-primary`; orange is the W2 fixture/stale banners only |
| no emoji cards | **PASS** | No emoji in page copy or markup |
| no wall-of-widgets | **PASS** | Header + order control + grouped rows |
| no gratuitous glassmorphism | **PASS** | Opaque sticky bar, no blur stacks |
| no filler marketing copy | **PASS** | Spec empty-state sentences; no tagline |

### Positive evidence (required — absence checks are not enough)

- **The slate reads as a scores app, not a dashboard.** `this-week-full-desktop-light.png` is a centered column of day headers and horizontal matchup rows — kickoff, teams, one figure — with no widget grid.
- **The interval band is quieter than the margin figure it accompanies.** `this-week-stale-revised.png` shows N1 `+4.1` in primary and N2 `[-23.8, +33.4]` in secondary on the same baseline.
- **The tier chip is legible without dominating the row.** `this-week-full-390-light.png` keeps C1 pills to the right of the matchup; team names remain the heaviest type.
- **Numbers align on the tabular grid across mixed-sign margins.** `this-week-stale-revised.png` places `+4.1`, `+4.8`, and `-1.6` on `tabular-nums` via Figure.
- **A row carrying both a stale badge and a revised marker retains its hierarchy.** The stale+revised capture puts matchup first, N1 margin second, C1 Lean chip third, then STALE (semantic-stale) or Revised (outline only); the suppressed third row has margin and no fabricated chip.

---

## Acceptance commands

```
$ cd webapp/site
$ npm run typecheck   # pass
$ npm run test        # vitest — 43 passed
$ npm run lint        # eslint + prettier — pass
$ npm run build       # next build — pass; / revalidate 6h
```

`GET /` and `GET /gallery/this-week-states` on the capture run: **zero browser console errors**. Layout-level FIXTURE + staleness banners inherit from W2 (fixture `published_at` 2024-09-24 vs 2026-08-13 clock).

Repo `make test`: 827 passed.

---

## Decisions / ambiguities

1. **Default order is kickoff** — defended above; conviction is one click, never a hidden preference.
2. **Zero-game week uses offseason copy** per W3-3.6, not the distinct “No FBS games scheduled this week.” sentence. Spec lists both; the task maps this case to offseason.
3. **Tap-row → Game Detail** deferred to W4. Rows are not links (no speculative `/game/[id]`).
4. **Empty-top-tiers clone is a subset**, not relabeled strong/clear rows, so no invented `conviction_label` or contradicting `p_favored`.
5. **Two-band revised clone** keeps the lean row’s fixture numbers and only sets revision flags, so screenshots do not invent a new μ.
6. **`p_favored` is never recomputed** in the client; sort reads `conviction_basis.p_favored` only.

---

*End of W3 task notes.*
