# W8-A — Ridge launch readiness (verification, a11y, posture corrections)

**Date:** 2026-08-14  
**Status:** Complete (code + verification evidence)  
**Authority:** `docs/webapp/DESIGN.md` §1, §3.2, §3.3, §4.4, §6; `docs/webapp/TASKS.md` cross-cutting; W6; W7 (incl. VERIFY / CLOSE-2 / BUCKET-AUDIT / TESTPUBLISH-GUARD)

No artifact-contract change. No export/push/tier edits. No R2 deletes. No analytics.

---

## 1. Claims-vs-reality (from `webapp-w7.md`)

| W7 claim | W8-A test | Held? |
|----------|-----------|-------|
| On-demand revalidation works (stock push path) | D1 auth pair + E2E `published_at` freshness | **HELD** — correct Bearer → 200; wrong → 401; fixture push → revalidate → site shows new stamp in **~10.8s** (not ~6h ISR) |
| `NCAA_QUANT_WEBAPP__REVALIDATE_URL` may be deployment-specific host | D1.3 host check | **HELD (corrected since W7)** — now `the-cfb-model.vercel.app` (stable production alias), not `…-hd5oqmobf-…` |
| `WORKSTATION_REVALIDATION_VERCEL` present but unread | D1.4 `.env` scan | **HELD (already gone)** — variable **absent** from `.env` now; recommend keep it absent |
| Production alias publicly reachable (accepted risk) | D2 route curls | **HELD** — public 200 on `/`, `/results`, `/about`, `/game/…` |
| Gallery / demo states gated or noindex-only | D2 | **HELD after gate** — gallery routes return **404** in production (`assertGalleryAllowed`); **200** in `next dev`. Was already `notFound()`; centralized + tested |
| Private R2 + SigV4 (not public-read) | D6 vs DESIGN §3.3 | **MISMATCH fixed in spec** — live posture was private; DESIGN said public-read; §3.3 amended |
| Tracked `.env.example` scrubbed of secrets | D acceptance | **HELD** — empty placeholders only (contents recorded below) |
| W7-BUCKET-AUDIT synthetic prefixes remain | D6 / operator item | **HELD** — `g-chaos-1`, `g-fix-*`, `v2/…schema 2.0.0` not deleted; operator cleanup item |
| W7-TESTPUBLISH-GUARD sandbox routing intact | D1.2 used stock `push_artifacts_to_r2` + fixtures, not helpers | **HELD** |
| Site renders ⇒ revalidation works | Explicitly rejected as evidence | N/A — D1 proved the mechanism |

---

## 2. D1 — On-demand revalidation

### Auth pair (production alias)

```
POST https://the-cfb-model.vercel.app/api/revalidate
Authorization: Bearer <correct WEBAPP_REVALIDATE_SECRET>
→ HTTP/1.1 200 OK
{"ok":true,"revalidated":true,"paths":["/","/results","/about","/game"],"at":"2026-08-14T22:20:05.774Z"}

POST … (Bearer deliberately-wrong-secret-w8a)
→ HTTP/1.1 401 Unauthorized
{"ok":false,"error":"unauthorized"}
```

### E2E freshness

| Step | Result |
|------|--------|
| BEFORE R2 `latest/meta.json` `published_at` | `2024-09-24T06:00:00Z` |
| Stock fixture push (`push_artifacts_to_r2`, `publish_scope=live`, stamped `published_at`) | `meta_last=True`, push ~10.2s |
| Stock revalidation | `status_code=200`, `ok/revalidated` |
| AFTER R2 | `published_at=2026-08-14T22:21:20Z` |
| Production GET `/` shows new stamp | **yes**, wall-clock from push start **~10.8s** |
| Fixtures restored | yes |

**Verdict:** On-demand revalidation is doing the work (not waiting on 6h ISR).

### Revalidate URL host

