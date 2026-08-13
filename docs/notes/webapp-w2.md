# W2 — Next.js scaffold + Ridge design system

**Date:** 2026-08-13  
**Status:** Complete  
**Authority:** `docs/webapp/DESIGN.md` §1, §3, §4

---

## Built

### Scaffold (`webapp/site/`)

| Path | Role |
|------|------|
| `src/app/layout.tsx` | Root layout — schema gate, fixture banner, staleness banner |
| `src/app/gallery/page.tsx` | Dev-only component gallery (404 in production) |
| `src/lib/artifacts/` | Hand-derived TS types, loader, schema-version gate |
| `src/lib/formatting/` | §4.2 number rules + time/staleness helpers |
| `src/styles/tokens.css` | Sole source of §4 palette, spacing, type scale literals |
| `src/components/` | Figure, GameRow, IntervalBand, TierChip, StaleBadge, RevisedMarker, PublishedAtStamp, MaintenanceState, StalenessBanner, FixtureBanner |
| `tests/` | Schema sync, formatting, schema-version, tabular guard, staleness |
| `eslint-plugin-ridge/` | `require-figure-for-numbers` lint rule |
| `scripts/check-tokens.mjs` | §4 token diff-check |

### Artifact layer

- **Types:** Hand-derived from committed JSON Schemas in `src/ncaa_quant/webapp/schemas/` plus field tables in DESIGN §1 (schemas are minimal; full shapes follow export contract).
- **Sync proof:** `tests/schema-sync.test.ts` validates all five W1 fixture files with Ajv against committed schemas; structural assertions on required TS fields.
- **Loader:** `loadArtifact()` reads `ARTIFACT_BASE_PATH` (default `webapp/fixtures/`) locally or `ARTIFACT_BASE_URL` for prod R2 (W7 wiring).
- **Schema gate:** `SUPPORTED_SCHEMA_MAJOR = 1`; major mismatch → `MaintenanceState` (tested with doctored `2.0.0` in `schema-version.test.ts`).

### FIXTURE banner — impossible to omit

Rendered in **`layout.tsx` only**, after `meta.json` load:

```tsx
{showFixtureBanner ? <FixtureBanner /> : null}
```

No page component can suppress it: the banner is not imported or conditionally skipped by routes — it sits above `{children}` in the root layout. Every route inherits layout.tsx per App Router semantics.

---

## Token diff-check evidence

```
$ npm run check:tokens
Token diff-check PASSED — all §4.1/§4.2 values match tokens.css
```

All `--bg-*`, `--text-*`, `--accent`, semantic colors, and `--type-*` sizes/lines match DESIGN §4 tables exactly (script compares lowercased hex against `scripts/check-tokens.mjs` EXPECTED map).

---

## Tabular numeral guard

1. **CSS:** `.figure { font-variant-numeric: tabular-nums lining-nums; }` on `Figure` primitive.
2. **ESLint:** `ridge/require-figure-for-numbers` bans `.toFixed()` in `src/components/` and `src/app/` (allowed in `lib/formatting/`).
3. **Test:** `tests/tabular-numeral-guard.test.ts` asserts Figure CSS + no forbidden patterns in UI paths.

---

## Number formatting unit tests

`tests/formatting.test.ts` — 13 cases:

- Margin sign + 1 decimal; precision cap vs σ
- σ prefix label
- Total 1 decimal, no sign
- Probability 68% vs 9.4% thresholds
- Interval `μ [lo, hi]` band
- Honest absence: em dash, "not computed", "Forecast unavailable" + `null_reason` tooltip
- Doctored ADR 0014 `cold_start_insufficient` (no `null_reason` in committed fixtures; case covered per §1.8 spec)

---

## Gallery — fixture game IDs used

| Component demo | Fixture `game_id` | `conviction_tier` |
|----------------|-------------------|---------------------|
| Strong lean row | `401628378` | `strong_lean` |
| Clear lean row | `401628377` | `clear_lean` |
| Lean row | `401628373` | `lean` |
| Toss-up row | `401628498` | `toss_up` |
| Neutral site | first `neutral_site: true` in slate | — |

Doctored demos (inline clones, fixtures unmodified):

- **Stale:** cloned from `401628373` with `stale_stamp: "STALE(odds, 4.0h)"`, tier suppressed
- **Revised:** cloned from `401628377` with `tier_primary: "lean"`, `tier_revised_since_primary: true`
- **Null forecast:** cloned from `401628498` with `mu_margin: null`, `null_reason: "cold_start_insufficient"`
- **Staleness banner:** doctored meta dates (`published_at` 2024-09-20, `next_expected` 2024-09-21)
- **Maintenance:** `MaintenanceState` component standalone

