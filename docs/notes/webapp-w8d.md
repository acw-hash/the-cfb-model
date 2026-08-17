# W8-D — RSC projection, demo-states split, token/spec sync, W8-A completion

**Date:** 2026-08-17  
**Status:** Complete  
**Closes:** W8-A, W8-C, W8-SPLIT-DEMO, W8-TOKENS-SPEC  
**Authority:** `docs/webapp/DESIGN.md` §1, §4.1, §4.4, §6.3; `docs/webapp/TASKS.md` cross-cutting 1–5; `docs/notes/webapp-w8a.md`; `docs/notes/webapp-w6.md`

No artifact-contract change. No export/push/tier edits. No R2 writes or deletes. No analytics. No new npm dependency.

Production measurements used `ARTIFACT_SOURCE=fixtures npm run build && npm start -- -p 3462` (Next.js 15.5.23 production server), not `next dev`.

---

## 1. Claims-vs-reality (W8-A claims this task retested)

| W8-A claim | W8-D test | Held? |
|------------|-----------|-------|
| This Week RSC payload leaks full `GamePrediction[]` (`p_cover_home` ×56, etc.) | D1.5 greps on production-build `/` HTML | **HELD, then fixed.** After projection: every non-allowlist field **0** hits; allowlist fields **56** (one per game). |
| Leak is model-internal (no market fields) | Market-name test + payload greps | **HELD.** `p_cover_home`/`p_over` were unlabelled JSON keys, not books/lines. DTO drops them. |
| `TrackRecordSection` imports `demo-states` helpers (hygiene, not fabricated fallback) | Import graph + call-site types | **HELD, then fixed.** `metricById(track.metrics, id)` still returns `TrackRecordMetric \| undefined`; module path decoupled. |
| a11y AFTER was `next dev`; BEFORE was production — not comparable | D4 axe on `next start` at 320/390/desktop both themes | **HELD as a measurement defect.** Production-build AFTER: **0** axe violations all 24 cells. W8-A's dev-server zeros are no longer the evidence. |
| WCAG 2.1 AA includes 1.4.10 at 320px; W8-A tested 390 + desktop only | D4 width matrix + document `scrollWidth` | **HELD as a coverage gap.** 320 added. Page-level overflow **0** on all four pages. See D4 overflow notes. |
| L6 “AA met” | D4.4 restatement | **OVERCLAIM.** Restated: axe on production build + named manual checks; **no screen-reader pass**. Successor **W8-SR**. |
| `check-tokens.mjs` and DESIGN §4.1 still list pre-AA tertiary | D3 wiring + hex/contrast | **HELD.** More important: `check:tokens` existed but was **not** in `npm test`, `npm run lint`, or CI. Guard was decorative. Now wired into `npm test` with AA ratio assertions. |
| Betting-language grep PASS without pasted command | D5.1 | **HELD as an evidence gap.** Command + full output pasted below. |
| DESIGN §3.3 amended (pointer only) | D5.2 | **HELD as an evidence gap.** Full §3.3 pasted below. |
| D3 contract-and-present list archived, not pasted | D5.3 | **HELD as an evidence gap.** Archived file pasted below. |
| Fixture restore after live-scope push | D5.4 `list_objects_v2` | **HELD after inventory.** `latest/*` is 2024 week-5 `fixture: true`, `published_at=2024-09-24T06:00:00Z`. No `v1/2026/` keys. Audit prefixes remain. |
| L3 OPEN / counsel | D6 | **OPERATOR ACCEPTED RISK.** Verbatim operator record appended to `webapp-w6.md`. |

---

## 2. D1 — This Week client payload projection

### 2.1 Field provenance (consumption, not judgment)

Kickoff **grouping** uses `Intl.DateTimeFormat().resolvedOptions().timeZone`, so sort/group **cannot** run entirely server-side. `p_favored` is flattened from `conviction_basis` (the rest of that object does not cross). `SortControl` / `SlateGroupHeader` / `ThisWeekHeader` consume no per-game artifact fields (header uses page-level `season`/`week`/`published_at`/`refresh_kind` from `meta.json`).