`NCAA_QUANT_WEBAPP__REVALIDATE_URL` → host **`the-cfb-model.vercel.app`** (stable production alias).

### `WORKSTATION_REVALIDATION_VERCEL`

**Does not exist** in workstation `.env`. Recommend: leave absent (do not reintroduce). W8-A did not edit `.env`.

---

## 3. D2 — Demo-state exposure

### Production route table (unauthenticated)

| Route | Status |
|-------|--------|
| `/` | 200 |
| `/about` | 200 |
| `/results` | 200 |
| `/gallery` | **404** |
| `/gallery/this-week-states` | **404** |
| `/gallery/game-detail-states` | **404** |
| `/gallery/results-states` | **404** |
| `/api/revalidate` GET | 405 |
| `/robots.txt` | 200 |
| `/game/401628373` | 200 |
| `/game/does-not-exist-w8a` | 404 |

### Gate

- Helper: `webapp/site/src/app/gallery/gallery-gate.ts` (`NODE_ENV !== "production"`).
- Production: 404. Local `next dev` (`:3471` / `:3460`): **200**.
- Test: `tests/gallery-gate.test.ts` (enabled in development / disabled in production).

### Demo-state import finding

| Module | Importers |
|--------|-----------|
| `lib/this-week/demo-states.ts` | gallery only (+ `game-detail/demo-states`) |
| `lib/game-detail/demo-states.ts` | gallery only |
| `lib/results/demo-states.ts` | gallery **and** `components/Results/TrackRecordSection.tsx` |

**Finding:** `TrackRecordSection` (public `/results`) imports `EXPECTED_METRIC_IDS` / `metricById` from `demo-states.ts`. Those symbols are shared helpers, not fabricated rows, but the **module path** couples a public page to the demo-states file. Fabricated clones (`cloneUngradedStatuses`, etc.) are gallery-only. **Successor:** W8-SPLIT-DEMO — move shared metric helpers out of `demo-states.ts` (requires a non-a11y component/lib edit; stopped here).

---

## 4. D3 — Artifact field diff (`latest/` on R2)

Objects: `meta.json`, `week_predictions.json`, `results_2024.json`, `track_record.json`, `team_ratings_2024.json`.

### contract-and-present

All DESIGN §1 fields observed in current `latest/` except empty-array nesting noted below. Full enumeration archived in session D3 run (`present-field-leaves.json` under `_artifacts/webapp-w8a/`).

### contract-missing

| Path | Note |
|------|------|
| `games[].stale_sources[].source` / `age_hours` / `last_good_at` | `stale_sources` arrays are **empty** on current fixture slate — nested keys not observable. Field `stale_sources` itself is present. Not a contract defect. |

### present-but-undocumented

| Item | Classification |
|------|----------------|
| Top-level `fixture: true` | Additive / fixture labeling (§1.7) — expected |
| `teams.<numericId>` keys | Contract shape `teams.<team_id>`; dynamic ids are **not** undocumented fields |

### Market-field assertion (§1.2)

```
PASS: no spread/total_line/moneyline/book/market_implied field names in latest/*
```

Test: `tests/artifact-market-fields.test.ts` — **passing**.

### Operational disclosures (not defects)

| Field | Intended? |
|-------|-----------|
| `model_identity.run_id` | Yes — provenance |
| `model_identity.champion_version` | Yes — provenance |
| `stale_stamp` e.g. `STALE(odds, 4.0h)` | Yes — §3.2 input staleness; reveals odds as an input + age without a line number |

Unchanged.

---

## 5. D4 — RSC payload leak

### Executed greps (production HTML)

**`/` (This Week):** Many hits for non-rendered fields (`conviction_basis`, `mu_sigma_ratio`, `p_favored`, `p_cover_home`, `p_over`, `mu_total`, `home_team_id`, `ensemble_scope_label`, …) — full `GamePrediction[]` serialized into the RSC flight because `ThisWeekSlate` is a **Client Component** receiving `games={week.games}`.

