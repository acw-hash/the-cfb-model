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

---

## W7-VERIFY attempt — 2026-08-13 (still blocked)

**Claim checked:** “Credentials are configured.”  
**Result:** workstation still cannot run deferred live acceptance. No deploy URL
was exercised; no new features; no public exposure.

### Credential discovery (names only; values never logged)

| Location | Finding |
|----------|---------|
| Repo `.env` | Only `CFBD_API_KEY` / `ODDS_API_KEY` present. **Absent:** `R2_*`, `WEBAPP_REVALIDATE_SECRET`, `NCAA_QUANT_WEBAPP__*`, bypass secret |
| `webapp/site/.env.local` / `.env` | Missing |
| Process / User environment | No R2 / Vercel / revalidate / bypass vars |
| Vercel CLI (`npx vercel whoami`) | **Logged out** (CLI present; no auth) |
| `webapp/site/.vercel/` | Not linked |

### Deferred items — status after this attempt

| Priority | Item | Status |
|----------|------|--------|
| 1 | W7-0 `curl -I` unauth → 401/403 + `X-Robots-Tag: noindex…` | **OPEN** — no protected preview URL available to this agent |
| 2 | W7-1.2 FIXTURE/LIVE banner both directions on deploy | **OPEN** — requires protected URL + R2 live/fixture publishes |
| 3 | W7-4 items 1–5 (E2E + revalidate through Deployment Protection bypass + timings; staleness; STALE; schema gate; cost) | **OPEN** — same blockers |
| 4 | Append-only notes | Done (this section) |

### Still required from operator (then re-run W7-VERIFY)

1. Paste the **Deployment-Protection-enabled** preview URL (do not share an unprotected production URL).
2. Workstation `.env` entries (write path): `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `NCAA_QUANT_WEBAPP__EXPORT_ENABLED=true`, `NCAA_QUANT_WEBAPP__R2_BUCKET`, `NCAA_QUANT_WEBAPP__R2_ENDPOINT_URL`, `NCAA_QUANT_WEBAPP__REVALIDATE_URL`, `WEBAPP_REVALIDATE_SECRET`.
3. `VERCEL_AUTOMATION_BYPASS_SECRET` (or equivalent) for server-to-server revalidate **through** protection.
4. `vercel login` (or `VERCEL_TOKEN`) on this workstation if deploy/env inspection is needed from CLI.

Until those are present **on this machine**, live acceptance remains blocked by design (W7 forbids creating a reachable URL without protection).

---

## W7-ENV — local env wiring (2026-08-13)

Configuration only. No deploy. No code changes. No secret values recorded here.

### Variable names (from `.env.example` + `config.py` / site loader)

**Workstation (`.env`)**

| Name | Expects |
|------|---------|
| `R2_ACCESS_KEY_ID` | S3-compatible Access Key ID (write); not `cfat_…` |
| `R2_SECRET_ACCESS_KEY` | Matching Secret Access Key |
| `WEBAPP_REVALIDATE_SECRET` | Shared bearer secret for `/api/revalidate` |
| `NCAA_QUANT_WEBAPP__EXPORT_ENABLED` | `true` on preview workstation only |
| `NCAA_QUANT_WEBAPP__R2_BUCKET` | Bucket name |
| `NCAA_QUANT_WEBAPP__R2_ENDPOINT_URL` | `https://<accountid>.r2.cloudflarestorage.com` |
| `NCAA_QUANT_WEBAPP__REVALIDATE_URL` | Protected preview `…/api/revalidate` |

(Also present for other pipelines: `CFBD_API_KEY`, `ODDS_API_KEY`, optional `NTFY_AUTH_TOKEN` / `TELEGRAM_BOT_TOKEN`.)

**Site (`webapp/site/.env.local`)**

