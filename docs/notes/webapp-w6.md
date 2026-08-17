# W6 — Methodology / About, disclaimers, site navigation

**Date:** 2026-08-13  
**Status:** Complete  
**Authority:** `docs/webapp/DESIGN.md` §1, §4, §5.4, §6; `docs/notes/23-readout.md`; `docs/notes/webapp-w2.md`–`w5.md`

---

## W6-0 — Site navigation decision

**Choice: (a) persistent minimal header** with the Ridge wordmark and three page links (This Week · Results · About), plus a quiet site footer for disclaimer discoverability.

**Defense.** Four public pages with only page-local C1 crumbs left a shared `/game/{id}` landing unable to discover Results or About without the browser Back stack. Continuing the page-local mesh would require every page to know every other page and would still fail cold landings. A full chrome bar would fight §4 restraint and the Apple Sports benchmark; a single-line header (T3 wordmark + C1 links, non-sticky, hairline rule) is the smallest global affordance that closes the discovery gap. Page-local Results/Game Detail crumbs were removed so navigation does not compete with content.

**Where the wordmark lives.** `SiteHeader` — “Ridge” links to `/`. First-time visitors learn what Ridge is from (1) the wordmark, (2) the opening identity paragraph on `/about` (also linked in the header), and (3) the layout metadata description. The first-visit disclaimer banner also states forecasts-with-uncertainty / not betting recommendations before fold.

**URL-state survival (intended, not accidental).**

| State | Behavior |
|-------|----------|
| `?tab=games` on `/results` | Page-local via `history.replaceState` in `ResultsTabs`. Header “Results” is bare `/results` → default Recorded results tab. Leaving and returning via header does **not** preserve `?tab=`. |
| `?order=conviction` on `/` | Same pattern (This Week). Header “This Week” is bare `/` → kickoff default. |
| Hash anchors (`#disclaimer`, `#responsible-gambling`) | Preserved when following footer/About links; not used by the header. |

---

## Built

| Path | Role |
|------|------|
| `src/app/about/page.tsx` | `/about` — Methodology / About |
| `src/app/layout.tsx` | SiteHeader + FirstVisitDisclaimer + SiteFooter around children |
| `src/components/SiteHeader/` | Wordmark + nav |
| `src/components/SiteFooter/` | Short disclaimer + About anchors + 1-800-GAMBLER |
| `src/components/FirstVisitDisclaimer/` | §5.4 session-dismissible §6.1 banner |
| `src/components/About/` | Full About composition |
| `src/lib/about/copy.ts` | Model copy, §6 strings, attribution placeholders |
| `tests/about.test.tsx` | Stranger test, §6 parity, no invented identity |
| `tests/cross-page-nav.test.ts` | Updated for site chrome |
| `tests/capture-about-screenshots.mjs` | Playwright evidence (ephemeral) |

Also: Offseason → live `/results` link; Scope → `/about` link; Game Detail / Results page-local nav removed; game not-found relies on header.

---

## W6-1 — What Ridge is

Plain-language two-stage description (rating engine → mapping layer → margin/total + band). Jargon glossed inline: state-space/Kalman, reduced ensemble, conformal. Numbers: central estimate vs band vs conviction-as-forecast-not-bet. Data: CFBD on workstation; site never calls CFBD/Odds; Tue primary + Thu–Sat refresh; market lines internal-only with rationale. Honesty commitments listed, including Results verdict **NOT CURRENTLY FIT TO BET**.

---

## W6-2 — Disclaimers and responsible gambling

**§6.1** rendered verbatim (year substituted) in the About disclaimer block and in the first-visit banner. Readability-only adaptation: same sentences; B1 type in a `--bg-secondary` block (not fine-print C2).

**§6.2** rendered as its own About section with `tel:` link on **1-800-GAMBLER**. Placement: after Disclaimer, before Attribution — same visual weight as other sections, not a footer footnote.

**Discoverability from every page.** Mechanism: (1) `SiteFooter` short substance + links to `/about#disclaimer` and `/about#responsible-gambling` + helpline line; (2) session-dismissible first-visit banner with full §6.1. Sufficient without a sticky legal bar: always reachable, quiet relative to forecasts.