| Field | Consuming component | What it renders / does |
|-------|---------------------|------------------------|
| `game_id` | `ThisWeekSlate` | Row key; `href=/game/{game_id}` |
| `away_team` | `GameRow` | Matchup heading `{away} @ {home}` |
| `home_team` | `GameRow` | Matchup heading |
| `kickoff_utc` | `GameRow`; `sort.ts` | Local kickoff (C2) + UTC tooltip; kickoff sort + local-day groups |
| `neutral_site` | `GameRow` | Quiet `N` marker when true |
| `mu_margin` | `IntervalBand` | N1 signed margin (or forecast-unavailable) |
| `sigma_margin` | `IntervalBand` | Rounding input for displayed μ / band (§4.2 precision cap); σ itself is not shown on the row |
| `margin_interval_lo` | `IntervalBand` | N2 `[lo, hi]` lower |
| `margin_interval_hi` | `IntervalBand` | N2 `[lo, hi]` upper |
| `null_reason` | `IntervalBand` | Tooltip/copy when μ is null (`Forecast unavailable`) |
| `conviction_tier` | `TierChip`; `sort.ts` | Chip CSS class; conviction sort/group |
| `conviction_label` | `TierChip` | Chip text verbatim |
| `tier_revised_since_primary` | `RevisedMarker` | Show/hide “Revised” |
| `tier_primary` | `RevisedMarker` | Tooltip `{tier_primary} → {conviction_tier}` |
| `stale_stamp` | `StaleBadge` | Badge text e.g. `STALE(odds, 4.0h)` |
| `stale_sources[].source` | `StaleBadge` | Tooltip source name |
| `stale_sources[].age_hours` | `StaleBadge` | Tooltip age |
| `stale_sources[].last_good_at` | `StaleBadge` | Tooltip last-good timestamp |
| `p_favored` (derived from `conviction_basis.p_favored`) | `sort.ts` | Conviction tie-break (descending; null last). **Not shown** as a labelled figure |

`IntervalBand`, `RevisedMarker`, and `StaleBadge` are shared with Game Detail. Their prop shapes were **not** narrowed, so Game Detail (outside the sanctioned list) keeps compiling. The DTO still carries every field those This Week descendants actually read.

### 2.2 DTO

`ThisWeekGame` in `webapp/site/src/lib/artifacts/types.ts`. Projection: `projectThisWeekGame` / `projectThisWeekGames` in `webapp/site/src/lib/this-week/project.ts`. Server Component `app/page.tsx` calls `projectThisWeekGames(week.games)` before passing into `ThisWeekSlate`.

### 2.3 Sort stays client-side

Visitor-local calendar grouping needs the timezone. Conviction sort needs `p_favored` as a number — derived server-side, not the raw `conviction_basis` object.

### 2.4 Behavior / screenshots

- Vitest: `renderToStaticMarkup(ThisWeekSlate games={week.games}) === renderToStaticMarkup(ThisWeekSlate games={projectThisWeekGames(week.games)})` — identical HTML.
- Vitest: kickoff and conviction order, plus group ids, identical on full `GamePrediction[]` vs DTO.
- `/results`, `/about`, `/game/[gameId]` were not visually edited. Pixel delta from D1 is **none by construction**.
- Production-build captures (after): `docs/notes/_artifacts/webapp-w8d/shots-after/*.png` (four pages × two themes × 390/desktop).

§4.4 anti-pattern list (verbatim) vs this visual set — no visual redesign; projection only:

```
- no default-shadcn aesthetic
- no purple-gradient heroes
- no emoji cards
- no wall-of-widgets
- no gratuitous glassmorphism
- no filler marketing copy
```

### 2.5 Production-build `/` payload greps

Executed against `http://127.0.0.1:3462/` (`next start`, fixtures). Status 200, 86678 bytes. Counts:

```
conviction_basis	0
mu_sigma_ratio	0
p_cover_home	0
p_over	0
p_cover_home_credible	0
p_over_credible	0
p_win_home	0
p_win_home_credible	0
home_team_id	0
away_team_id	0
conference_game	0
mu_total	0
sigma_total	0
sigma_total_credible	0
total_interval_lo	0
total_interval_hi	0
sigma_margin_credible	0
margin_interval_nominal	0
conviction_team	0
is_stale	0
vintage_label	0
ensemble_scope_label	0
feature_time_label	0
champion_version	0
run_id	0
hysteresis_applied	0
favored_side	0
raw_tier	0
previous_tier	0
--- allowlist samples ---
p_favored	56
conviction_tier	56
stale_stamp	56
game_id	56
mu_margin	56
```

HTML saved: `docs/notes/_artifacts/webapp-w8d/prod-home.html`.

### 2.6 Server/Client classification and sibling over-serialization

| Surface | Classification | Same class of leak? |
|---------|----------------|---------------------|
| `TrackRecordSection.tsx` | **Server Component** (no `"use client"`). Imported from `ResultsPage` (server). | No. Metric helpers were a module-path issue (D2), not RSC JSON of demo rows. |
| `/results` | `ResultsTabs` is a Client Component that receives **already-rendered children slots**, not `track`/`results` objects. | **No.** Grep of production `/results` HTML: `conviction_basis`, `p_cover_home`, `p_over`, `run_id`, `champion_version`, `mu_sigma_ratio`, `home_team_id` all **0**. |
| `/game/[gameId]` | `GameDetail` and descendants are Server Components. | **Not the same class.** `p_cover_home` / `p_over` appear **3 times** as **labelled** ProbabilityList copy (`Cover (model ref)` / `Over (model ref)`). `run_id` / `champion_version` / `conviction_basis` / `home_team_id`: **0**. Operational extras do not ride a client flight. Not fixed here. |

---

## 3. D2 — demo-states decoupled from `/results`

Moved `EXPECTED_METRIC_IDS` and `metricById` to `webapp/site/src/lib/results/metrics.ts`. `demo-states.ts` re-exports them. Call sites:

- `TrackRecordSection.tsx` → `@/lib/results/metrics` (public results)
- `demo-states.ts` → `@/lib/results/metrics` (gallery keeps importing demo-states)

### 3.1 Production import search (per module)

```
rg "demo-states" webapp/site/src
```

Remaining hits (2026-08-17):

| Module | Importers |
|--------|-----------|
| `lib/results/demo-states.ts` | `app/gallery/results-states/page.tsx` only |
| `lib/this-week/demo-states.ts` | `app/gallery/this-week-states/page.tsx`; `lib/game-detail/demo-states.ts` |
| `lib/game-detail/demo-states.ts` | `app/gallery/game-detail-states/page.tsx` |

`TrackRecordSection` no longer imports any demo-states module. Tests still import demo clones (`tests/results.test.tsx`, `tests/this-week-states.test.ts`, `tests/game-detail.test.ts`) — not production routes.

### 3.2 Client bundle (D2.3)

Searched production `.next/static` for distinctive demo literals `demo-ungraded`, `Demo Home`, `Home U`, `cloneUngradedStatuses`: **no matches**.

Those strings **do** appear in `.next/server/app/gallery/results-states/page.js` (server bundle of the 404-gated gallery).

**Finding (now moot for `/results`):** even **before** the split, `TrackRecordSection` is a Server Component. Fabricated demo rows were not in the client JS bundle; the defect was the **module path** coupling a public page to a file whose purpose is fabricated gallery data. After the split, the public import graph no longer reaches that file.

### 3.3 Undefined branch (honest absence)

```53:62:webapp/site/src/components/Results/TrackRecordSection.tsx
            {expectedIds.map((id) => {
              const metric: TrackRecordMetric | undefined = metricById(track.metrics, id);
              return (
                <MetricRow
                  key={id}
                  metric={metric ?? null}
                  expectedId={id}
                  expectedLabel={metric?.label ?? id}
                />
              );
            })}
```