**`/game/401628373`:** Grep of curated non-rendered operational fields (`run_id`, `champion_version`, `mu_sigma_ratio`, …) → **no hits**. `GameDetail` tree is server-rendered.

Evidence: `docs/notes/_artifacts/webapp-w8a/d4-payload-hits.json`.

### Component boundary

**Responsible:** `app/page.tsx` → `ThisWeekSlate` (`"use client"`) props. Fix requires projecting a slim client DTO in the Server Component — **`page.tsx` is outside W8-A sanctioned edits** → STOP and successor **W8-C (RSC projection)**.

### Payload test

`tests/payload-leak.test.tsx` asserts **Odds API market field names** absent from rendered This Week HTML — **passing**. Broader non-rendered field leak is the W8-C finding above (not silently claimed fixed).

---

## 6. D5 — WCAG 2.1 AA

### Automated axe (wcag2a / wcag2aa / wcag21aa)

**BEFORE** (production, pre-fix tokens/banners) — violation **counts**:

| Page | light/390 | light/desktop | dark/390 | dark/desktop |
|------|-----------|---------------|----------|--------------|
| `/` | 1 | 1 | 1 | 1 |
| `/game/…` | 2 | 2 | 2 | 2 |
| `/results` | 2 | 2 | 2 | 2 |
| `/about` | 1 | 1 | 1 | 1 |

Dominant rules: `color-contrast`; also `definition-list` (game), `link-in-text-block` (results).

**AFTER** (local `next dev` + fixes) — **all 0**:

| Page | light/390 | light/desktop | dark/390 | dark/desktop |
|------|-----------|---------------|----------|--------------|
| `/` | 0 | 0 | 0 | 0 |
| `/game/…` | 0 | 0 | 0 | 0 |
| `/results` | 0 | 0 | 0 | 0 |
| `/about` | 0 | 0 | 0 | 0 |

Artifacts: `a11y-before.json`, `a11y-after.json`.

### Fixes applied (a11y only)

- `--text-tertiary` light `#75757a` (was `#aeaeb2`, 2.21:1); dark `#8e8e93` (was `#636366`, 3.51:1)
- Staleness / STALE badge: solid `--semantic-stale` bg + `--bg-primary` text
- Provenance `<dl>`: meaning inside `<dd>` (definition-list)
- Results scope links: underline + accent (link-in-text-block)
- Track record: semantic `<table>` + focusable scroll region
- `prefers-reduced-motion` gate in `globals.css`
- RatingTrajectoryChart: labelled figure + dashed-vs-solid encoding note in caption

**Note:** `scripts/check-tokens.mjs` and DESIGN §4.1 still list pre-AA tertiary hexes — **successor W8-TOKENS-SPEC** to sync (not sanctioned to edit §4.1 / check-tokens here).

### Contrast ratios (post-fix tokens)

| Pair | Ratio | AA normal 4.5 | AA large 3.0 |
|------|-------|---------------|--------------|
| text-primary / bg-primary light | 16.83:1 | PASS | PASS |
| text-secondary / bg-primary light | 5.07:1 | PASS | PASS |
| text-tertiary / bg-primary light | **4.58:1** | PASS | PASS |
| accent / bg-primary light | 4.70:1 | PASS | PASS |
| semantic-stale / bg-primary light | 5.07:1 | PASS | PASS |
| text-primary / bg-secondary light | 15.46:1 | PASS | PASS |
| text-secondary / bg-secondary light (tier chips) | 4.66:1 | PASS | PASS |
| text-primary / bg-primary dark | 19.29:1 | PASS | PASS |
| text-secondary / bg-primary dark | 7.31:1 | PASS | PASS |
| text-tertiary / bg-primary dark | **6.44:1** | PASS | PASS |
| accent / bg-primary dark | 5.76:1 | PASS | PASS |
| white / semantic-stale banner light | 5.07:1 | PASS | PASS |