No invented age-gating, jurisdiction, or ToS text.

---

## W6-3 — Legal flags (L1–L6)

**Public page decision:** L1–L6 are **not** public copy. They remain operator/counsel review items. The About page does not list “legal flags.”

**Constraints honored without guessing:**

| Flag | Page constraint |
|------|-----------------|
| L1 CFBD ToU | Required CFBD attribution rendered; no claim of affiliation; no logos. ToU compliance itself is counsel’s call (see checklist). |
| L2 team marks | School names as text only; no official logos (v1 already). |
| L3–L5 | No invented jurisdictional, age-gate, or analytics claims. |
| L6 a11y | Not claimed verified; target remains WCAG 2.1 AA for W7 review. |

### PRE-LAUNCH LEGAL CHECKLIST (operator → counsel)

| ID | Item | Status | Must resolve before W7 public deploy |
|----|------|--------|--------------------------------------|
| L1 | CFBD data terms for public display of schedule/scores/team names via R2 JSON | **OPEN** — attribution present; ToU not verified | Counsel sign-off on third-party display under CFBD ToU |
| L2 | Team-name / mark usage (text only; no logos) | **OPEN** — v1 uses school names only | Trademark / fair-use review |
| L3 | State-level sports-content / gambling-adjacent rules | **OPEN** — site is informational forecasting only | Counsel review of whether forecasting-only posture is sufficient in target states |
| L4 | Privacy / analytics | **OPEN / default-safe** — no third-party analytics in v1; Vercel request logs only | Confirm no analytics added before launch; if added, cookie/consent review |
| L5 | Age gating | **OPEN** — no age verification; RG link present | Whether RG link alone is sufficient |
| L6 | Accessibility WCAG 2.1 AA | **OPEN** — not verified in W0–W6 | Accessibility pass before public launch |

**Launch blocked on human/legal sign-off for L1–L3 at minimum** (per DESIGN §6.3).

---

## W6-4 — Attribution and contact

**Rendered now:** CFBD attribution (required).  
**Placeholder (clearly marked):** `[Operator to supply: public attribution / contact before W7 launch]`

**Operator must supply before W7:**

1. Public author / project attribution line (name or entity as counsel prefers)
2. Contact method (email or form URL) if any
3. Whether a public repository link should appear (do not invent)
4. Confirmation that CFBD attribution wording above is acceptable under L1 review

No author name, company, email, social handle, or repo link was invented.

---

## W6-5 — States and evidence

| State | Evidence |
|-------|----------|
| About 390 light/dark | `about-390-light.png`, `about-390-dark.png` |
| About desktop light/dark | `about-desktop-light.png`, `about-desktop-dark.png` |
| Disclaimer treatment alone | `about-disclaimer.png` |
| Responsible-gambling alone | `about-responsible-gambling.png` |
| Header at top | `nav-header-top.png` |
| Header scrolled away (non-sticky) | `nav-header-scrolled.png` |
| First-visit disclaimer | `about-first-visit-disclaimer.png` |

Capture: `tests/capture-about-screenshots.mjs`. **Zero console errors** on `/about`.

### §4.4 Anti-pattern checklist

| Item | Verdict | Notes |
|------|---------|-------|
| no default-shadcn aesthetic | **PASS** | Hand-rolled CSS modules; text column only (`about-desktop-light.png`) |
| no purple-gradient heroes | **PASS** | Flat `--bg-primary`; orange reserved for fixture/stale banners (`about-390-light.png`) |
| no emoji cards | **PASS** | No emoji in About copy or chrome (`about-disclaimer.png`) |
| no wall-of-widgets | **PASS** | Vertical sections; no dashboard tiles (`about-390-light.png`) |
| no gratuitous glassmorphism | **PASS** | Opaque surfaces, no blur (`about-responsible-gambling.png`) |
| no filler marketing copy | **PASS** | Spec-backed explanation and §6 copy only (`about-390-light.png`) |

