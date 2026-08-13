# W7 — Private preview deploy (NOT public launch)

**Date:** 2026-08-13  
**Status:** Code complete; **LIVE DEPLOY BLOCKED** on operator credentials  
**Authority:** `docs/webapp/DESIGN.md` §1, §3, §6; W1 push seam; W1A-FIX deferral; W6 legal checklist

---

## STOP — W7-0 live access posture cannot be verified yet

This workstation has **no** Cloudflare R2 credentials, **no** Vercel CLI/login, and
**no** `WEBAPP_REVALIDATE_SECRET` in `.env`. Creating a reachable Vercel URL
without Deployment Protection would make the site publicly reachable — forbidden
by this task. Therefore **no deploy was executed**.

### Operator must supply before live acceptance can finish

1. **Vercel** account access (`vercel login` or token) and a project rooted at
   `webapp/site/`
2. Enable **Deployment Protection** (Standard Protection / password / SSO) on
   that project **before** the first unprotected production URL is shared
3. **Cloudflare R2** private bucket name + endpoint
4. **Workstation write** R2 API token → `.env` (`R2_ACCESS_KEY_ID` /
   `R2_SECRET_ACCESS_KEY`) — never in Vercel
5. **Vercel read-only** R2 API token → Vercel server env (separate from write)
6. Shared `WEBAPP_REVALIDATE_SECRET` on both sides
7. Preview workstation only: `NCAA_QUANT_WEBAPP__EXPORT_ENABLED=true` plus
   bucket / endpoint / `REVALIDATE_URL`

Until then, acceptance items that require a deployed URL remain **OPEN**.

---

## W7-0 — Access posture (designed; verify after deploy)

| Control | Mechanism |
|---------|-----------|
| Non-public site | Vercel **Deployment Protection** (dashboard) — only the operator can open the URL |
| Non-indexable | `X-Robots-Tag: noindex, nofollow, noarchive` on `/:path*` (`next.config.ts`) + `app/robots.ts` disallow all |
| R2 | **Private bucket; server-side credential** (SigV4 GET). World-readable public-read was **not** chosen |
| MLflow / Prefect / workstation | Never exposed — see security boundary below |

**Unauthenticated external request evidence:** *deferred — no protected deploy yet.*
After deploy, paste: `curl -I https://<preview>/` → expect `401`/`403` from
Deployment Protection, plus `X-Robots-Tag: noindex…`.

### R2 access model (stated)

**Server-side with credential** (task default). The site never uses a public R2
base URL in private preview. A world-readable bucket would be public exposure of
artifacts even without the site; that tradeoff was **not** taken.

DESIGN §3.3 still documents public-read as the eventual launch architecture; W7
overrides for private preview. Public-read remains a W8 / operator decision.

### §3.3 Security boundary — restated and checked

| Asset | Exposure | W7 check |
|-------|----------|----------|
| R2 objects | Private; site fetches with read-only server env | Write key stays workstation-only (`.env.example`) |
| Vercel app | Protected preview only; `noindex` | No public domain; no analytics scripts |
| R2 write credential | Workstation only | Not listed in `webapp/site/.env.example` as a write path for Vercel |
| MLflow UI | Never public | Site has no MLflow client/URL |
| Prefect UI | Never public | Site has no Prefect client/URL |
| Workstation / DuckDB / Parquet | Never public | Artifacts only via R2 JSON |
| CFBD / Odds keys | Workstation only | Zero credit path unchanged (§3.5) |

L1–L6 legal checklist from W6 remains **OPEN**. This task does **not** treat
them as resolved. Public launch is **W8**.

---

## W7-1 — Real artifact path (wired)

### Selection

| Environment | Source |
|-------------|--------|
| Local / unset `ARTIFACT_SOURCE`, not on Vercel | `../fixtures` (or `ARTIFACT_BASE_PATH`) |
| `ARTIFACT_SOURCE=r2` **or** any Vercel deploy (`VERCEL` set) | Private R2 `latest/*` via SigV4 |

### Loud failure

On Vercel (or `ARTIFACT_SOURCE=r2`), missing R2 env vars **throw** at
`resolveArtifactBase()` — the site will not silently serve fixtures. Unit:
`tests/artifact-source.test.ts`.

### FIXTURE / LIVE honesty

Layout still drives the FIXTURE banner from `meta.fixture === true` (unchanged).
Live publishes must omit `fixture` / set false; fixture sets set `fixture: true`.
**Deployed verification deferred** until operator credentials exist.

### `webapp.export_enabled` scope

- Code default remains **`False`**
- Preview-only: set `NCAA_QUANT_WEBAPP__EXPORT_ENABLED=true` on the **operator
  workstation** used for private-preview publishes
- Do not enable on machines that must not write R2
- Disable: unset / `false` — `predict_publish` skips export+push

---

## W7-2 — On-demand revalidation