`MetricRow` when `metric == null` renders a table row with `data-testid={metric-missing-${id}}` and copy `Not in the recorded artifact` (`MISSING_METRIC_COPY`). Covered by `tests/results.test.tsx`. Does **not** silently omit. DESIGN §1.8 honest absence: **held**.

### 3.4 Test vs lint rule

Chose a **vitest import-graph walk** (`tests/no-demo-states-in-production-routes.test.ts`), not `eslint-plugin-ridge`. Gallery under `src/app/gallery/**` must keep importing demo-states; a `src/app/**` lint ban would false-fail gallery or miss re-exports through `lib/`. Production entries (`page.tsx` / `layout.tsx` / `route.ts` / `robots.ts` / `not-found.tsx`, excluding gallery) are walked; test **passes**.

---

## 4. D3 — token/spec sync and the guard

### 4.1 Wiring answer (the more important finding)

**Before this task:** `scripts/check-tokens.mjs` was **not** executed by `npm test`, `npm run lint`, or GitHub CI.

Evidence:

- `package.json` had `"test": "vitest run"` and a unused `"check:tokens": "node scripts/check-tokens.mjs"`.
- `"lint": "eslint src && prettier --check ."`.
- `.github/workflows/ci.yml` runs uv/ruff/mypy/pytest only — **no npm**.

That is why W8-A could report 111 tests + clean lint while the script still asserted `#aeaeb2` / `#636366`. **The guard was decorative.** Hex drift in DESIGN §4.1 was the symptom; unwired check was the defect.

**After:** `"test": "vitest run && node scripts/check-tokens.mjs"`. Contrast pairs from the W8-A ratio table assert WCAG 1.4.3 AA normal **4.5:1**.

### 4.2 Bite proof

Deliberate local change `--text-tertiary: #aeaeb2` (pre-AA):

```
Token diff-check FAILED:
light --text-tertiary: expected #75757a, got #aeaeb2
contrast light --text-tertiary/--bg-primary: 2.21:1 < AA 4.5:1
EXIT=1
```

Revert:

```
Token diff-check PASSED — all §4.1/§4.2 values match tokens.css
… light --text-tertiary/--bg-primary: 4.58:1 (>= 4.5) …
REVERT_EXIT=0
```

`tokens.css` was not left modified.

### 4.3 DESIGN §4.1 as amended (tertiary only; `tokens.css` untouched)

| Token | Light | Dark | Use |
|-------|-------|------|-----|
| `--bg-primary` | `#FFFFFF` | `#000000` | Page background |
| `--bg-secondary` | `#F5F5F7` | `#1C1C1E` | Group headers, table zebra |
| `--text-primary` | `#1D1D1F` | `#F5F5F7` | Body, team names |
| `--text-secondary` | `#6E6E73` | `#98989D` | Labels, kickoff time |
| `--text-tertiary` | `#75757A` | `#8E8E93` | Provenance, footnotes |
| `--accent` | `#0071E3` | `#0A84FF` | Links, focus ring |
| `--semantic-stale` | `#BF4800` | `#FF9F0A` | Site + input stale banners |
| `--semantic-revised` | `#6E6E73` | `#98989D` | Quiet "Revised" badge |
| `--semantic-positive` | `#1D1D1F` | `#F5F5F7` | Favored margin (not green/red betting) |
| `--border-subtle` | `#D2D2D7` | `#38383A` | Row dividers |

---

## 5. D4 — production-build a11y, 320px, L6 correction

Command: `RIDGE_A11Y_BASE=http://127.0.0.1:3462 RIDGE_A11Y_LABEL=prod-current node tests/a11y-pass.mjs`  
Tags: `wcag2a`, `wcag2aa`, `wcag21aa`. Artifact: `docs/notes/_artifacts/webapp-w8d/a11y-prod-current.json`.