| Name | Expects |
|------|---------|
| `ARTIFACT_SOURCE` | `r2` for private-preview path |
| `R2_BUCKET` | Same bucket name |
| `R2_ACCOUNT_ID` | Cloudflare account id (or set `R2_ENDPOINT_URL` instead) |
| `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | Prefer **read-only** S3 pair |
| `WEBAPP_REVALIDATE_SECRET` | Same shared secret as workstation |

### gitignore evidence

```
$ git check-ignore -v .env
.gitignore:42:.env	.env
$ git check-ignore -v webapp/site/.env.local
.gitignore:43:.env.*	webapp/site/.env.local
```

Neither path is tracked (`git ls-files` does not know them).

### Files written — fill status (names only)

| File | Configured (non-placeholder) | Operator must fill |
|------|------------------------------|--------------------|
| `.env` | `CFBD_API_KEY`, `ODDS_API_KEY`, `NCAA_QUANT_WEBAPP__EXPORT_ENABLED`, `NCAA_QUANT_WEBAPP__R2_BUCKET` | `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `WEBAPP_REVALIDATE_SECRET`, `NCAA_QUANT_WEBAPP__R2_ENDPOINT_URL` (replace `REPLACE_ACCOUNT_ID`), `NCAA_QUANT_WEBAPP__REVALIDATE_URL` (replace `REPLACE_PREVIEW`); optional NTFY/Telegram |
| `webapp/site/.env.local` | `ARTIFACT_SOURCE`, `R2_BUCKET` | `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `WEBAPP_REVALIDATE_SECRET` |

### Connectivity check

**Not run yet** — R2 S3 key placeholders still present. After the operator fills the
lines above, re-run W7-ENV step 4 (list/HEAD only; no value echo). If keys are
`cfat_…` Cloudflare API tokens rather than an S3 Access Key pair → STOP (SigV4
loader requires the S3 pair).

---

## W7-ENV-CHECK — R2 connectivity (2026-08-13)

Read-only. No bucket writes. No deploy. No credential values recorded.

### Variable status (workstation `.env`)

| Name | Status |
|------|--------|
| `R2_ACCESS_KEY_ID` | PRESENT (S3-like shape; not `cfat_`) |
| `R2_SECRET_ACCESS_KEY` | PRESENT (S3-like shape; not `cfat_`) |
| `NCAA_QUANT_WEBAPP__R2_BUCKET` | PRESENT |
| `NCAA_QUANT_WEBAPP__R2_ENDPOINT_URL` | PRESENT but **malformed** (see below) |
| `NCAA_QUANT_WEBAPP__EXPORT_ENABLED` | PRESENT |
| `WEBAPP_REVALIDATE_SECRET` | PLACEHOLDER |
| `NCAA_QUANT_WEBAPP__REVALIDATE_URL` | PLACEHOLDER |

### Connectivity — FAIL

| Field | Result |
|-------|--------|
| Operation | `list_objects_v2` (MaxKeys=10), read-only |
| HTTP status | n/a (request not issued) |
| Object count | n/a |
| Diagnosis | **endpoint_malformed** |

**Endpoint diagnosis (structure only):** value length 100; does not start with `https://`; contains an embedded `https://…r2.cloudflarestorage.com` after a leading key-name prefix (`NCAA_QUANT_W…` ordinals at start). `urlparse` yields empty scheme/netloc. Canonical pattern `https://<32-hex>.r2.cloudflarestorage.com` does not match.

No fix attempted (per task). Credentials were not classified as `cfat_` tokens; auth was not reached.

### gitignore evidence

```
$ git check-ignore -v .env
.gitignore:42:.env	.env
```

---

## W7-ENV-CHECK reverify — 2026-08-13

Operator corrected endpoint. Read-only recheck only.

### Variable status

| Name | Status |
|------|--------|
| `R2_ACCESS_KEY_ID` | PRESENT (S3-like; not `cfat_`) |
| `R2_SECRET_ACCESS_KEY` | PRESENT (S3-like; not `cfat_`) |
| `NCAA_QUANT_WEBAPP__R2_BUCKET` | PRESENT |
| `NCAA_QUANT_WEBAPP__R2_ENDPOINT_URL` | PRESENT (canonical `https://…r2.cloudflarestorage.com`) |
| `NCAA_QUANT_WEBAPP__EXPORT_ENABLED` | PRESENT |
| `WEBAPP_REVALIDATE_SECRET` | PLACEHOLDER |
| `NCAA_QUANT_WEBAPP__REVALIDATE_URL` | PLACEHOLDER |

### Connectivity — PASS

| Field | Result |
|-------|--------|
| Operation | `list_objects_v2` (MaxKeys=10), read-only |
| HTTP status | **200** |
| Object count | **0** (empty bucket — expected PASS) |

### gitignore evidence

```
$ git check-ignore -v .env
.gitignore:42:.env	.env
```

---

## W7-VERIFY attempt — 2026-08-13 (credentials partial; preview URL missing)

**Claim checked:** “Credentials and protected preview deploy are now configured
(R2 private bucket, Vercel Standard Protection ON, automation bypass secret and
WEBAPP_REVALIDATE_SECRET both set on workstation and site).”

**Result:** Claim is **only partially true on this workstation**. Live acceptance
items that need a Deployment-Protection-enabled preview URL remain **BLOCKED**.
No deploy was created or altered by this agent. No credential values recorded.
No public exposure.

### Credential discovery (names only; values never logged)

