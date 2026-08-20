# PROD-500 — production 500 on R2-backed routes (CLOSE)

**Branch:** `prod-500`  
**Closed:** 2026-08-20  
**Authority:** `docs/webapp/DESIGN.md` §1.8, §3.1, §3.2, §3.4, §5.2, §5.3; `docs/notes/webapp-w7.md` (W7-1, W7-2, W7-5).

**R2 writes this task:** zero. Read-only `RIDGE_READ_*` credentials only.

---

## Root cause

Every public route performs (or inherits via root layout) an R2 fetch with
`cache: "no-store"` (`webapp/site/src/lib/artifacts/r2.ts`). Build-time
pre-render uses local fixtures and marks routes static/ISR. On W7-2 on-demand
revalidation after publish — or any other regeneration — Next.js 15 throws:

```
Error: Page changed from static to dynamic at runtime <route>, reason: no-store fetch
  https://…r2.cloudflarestorage.com/ridge-artifacts/latest/<artifact>.json <route>
```

This is **not** limited to `/results` and `/game/[id]`. Pre-fix evidence with
valid read-only R2 credentials showed **all four** routes 500 after
`POST /api/revalidate`. First-request 200s are ISR cache HITs and prove nothing.

**`MissingConfidenceIntervalError` is not the diagnosis.** That guard
(`rateHasCi` / `MetricRow` honest absence) is **hardening** for a latent §1.8
case. Current live `track_record.json` is complete; the CI path never fired.

---

## Pre-fix evidence — commit `c58d6b7`, valid read-only R2 creds

**Setup:** `npm run build` with `ARTIFACT_SOURCE=local`. `next start` with
`ARTIFACT_SOURCE=r2`, real `RIDGE_READ_*` keys, bucket `ridge-artifacts`.
No SSL errors; R2 GETs succeed.

### Cache HIT (misleading — not acceptance)

```
/ -> HTTP 200
/about -> HTTP 200
/results -> HTTP 200
/game/401628373 -> HTTP 200
```

### After `POST /api/revalidate` (publish path — the real failure)

```
/api/revalidate -> HTTP 200 {"ok":true,"revalidated":true,...}

/ -> HTTP 500
/about -> HTTP 500
/results -> HTTP 500
/game/401628373 -> HTTP 500
```

### stderr (verbatim, representative)

```
Error: Page changed from static to dynamic at runtime /, reason: no-store fetch
  https://18af8a92dace4a97249d7349324faafd.r2.cloudflarestorage.com/ridge-artifacts/latest/meta.json /

Error: Page changed from static to dynamic at runtime /about, reason: revalidate: 0 fetch
  https://18af8a92dace4a97249d7349324faafd.r2.cloudflarestorage.com/ridge-artifacts/latest/meta.json /about

Error: Page changed from static to dynamic at runtime /results, reason: no-store fetch
  https://18af8a92dace4a97249d7349324faafd.r2.cloudflarestorage.com/ridge-artifacts/latest/track_record.json /results

Error: Page changed from static to dynamic at runtime /game/401628373, reason: no-store fetch
  https://18af8a92dace4a97249d7349324faafd.r2.cloudflarestorage.com/ridge-artifacts/latest/week_predictions.json /game/[gameId]
```

No `MissingConfidenceIntervalError` in server log (`.prod500-prefix-repro.txt`).

Sept 1 publish fires W7-2 revalidation → without this fix, homepage and about
outage on the same path that clears the ISR cache.

---

## Fix — layout-level `force-dynamic`

**Where:** `webapp/site/src/app/layout.tsx` — `export const dynamic = "force-dynamic"`.

**Why layout, not only per-page:** the shared failure mode is the root layout's
`no-store` meta fetch. Any non-dynamic descendant regenerates into that fetch and
500s. Layout-level covers `/`, `/about`, `/results`, `/game/[id]` (and gallery)
in one place. Per-page `force-dynamic` remains on `/results` and `/game/[id]` from
the first pass (redundant, harmless).

Operator decision 2026-08-19 (KEEP for launch): `force-dynamic` fails safe
(always fresh, more R2 reads); ISR fails unsafe (missed revalidation can serve
a cached page whose staleness banner was computed at generation time).

### Consequences

| Route | Caching | W7-2 revalidation hook |
|-------|---------|------------------------|
| `/` | **Uncached** (layout `force-dynamic`) | **Inert** for caching |
| `/about` | **Uncached** | **Inert** |
| `/results` | **Uncached** | **Inert** |
| `/game/[id]` | **Uncached** | **Inert** |

Hook still returns 200; it does not change visitor-visible freshness on these
routes. Documented in `docs/runbooks/pre_publish.md`.

### §3.4 cost check (uncached R2 Class B reads)

Per request, approximate R2 GET count (each artifact = one Class B op):

