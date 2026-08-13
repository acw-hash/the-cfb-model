# W5 — Results / Track Record page

**Date:** 2026-08-13  
**Status:** Complete (W5-0 + Results page)  
**Authority:** `docs/webapp/DESIGN.md` §1, §4, §5.3; `docs/notes/23-readout.md`; `docs/notes/webapp-w1.md`; `docs/notes/webapp-w4.md`

---

## W5-0 — Verify cross-page nav (report; no fix)

This Week rows already link to `/game/{game_id}` (W4). W5-0 asked whether the reverse path exists and is obvious: a reader on a game page can get back to the slate. If missing, add a quiet §4 breadcrumb or header link, not a floating button.

**Verdict: not broken. No UI change.**

There is no site-wide header or `<nav>` in `layout.tsx` (banners only). The reverse path is a page-local C1 link, which is the §4-consistent affordance.

### Forward: This Week → Game Detail

`ThisWeekSlate` wraps every `GameRow` in `next/link`:

```tsx
<Link key={game.game_id} href={`/game/${game.game_id}`} className={styles.rowLink}>
  <GameRow game={game} />
</Link>
```

`rowLink` inherits color and has no underline; hover uses `--bg-secondary`. Isolated `/gallery` GameRow demos stay unlinked (W4).

**Live HTML (2026-08-13, `GET /` 200):** 56 `href="/game/{game_id}"` anchors — one per fixture-week game.

### Reverse: Game Detail → This Week

`GameDetail` renders, above the matchup `<h1>`:

```tsx
<p className={styles.back}>
  <Link href="/">This Week</Link>
</p>
```

| Check | Result |
|-------|--------|
| Destination | `/` — This Week slate (§5.1) |
| Placement | First element in `<article data-testid="game-detail">`, before `MatchupHeader` |
| Type | C1 (`13px / 16px`), `--text-secondary`, no underline; hover `--accent` |
| Chrome | Not `position: fixed` / `sticky`; not a `<button>` |
| Focus | Root `a:focus-visible` uses `--focus-ring` |

**Live HTML (2026-08-13, `GET /game/401628378` 200):**

```
<article … data-testid="game-detail"><p class="GameDetail_back__…"><a href="/">This Week</a></p><header …><h1 …>Mississippi State
```

The link is in the SSR HTML (not JS-only). Copy is the §5.1 page name, so the destination is readable without a chevron or “Back”. Browser Back is not required.

Unknown `game_id` (`not-found.tsx`) uses the same `This Week` → `/` link under the 404 copy. Same C1 quiet treatment; not a floating control.

### Why this is obvious enough

Root layout has no wordmark or persistent nav. Without the page-local link, a shared-URL landing would have only the browser Back button. The C1 line sits where a breadcrumb belongs — above the T1 matchup — quiet relative to the forecast, identifiable as a link on hover/focus. That matches the task’s “quiet breadcrumb or header link, not a floating button.”

### What was not added

- No floating / sticky back button
- No site-wide header (W5 Results / W6 About may want shared chrome later; not invented here)
- No `?order=` preservation on return; `/` is the kickoff-default slate (W3)

Lock: `webapp/site/tests/cross-page-nav.test.ts`.

## Acceptance commands (W5-0)

```
$ cd webapp/site
$ npm run test        # vitest — 61 passed (incl. 4 W5-0 nav cases)
$ make test           # pytest — 827 passed, 1 deselected
```

No UI files changed for W5-0.

---

## Built (Results page)

| Path | Role |
|------|------|
| `src/app/results/page.tsx` | `/results` — SSR + ISR 6h; loads `track_record` + `results_<meta.season>` |
| `src/app/gallery/results-states/page.tsx` | Dev-only doctored-clone gallery (W5-5) |
| `src/components/Results/` | Verdict, metrics, graded rows, tabs, scope |
| `src/lib/formatting/track-record.ts` | Recorded-value formatters + CI assert |
| `src/lib/results/` | Copy, grade-status labels, demo clones |
| `src/lib/artifacts/loader.ts` | `loadResultsSeason` + 2025 lockbox refuse |
| `tests/results.test.tsx` | CI-required, no-aggregate grep, states |
| `tests/capture-results-screenshots.mjs` | Playwright evidence (ephemeral; not a dep) |

