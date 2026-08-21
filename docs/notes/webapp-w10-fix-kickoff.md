# W10-FIX — kickoff time rendering on Game Detail

**Date:** 2026-08-21  
**Branch:** `w10-fix-kickoff` (off `main` @ `7251aa6`; not off `w10-ui`)  
**Authority:** DESIGN §2 (Game Detail), §1.1 (`kickoff_utc` is UTC by contract)

## Survey (before edit)

| Site | Component | Helper | Runtime | Verdict |
|------|-----------|--------|---------|---------|
| This Week slate | `GameRow` under `ThisWeekSlate` (`"use client"`) | `formatKickoffLocal` | Browser TZ | Correct |
| Game Detail | `MatchupHeader` (Server Component) | same | Vercel UTC | **Bug** |
| Results graded rows | `GradedGameRow` (SSR children into client tabs) | same | Vercel UTC | Same SSR defect |
| Shared helper | `lib/formatting/time.ts` | no `timeZone` arg | runtime default | Ambiguous |
| Not kickoff | `PublishedAtStamp` / `formatAbsoluteUtc` | intentional UTC | OK | Out of scope |

Root cause: one helper, three call sites. Slate is under a client boundary; Game Detail
and Results format on the server. Indiana/Maryland `2024-09-28T16:00:00Z` → slate
12:00 PM ET, detail 4:00 PM UTC wall clock. Not coincidence and not double-conversion.

`w10-ui` only rewrites `ForecastBlock` under GameDetail — `MatchupHeader` conflict
surface is empty for this branch. Operator note: rebase conflict when `w10-ui` lands
is expected elsewhere, not here.

Timezone handling depended on **server** locale for SSR surfaces. Fix uses the
visitor zone (same pattern as slate grouping), not server locale.

## Pre-fix failure (demonstrated)

Under `TZ=UTC`, before the helper accepted `timeZone`:

```
expected 'Sat, Sep 28, 4:00 PM' to match /12:00\s*PM/
```

`formatKickoffLocal(iso, "America/New_York")` ignored the second argument and used
the host zone — the production Game Detail path.

## What shipped

- `formatKickoffLocal(iso, timeZone?)` — explicit IANA zone for local formatting
- `KickoffTime` client component — resolves `Intl…timeZone` in the browser; optional
  `timeZone` prop for tests
- Wired through `GameRow`, `MatchupHeader`, `GradedGameRow`
- `/about` copy: withheld uncertainty bands are deliberate (no invented count)
- Tests: `tests/kickoff-local.test.tsx` (Indiana/Maryland ET + UNLV/Memphis date boundary)

## Ambiguities

- Tooltip still shows UTC (`title={kickoff.utc}`); visible text is visitor-local.
  Dropped the legacy `"${kickoff.utc} UTC"` double suffix while touching call sites.
- `timeZone` prop on row/header components is test-only injection; production omits it.

## Out of scope (respected)

No `w10-ui` hierarchy work. No `src/ncaa_quant/**`. No R2 / publish / export gate.
No DESIGN.md edits.
