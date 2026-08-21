# W10-UI — Visual hierarchy pass

**Date:** 2026-08-19
**Branch:** `w10-ui` (off `main`; not merged)
**Status:** **IMPLEMENTED — ACCEPTANCE INCOMPLETE.** Operator decisions closed and code
shipped on `w10-ui` (2026-08-19). Outstanding before merge: §4 visual review with §4.4
anti-pattern list (operator verdict). Not merged.
**Authority:** `docs/webapp/DESIGN.md` §1.8, §4, §5, §6; `docs/notes/webapp-w2.md`,
`w3.md`, `w6.md`, `w7.md`; TASKS.md cross-cutting acceptance (all UI tasks).

**Commit order note (§1 only):** the hierarchy statement in §1 predates implementation;
every subsequent UI change is argued against it. §2 decision blocks and §5 evidence were
added after code shipped.

---

## 1. Hierarchy statement

Three tiers. This is the whole argument; everything below is consequence.

| Tier | What is in it | Why | Artifact fields (§5) |
|------|---------------|-----|----------------------|
| **Primary** | The forecast figure `μ` and its interval `[lo, hi]` | The forecast with its uncertainty **is** the product. A reader who sees only one thing per row must see this. The interval is primary *with* the number, not attached to it — a point estimate shown alone is the thing this project refuses to be. | `mu_margin`, `margin_interval_lo`, `margin_interval_hi` |
| **Secondary** | The matchup — team names, kickoff | Identifies *which* forecast. Necessary to read the row, but it is the label on the number, not the content. | `away_team`, `home_team`, `neutral_site`, `kickoff_utc` |
| **Tertiary** | Provenance, tier chip, revised/stale badges, group headers, nav, footer | Qualifies and locates the forecast. Must remain legible and reachable — none of it may be removed — but none of it competes for first read. | `conviction_label`, `tier_revised_since_primary`, `stale_stamp`, `vintage_label`, `ensemble_scope_label`, `feature_time_label`, `published_at`, `graded_from` |

### The rule

> A change passes W10-UI only if it moves a Primary element up relative to its row, or a
> Tertiary element down, without removing anything. If a change makes something bigger without
> making something else smaller, it is not hierarchy — it is emphasis, and emphasis applied
> everywhere is the flat slate we already have.

### Two consequences stated up front

1. **Team names must get smaller or lighter.** They were the largest, heaviest element in the
   row pre-W10-UI (T3 17/22/600 vs N1 17/22/500). Shipped: T3 → B2 (15/20/400).
2. **Honest absence is Primary, not Tertiary.** When `margin_interval_*` is null, the "—" sits
   in the Primary slot at Primary weight. **Was true pre-W10-UI; fixed 2026-08-19 — see §5.**

### Per-surface application

| Surface | Primary | Secondary | Tertiary |
|---------|---------|-----------|----------|
| `/` game row | `mu_margin` + interval | teams, kickoff | tier chip, revised dot, per-game stale, date-group header |
| `/game/[id]` | margin block (`mu_margin`, `sigma_margin`, interval) | matchup header, win probability | total block, cover/over ("model reference only"), provenance strip, publish line, trajectories |
| `/results` Tab A | forecast vs `actual_margin`, `margin_interval_hit` | matchup, actual score | tier at publish, `graded_from` note |
| `/results` Tab B | verdict label + its §1.4 paragraph | metric values with CI bounds | `regime`, `vintage`, `run`, `n` |
| `/about` | body prose at readable measure | section headings | attribution, provenance |

Note the deliberate inversion on `/results` Tab B: the **verdict paragraph is Primary**, above
the metrics table. "The §1.4 paragraph" means `track_record.verdict.plain_language`, not the
webapp-authored lay summary.

---

## 2. STOP AND REPORT — operator decisions (closed 2026-08-19)

### 2.1 — STOP #2 (a). §4.2 cannot express the hierarchy. Amendment required.

**Decision: A — re-weight only. No §4.2 amendment.**