---

## W5-1 — Recorded results

Every metric renders from `track_record.json` only: value, CI bounds (when `ci_kind ≠ none`), n, label, regime (basis), vintage, run, notes. No client recompute.

**Precision:** `formatRecordedPercent` / `formatRecordedNumber` use the artifact’s decimal places via `String(value)` — `50.7` stays `50.7%`, never §4.2 probability integer rounding to “51%”.

**CI-required:** `assertRateHasCi` throws `MissingConfidenceIntervalError` if `unit === "percent"` and a numeric value lacks both bounds. `MetricRow` calls it before render. Test covers throw + every fixture percent rate rendering with `[lo%, hi%]` beside the value.

### How the layout makes “this interval includes 50%” legible

Value and CI share **N1 / `--text-primary`** on one line (`50.7%` then `[48.7%, 52.7%]`). The interval is not C2 footnote weight. When `ci_lower ≤ 50 ≤ ci_upper`, a C1 line with a small mark states the factual geometry: “50 lies inside this interval” — no editorial claim about skill. Screenshot: `results-ci-treatment.png`.

---

## W5-2 — Verdict

**Label (verbatim from artifact):** `NOT CURRENTLY FIT TO BET`

**Lay-reader copy (drafted):**

> Point predictions are credible: the rating engine learns in-season, and the recorded error scores are in a sane range. No betting edge has been demonstrated against the closing line. Against-the-spread results sit around 50%, and their confidence intervals include 50%. Probabilistic scores lose to the market baseline.

**Recorded finding:** artifact `verdict.plain_language` shown under a C2 “Recorded finding” label (DESIGN §5.3 verbatim paragraph).

**Tone rationale:** Kicker is “Finding,” not “Disclaimer” or “Warning.” T2 label, B1 lay body — calm authority. No apology framing, no legal-hedge cascade. Sits directly under the page title (before tabs) so scanners cannot miss it; not a full-bleed red banner (avoids self-flagellation). Screenshot: `results-verdict.png`.

§5.3 asked for the artifact paragraph verbatim; W5-2 asked for lay-reader reasoning. Both appear: lay lead for scanning, recorded paragraph as the frozen finding.

---

## W5-3 — Graded games

Per-game rows show pre-kickoff μ + interval (`IntervalBand`), actual margin/total, interval hit/miss, tier chip, and `graded_from` (`refresh_kind` + `published_at`) with “locked before kickoff.”

Ungraded statuses from W1-2 / grade export — each explicit, never omitted:

| Status | Label | Demo |
|--------|-------|------|
| `graded` | Graded | fixture rows |
| `game_not_final` | Game not final | fixture Liberty @ App State + gallery clone |
| `no_pre_kickoff_publish` | No pre-kickoff publish | gallery clone |
| `postgame_missing` | Postgame missing | gallery clone |

### Fixture honesty (beyond layout FIXTURE banner)

Page-level callout when `results.fixture === true` (`data-testid="fixture-grades-note"`):

> The graded games below are development fixture data (season 2024, allow_historical_fixture). They are not Ridge’s published live track record. The live record begins with 2026 grades.

A reader on Graded games cannot mistake 2024 fixture grades for the live record: (1) root FIXTURE banner, (2) this section callout naming `allow_historical_fixture`, (3) Scope stating live publishing begins 2026.

### Lockbox / no aggregate over ≤2025

On-page (`data-testid="lockbox-no-aggregate"`):

> This page does not compute any aggregate accuracy statistic from graded games for seasons 2025 or earlier — no overall percentages, no interval-coverage totals. Per-game rows only. That absence is deliberate.

`loadResultsSeason(2025)` throws. Season selector is not offered for 2025. No hit-rate counts rendered from graded rows.

---

## W5-4 — Scope

`ScopeSection` states walk-forward 2019 + 2021–2024, 2025 lockbox never evaluated, live publishing begins 2026 with an empty graded record until Week 1 completes.

---

## W5-5 — States demonstrated

| State | Where |
|-------|--------|
| Empty live record (2026) | `/gallery/results-states` + `emptyLiveResults()` |
| Fixture-only view | `/results` with fixture artifacts + fixture grades note |
| Interval miss | Buffalo @ UConn (`401629032`) — gallery + fixture |
| Each ungraded status | gallery clones + fixture `game_not_final` |
| Missing track_record metric | gallery drops `fund_ats_snapshots` → “Not in the recorded artifact” |