No a11y CSS/markup was changed in this task (axe counts already 0 on the production build). **Before = after** on this build. W8-A's incomparable dev-server AFTER is retired as evidence.

### 5.1 Violation counts (production `next start`)

All cells **0** violations. No allowlisting. No rule downgrades.

| Page | theme | 320 | 390 | desktop |
|------|-------|-----|-----|---------|
| `/` | light | 0 | 0 | 0 |
| `/` | dark | 0 | 0 | 0 |
| `/game/401628373` | light | 0 | 0 | 0 |
| `/game/401628373` | dark | 0 | 0 | 0 |
| `/results` | light | 0 | 0 | 0 |
| `/results` | dark | 0 | 0 | 0 |
| `/about` | light | 0 | 0 | 0 |
| `/about` | dark | 0 | 0 | 0 |

### 5.2 Horizontal overflow at 320px

`document.documentElement.scrollWidth === clientWidth` (**viewport overflow 0**) on every page/theme/width, including 320.

Internal `scrollWidth > clientWidth` (not page 2D scroll):

- **This Week matchup `h3`:** `white-space: nowrap` + ellipsis. Long names clip; full names remain on Game Detail via the row link. Not an axe hit.
- **Track-record `tableWrap`:** `overflow-x: auto`, 428px table vs ~288px at 320. WCAG 1.4.10 excepts two-dimensional **data tables**. This is the W8-A focusable scroll region. Page itself does not 2D-scroll.
- **RatingTrajectoryChart:** **0** overflowing nodes at 320 (SVG `width: 100%`).

No CSS redesign (would be a visual change). Successor if stacked 320 layout is wanted: **W8-REFLOW-TABLE** (optional; not a current axe fail).

### 5.3 Corrected L6 wording (also in `webapp-w8a.md`)

Verified by axe `wcag2a` / `wcag2aa` / `wcag21aa` on a **production build** (`next start`) at **320 / 390 / desktop** in both themes, plus the named W8-A manual checks (keyboard focus screenshots, trajectory figcaption text equivalent, `prefers-reduced-motion`, semantic table, About heading order), **with no screen-reader verification**. Successor for a screen-reader pass on the Results table and the RatingTrajectoryChart text equivalent: **W8-SR**.

axe covers a minority of WCAG 2.1 AA success criteria. This task does not claim more than that.

---

## 6. D5 — W8-A acceptance items that came back by reference

### 6.1 No-betting-language grep (command + full output)

```
rg -n -i --pcre2 "best bet|yes bet|\bplay\b|edge vs market|\bunits\b" docs/webapp/DESIGN.md docs/webapp/TASKS.md
rg_exit=1
```

Empty stdout, exit 1 (ripgrep: no matches). **PASS (zero matches)** with the command shown.

Second W0 pattern (explicit “does not” / verdict / CDN edge allowed):

```
rg -n -i --pcre2 "\b(bet|pick|play|edge|units)\b" docs/webapp/
```

```
docs/webapp/TASKS.md:7:**Product constraint (all tasks):** Ridge is **not** a betting-recommendations product. No picks, lines, or edge claims in code, copy, or artifacts.
docs/webapp/TASKS.md:150:- Verdict banner: **NOT CURRENTLY FIT TO BET** with full plain-language paragraph — unrounded, unsoftened.
docs/webapp/TASKS.md:173:- "What Ridge does not show" section lists no picks/lines/edge explicitly.
docs/webapp/DESIGN.md:7:Ridge is a read-only view over `predict_publish` outputs. It is **not** a betting-recommendations product: no picks, no lines, no edge claims, anywhere on the site or in its artifacts.
docs/webapp/DESIGN.md:165:| `p_win_home_realized` | float \| null | 1.0 or 0.0 for Brier post-hoc; not displayed as a "pick" |
docs/webapp/DESIGN.md:180:    "label": "NOT CURRENTLY FIT TO BET",
docs/webapp/DESIGN.md:181:    "plain_language": "Point-prediction machinery is credible (weekly MAE curve passes, MAE/CRPS sane, A2 Clause A confirms in-season learning on the rating engine) but no edge vs the close is demonstrated (ATS straddles ~50% on fundamental REGRADED_V2; log-loss loses universally to 0.693; CLV unmeasurable) and two §1.6 instruments remain unmeasurable (CLV; honest OU via possessions)."
docs/webapp/DESIGN.md:214:- Verdict string: **NOT CURRENTLY FIT TO BET**
docs/webapp/DESIGN.md:468:  subgraph cloud["Public edge"]
docs/webapp/DESIGN.md:482:4. **Next.js** — Server Components fetch from R2 public URL (or Cloudflare Worker proxy if bucket is private-with-signed-edge); cache with ISR.
docs/webapp/DESIGN.md:733:**Verdict display (exact):** **NOT CURRENTLY FIT TO BET** — with the full plain-language paragraph from §1.4 verbatim.
rg_exit=0
```