Team names in the `/` game row demote T3 (17/22/600) → B2 (15/20/400). Forecast figure
stays N1 (17/22/500). Hierarchy by demotion, per the §1 rule.

<details><summary>NOT ADOPTED — proposed §4.2 D1/D2 display-scale amendment</summary>

| Scale | Size / line | Weight | Tracking | Use |
|-------|-------------|--------|----------|-----|
| **D1** | **28px / 30px** | 600 | **−0.02em** | Forecast figure, `/` game row |
| **D2** | **40px / 42px** | 600 | **−0.025em** | Forecast figure, `/game/[id]` margin block |
| T1 | 28px / 32px | 600 | −0.01em | Page title |
| T3 | **15px / 20px** | 600 | 0 | Team names (game row) — *reduced from 17px* |
| N2 | 15px / 20px | 400 | 0 | Interval bounds, probabilities |

Cheaper alternatives recorded for the operator: **(A)** re-weight only (chosen); **(B)** D1 only.

</details>

### 2.2 — STOP #2 (b). Rendered `IntervalBand` is forbidden by §4.3.

**Decision: C — typographic. No §4.3 amendment to the interval-band bullet.**

Keep `IntervalBand` text-only: μ at N1; bounds at N2 `--text-secondary`; brackets
`--text-tertiary`; interval on its own line beneath the number. No hairline, marker, rule,
fill, axis, or grid.

**Diagram follow-up (2026-08-19):** decision C specified "interval on its own line," which the
pre-W10 single-row §4.3 diagram could not express. The game-row pattern diagram in
`DESIGN.md` §4.3 was amended post-ship to describe the multi-line layout (μ line, interval
line, `.meta` row). The `**Interval band** — text-only \`μ [lo, hi]\`; no error-bar
graphics, no shaded chart junk` bullet remains byte-identical. Precedent: ADR 0015 (code wins;
spec amended to describe implementation).

<details><summary>NOT ADOPTED — proposed §4.3 hairline-rule amendment</summary>

Hairline rule spanning `[lo, hi]` with marker at μ, bounds in N2, max 120px, no fill/axis/grid;
null → "—" per §1.8.

</details>

### 2.3 — STOP #2 (c). §4.1 use-column, date-group headers.

**Decision: yes — §4.1 use-column edit.**

`--bg-secondary` Use column updated to `Table zebra; disclaimer block (§6.1 per W6)`.
Token values unchanged. `SlateGroupHeader` retires the filled-slab group-header use (hairline
+ whitespace shipped).

### 2.4 — STOP #1. First-visit disclaimer.

**Decision: Option 2 — full block on first page of a session only.**

Add `ridge-disclaimer-seen` written on mount (alongside existing `ridge-disclaimer-dismissed`
on dismiss). Full §6.1 block on the first page of a session; thereafter footer + `/about`.
§6.1 / §6.2 strings byte-identical.

Option 0 (pre-implementation): `sessionStorage` dismissal worked; the viewport complaint was
site-wide repetition until dismiss, not a broken check. Option 2 addresses that without
shrinking the legal surface.

### 2.5 — STOP #4. `/results` paragraphs — invert DELIVERABLE 4.

**Decision: invert DELIVERABLE 4.**

`verdict.plain_language` (§1.4 / §5.3 verbatim) at primary weight (B1 `--text-primary`).
`VERDICT_LAY_SUMMARY` collapsed in a disclosure — verbatim, in the DOM, one tap. Verdict
label `NOT CURRENTLY FIT TO BET` stays exact.

---

## 2.6 — DELIVERABLE 2 (amended for decision A)

Original DELIVERABLE 2 called for a display figure (D1/D2) on the game row. Operator
declined the §4.2 type-scale amendment, so DELIVERABLE 2 is reworded:

> Establish **relative** visual hierarchy within the existing §4.2 scale: the forecast
> figure reads before team names on `/` without enlarging N1 or adding display scales.
> Interval band stays text-only per §4.3 (decision C): μ and bounds stacked typographically,
> not graphically.