---

## W5-6 — Visual evidence

Screenshots in `webapp/site/docs/screenshots/`:

| File | Subject |
|------|---------|
| `results-390-light.png` | Full Results, 390px, light |
| `results-390-dark.png` | Full Results, 390px, dark |
| `results-desktop-light.png` | Desktop light |
| `results-verdict.png` | Verdict section alone |
| `results-ci-treatment.png` | CI treatment alone |
| `results-empty-live.png` | Empty live record |
| `results-interval-miss.png` | Interval-miss row |

Capture: `tests/capture-results-screenshots.mjs` (Playwright ephemeral). **Zero console errors** on `/results` and `/gallery/results-states`.

### §4.4 Anti-pattern checklist

| Item | Verdict | Notes |
|------|---------|-------|
| no default-shadcn aesthetic | **PASS** | Hand-rolled CSS modules; text-first metric stack; no component-library chrome (`results-desktop-light.png`) |
| no purple-gradient heroes | **PASS** | Flat `--bg-primary`; orange is fixture/stale banners only (`results-390-light.png`, `results-390-dark.png`) |
| no emoji cards | **PASS** | No emoji in page copy or markup (`results-verdict.png`, `results-interval-miss.png`) |
| no wall-of-widgets | **PASS** | Vertical stack: finding → tabs → metric/graded rows → scope; no dashboard tile grid (`results-desktop-light.png`) |
| no gratuitous glassmorphism | **PASS** | Opaque surfaces, no blur (`results-ci-treatment.png`, `results-empty-live.png`) |
| no filler marketing copy | **PASS** | Artifact labels, recorded finding, and explicit “no single accuracy number” honesty only (`results-390-light.png`) |

### Positive-evidence sentences

1. **CIs as part of each number** — In `results-ci-treatment.png`, `50.7%` and `[48.7%, 52.7%]` share N1 primary weight on one line; “50 lies inside this interval” is geometry, not a caveat footnote.
2. **Verdict as confident honesty** — In `results-verdict.png`, “FINDING” + T2 `NOT CURRENTLY FIT TO BET` reads as a standing project conclusion, not an apology banner.
3. **No headline accuracy number is deliberate** — In `results-390-light.png`, the Recorded results section opens with “There is no single accuracy number for this model” before any metric; no composite score appears above the fold.
4. **Graded rows as a record, not a leaderboard** — In `results-interval-miss.png`, the row is chronological matchup + locked publish + hit/miss; no ranking, no streak, no win-rate chip.
5. **Empty live-record as “not yet”** — In `results-empty-live.png`, copy says “empty launch state, not a missing file” with the lockbox no-aggregate line still present.

---

## No-aggregate grep evidence

```
no-aggregate grep patterns: /overall accuracy/i | /composite score/i | /model quality/i | /\baccuracy:\s*\d/i | /\b\d+(\.\d+)?%\s*(accurate|correct|overall)\b/i | /\b\d+\s*\/\s*\d+\s*(correct|hits|intervals?)\b/i
no-aggregate hits: NONE
```

---

## Spec ambiguities resolved

1. **Tabs vs stack:** §5.3 names Tab A / Tab B. Implemented as client `ResultsTabs` (`?tab=games`); verdict + scope stay outside tabs so the finding is always visible.
2. **Lay vs verbatim verdict body:** Both (see W5-2).
3. **`GradedGame` nullability:** Types updated so ungraded rows may have null scores / null `graded_from` (matches export + fixture).
4. **Results file discovery:** `loadResultsSeason(season)` reads `results_<season>.json`; missing → empty state; 2025 refused.

---

## Acceptance commands

```
$ cd webapp/site
$ npm run typecheck
$ npm run test        # 79 passed (17 Results)
$ npm run lint
$ npm run build       # /results + gallery/results-states routes
```

*End of W5.*

---

## W6-0 note (2026-08-13)

§4.4 anti-pattern checklist was already in **table form** at W5 close (verdict + screenshot-cited note per item above). No backfill rewrite required. Site-wide nav, deferred in W5-0, is implemented in `docs/notes/webapp-w6.md`.