| Route | Artifacts fetched |
|-------|-------------------|
| `/` | layout `meta` + `week_predictions` ≈ **2** |
| `/about` | layout `meta` ≈ **1** |
| `/results` | layout `meta`, page `meta`, `track_record`, optional `results_<season>` ≈ **3–4** |
| `/game/[id]` | layout `meta`, `week_predictions`, `team_ratings_2024` ≈ **3** |

Week-1 forecast-only traffic remains **well within** DESIGN §3.4 (~50k reads/mo
estimate, 1M/mo free tier). No re-architecture required.

### Staleness banner freshness

`StalenessBanner` is rendered in `layout.tsx` via `isSiteStale(..., now = new Date())`
at **request time** under `force-dynamic`. No STOP.

---

## Hardening (kept, not root cause)

- **`rateHasCi` / `MetricRow`:** honest absence for percent rates without CI (§1.8).
- **`lookupTeam` / `seriesForTeam`:** survive empty `teams` / missing `weeks` (§1.8).
- **`/results` loader:** try/catch → `MaintenanceState` on fetch failure.
- **`parseSchemaMajor`:** restored **throw on invalid** semver; `isSchemaVersionSupported`
  catches → `MaintenanceState` at route gates (W7-1 loud failure, not silent `null`).

---

## Post-launch task (optimization — not a bug fix)

**PROD-500-ISR** — Optionally restore ISR + W7-2 on-demand revalidation for
public routes to reduce Class B reads. Primary constraint: staleness banner must
remain request-fresh (or revalidation must be proven reliable). This is a cost /
caching optimization after launch freeze, not a correctness fix for the
static-to-dynamic 500.

---

## Operator authorizations (paths outside sanctioned PROD-500 list)

| Path | Rationale |
|------|-----------|
| `webapp/site/src/lib/formatting/track-record.ts` | §1.8 `rateHasCi` honest-absence helper for percent rates without CI |
| `webapp/site/scripts/prod500-smoke.mjs` | Production-build smoke: build, start, **revalidate**, then assert four routes 200 |
| `webapp/site/package.json` (`test:smoke`) | npm entrypoint for smoke script |

---

## Post-fix verification — `next start` against R2 (post-revalidation)

**Method:** build with fixtures → `next start` with read-only R2 →
`POST /api/revalidate` → assert. First-request HITs are not recorded as acceptance.

```
/api/revalidate -> HTTP 200 {"ok":true,"revalidated":true,"paths":["/","/results","/about","/game"],"at":"2026-08-20T13:26:19.878Z"}
=== post-revalidation (required) ===
/ -> HTTP 200
/about -> HTTP 200
/results -> HTTP 200
/game/401628373 -> HTTP 200
all four routes 200 post-revalidation
```

Build marks `/`, `/about`, `/results` as `ƒ` Dynamic (layout `force-dynamic`).

### `npm run test:smoke`

```
prod500-smoke: npm run build …
[/ , /about, /results marked ƒ Dynamic]
prod500-smoke: next start on :3099 …
prod500-smoke: POST /api/revalidate (publish path) …
/api/revalidate -> 200 {"ok":true,"revalidated":true,"paths":["/","/results","/about","/game"],"at":"2026-08-20T13:35:17.226Z"}
prod500-smoke: asserting post-revalidation …
/ -> 200
/about -> 200
/results -> 200
/game/401628373 -> 200
prod500-smoke: all routes 200 post-revalidation
```

### Guard suite (`npm run typecheck && npm run test && npm run lint && npm run build`)

```
> ridge-site@0.1.0 typecheck
> tsc --noEmit

> ridge-site@0.1.0 test
 Test Files  22 passed (22)
      Tests  136 passed (136)
Token diff-check PASSED — all §4.1/§4.2 values match tokens.css

> ridge-site@0.1.0 lint
All matched files use Prettier code style!

> ridge-site@0.1.0 build
 ✓ Compiled successfully
[/ , /about, /results ƒ Dynamic]
```

---

## Acceptance

- [x] Pre-fix post-revalidation stderr captured; root cause = any non-dynamic
      route + no-store fetch, not CI throw
- [x] Pre-fix control with valid credentials; cache-HIT 200s documented as
      non-evidence
- [x] Layout-level `force-dynamic`; consequences in this note + `pre_publish.md`;
      §3.4 cost check; banner request-time; PROD-500-ISR scoped as optimization
- [x] `parseSchemaMajor` loud failure restored + test
- [x] All four routes **200 post-revalidation** against R2 (pasted above)
- [x] `npm run test:smoke` green post-revalidation (pasted above)
- [x] Guard suite after layout change (pasted above)
- [x] Three authorizations logged
- [x] Zero R2 writes
- [x] No route left returning 500 after revalidation