**Deferred:** D1 (28/30/600) and D2 (40/42/600) display scales pending operator visual review
with captures (§5). Revisit if re-weight-only hierarchy fails at 390px.

---

## 3. Scope clarification — null-heavy slate

DELIVERABLE 8 fixture slate with ~20% null `conviction_tier` / `margin_interval_*` lives
under `webapp/site/tests/fixtures/week_predictions_null_heavy.json`, labeled `"fixture": true`.
No writes to `webapp/fixtures/`. Sort coverage in `tests/this-week-sort.test.ts` (W7-SORTFIX
null-last).

---

## 4. Verification — V1–V6 recorded 2026-08-19 pre-implementation. V1 and V5 describe state since changed — see §5.

| # | Item | Result (2026-08-19 pre-ship) |
|---|------|------------------------------|
| **V1** | Which component renders `/` interval | **`IntervalBand`**, used by `GameRow`. Text-only N1 μ + N2 `[lo, hi]`. When bounds were null, the range `Figure` was omitted (μ only). Game Detail `ForecastBlock` showed "Interval not computed" in a separate `<p>`, not a Primary "—". |
| **V2** | `/about` body color | **`--text-primary`** (`.body` in `AboutPage.module.css`). Not `--semantic-stale`. |
| **V3** | `tabular-nums lining-nums` | **`Figure`** always applies it. Formatted numbers go through `Figure`. |
| **V4** | §6.1 / §6.2 string bytes | **Not mutated.** Source: `DISCLAIMER_TEMPLATE`, `RESPONSIBLE_GAMBLING_COPY`. |
| **V5** | `sessionStorage` dismissal | **Worked as specified (Option 0 clean).** Site-wide until dismiss. Option 2 shipped afterward. |
| **V6** | Which `/results` paragraph is §1.4 | **Recorded finding = `verdict.plain_language`.** Lay paragraph is `VERDICT_LAY_SUMMARY`. Inverted in ship. |

---

## 5. Evidence — captures recorded; §4 operator review pending

Captures: `docs/notes/_artifacts/webapp-w10-ui/screenshots/` (2026-08-19, `next dev` port
3562, `ARTIFACT_SOURCE=local`, `ARTIFACT_BASE_PATH=webapp/fixtures`). Production
`next start` returned 500 on `/results` and `/game/[id]` with `.env.local` R2 config; dev +
fixtures used instead.

### Betting-language grep gate (cross-cutting rule 4)

Command (POSIX; Windows run used `rg` with identical patterns):

```
cd webapp/site && grep -rniE \
  '\b(bet|pick|edge|wager|value|play|lock|fade|hammer|units?|parlay|kelly|clv)\b' \
  src/ | grep -vE '(does not|not currently|no edge|responsible|gambler|1-800)'
```

Full output: `docs/notes/_artifacts/webapp-w10-ui/betting-grep-gate.txt`

**Verdict: PASS.** No unexpected recommendation framing in user-facing copy.

| Hit class | Examples | Disposition |
|-----------|----------|-------------|
| TypeScript identifiers | `Pick<`, `value`, `unit`, `.value` CSS | Code false positives — not copy |
| `/about` negation copy | "not a pick", "No picks", "no implied edge claims" | Sanctioned §6 / honesty commitments |
| Lay summary (disclosure) | `VERDICT_LAY_SUMMARY`: "No betting edge…", "CLV…" | Collapsed disclosure; faithful 23-reval paraphrase, not recommendation framing |
| Verdict paragraph | `verdict.plain_language` (artifact) | Sanctioned §1.4 / §5.3 verbatim |

### Acceptance commands

```
$ cd webapp/site
$ npm run typecheck     # PASS
$ npm run test          # PASS (138 tests)
$ npm run lint          # PASS
$ npm run build         # PASS
```

Token diff-check and contrast ratios: PASS (via `npm run test` → `check-tokens.mjs`).

### Implementation summary