IntervalBand uses tertiary on primary — now AA.

### Manual checks

| Check | Evidence |
|-------|----------|
| Keyboard focus (SortControl, ResultsTabs, disclaimer dismiss, SiteHeader) | `docs/notes/_artifacts/webapp-w8a/focus/*.png` |
| RatingTrajectoryChart non-color encoding | Solid vs dashed strokes + figcaption text equivalent |
| prefers-reduced-motion | `globals.css` reduce media query |
| Results semantic table | Track record `<table>` with `<th scope>` |
| About heading order | h1 then h2 sections only |

### AA verdict

**WCAG 2.1 AA target is met for the four public pages under axe wcag2a/aa/21aa in both themes at 390px and desktop after fixes, with contrast numbers above and focus screenshots attached.** No rule was allowlisted.

---

## 7. D6 — DESIGN §3.3 amended

See `docs/webapp/DESIGN.md` §3.3 (private bucket; public-read DEFERRED subsection; successor **W8-R2-PUBLIC**).

**Operator action (no deletes in W8-A):** clean non-`latest/` synthetic / doctored prefixes from W7-BUCKET-AUDIT (`v1/…/daily_refresh` `g-chaos-1`, `v1/2024/w6/…` `g-fix-*`, `v2/…` schema 2.0.0).

---

## 8. D7 — L1 record + CFBD terms archive

- Correction appended to `docs/notes/webapp-w6.md`
- Archive: `docs/notes/_artifacts/webapp-w8a/cfbd-terms-2026-08-12.md` (effective **August 12, 2026**)

---

## 9. L1–L6 final status

| ID | Status | Evidence |
|----|--------|----------|
| L1 | **RESOLVED** | ToU archive + w6 correction; attribution recommended, still rendered |
| L2 | **RESOLVED** | Text-only school names / no logos; ToU §8 |
| L3 | **OPEN** | Unchanged — counsel |
| L4 | **OPEN / default-safe** | No analytics added |
| L5 | **OPEN** | Unchanged |
| L6 | **RESOLVED** | D5 a11y before/after + contrast + focus shots |

---

## 10. Deferrals → successor tasks

| Item | Successor |
|------|-----------|
| Project This Week client props (stop full `GamePrediction` RSC leak) | **W8-C** |
| Split `EXPECTED_METRIC_IDS` out of `demo-states.ts` | **W8-SPLIT-DEMO** |
| Public-read R2 (projected artifacts only) | **W8-R2-PUBLIC** |
| Operator delete/clean synthetic R2 prefixes | **Operator** (not a code task) |
| Sync DESIGN §4.1 + `check-tokens.mjs` tertiary hexes to AA values | **W8-TOKENS-SPEC** |
| About identity / contact / repo URL placeholders | **W8-B** (operator-blocked) |

---

## 11. `.env.example` (scrub verification)

```
ARTIFACT_SOURCE=r2
R2_BUCKET=
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
WEBAPP_REVALIDATE_SECRET=
```

Empty placeholders only — confirmed.

---

## 12. §4.4 anti-pattern list (verbatim) vs visual changes

```
- no default-shadcn aesthetic
- no purple-gradient heroes
- no emoji cards
- no wall-of-widgets
- no gratuitous glassmorphism
- no filler marketing copy
```

Visual deltas this task: tertiary token darkening; solid stale banner/badge (same orange semantic, stronger fill); track-record HTML table (flat, no cards); link underlines on Results scope; focus-ring already present. **No** purple gradients, emoji, glass, marketing copy, or shadcn chrome introduced.

---

## 13. Acceptance commands

```
$ cd webapp/site
$ npm test          # 111 passed (incl. gallery-gate, market-fields, payload-leak)
$ npm run lint      # clean
$ npm run build     # clean
$ # betting-language grep: PASS (zero matches)
```

---

*End of W8-A.*
