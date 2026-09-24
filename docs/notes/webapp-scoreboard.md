# Scoreboard density — This Week rows + Game Detail (2026-09-24)

**Scope:** Layout only. No data, schema, or Worker changes.

## What changed

### This Week (`GameRow`)
- Apple Sports–style two-line team stack (away / home), T3 + ellipsis.
- Unsigned `|mu_margin|` and market `|market_home_margin|` sit on the favored
  team's line; win % beside the margin; exact 0 → `PK` centered; null → `—`
  on the home line only.
- Model and market are independent (split favorites land on different lines).
- Shared `ScoreboardGrid` column template for sticky header + rows.
- Two-level headers: MODEL / MARKET over per-column WINS BY · WIN % · TIER.
- Kickoff is time-only; date stays in the day group header.
- Short tier word from `conviction_tier` (Strong / Clear / Lean / Toss-up).
- O/U and interval band removed from the list (Game Detail only).
- HowToReadKey: one dismissible C2 line (no panel).

### Game Detail (`ModelAndMarket`)
- Unsigned margins + favored win % in the comparison table.
- Footnotes behind `<details>About these odds</details>` (closed by default).
- Attribution always visible: `Odds: {provider} · as of {time}`.

## Production promote (2026-09-24)

| Field | Value |
|-------|--------|
| Commit | `6cfdb03` on `main` |
| Rollback target (pre-scoreboard) | `https://the-cfb-model-22g890fa1-alecs-projects-2eeacfd8.vercel.app` |
| Production deployment | `https://the-cfb-model-ghlprnkhm-alecs-projects-2eeacfd8.vercel.app` |
| Production alias | https://the-cfb-model.vercel.app |
| Promote | git push to `main` (GitHub → Vercel); not `--archive` |
| Prod screenshots (dark) | `webapp/site/docs/screenshots/scoreboard/production/this-week-380-dark.png`, `…/this-week-1280-dark.png` |

### Post-deploy verify (2026-09-24)
- Header sub-labels present at 380 (MODEL / MKT / WINS BY) and 1280 (WINS BY · WIN % · TIER)
- How-to line shows, dismisses, stays dismissed on reload
- `?order=conviction` → `data-order="conviction"`
- `/game` Model and market present; About these odds collapsed; attribution outside
- Dark mode: market margins render; column rule present

Rollback: `npx vercel rollback https://the-cfb-model-22g890fa1-alecs-projects-2eeacfd8.vercel.app`
(also recorded in `docs/runbooks/odds_refresh.md`).

## Spec ambiguities / choices
- **Short tier provenance:** derived from `conviction_tier` enum (not by
  parsing `conviction_label`), so the artifact field mapping stays explicit.
- **Carried-forward on rows:** quiet C2 under the market group on desktop;
  hidden on mobile to protect ~72px row height.
- **MarketCell:** unused after GameRow rewrite; left in tree (no Worker/schema
  touch). Can delete in a follow-up cleanup.
- **N1 weight 600:** token N1 is weight 500; model margin overrides to 600
  per the scoreboard brief (still §4 color tokens only).

## Evidence
- Before: `webapp/site/docs/screenshots/scoreboard/before/`
- After / header / align: `…/after/`, `…/header-tweak/`, `…/align-howto/`
- Production dark: `…/production/` (380 + 1280)
