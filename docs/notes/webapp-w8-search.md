# W8 — This Week team search

**Date:** 2026-09-02  
**Status:** Complete  
**Authority:** `docs/webapp/DESIGN.md` §4, §5.1; `docs/notes/webapp-w3.md`, `webapp-w6.md`, `webapp-w7.md` (W7-SORTFIX)

---

## Built

| Path | Role |
|------|------|
| `src/lib/this-week/search.ts` | `filterGamesByQuery`, normalization, `parseSearchQuery` |
| `src/components/TeamSearch/` | Single-line search input with clear affordance |
| `src/components/ThisWeekSlate/ThisWeekSlate.tsx` | Filter → sort → group; `?q=` URL sync |
| `src/components/ThisWeekSlate/ThisWeekSlate.module.css` | Controls stack, no-match copy, aria-live region |
| `tests/this-week-search.test.tsx` | Matching rules, null teams, grouping, hydration |

---

## Matching rules

- **Fields:** `away_team` and `home_team` only.
- **Normalization:** trim; case-insensitive; `String.prototype.normalize("NFD")` with combining marks stripped; apostrophe-like characters (straight, curly, okina U+02BB, modifier apostrophe U+02BC, prime, backtick, acute) removed so `Hawaiʻi` / `Hawai'i` / `Hawaii` align; internal whitespace collapsed. Queries are tokenized, then each token passes through the same `normalizeTeamSearchText` function as team names.
- **Tokens:** query split on whitespace; a game matches when **every** token is a substring of either team name (per-token OR across sides). Example: `michigan ohio` matches Ohio State @ Michigan State; `ohio st` matches Ohio State.
- **Empty query:** whitespace-only input returns the input array unchanged (referential stability for memoization).
- **No fuzzy match:** substring only; no Levenshtein or third-party search library.

---

## Null-team handling (W7-SORTFIX)

Live R2 rows can have `null` or empty `away_team` / `home_team`. `normalizeTeamSearchText` returns `""` for nullish values without calling string methods on null. A game with both names missing never matches a non-empty query. Tests clone `g-fix-1` / `g-fix-2`-shaped records from `this-week-sort.test.ts`.

---

## URL state

Mirrors the existing `?order=` convention:

| State | Behavior |
|-------|----------|
| `?q=<query>` on `/` | Page-local via `history.replaceState` in `ThisWeekSlate`. Bare `/` from `SiteHeader` clears query (header links are bare paths). |
| Combined with `?order=` | Order and query writers both use `URL` so the other param is preserved. |

Load hydrates the input and filtered list from `?q=` in a `useEffect` (same pattern as order). No `router.push`, no scroll jump, no refetch.

---

## UI / §4

- Search sits directly above the Order control, full width, non-sticky (sticky bar unchanged for header + controls).
- Hairline `--border-subtle` border, `--bg-primary` fill, 8px radius (matches sort chips).
- B2 placeholder, B1 input text; `--text-secondary` placeholder; `--text-primary` value.
- Focus ring via global `box-shadow: var(--focus-ring)` on `:focus-visible`.
- Clear `×` only when non-empty; `--text-tertiary`.
- No-match: B2 `--text-secondary` — “No games match that team.” plus text clear button. Offseason / pre-first-publish / empty-slate states unchanged (rendered only when `games.length === 0` on the server).

---

## Accessibility

- `<input type="search">` with visually hidden `<label htmlFor="team-search">`.
- `aria-live="polite"` region announces “{n} games match” when the query is non-empty.
- Escape clears when focused; clear control is a `<button aria-label="Clear search">`.

---

## Tests (`tests/this-week-search.test.tsx`)

- Substring match on home and away team
- Case-insensitivity and surrounding whitespace
- Multi-token AND (`michigan ohio`; excluding token)
- Diacritic and apostrophe normalization
- Null/empty `away_team` / `home_team` do not throw
- Empty query returns full slate (same reference)
- Filter composes with kickoff and conviction grouping (no empty group headers; no trailing kickoff-unavailable group when unmatched)
- `?q=` hydration pipeline via `URLSearchParams` + `filterGamesByQuery`
- `TeamSearch` static markup (label, placeholder, clear affordance)

---

## §4.4 Anti-pattern checklist (verbatim)

```
- no default-shadcn aesthetic
- no purple-gradient heroes
- no emoji cards
- no wall-of-widgets
- no gratuitous glassmorphism
- no filler marketing copy
```

---

## Spec ambiguities resolved

1. **Lib file split** — search logic lives in `search.ts` beside `sort.ts` rather than bloating the sort module; both are under `src/lib/this-week/`.
2. **URL encoding** — `URLSearchParams` / `searchParams.set` handle encoding; tests use `Ohio%20State`.
3. **No-match vs empty slate** — search empty state only when the server delivered a non-empty `games[]` and the client filter returns zero rows.