| Step | Change |
|------|--------|
| §4.1 | `--bg-secondary` use column → table zebra + disclaimer block |
| §4.3 diagram | Game-row pattern updated to multi-line layout (interval-band bullet unchanged) |
| 2.1 (A) | Game row team names T3 → B2; N1 forecast unchanged |
| 2.2 (C) | `IntervalBand` stacked typographic band; brackets tertiary, bounds secondary |
| §1.8 `/` | Null interval renders `—` at Primary weight; fixed `min-height` on interval line |
| §1.8 Game Detail | `ForecastBlock`: `—` at Primary weight + rendered "Interval not computed — {reason}" (not `title=` only) |
| D3 | `SlateGroupHeader` hairline + space (no filled slab) |
| 2.4 | `ridge-disclaimer-seen` on mount; banner first page of session only |
| 2.5 | `verdict.plain_language` primary; lay summary in `<details>` |
| D8 | `tests/fixtures/week_predictions_null_heavy.json` (11/56 null rows) + sort tests |

### Screenshot matrix

| Surface | 390 light | 390 dark | Desktop light | Desktop dark |
|---------|-----------|----------|---------------|--------------|
| `/` | PASS `home-mobile-light.png` | PASS `home-mobile-dark.png` | PASS `home-desktop-light.png` | PASS `home-desktop-dark.png` |
| `/game/<id>` | PASS `game-detail-mobile-light.png` (gallery states) | PASS `game-detail-mobile-dark.png` | PASS `game-detail-desktop-light.png` | PASS `game-detail-desktop-dark.png` |
| `/results` Tab A | PASS `results-tab-a-mobile-light.png` | PASS `results-tab-a-mobile-dark.png` | PASS `results-tab-a-desktop-light.png` | PASS `results-tab-a-desktop-dark.png` |
| `/results` Tab B | PASS `results-tab-b-mobile-light.png` | PASS `results-tab-b-mobile-dark.png` | PASS `results-tab-b-desktop-light.png` | PASS `results-tab-b-desktop-dark.png` |
| `/about` | PASS `about-mobile-light.png` | PASS `about-mobile-dark.png` | PASS `about-desktop-light.png` | PASS `about-desktop-dark.png` |
| Zero-banner state | PASS `zero-banner-mobile-light.png` | — | PASS `zero-banner-desktop-light.png` | — |
| Null-heavy slate | PASS `null-heavy-slate-mobile-light.png` | — | PASS `null-heavy-slate-desktop-light.png` (empty-top gallery proxy) | — |
| `/?order=conviction` with nulls | PASS `conviction-sort-mobile-light.png` | — | — | — |

### Row height / density (390px, post-W10 fixture slate)

Measured on `/` with Playwright (`row-height-metrics.json`):

| Metric | Value |
|--------|-------|
| Row count | 56 |
| Row height (each) | 107px (uniform) |
| Total slate height | 6260px |
| Pre-W10 baseline | **Not captured in this pass** |

**Self-assess (not operator PASS):** multi-line rows trade vertical space for hierarchy. The
slate still scans as a single column list; 107px/row × 56 is long but not broken. Density
vs pre-W10 cannot be quantified without a before capture.

### Re-weight-only hierarchy (390px)

**Self-assess (not operator PASS):** in `home-mobile-light.png`, N1 forecast figures in the
right column read before B2 team names in the left column — a modest delta (17/500 vs 15/400).
Whether that is enough to win first read is an **operator call**; captures are attached for
§2.6 D1/D2 trigger evaluation.

### §4.4 anti-pattern checklist (agent self-assess — operator verdict pending)

| Item | Self-assess | Screenshot |
|------|-------------|------------|
| no default-shadcn aesthetic | PASS | `home-mobile-light.png` |
| no purple-gradient heroes | PASS | all captures |
| no emoji cards | PASS | all captures |
| no wall-of-widgets | PASS | `home-mobile-light.png` |
| no gratuitous glassmorphism | PASS | all captures |
| no filler marketing copy | PASS | `about-mobile-light.png` |

**§4 visual review: PENDING operator verdict.** Agent self-assess is not acceptance.

---

*End of W10-UI notes.*