| Location / name | Status |
|-----------------|--------|
| `.env` `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | **PRESENT** (S3-like lengths; not `cfat_`) |
| `.env` `NCAA_QUANT_WEBAPP__EXPORT_ENABLED` | **PRESENT** (`true`) |
| `.env` `NCAA_QUANT_WEBAPP__R2_BUCKET` | **PRESENT** (`ridge-artifacts`) |
| `.env` `NCAA_QUANT_WEBAPP__R2_ENDPOINT_URL` | **PRESENT** (canonical `https://….r2.cloudflarestorage.com`) |
| `.env` `WEBAPP_REVALIDATE_SECRET` | **PRESENT** |
| `.env` `NCAA_QUANT_WEBAPP__REVALIDATE_URL` | **PLACEHOLDER** — host is still `REPLACE_PREVIEW.vercel.app` |
| `.env` `VERCEL_AUTOMATION_BYPASS_SECRET` | **ABSENT** (canonical name) |
| `.env` `WORKSTATION_REVALIDATION_VERCEL` | **PRESENT** (non-canonical name; not read by `config.py` / `push.py`) |
| `webapp/site/.env.local` R2 read keys / `WEBAPP_REVALIDATE_SECRET` / account id | **PLACEHOLDER** (`REPLACE_*`) |
| Process / User env (`VERCEL_TOKEN`, bypass, revalidate) | **ABSENT** |
| Vercel CLI (`npx vercel whoami`) | **Logged out** |
| `webapp/site/.vercel/` | **Not linked** |

R2 read-only recheck this run: `list_objects_v2` **HTTP 200**, **key_count=0**
(empty bucket — no published `latest/*` yet).

**Security note (no values):** tracked `.env.example` currently has **non-empty**
`R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` assignments. Operator should scrub
to empty placeholders and rotate those keys if they were ever real.

### Priority results

#### 1) W7-0 — unauthenticated `curl -I` — **BLOCKED**

**Reason:** No real protected preview URL is available to this agent.
`NCAA_QUANT_WEBAPP__REVALIDATE_URL` still points at
`https://REPLACE_PREVIEW.vercel.app/api/revalidate`. Vercel CLI is logged out;
project is unlinked; no `*.vercel.app` host appears in local config or prior
notes except the placeholder.

**Evidence that could not be collected:**

```
# expected after operator pastes real protected URL:
curl -I https://<protected-preview>/
# expect: HTTP/2 401 or 403 from Deployment Protection
# expect: X-Robots-Tag: noindex, nofollow, noarchive
```

**Not run** against the placeholder host (would not prove protection).

#### 2) W7-1.2 — FIXTURE/LIVE banner both directions on deploy — **BLOCKED**

**Reason:** Requires (a) protected preview URL reachable with operator/bypass
auth, and (b) R2 `latest/meta.json` toggled `fixture: true` then live
(`fixture` omitted/false) with revalidate. Bucket is empty; no preview URL.

#### 3) W7-4 items 1–5 — **BLOCKED** (each)

| # | Check | Status | Specific blocker |
|---|-------|--------|------------------|
| 1 | E2E publish → export → push → revalidate → site + timings | **BLOCKED** | Placeholder `REVALIDATE_URL`; empty R2; no preview URL; `push.py` sends Bearer revalidate secret only — **does not** attach `x-vercel-protection-bypass` / read `WORKSTATION_REVALIDATION_VERCEL` |
| 2 | Site staleness banner (>36h + past next slot) | **BLOCKED** | No deployed URL to observe |
| 3 | Per-game STALE stamps | **BLOCKED** | No deployed URL; no stale-stamped publish in R2 |
| 4 | Schema major → maintenance gate | **BLOCKED** | No deployed URL; cannot doctor/restore `meta` on a live site |
| 5 | Cost vs free tier / $20 ceiling | **BLOCKED** | No Vercel login / project link on this machine; zero preview traffic observable here. Remains free-tier oriented by architecture only |

#### 4) Notes append — **DONE** (this section)

### Still required from operator (then re-run W7-VERIFY)

1. Set `NCAA_QUANT_WEBAPP__REVALIDATE_URL` to the real **Deployment-Protection-ON**
   preview `…/api/revalidate` (not `REPLACE_PREVIEW`).
2. Paste that same preview origin (or confirm it in `.env`) so W7-0 `curl -I`
   can be run from this workstation.
3. Put the automation bypass under the name the verification / Vercel docs
   expect (`VERCEL_AUTOMATION_BYPASS_SECRET`), or confirm how workstation
   revalidate POSTs are supposed to send `x-vercel-protection-bypass`
   (`push.py` does not send it today).
4. Ensure Vercel project server env has read-only R2 + matching
   `WEBAPP_REVALIDATE_SECRET` (site `.env.local` placeholders do not prove
   dashboard env).
5. Optional for CLI inspection: `vercel login` / link `webapp/site`.

Until the **preview URL** is present on this machine, items 1–3 stay **BLOCKED**
by design (W7 forbids inventing or opening an unprotected reachable deploy).

---

## W7-VERIFY attempt — 2026-08-13 (preview URL live; site R2 still broken)

**Claim checked:** “Credentials and protected preview deploy are now configured
(R2 private bucket, Vercel Standard Protection ON, automation bypass secret and
WEBAPP_REVALIDATE_SECRET both set on workstation and site).”

