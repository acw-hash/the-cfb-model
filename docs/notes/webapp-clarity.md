# Clarity pass — Results banner, About rewrite, readable game pages

**Date:** 2026-09-24  
**Scope:** `webapp/site/` code + `docs/webapp/DESIGN.md` amendments + this note.  
**Not in scope:** R2, publish, `src/ncaa_quant/`, artifact schemas, deploy (awaiting operator go-ahead).

## Phase 0 findings

### Verdict / VerdictBanner hits (`src/`, `tests/`)

Implemented name was `VerdictBlock` (no `VerdictBanner` component). Hits before removal:

| File | Notes |
|------|--------|
| `src/components/Results/VerdictBlock.tsx` | Deleted |
| `src/components/Results/ResultsPage.tsx` | Import/render removed |
| `src/app/gallery/results-states/page.tsx` | Verdict gallery section removed |
| `src/lib/about/copy.ts` | Honesty verdict line removed in rewrite |
| `src/lib/artifacts/types.ts` | `verdict` type retained (artifact unchanged) |
| `src/lib/results/copy.ts` | `VERDICT_LAY_SUMMARY` retained (unused by UI) |
| `tests/results.test.tsx` | Asserts banner **absent** |
| `tests/about.test.tsx` | No longer expects fit-to-bet string |
| `tests/capture-results-screenshots.mjs` | Verdict crop removed |
| `tests/fixtures-incomplete/track_record.json` | Fixture data only |

**Verdict hits outside `/results` and `/about` (reported, not removed):**  
`types.ts` schema field, `results/copy.ts` lay summary constant, fixture JSON, screenshot script (updated only for Results crop).

### Components

| Surface | Components | Tests |
|---------|------------|-------|
| This Week row | `GameRow`, `IntervalBand`, `ThisWeekSlate`, `HowToReadKey` | `interval-absence`, `this-week-*`, `payload-leak`, … |
| Game Detail | `GameDetail`, `PlainSummary`, `ForecastBlock`, `RevisionBlock`, `MoreDetail`, `RatingTrajectoryChart`, `ProvenanceStrip` | `game-detail*`, `interval-absence` |
| About | `AboutPage`, `src/lib/about/copy.ts` | `about.test.tsx` |

### Deploy mechanism

**NOT FOUND** as an explicit “Vercel Git Integration → branch X” document.

Evidence that exists:

- `webapp/site/vercel.json` — Next.js, `buildCommand: npm run guard`, region `iad1`
- `docs/notes/webapp-w7.md` — project rooted at `webapp/site/`; `.vercel/` was **not linked**; CLI login was out of date at W7
- `.github/workflows/site.yml` — CI typecheck/lint/test only; no deploy step
- No `docs/runbooks/ridge_deploy.md`

**Phase 2 must not proceed until the operator confirms the deploy path.**

### Tier thresholds in site code

**Not present** before this pass. Added once as `TIER_ENTER` in `src/lib/formatting/tier-copy.ts` (0.85 / 0.70 / 0.575) for explainers only — site still does not recompute tiers.

### `def_epa` sign

**Confirmed** (`docs/notes/14.md`): higher `def_epa` = more EPA suppressed = better defense. Chart captions state higher-is-better for both offense and defense.

## What changed

1. **Results** — Verdict banner removed; metrics table untouched; artifact `verdict` retained.
2. **About** — First-person rewrite; live worked example from `week_predictions`; schedule from `meta.publish_schedule`; no Results-verdict reference; §6.1/§6.2 verbatim.
3. **Game Detail** — Plain-English lead summary; team-named margins; “Typical miss” instead of σ; tier explainer; total “Combined score: about …”; provenance in collapsed More detail. Cover/over already withdrawn (ADR 0015) — reported, nothing to collapse.
4. **This Week** — Team-named favorite + margin; credible favored win %; range **omitted on rows** (too long at 390px when team-named); session-dismissible How to read key.

Shared helper: `src/lib/formatting/team-margin.ts` (+ unit tests).

## Decisions

- **This Week interval:** omit on the row; full range on Game Detail only (noted in DESIGN §5.1 amendment).
- **Cover/over:** already absent from schema 1.2.0; win chance lives in the plain summary; More detail holds provenance + a one-line note that those numbers are model metadata.
- **Tier thresholds:** single `TIER_ENTER` constant for copy; export remains authoritative for `conviction_tier`.

## Acceptance

```
cd webapp/site
npm run typecheck && npm run lint && npm run test && npm run build
```

| Metric | Before | After |
|--------|--------|-------|
| Vitest tests | 175 | 189 |

Difference: +team-margin unit tests, About worked-example / schedule cases, Results “banner absent” assertion, This Week `p_win_home` projection case; one interval-absence case dropped (reserved height for omitted range line). Net +14.

## Screenshots list

Under `docs/notes/_artifacts/webapp-clarity/shots/` (captured 2026-09-24 against local fixtures on `next dev`, `ARTIFACT_SOURCE=fixtures`):

| File pattern | Notes |
|--------------|--------|
| `home-{light,dark}-{390,desktop}.png` | Team-named margins + win %; How to read key |
| `game-home-fav-*` | `/game/401628373` Arkansas @ Texas A&M |
| `game-away-fav-*` | `/game/401628498` Ohio State @ Michigan State |
| `game-suppressed-sigma-LABELED-*` | Gallery doctored clone (`state-suppressed`) |
| `results-*` | No verdict banner |
| `about-*` | New copy + live worked example |

Console errors on capture: **NONE**.

## Fix round (2026-09-24)

### Interval nominals in fixture `week_predictions`
All 56 games: `margin_interval_nominal = 0.8` (null: 0). Clean tenth → "8 in 10".

### `VERDICT_LAY_SUMMARY`
Defined only in `src/lib/results/copy.ts`. **Not imported or rendered** anywhere after VerdictBlock removal (no components, metadata, OG, or banners). Left in place.

### Schedule local time
`meta.publish_schedule` values are day-of-week cadence labels (`Tue 06:00 UTC`), not ISO timestamps. **Left as UTC**; tooltip notes they are cadence labels. Cannot convert to viewer-local without inventing a calendar date.

## Final copy round (2026-09-24)

Exact About copy applied (identity, worked-example win-chance + sticky tier paragraph from `TIER_ENTER`, how-it-works range via `howItWorksRangeSentence`, won't-show, honesty). Next-update line from `meta.next_expected_publish_utc` (local + UTC tooltip; omitted if null/past). Section title contracted to "What Ridge won't show you". About re-shot only.

## Pre-deploy copy fix (2026-09-24)

- Range phrasing: shared `formatNominalCoveragePhrase` label is now `"N times in 10"` (was `"N in 10"`). About how-it-works: `"about 8 times in 10"` (was `"about 8 in 10 of the time"`). Game summaries use the same label via `formatIntervalLandSentence`.
- Lean threshold display: `formatProbability(0.575)` → `58%` via round-half-up that avoids the `0.575 * 100 → 57.4999…` FP trap (was `57%`).
- Acceptance: typecheck / lint / test (202) / build green.

## Phase 2 deploy

**Mechanism (operator-confirmed):** Vercel Git integration; production branch `main`; root `webapp/site`.
