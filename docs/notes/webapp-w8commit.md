# W8-COMMIT — deferred W8-A / W8-D commit, clean checkout, production

**Date:** 2026-08-17  
**Status:** Complete (pushed)  
**Authority:** W8-COMMIT operator task; Amendment 1 to W8-C Phase 4 correction

Verify "committed" against `git log` for a representative path, not against a
notes file. W8-A and W8-D notes said complete while the work was uncommitted.

| SHA | Message | Representative path |
|-----|---------|---------------------|
| `4683bb3` | W8-B About attribution | `webapp/site/src/app/about/page.tsx` |
| `e20cad5` | W8-A payload projection | `webapp/site/src/lib/this-week/project.ts` |
| `4a0dd4a` | W8-D axe / token guard | `webapp/site/scripts/check-tokens.mjs` |
| `913312c` | W8-C withdraw cover/over | `src/ncaa_quant/webapp/export.py` |

Until 2026-08-17 production served `73011b2` (2024 fixtures, full GamePrediction
RSC, no This Week projection). `e20cad5` / `4a0dd4a` were committed late at this
task, then pushed, then `913312c` on a second push so Phase 4 could still see
Cover on `/game`.

## Phase 0.1 (recorded; closed by W8-C, not by W8-A)

`p_cover_home` / `p_over` are **market-referenced** (CFBD close via
`_lookup_closes` + MC + optional PIT). Relabel vs withdraw: ADR 0015, withdraw.
A name-based field diff cannot see an algebraic leak through published
`sigma_margin`. That limitation remains.

## Phase 4 (Amendment 1)

Field-name loop against `/` only is **zero for all five names** after W8-A.
That is **not** closure of the leak.

On production at `4a0dd4a` (before W8-C):

```
# /
conviction_basis: 0
p_cover_home: 0
p_over: 0
p_win_home: 0
home_team_id: 0
mu_margin: 56

# /game/401628373
Cover (model ref): 2
```

On production at `913312c` (W8-C): `Cover (model ref): 0`; `/results` ATS: 12.
See `docs/notes/webapp-w8c.md`.

TS2540 on `tests/gallery-gate.test.ts` assigning `process.env.NODE_ENV` is
**W9-1**. Not touched.