Gallery excluded from production: `notFound()` when `NODE_ENV === "production"` + `robots: { index: false }`.

---

## Visual review — screenshots

Captured at `webapp/site/docs/screenshots/` (Playwright one-off script; not a runtime dependency):

| File | Viewport | Theme |
|------|----------|-------|
| `gallery-light-desktop.png` | 1280px | light |
| `gallery-dark-desktop.png` | 1280px | dark |
| `gallery-light-mobile.png` | 390px | light |
| `gallery-dark-mobile.png` | 390px | dark |

Layout-level staleness + FIXTURE banners visible on all captures (fixture `published_at` is Sep 2024; current date triggers §3.2 site staleness honestly). Maintenance and doctored staleness panels appear in the gallery "Layout states" section at page bottom.

---

## §4.4 Anti-pattern checklist

| Item | Verdict | Notes |
|------|---------|-------|
| no default-shadcn aesthetic | **PASS** | Hand-rolled CSS modules; no component library. |
| no purple-gradient heroes | **PASS** | Flat `--bg-primary` backgrounds only. |
| no emoji cards | **PASS** | No emoji anywhere in gallery. |
| no wall-of-widgets | **PASS** | Single-column list + small inline demos. |
| no gratuitous glassmorphism | **PASS** | No blur/translucent card stacks. |
| no filler marketing copy | **PASS** | Gallery labels name components only. |

---

## Dependencies (policy compliance)

**Runtime:** `next`, `react`, `react-dom`

**Dev:** `typescript`, `eslint`, `eslint-config-next`, `@eslint/eslintrc`, `prettier`, `vitest`, `ajv`, `ajv-formats`, `@types/*`

**Not added:** Tailwind, shadcn/radix/mui/chakra, charting libraries, CSS frameworks.

Local `eslint-plugin-ridge` is an in-repo file package (zero npm deps).

---

## Acceptance commands

```
$ cd webapp/site
$ npm run typecheck   # tsc --noEmit — pass
$ npm run test        # vitest — 27 passed
$ npm run lint        # eslint + prettier — pass
$ npm run check:tokens
$ npm run build       # next build — pass; gallery 404 at runtime in prod
```

Dev gallery: `npm run dev` → `/gallery` (zero server console errors on GET).

---

## Decisions / ambiguities

1. **Hand-derived types** — JSON Schemas are intentionally minimal (W1); TS types follow DESIGN §1 field tables. Ajv validates fixtures against committed schemas; TS structural tests guard required fields schemas omit.
2. **Gallery `/` root** — returns 404 (no placeholder landing; real pages are W3–W6).
3. **Live staleness in dev** — fixture `meta.published_at` (2024-09-24) correctly triggers site staleness banner with today's clock; demonstrates §3.2 honestly rather than freezing `Date`.
4. **Screenshot tooling** — Playwright used via ephemeral `npm install --no-save` for W2-4 evidence only; not listed in `package.json` dependencies.

---

*End of W2 task notes.*

---

## W3-AMENDED — primitive drift (2026-08-13)

W3 changed two W2 primitives so the 56-game This Week slate could pass §4.3 density. The original W2 text above is unchanged; this records the tree as it stands after W3.

1. **`IntervalBand`** — μ is N1 (`--text-primary`); `[lo, hi]` is N2 (`--text-secondary`). W2 rendered the whole `μ [lo, hi]` string as one N2 figure, so the band could not read quieter than the margin.

2. **`GameRow` at `≤640px`** — two-row compact grid (kickoff spanning rows 1–2; matchup + figures in column 2) instead of a three-row stack. 56 stacked cards would not read as a scores app at 390px.

**§4.3 as written:** Neither change requires a spec amendment. The Game row pattern already names “N1 margin + N2 interval band inline”; the IntervalBand split implements that line. Interval band remains text-only `μ [lo, hi]` with no error-bar graphics. The Game row ASCII is the ≥640px horizontal layout; the compact grid keeps the same elements, order, and type roles at mobile width.

W4-0 gallery check (2026-08-13): `/gallery` still mounts every W2 demo (GameRow for all four tiers + neutral + stale + revised, IntervalBand including null forecast, TierChip, StaleBadge, RevisedMarker, PublishedAtStamp, StalenessBanner, MaintenanceState, layout FIXTURE + site-staleness banners). Evidence in `docs/notes/webapp-w4.md` (W4-0) and `webapp/site/docs/screenshots/gallery-w4-0-*.png`.