Site copy recommendation-framing:

```
rg -n -i --pcre2 "best bet|yes bet|edge vs market|lock it in|must bet|recommended bet" webapp/site/src
rg_exit=1
```

Empty stdout. **PASS.**

### 6.2 DESIGN §3.3 as amended (full text)

```
### 3.3 Security boundary

| Asset | Exposure | Notes |
|-------|----------|-------|
| R2 bucket objects (JSON) | **Private**; server-side credentialed read (SigV4) | No public object URLs. Next.js Server Components fetch with read-only R2 API credentials in Vercel server env. World-readable public-read is **not** the live posture. |
| Vercel app | **Public** (operator-accepted for launch readiness) | Static + SSR; env vars = R2 read credentials + revalidation secret (server-only). `noindex` via `X-Robots-Tag` + `robots.ts`. |
| R2 write credential | **Workstation only** | Never in Vercel, never in git |
| MLflow UI | **Never public** | DESIGN §10; localhost bind |
| Prefect UI | **Never public** | DESIGN §10 |
| Workstation / DuckDB / Parquet | **Never public** | No inbound ports |
| CFBD / Odds API keys | **Workstation only** | Webapp consumes zero credits (§3.5) |

#### Public-read R2 — DEFERRED

W0 described R2 as “Public read via HTTPS.” W7 shipped a **private** bucket with SigV4 server-side reads (`webapp/site/src/lib/artifacts/r2.ts`). Public-read remains deferred for these reasons on record:

1. **Field surface:** Public object URLs would publish every artifact field, not only every rendered field (see W8-A D4 RSC payload findings — even the private model already serializes full `GamePrediction` objects into the This Week client boundary).
2. **Synthetic / doctored prefixes:** W7-BUCKET-AUDIT found non-`latest/` prefixes still holding synthetic games (`g-chaos-1`, `g-fix-1`, `g-fix-2`) and a doctored `schema_version=2.0.0` object under `v2/…`, all with live-looking meta. Under public-read those become publicly fetchable synthetic model output at guessable URLs. **Operator action:** clean those prefixes (W8-A does not delete R2 objects).
3. **Sandbox exposure:** `sandbox/` (W7-TESTPUBLISH-GUARD) would be world-readable alongside `latest/`.

**Successor task:** W8-R2-PUBLIC (name reserved) — enable public-read **only** after: (a) a separate bucket or `public/` prefix carrying **projected** artifacts (rendered-field subset only), (b) synthetic / doctored non-`latest/` prefixes cleaned, (c) W8-A D3 field diff green, and (d) CFBD ToU §2 / §5 constraints reviewed against any public raw-response mirror risk (see `docs/notes/webapp-w6.md` L1 correction and archived terms).

Cross-reference: CFBD Terms §2 (API key stays server-side, never in a public repo) and §5 (no programmatic third-party access to stored raw API responses) bear on this deferral.
```