| Piece | Location |
|-------|----------|
| Endpoint | `webapp/site/src/app/api/revalidate/route.ts` |
| Auth | `Authorization: Bearer <WEBAPP_REVALIDATE_SECRET>` (or `x-revalidate-secret`) |
| Trigger | `push_artifacts_to_r2` **after** meta-last upload |
| Failure | Best-effort: push succeeds; alerts via `AlertKind.WEBAPP_EXPORT_FAILURE` titled “Ridge on-demand revalidation failed”; ISR 6h remains fallback |

Tests: unauthed + wrong-secret → 401; push failure path non-fatal + alert
(`tests/unit/test_webapp_w7.py`, `tests/revalidate.test.ts`).

---

## W7-3 — Tier-change instrumentation (W1A-FIX)

**Format:** JSONL, one record per game per publish  
**Path:** `data/webapp/tier_changes.jsonl` (config: `webapp.tier_changes_path`)  
**Fields:** `game_id`, `prior_tier`, `new_tier`, `hysteresis_applied`, `p_favored`,
plus `published_at`, `season`, `week`, `refresh_kind`

Workstation-only — **not** pushed to R2, **not** shown on the site.

### Flap exposure — UNRESOLVED

The **42.1%** pooled boundary-proximity figure from W1A remains **UNRESOLVED**
as a proxy for realized intra-week tier flicker. This instrumentation is what
will resolve it after **four live publish weeks of 2026** are recorded.

---

## W7-4 — Operational verification

| Check | Status |
|-------|--------|
| E2E publish → export → push → revalidate → site | **BLOCKED** (no R2/Vercel) |
| FIXTURE banner live vs fixture | **BLOCKED** |
| Site staleness banner (>36h + past next slot) | Code present; **deployed verify BLOCKED** |
| Per-game STALE stamps | Code present; **deployed verify BLOCKED** |
| Schema major → maintenance | Code present; **deployed verify BLOCKED** |
| Cost vs free tier / $20 ceiling | **N/A until traffic** — architecture remains free-tier oriented; report after first preview week |

---

## W7-5 — Runbook (operator, six months from now)

### Publish manually

1. Ensure preview env: `NCAA_QUANT_WEBAPP__EXPORT_ENABLED=true`, R2 write creds,
   `NCAA_QUANT_WEBAPP__R2_BUCKET`, `NCAA_QUANT_WEBAPP__R2_ENDPOINT_URL`,
   `NCAA_QUANT_WEBAPP__REVALIDATE_URL`, `WEBAPP_REVALIDATE_SECRET`
2. Run the usual `predict_publish` for the target season/week/refresh_kind
3. Confirm push audit includes `meta_last: true` and `revalidation.ok`
4. Open the **protected** preview URL; confirm FIXTURE banner is **absent** for
   live meta and numbers match the published artifacts

### Roll back to a prior artifact set

1. From R2, copy the desired versioned prefix
   `v1/<season>/w<week>/<refresh_kind>/*` over `latest/*` (data files first,
   `meta.json` last)
2. POST `/api/revalidate` with the shared secret, or wait ≤6h for ISR

### Site looks stale

1. Check `meta.published_at` / `next_expected_publish_utc` in R2 `latest/meta.json`
2. If publish was missed → run publish; banner clears on fresh meta
3. If publish succeeded but site old → check revalidation alert / secret / URL;
   ISR is the fallback

### Disable export

Set `NCAA_QUANT_WEBAPP__EXPORT_ENABLED=false` (or unset). Core predict still runs;
no R2 write.

### Revalidation hook failed

1. Check notifier for “Ridge on-demand revalidation failed”
2. Verify `WEBAPP_REVALIDATE_SECRET` matches on workstation and Vercel
3. Verify Deployment Protection does not block the server-to-server POST (use a
   protection bypass / automation exception if required)
4. Site remains correct within 6h ISR even if the hook fails

---

## Built (sanctioned edits only)

| Path | Role |
|------|------|
| `src/ncaa_quant/webapp/push.py` | Revalidation hook after meta-last |
| `src/ncaa_quant/webapp/export.py` | Tier-change JSONL |
| `src/ncaa_quant/pipelines/predict.py` | Pass notifier into export/push |
| `src/ncaa_quant/config.py` | `revalidate_url`, `tier_changes_path`, secret |
| `webapp/site/src/app/api/revalidate/` | Authenticated revalidation |
| `webapp/site/src/lib/artifacts/` | Private R2 loader + SigV4 |
| `webapp/site/next.config.ts`, `robots.ts`, `vercel.json`, `.env.example` | Deploy / noindex |
| `tests/unit/test_webapp_w7.py`, site tests | Acceptance coverage |

---

## Acceptance commands (code gate)

```
$ cd webapp/site && npm run typecheck && npm run test && npm run build
$ make test
```

Site lint: eslint clean; prettier may still warn on pre-existing
`tests/capture-about-screenshots.mjs` (untouched).

---

*End of W7 task notes.*