**Result:** Workstation write path + protected **deployment URL** are usable.
**Deployed site still cannot render artifacts** (MaintenanceState after a successful
fixture push to R2). Production alias is **publicly reachable** — W7 non-public
posture is **not** satisfied for that host. No code changes. No credential values
recorded. Protection was **not** removed; no custom domain attached; W6 legal
checklist remains **OPEN**.

### Credential discovery (names only; values never logged)

| Location / name | Status |
|-----------------|--------|
| `.env` `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | **PRESENT** (S3-like; not `cfat_`) |
| `.env` `NCAA_QUANT_WEBAPP__EXPORT_ENABLED` | **PRESENT** (`true`) |
| `.env` `NCAA_QUANT_WEBAPP__R2_BUCKET` | **PRESENT** (`ridge-artifacts`) |
| `.env` `NCAA_QUANT_WEBAPP__R2_ENDPOINT_URL` | **PRESENT** (canonical R2 host) |
| `.env` `WEBAPP_REVALIDATE_SECRET` | **PRESENT** |
| `.env` `NCAA_QUANT_WEBAPP__REVALIDATE_URL` | **PRESENT** — host `the-cfb-model-hd5oqmobf-alecs-projects-2eeacfd8.vercel.app` |
| `.env` `VERCEL_AUTOMATION_BYPASS_SECRET` | **PRESENT** |
| `webapp/site/.env.local` | Exists; `ARTIFACT_SOURCE=r2`, `R2_BUCKET=ridge-artifacts`; R2 keys / account / revalidate secret still **PLACEHOLDER** (local only — does not prove Vercel dashboard) |
| Vercel CLI | **Logged out**; `webapp/site/.vercel/` absent |

R2 notes this run: bucket `ridge-artifacts` exists; `ridge-artifacts-preview`
→ **NoSuchBucket**. After fixture push: `latest/*` key_count=**5**. Read-pair
creds in `.env` (`RIDGE_READ_*`) can GET `latest/meta.json` (OK).

---

### 1) W7-0 — unauthenticated `curl -I` — **BLOCKED** (posture incomplete)

**Protected deployment URL** (from `NCAA_QUANT_WEBAPP__REVALIDATE_URL` origin):
Standard Protection is ON for this host. Unauthenticated **GET** returns **302**
to Vercel SSO (not 401/403 — SSO redirect is how Standard Protection answers
HTML). `X-Robots-Tag: noindex` is present on that response.

```
$ curl -I https://the-cfb-model-hd5oqmobf-alecs-projects-2eeacfd8.vercel.app/
HTTP/1.1 302 Found
Cache-Control: no-store, max-age=0
Content-Type: text/plain
Date: Fri, 14 Aug 2026 00:41:47 GMT
Location: https://vercel.com/sso-api?url=https%3A%2F%2Fthe-cfb-model-hd5oqmobf-alecs-projects-2eeacfd8.vercel.app%2F&nonce=…
Server: Vercel
Set-Cookie: _vercel_sso_nonce=…; Max-Age=3600; Path=/; Secure; HttpOnly; SameSite=Lax
Strict-Transport-Security: max-age=63072000; includeSubDomains; preload
X-Frame-Options: DENY
X-Robots-Tag: noindex
X-Vercel-Id: cle1::…
```

Unauthenticated **POST** `/api/revalidate` (no bypass) → **401**
`Protected deployment` / `vercel_auth_enabled: true` (confirms protection on
API as well).

**Production alias exposure (critical):**

```
$ curl -I https://the-cfb-model.vercel.app/
HTTP/1.1 200 OK
… Content-Type: text/html; charset=utf-8 …
X-Robots-Tag: noindex, nofollow, noarchive
X-Matched-Path: /
X-Powered-By: Next.js
```

Unauthenticated GET returns full HTML (MaintenanceState body observed). That
host is **publicly reachable**. W7 forbids a publicly reachable site; this is
why W7-0 is **BLOCKED** overall despite protection on the hashed deployment URL.
`noindex` is present but does **not** equal access control.

**Agent did not** disable protection, attach a domain, or widen exposure.

---

### 2) W7-1.2 — FIXTURE/LIVE banner both directions on deploy — **BLOCKED**

**What ran**

1. Pushed full `webapp/fixtures/*` to R2 `ridge-artifacts` via `push_artifacts_to_r2`
   (`meta_last=true`, `fixture: true`, `schema_version: 1.1.0`).
2. On-demand revalidate **with** `x-vercel-protection-bypass` → HTTP **200**
   `{"ok":true,"revalidated":true,…}`.
3. Fetched `/` **with** bypass against the protected deployment URL.

**Site result (both before and after push):** still MaintenanceState —

`Ridge is updating — check back shortly. Published artifacts use a schema version this build does not support.`

No `FIXTURE DATA` banner. Live (fixture omitted/false) direction **not attempted**
after this failure — would not be observable while the site cannot load `meta`.

**Specific blocker:** Deployed app is not successfully reading `ridge-artifacts/latest/*`
(load failure and unsupported-schema both render the same `MaintenanceState` copy).
Local `.env.local` still names non-existent `ridge-artifacts`; Vercel
dashboard R2 env cannot be inspected here (`vercel` logged out). Until Vercel
server env points at the real private bucket with working read creds, banner
honesty cannot be verified in the deployed environment.

---

### 3) W7-4 items 1–5

| # | Check | Status | Evidence / blocker |
|---|-------|--------|--------------------|
| 1 | E2E publish → export → push → revalidate → site + timings | **BLOCKED** (partial workstation path only) | **Push** of fixture set: `push_ms≈3255`, upload `elapsed_ms` sum≈2932, 10 keys (versioned+latest), `meta_last=true`. **Stock** `trigger_on_demand_revalidation` (Bearer only, no bypass) → **FAIL HTTP 401** Protection (`push.py` does not send `x-vercel-protection-bypass`). **Manual** revalidate with bypass → **200** in ≈310–1056ms. **Site** after push+revalidate: still MaintenanceState — E2E does **not** complete to a rendered slate. |
| 2 | Site staleness banner (>36h + past next slot) | **BLOCKED** | Cannot observe layout banners while MaintenanceState short-circuits. |
| 3 | Per-game STALE stamps | **BLOCKED** | Same; no rendered `GameRow` / `StaleBadge` on deploy. |
| 4 | Schema major → maintenance gate | **BLOCKED** as a *controlled* check | Site already shows MaintenanceState, but that is indistinguishable from R2 load failure with current UI copy — cannot credit a deliberate schema-major doctor/restore cycle. |
| 5 | Cost vs free tier / $20 ceiling | **BLOCKED** | No Vercel login / usage dashboard on this workstation. Architecture remains Hobby/free-tier oriented; no traffic or invoice evidence to report. |

**Revalidation through Deployment Protection (bypass)** — isolated probe **PASS**:

- Without bypass: HTTP **401** `Protected deployment`
- With `x-vercel-protection-bypass`: HTTP **200** `ok/revalidated`
- Stock `push.py` path: **does not** attach bypass → **401** (automation gap remains)

---

### 4) Notes append — **DONE** (this section)

### Operator actions required before re-run can pass

1. **Enable Deployment Protection on Production** (or remove/unpublish the public
   `*.vercel.app` production alias) so unauthenticated GET cannot return 200 HTML.
   Keep Standard Protection on previews.
2. Fix **Vercel server env** for private R2: `R2_BUCKET=ridge-artifacts` (not
   `ridge-artifacts`), plus `R2_ACCOUNT_ID` or `R2_ENDPOINT_URL`, and
   **read-only** `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY`, and matching
   `WEBAPP_REVALIDATE_SECRET`. Redeploy after env change.
3. Decide how workstation push sends bypass: either teach `push.py` to add
   `x-vercel-protection-bypass` from `VERCEL_AUTOMATION_BYPASS_SECRET`, or
   configure a Vercel automation exception — stock push revalidate is 401 today.
4. Then re-run W7-1.2 (fixture banner on → live banner off) and W7-4 doctor
   checks (staleness / STALE / schema major) against the **protected** URL only.

Fixture artifacts were **left** in R2 `latest/*` (`fixture: true`) for the next
attempt once Vercel read env is fixed.

---

## W7-VERIFY attempt — 2026-08-13 (re-run; still blocked)

**Claim checked:** “Credentials and protected preview deploy are now configured
(R2 private bucket, Vercel Standard Protection ON, automation bypass secret and
WEBAPP_REVALIDATE_SECRET both set on workstation and site).”

**Result:** Workstation write path + bypass secret + protected **deployment** URL
are present and usable. **W7 non-public posture still fails** because production
alias remains publicly reachable. **Deployed site still cannot render R2
artifacts** (MaintenanceState after successful fixture *and* live-shaped pushes).
No code changes. No credential values recorded. Protection was not removed; no
custom domain attached; W6 legal checklist remains **OPEN**.

### Credential discovery (names only; values never logged)

| Location / name | Status |
|-----------------|--------|
| `.env` `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | **PRESENT** |
| `.env` `NCAA_QUANT_WEBAPP__EXPORT_ENABLED` | **PRESENT** (`true`) |
| `.env` `NCAA_QUANT_WEBAPP__R2_BUCKET` | **PRESENT** (`ridge-artifacts`) |
| `.env` `NCAA_QUANT_WEBAPP__R2_ENDPOINT_URL` | **PRESENT** (canonical R2 host) |
| `.env` `WEBAPP_REVALIDATE_SECRET` | **PRESENT** |
| `.env` `NCAA_QUANT_WEBAPP__REVALIDATE_URL` | **PRESENT** — host `the-cfb-model-hd5oqmobf-alecs-projects-2eeacfd8.vercel.app` |
| `.env` `VERCEL_AUTOMATION_BYPASS_SECRET` | **PRESENT** |
| `webapp/site/.env.local` | Exists; `ARTIFACT_SOURCE=r2`, `R2_BUCKET=ridge-artifacts-preview`; R2 keys / account / revalidate still **PLACEHOLDER** (local only — does not prove Vercel dashboard) |
| Vercel CLI | **Logged out**; `webapp/site/.vercel/` absent |

R2 this run: `latest/*` key_count=**5** before/after cycle; `meta.schema_version=1.1.0`
(compatible with `SUPPORTED_SCHEMA_MAJOR=1`). After cycle, fixtures restored
(`fixture: true`).

---

### 1) W7-0 — unauthenticated `curl -I` — **BLOCKED** (posture incomplete)

**Protected deployment URL** (from `NCAA_QUANT_WEBAPP__REVALIDATE_URL` origin):
Standard Protection ON. Unauthenticated **HEAD/GET** returns **302** to Vercel
SSO (not 401/403 — SSO redirect is how Standard Protection answers HTML).
`X-Robots-Tag: noindex` is present.

```
$ curl -I https://the-cfb-model-hd5oqmobf-alecs-projects-2eeacfd8.vercel.app/
HTTP/1.1 302 Found
Cache-Control: no-store, max-age=0
Content-Type: text/plain
Date: Fri, 14 Aug 2026 00:49:06 GMT
Location: https://vercel.com/sso-api?url=https%3A%2F%2Fthe-cfb-model-hd5oqmobf-alecs-projects-2eeacfd8.vercel.app%2F&nonce=…
Server: Vercel
Set-Cookie: …
Strict-Transport-Security: max-age=63072000; includeSubDomains; preload
X-Frame-Options: DENY
X-Robots-Tag: noindex
X-Vercel-Id: …
```

Unauthenticated **POST** `/api/revalidate` (no bypass) → **401**
`Protected deployment` / `vercel_auth_enabled: true`.

**Production alias exposure (unchanged blocker):**

```
$ curl -I https://the-cfb-model.vercel.app/
HTTP/1.1 200 OK
… Content-Type: text/html; charset=utf-8 …
X-Robots-Tag: noindex, nofollow, noarchive
X-Matched-Path: /
X-Powered-By: Next.js
```

Unauthenticated GET returns full HTML (MaintenanceState body). That host is
**publicly reachable**. W7 forbids a publicly reachable site; this is why W7-0
is **BLOCKED** overall despite protection on the hashed deployment URL.
`noindex` ≠ access control.

**Agent did not** disable protection, attach a domain, or widen exposure.

---

### 2) W7-1.2 — FIXTURE/LIVE banner both directions on deploy — **BLOCKED**

**What ran (both directions attempted)**

1. **Fixture push** of `webapp/fixtures/*` → R2 `ridge-artifacts`
   (`meta_last=true`, `fixture: true`, `schema_version: 1.1.0`).
   Timings: `push_ms≈4219`, upload `elapsed_ms` sum≈3743, 10 keys.
2. Stock `push.py` revalidate (Bearer only) → **FAIL HTTP 401** Protection.
3. Manual revalidate with `x-vercel-protection-bypass` → **200** in ≈212ms
   `{"ok":true,"revalidated":true,…}`.
4. GET `/` **with** bypass against protected deployment → **MaintenanceState**
   (no `FIXTURE DATA` banner).
5. **Live-shaped push** (same artifacts with `fixture` key **omitted** from meta
   + other JSON) → `meta_last=true`; R2 confirm `fixture=<absent>`.
   Timings: `push_ms≈2748`, upload sum≈2729.
6. Manual bypass revalidate → **200** in ≈181ms.
7. GET `/` with bypass → still **MaintenanceState** (banner clear not observable).

**Site markers (identical both directions):**

| After | `has_fixture_banner` | `has_maintenance` | `has_schema_msg` |
|-------|----------------------|-------------------|------------------|
| Fixture set | false | true | true |
| Live set (`fixture` absent) | false | true | true |

Visible copy: `Ridge is updating — check back shortly. Published artifacts use a
schema version this build does not support.`

**Specific blocker:** Deployed app is not successfully reading
`ridge-artifacts/latest/*`. Load failure and unsupported-schema share the same
`MaintenanceState` copy; R2 meta is `1.1.0` (major 1 = supported), so this is
**not** a real schema gate — it is an R2 load failure path. Local
`.env.local` still names non-existent `ridge-artifacts-preview` and placeholders;
Vercel dashboard R2 env cannot be inspected here (`vercel` logged out). Until
Vercel server env points at the real private bucket with working read creds
**and** a redeploy picks them up, banner honesty cannot be verified on deploy.

Fixtures restored to R2 `latest/*` (`fixture: true`) after the cycle.

---

### 3) W7-4 items 1–5

| # | Check | Status | Evidence / blocker |
|---|-------|--------|--------------------|
| 1 | E2E publish → export → push → revalidate → site + timings | **BLOCKED** (partial workstation path only) | Fixture push timings above; stock revalidate **401** (no bypass header in `push.py`); manual bypass revalidate **200** ≈180–212ms; site remains MaintenanceState — E2E does **not** complete to a rendered slate. |
| 2 | Site staleness banner (>36h + past next slot) | **BLOCKED** | Cannot observe layout banners while MaintenanceState short-circuits. (Fixture `published_at` is already 2024-09-24 — would be stale *if* meta loaded.) |
| 3 | Per-game STALE stamps | **BLOCKED** | Same; no rendered `GameRow` / `StaleBadge` on deploy. |
| 4 | Schema major → maintenance gate | **BLOCKED** as a *controlled* check | Site already shows MaintenanceState from R2 load failure — indistinguishable from schema-major copy; cannot credit a deliberate doctor/restore cycle. |
| 5 | Cost vs free tier / $20 ceiling | **BLOCKED** | No Vercel login / usage dashboard on this workstation. Architecture remains Hobby/free-tier oriented; no traffic or invoice evidence to report. |

**Revalidation through Deployment Protection (bypass)** — isolated probe **PASS**:

- Unauth / Bearer-only: HTTP **401** `Protected deployment`
- Bearer + `x-vercel-protection-bypass`: HTTP **200** `ok/revalidated` (≈180–884ms this run)
- Stock `push.py` path: **does not** attach bypass → **401** (automation gap remains)

Production unauth HTML also MaintenanceState (same failure mode; publicly reachable).

---

### 4) Notes append — **DONE** (this section)

### Operator actions required before re-run can pass

1. **Enable Deployment Protection on Production** (or remove/unpublish the public
   `the-cfb-model.vercel.app` production alias) so unauthenticated GET cannot
   return 200 HTML. Keep Standard Protection on previews.
2. Fix **Vercel server env** (Production *and* Preview): `R2_BUCKET=ridge-artifacts`
   (not `ridge-artifacts-preview`), `R2_ACCOUNT_ID` or `R2_ENDPOINT_URL`,
   **read-only** `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY`, matching
   `WEBAPP_REVALIDATE_SECRET`, `ARTIFACT_SOURCE=r2`. **Redeploy** after env
   change; point `NCAA_QUANT_WEBAPP__REVALIDATE_URL` at a protected deployment
   that has the new env.
3. Decide how workstation push sends bypass: teach `push.py` to add
   `x-vercel-protection-bypass` from `VERCEL_AUTOMATION_BYPASS_SECRET`, or
   configure a Vercel automation exception — stock push revalidate is 401 today.
4. Then re-run W7-1.2 (fixture banner on → live banner off) and W7-4 doctor
   checks (staleness / STALE / schema major) against the **protected** URL only.

Fixture artifacts are again in R2 `latest/*` (`fixture: true`) for the next
attempt once Vercel read env is fixed.

---

## W7-CLOSE — 2026-08-14 (bypass wired; deferred verification re-run)

**Code:** `push.py` reads `VERCEL_AUTOMATION_BYPASS_SECRET` via
`SecretsSettings` and sends `x-vercel-protection-bypass` on the revalidation
POST when set; unchanged when unset. Secret is never logged, echoed, or written
to push audit / alerts (unit tests cover both paths + env wiring).

**Operator decision (accepted risk, not relitigated):** Production alias
`the-cfb-model.vercel.app` remains **publicly reachable** (unauthenticated GET
→ 200 HTML). Operator accepts this posture for W7. W6 legal checklist remains
**OPEN**. Protection was not removed; no custom domain; no search-engine
submission.

**Claim checked:** R2 bucket corrected to `ridge-artifacts` in Vercel dashboard;
redeploy occurred; stock push path should now complete E2E through revalidation.

**Result:** Workstation push + **stock** revalidation **PASS** (HTTP 200 with
bypass header). R2 `latest/*` is populated and readable from workstation (write +
read creds, `key_count=5`, `meta.schema_version=1.1.0`, `fixture=true`).
**Deployed site still cannot load R2 artifacts** — both protected deployment and
production alias render `MaintenanceState` after every push + successful
revalidate. Banner / staleness / STALE / controlled schema checks remain
**BLOCKED** on deploy.

### Credential discovery (names only; values never logged)

| Location / name | Status |
|-----------------|--------|
| `.env` `VERCEL_AUTOMATION_BYPASS_SECRET` | **PRESENT** (now read by `push.py`) |
| `.env` `WEBAPP_REVALIDATE_SECRET` | **PRESENT** |
| `.env` `NCAA_QUANT_WEBAPP__REVALIDATE_URL` | **PRESENT** — host `the-cfb-model-hd5oqmobf-alecs-projects-2eeacfd8.vercel.app` |
| `.env` R2 write + `RIDGE_READ_*` | **PRESENT** — both can list/GET `ridge-artifacts/latest/*` |
| Vercel CLI | **Logged out** (dashboard env not inspectable here) |

R2 this run: `latest/*` key_count=**5**; fixtures restored (`fixture: true`).

---

### 1) W7-0 — unauthenticated `curl -I` — **ACCEPTED RISK** (posture note)

**Protected deployment URL** (from `NCAA_QUANT_WEBAPP__REVALIDATE_URL` origin):
Standard Protection ON. Unauthenticated HEAD → **302** to Vercel SSO.
`X-Robots-Tag: noindex`.

**Production alias** (`the-cfb-model.vercel.app`): Unauthenticated HEAD → **200**
`text/html`. `X-Robots-Tag: noindex, nofollow, noarchive`. **Publicly
reachable** — operator **accepts** for W7 (see decision above). W6 legal
checklist **OPEN**.

Unauthenticated POST `/api/revalidate` (no bypass) → **401** `Protected deployment`.

---

### 2) W7-1.2 — FIXTURE/LIVE banner both directions on deploy — **BLOCKED**

**What ran (both directions)**

1. **Fixture push** of `webapp/fixtures/*` → R2 `ridge-artifacts`
   (`meta_last=true`, `fixture: true`, `schema_version: 1.1.0`).
   Timings: `push_ms≈4358`, upload `elapsed_ms` sum≈3043, 10 keys.
2. **Stock** `push_artifacts_to_r2` revalidate (Bearer + bypass from env) → **200**
   (≈141ms isolated probe).
3. GET `/` with bypass (protected) → **MaintenanceState** — no `FIXTURE DATA`
   banner.
4. **Live-shaped push** (`fixture` key omitted from all JSON) → `meta_last=true`.
   Timings: `push_ms≈3046`, upload sum≈2870.
5. Stock revalidate → **200**.
6. GET `/` with bypass → still **MaintenanceState** (banner clear not observable).

**Site markers (identical both directions, protected + production):**

| After | `has_fixture_banner` | `has_maintenance` | `has_schema_msg` |
|-------|----------------------|-------------------|------------------|
| Fixture set | false | true | true |
| Live set (`fixture` absent) | false | true | true |

**Specific blocker:** Deployed app is not successfully reading
`ridge-artifacts/latest/*` despite workstation-confirmed objects and successful
on-demand revalidate. R2 meta is `1.1.0` (major 1 = supported), so this is the
**load-failure** path, not a real schema gate. Vercel server env (read creds /
`R2_BUCKET` / endpoint) cannot be verified from this workstation (`vercel`
logged out). Operator-reported dashboard fix + redeploy did not unblock deploy
reads in this verification run.

Fixtures restored to R2 `latest/*` (`fixture: true`) after the cycle.

---

### 3) W7-4 items 1–5

| # | Check | Status | Evidence / blocker |
|---|-------|--------|--------------------|
| 1 | E2E publish → export → push → revalidate → site + timings | **PARTIAL PASS** | Fixture push `push_ms≈4358`; live push `push_ms≈3046`; stock revalidate **200** both cycles (bypass wired). Site remains MaintenanceState — E2E does **not** complete to a rendered slate. |
| 2 | Site staleness banner (>36h + past next slot) | **BLOCKED** | MaintenanceState short-circuits layout; fixture `published_at` would trigger staleness *if* meta loaded. |
| 3 | Per-game STALE stamps | **BLOCKED** | Doctored `STALE(odds, 4.0h)` push + revalidate OK; no rendered `StaleBadge` on deploy. |
| 4 | Schema major → maintenance gate | **BLOCKED** as a *controlled* check | Doctored `schema_version=2.0.0` push + revalidate OK; site already MaintenanceState from load failure — indistinguishable from deliberate schema gate. |
| 5 | Cost vs free tier / $20 ceiling | **BLOCKED** | No Vercel login / usage dashboard on this workstation. Architecture remains Hobby/free-tier oriented. |

**Revalidation through Deployment Protection (stock path)** — **PASS**:

- Unauth / Bearer-only: HTTP **401** `Protected deployment`
- Stock `push.py` with `VERCEL_AUTOMATION_BYPASS_SECRET` set: HTTP **200**
  `ok/revalidated` (≈63–141ms this run)
- Push audit: `audit_leaked_secret_names=[]` (no secret names in JSON trail)

---

### 4) Notes append — **DONE** (this section)

### Operator actions if deploy reads should pass

1. Confirm Vercel **Production** server env: `R2_BUCKET=ridge-artifacts`,
   `R2_ENDPOINT_URL` or `R2_ACCOUNT_ID`, **read-only**
   `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY`, `WEBAPP_REVALIDATE_SECRET`,
   `ARTIFACT_SOURCE=r2` — and that the **production** deployment serving
   `the-cfb-model.vercel.app` picked up the env after redeploy (not just
   Preview).
2. Re-run W7-1.2 banner cycle and W7-4 doctor checks once GET `/` serves
   artifacts instead of MaintenanceState.

Fixture artifacts are again in R2 `latest/*` (`fixture: true`).