Note: bullet 1 still describes the **pre-W8-D** This Week leak. Live RSC payload is now the DTO; public-read of raw `latest/week_predictions.json` would still publish every GamePrediction field. That is why W8-R2-PUBLIC remains deferred.

### 6.3 D3 contract-and-present field list (archived file, pasted)

`docs/notes/_artifacts/webapp-w8a/present-field-leaves.json` — unique JSON leaves observed in `latest/` during W8-A, including dynamic team ids as strings then the contract field names:

```json
[
  "103", "113", "12", "120", "127", "130", "135", "142", "145", "150", "151", "152", "153", "154", "158", "164", "166", "167", "183", "189", "193", "194", "195", "197", "2", "2005", "2006", "201", "202", "2026", "2032", "204", "2050", "2084", "21", "2116", "2117", "213", "2132", "218", "2199", "221", "2226", "2229", "2247", "228", "2294", "23", "2305", "2306", "2309", "2335", "2348", "235", "238", "239", "2390", "2393", "24", "242", "2426", "2429", "2433", "2439", "2440", "245", "2459", "248", "2483", "249", "25", "2509", "251", "252", "2534", "254", "256", "2567", "2572", "2579", "258", "259", "26", "2623", "2628", "2633", "2636", "2638", "264", "2641", "2649", "265", "2653", "2655", "2711", "275", "2751", "276", "277", "278", "290", "295", "30", "309", "324", "326", "328", "333", "338", "344", "349", "356", "36", "38", "41", "5", "52", "55", "57", "58", "59", "6", "61", "62", "66", "68", "70", "77", "8", "84", "87", "9", "96", "97", "98", "99",
  "actual_margin", "actual_total", "artifact_pointers", "as_of_utc", "away_points", "away_team", "away_team_id",
  "champion_model", "champion_version", "ci_kind", "ci_lower", "ci_upper", "combined_stamp", "conference_game",
  "conviction_basis", "conviction_label", "conviction_team", "conviction_tier", "def_epa", "def_sd",
  "ensemble_scope_label", "favored_side", "feature_time_label", "fixture", "game_id", "games", "grade_status",
  "graded_from", "grading_rule", "home_points", "home_team", "home_team_id", "home_win", "hysteresis_applied",
  "id", "is_stale", "kickoff_utc", "label", "margin_interval_hi", "margin_interval_hit", "margin_interval_lo",
  "margin_interval_nominal", "metrics", "model_identity", "model_version", "mu_margin", "mu_sigma_ratio",
  "mu_total", "n", "neutral_site", "next_expected_publish_utc", "notes", "null_reason", "off_epa", "off_sd",
  "p_cover_home", "p_cover_home_credible", "p_favored", "p_over", "p_over_credible", "p_win_home",
  "p_win_home_credible", "p_win_home_realized", "pace", "plain_language", "postgame_ratings", "previous_tier",
  "primary", "publish_schedule", "publish_stale", "published_at", "raw_tier", "refresh", "refresh_kind",
  "regime", "registered_at", "registry_name", "results_current_season", "run", "run_id", "schema_version",
  "school", "season", "sigma_margin", "sigma_margin_credible", "sigma_total", "sigma_total_credible",
  "source_memo", "sources", "stale_sources", "stale_stamp", "team_ratings", "teams", "tier_primary",
  "tier_revised_since_primary", "total_interval_hi", "total_interval_hit", "total_interval_lo",
  "total_interval_nominal", "track_record", "unit", "value", "verdict", "vintage", "vintage_label",
  "vintage_labels", "week", "week_predictions", "weeks"
]
```

Numeric strings are `teams.<team_id>` keys (W8-A: not undocumented fields). Nested `stale_sources[].*` were contract-missing because fixture arrays were empty.

### 6.4 Post-E2E R2 inventory (`list_objects_v2`, read-only)

Bucket `ridge-artifacts`. **key_count=39**. No Put/Delete. Full listing: `docs/notes/_artifacts/webapp-w8d/r2-inventory.txt`.

Prefixes:

| Prefix | n | What it is |
|--------|---|------------|
| `latest/` | 5 | Restored 2024 week-5 fixtures |
| `v1/` | 13 | Versioned live keys + W7-BUCKET-AUDIT leftovers |
| `v2/` | 5 | Doctored `schema_version=2.0.0` (W7-BUCKET-AUDIT) |
| `sandbox/` | 16 | W7-TESTPUBLISH-GUARD |

`latest/*` GET:

```
latest/meta.json fixture=True season=2024 week=5 published_at=2024-09-24T06:00:00Z
latest/results_2024.json fixture=True season=2024 games=56 published_at=2024-09-24T06:00:00Z
latest/team_ratings_2024.json fixture=True season=2024 published_at=2024-09-24T06:00:00Z
latest/track_record.json fixture=True metrics=13 published_at=2024-09-24T06:00:00Z
latest/week_predictions.json fixture=True season=2024 week=5 games=56 published_at=2024-09-24T06:00:00Z
```

Matches the committed 2024 week-5 fixture set (`fixture: true`, 56 games, stamp `2024-09-24T06:00:00Z`). W8-A's 2026 publish stamp is **not** in `latest/`. Last-modified `2026-08-14T22:21:3xZ` is the restore write clock, not the artifact `published_at`.

Live-scope push accounting: **no `v1/2026/` objects**. `v1/2024/w5/tuesday_primary/*` was overwritten by the restore (`published_at=2024-09-24T06:00:00Z`, `fixture true`, same sizes as `latest/`). Remaining live-looking prefixes are the **pre-existing W7-BUCKET-AUDIT** set:

- `v1/2024/w5/daily_refresh/*` — small week file (2569 B vs 107264 B full slate)
- `v1/2024/w6/tuesday_primary/*` — `fixture` absent, `published_at=2026-08-14T14:28:58Z`
- `v2/2024/w5/tuesday_primary/*` — `schema_version=2.0.0`, `fixture` absent
- `sandbox/**` — guard sandbox

Operator cleanup of synthetic prefixes remains W8-R2-PUBLIC / operator, **not** deleted here.

---

## 7. D6 — L3 operator entry

Appended verbatim to `docs/notes/webapp-w6.md`. Not edited, summarized, or extended.

Updated L1–L6 table in `webapp-w8a.md`: L1 **RESOLVED**, L2 **RESOLVED**, L3 **OPERATOR ACCEPTED RISK**, L4 **OPEN / default-safe**, L5 **OPEN**, L6 per D4.4. DESIGN §6.3's L1–L3 launch gate is now satisfied — two resolved, one disposed. L4 and L5 are not launch blockers under §6.3.

---

## 8. Deferrals → successor tasks

| Item | Successor |
|------|-----------|
| Screen-reader pass (Results table + trajectory text equivalent) | **W8-SR** |
| Public-read R2 of projected artifacts only | **W8-R2-PUBLIC** |
| Operator delete of synthetic / doctored non-`latest/` prefixes | **Operator** |
| About attribution / contact / repo URL | **W8-B** (operator-blocked) |
| Optional 320 stacked table / wrap-matchup (not an axe fail) | **W8-REFLOW-TABLE** |

---

## 9. Acceptance commands

```
$ cd webapp/site
$ npm test          # 119 passed + token/contrast guard
$ npm run lint      # clean
$ npm run build     # clean (ARTIFACT_SOURCE=fixtures)
```

`npm run typecheck` still fails on pre-existing `tests/gallery-gate.test.ts` `NODE_ENV` assignability (four TS2540s). `next build` typechecks application code and passed. Not introduced here; tests/ gallery-gate is outside D1–D3 logic.

---

*End of W8-D.*

## W8-COMMIT (2026-08-17)

This notes file existed before the code was on `main`. Commit `4a0dd4a`.
`npm test` now reports 129 passed (W8-C added allowlist / dual-fixture tests
after this file's 119 count). See `docs/notes/webapp-w8commit.md`.