### Positive-evidence sentences

1. **The page explains the model without marketing language** — In `about-390-light.png`, “How the forecast is built” is two-stage mechanism copy with glossed jargon, not feature bullets or a tagline.
2. **The disclaimer reads as informative, not as fine print to skip** — In `about-disclaimer.png`, §6.1 sits in a B1 secondary block under a T2 heading, same weight as other About sections.
3. **The responsible-gambling section reads as sincere, not as compliance** — In `about-responsible-gambling.png`, 1-800-GAMBLER is an accent `tel:` link in body type, not a buried C2 line.
4. **Navigation is discoverable without competing with page content** — In `nav-header-top.png`, the header is one quiet row; in `nav-header-scrolled.png` it scrolls away (non-sticky) so long About reading is uninterrupted.
5. **A first-time visitor can tell what Ridge is within one screen** — In `about-390-light.png`, the wordmark and the opening identity paragraph (“team-rating engine… predicted margin and combined score… with a range”) appear before deeper sections.

---

## Spec ambiguities resolved

1. **Header vs page-local mesh** — chose (a); documented above.
2. **§5.4 first-visit disclaimer** — implemented site-wide under the header (not About-only) so shared `/game/` URLs also surface it; dismissible via `sessionStorage`.
3. **Header stickiness** — non-sticky; evidence captures both scroll positions.
4. **L1–L6 on the public page** — none as public copy; checklist is the operator artifact.

---

## Acceptance commands

```
$ cd webapp/site
$ npm run typecheck
$ npm run test        # 91 passed (incl. About + nav)
$ npm run lint
$ npm run build       # /about route present
```

---

*End of W6.*

---

## W8-A correction — CFBD attribution (2026-08-14)

**Correction to W6-3 / W6-4 above.** W6 recorded CFBD attribution as **required**.
That was wrong relative to CFBD Terms of Use **effective August 12, 2026**
(archived at `docs/notes/_artifacts/webapp-w8a/cfbd-terms-2026-08-12.md`).

### What the terms say (verbatim sense)

- **§6 Attribution:** “Attribution is appreciated but not required.” Recommended
  line when practical: “Data provided by CollegeFootballData.com.”
- **§4:** Expressly permits publishing Derived Outputs (models, predictions,
  projections, visualizations) and displaying reasonable portions of factual API
  data as part of a larger product (carved out of Redistribution).
- **§2 / §5 (constraints still bearing on architecture):** API key stays
  server-side / never in a public repo; no programmatic third-party access to
  stored raw API responses. These bear on the deferred public-read R2 decision
  (DESIGN §3.3 as amended in W8-A).

### Ridge posture after correction

Ridge **still renders** CFBD attribution on About. That stays — recommended and
already shipped; not removed. No new attribution / contact / repo identity is
invented here (W8-B remains operator-blocked).

### PRE-LAUNCH LEGAL CHECKLIST — status update

| ID | Item | Status | Evidence |
|----|------|--------|----------|
| L1 | CFBD data terms for public display | **RESOLVED** (W8-A) | Attribution recommended not required; Derived Outputs + reasonable factual display permitted; archive `docs/notes/_artifacts/webapp-w8a/cfbd-terms-2026-08-12.md`. |
| L2 | Team-name / mark usage | **RESOLVED** (W8-A) | ToU §8 grants no trademark/logo rights; Ridge v1 already uses school names as text only with no official logos — the posture the ToU contemplates. No logos introduced in W8-A. |
| L3 | State-level sports-content rules | **OPEN** (unchanged) | As recorded in W6 |
| L4 | Privacy / analytics | **OPEN / default-safe** (unchanged) | No third-party analytics; L4 posture load-bearing — W8-A added none |
| L5 | Age gating | **OPEN** (unchanged) | As recorded in W6 |
| L6 | Accessibility WCAG 2.1 AA | **RESOLVED** (W8-A D5) | See `docs/notes/webapp-w8a.md` and `docs/notes/_artifacts/webapp-w8a/a11y-*.json` |
