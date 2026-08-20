# W10-UI — Visual hierarchy pass

**Date:** 2026-08-19
**Branch:** `w10-ui` (off `main`; not merged)
**Status:** **COMPLETE** (2026-08-19). Operator decisions recorded; implementation shipped on `w10-ui`.**Authority:** `docs/webapp/DESIGN.md` §1.8, §4, §5, §6; `docs/notes/webapp-w2.md`,
`w3.md`, `w6.md`, `w7.md`.

**Commit order note:** this document is written **before** any change under
`webapp/site/src/**`. Hierarchy first so every subsequent change can be argued
against it.

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

1. **Team names must get smaller or lighter.** They are the largest, heaviest element in the
   row today (T3 17/22/600 vs N1 17/22/500). Confirmed in `tokens.css` and `GameRow.module.css`.
2. **Honest absence is Primary, not Tertiary.** When `margin_interval_*` is null, the "—" sits
   in the Primary slot at Primary weight. It does not shrink into a footnote and the row does
   not reflow around the gap. **Current code does not do this on `/`:** `IntervalBand` omits
   the band entirely when bounds are null and leaves μ at N1 with no "—" slot (see V1).

### Per-surface application

| Surface | Primary | Secondary | Tertiary |
|---------|---------|-----------|----------|
| `/` game row | `mu_margin` + interval | teams, kickoff | tier chip, revised dot, per-game stale, date-group header |
| `/game/[id]` | margin block (`mu_margin`, `sigma_margin`, interval) | matchup header, win probability | total block, cover/over ("model reference only"), provenance strip, publish line, trajectories |
| `/results` Tab A | forecast vs `actual_margin`, `margin_interval_hit` | matchup, actual score | tier at publish, `graded_from` note |
| `/results` Tab B | verdict label + its §1.4 paragraph | metric values with CI bounds | `regime`, `vintage`, `run`, `n` |
| `/about` | body prose at readable measure | section headings | attribution, provenance |

Note the deliberate inversion on `/results` Tab B: the **verdict paragraph is Primary**, above
the metrics table. After V6, "the §1.4 paragraph" means `track_record.verdict.plain_language`,
not the webapp-authored lay summary.

---

## 2. STOP AND REPORT — operator decisions (closed 2026-08-19)

### 2.1 — STOP #2 (a). §4.2 cannot express the hierarchy. Amendment required.

**Decision: A — re-weight only. No §4.2 amendment.**

Team names in the `/` game row demote T3 (17/22/600) → B2 (15/20/400). Forecast figure
stays N1 (17/22/500). Hierarchy by demotion, per the §1 rule.
Confirmed: `tokens.css` matches DESIGN §4.2 exactly. T3 (team names) is 17/22/**600**;
N1 (headline margin) is 17/22/**500**. The flat row is §4 rendered correctly.

**Proposed amendment** (not written into DESIGN.md):

| Scale | Size / line | Weight | Tracking | Use |
|-------|-------------|--------|----------|-----|
| **D1** | **28px / 30px** | 600 | **−0.02em** | Forecast figure, `/` game row |
| **D2** | **40px / 42px** | 600 | **−0.025em** | Forecast figure, `/game/[id]` margin block |
| T1 | 28px / 32px | 600 | −0.01em | Page title |
| T3 | **15px / 20px** | 600 | 0 | Team names (game row) — *reduced from 17px* |
| N2 | 15px / 20px | 400 | 0 | Interval bounds, probabilities |

**Cheaper alternatives:**

- **(A)** Amend nothing; keep N1 for the number, drop team names to B2 (15/20/400). Hierarchy
  without a display figure. Fails DELIVERABLE 2 as worded.
- **(B)** Amend §4.2 for D1 only, not D2. Game Detail already has vertical room.

### 2.2 — STOP #2 (b). Rendered `IntervalBand` is forbidden by §4.3.

**Decision: C — typographic. No §4.3 amendment.**

Keep `IntervalBand` text-only: μ at N1; bounds at N2 `--text-secondary`; brackets
`--text-tertiary`; interval on its own line beneath the number. No hairline, marker, rule,
fill, axis, or grid.
DESIGN §4.3, verbatim: **Interval band** — text-only `μ [lo, hi]`; no error-bar graphics.

V1: `GameRow` **does** render `IntervalBand`. That component is the text-only band
(`μ` as N1 + `[lo, hi]` as N2). Bracketed text is the spec, not unused code.

A thin rule with a marker at μ **is** an error bar. Direct contradiction.

**Proposed amendment to §4.3** (not written): hairline rule, marker at μ, bounds in N2,
max 120px, no fill/axis/grid; null → "—" per §1.8, row height unchanged.

**Recommended alternative (C):** keep §4.3 unamended; `μ` at D1 (if 2.1 allows), bounds at
N2 `--text-secondary`, brackets `--text-tertiary`, interval on its own line under the number.

### 2.3 — STOP #2 (c). §4.1 use-column, date-group headers.

**Decision: yes — §4.1 use-column edit.**

`--bg-secondary` Use column updated to `Table zebra; disclaimer block (§6.1 per W6)`.
Token values unchanged. `SlateGroupHeader` retires the filled-slab group-header use.
`SlateGroupHeader.module.css` uses `background: var(--bg-secondary)`. DELIVERABLE 3 would
replace filled slabs with hairline + space. Token **values** unchanged; use column would
become "Table zebra; disclaimer block (§6.1 per W6)". Lowest risk of the three.

### 2.4 — STOP #1. First-visit disclaimer.

**Decision: Option 2 — full block on first page of a session only.**

Add `ridge-disclaimer-seen` written on mount (alongside existing `ridge-disclaimer-dismissed`
on dismiss). Full §6.1 block on the first page of a session; thereafter footer + `/about`.
§6.1 / §6.2 strings byte-identical.
**Option 0 (done):** not a broken `sessionStorage` check.

- `FirstVisitDisclaimer` reads/writes `ridge-disclaimer-dismissed` in `sessionStorage`.
- Mounted in `app/layout.tsx` under the header on **every route** until dismissed.
- After dismiss, `visible` is false and the key is set; reload in the same tab stays dismissed.
- New tab / new session shows it again (per-tab `sessionStorage`).
- If the reader never taps "Dismiss for this session", the block occupies the first viewport
  on every page **by design**, not by a bug.

Type note vs W6: W6 recorded the banner as B1. The **About** `#disclaimer` block is B1
(`AboutPage.module.css` `.disclaimerBlock`). The **first-visit banner body is B2**
(`FirstVisitDisclaimer.module.css` `.body`). Strings remain `disclaimerForYear` →
`DISCLAIMER_TEMPLATE` (§6.1).

| Option | Change |
|--------|--------|
| **1. Unchanged** | Keep site-wide until dismiss |
| **2. First page of a session only** | Full block once; thereafter footer + `/about` |
| **3. Compact bar** | Not recommended (reverses W6) |

**Recommendation: Option 2** if the viewport complaint survives Option 0 (it does: the
banner is large until dismiss, on every route). §6.1 / §6.2 strings stay byte-identical.

### 2.5 — STOP #4. `/results` paragraphs — invert DELIVERABLE 4.

**Decision: invert DELIVERABLE 4.**

`verdict.plain_language` (§1.4 / §5.3 verbatim) at primary weight (B1 `--text-primary`).
`VERDICT_LAY_SUMMARY` collapsed in a disclosure — verbatim, in the DOM, one tap. Verdict
label `NOT CURRENTLY FIT TO BET` stays exact.
`VerdictBlock.tsx`:

1. **Lay summary (primary weight, B1 `--text-primary`):** `VERDICT_LAY_SUMMARY` in
   `lib/results/copy.ts` — webapp-authored paraphrase of the 23-reval finding.
2. **Recorded finding (secondary, B2 `--text-secondary`):** `verdict.plain_language` from
   `track_record.json` — the §1.4 / §5.3 verbatim paragraph.

Collapsing "Recorded finding" would hide the paragraph §5.3 requires displayed and leave
the paraphrase at primary weight. Forbidden by the task and by W5 (unrounded, unsoftened).

**Correct move if hierarchy on Tab B proceeds:** keep `verdict.plain_language` at primary
weight; collapse the *lay* summary (verbatim in the disclosure, in the DOM, one tap).

---

## 2.6 — DELIVERABLE 2 (amended for decision A)

Original DELIVERABLE 2 called for a display figure (D1/D2) on the game row. Operator
declined the §4.2 type-scale amendment, so DELIVERABLE 2 is reworded:

> Establish **relative** visual hierarchy within the existing §4.2 scale: the forecast
> figure reads before team names on `/` without enlarging N1 or adding display scales.
> Interval band stays text-only per §4.3 (decision C): μ and bounds stacked typographically,
> not graphically.

**Deferred:** D1 (28/30/600) and D2 (40/42/600) display scales pending captured before/after
evidence at 390px. Revisit only if re-weight-only hierarchy fails visual review.

---

## 3. Scope clarification — null-heavy slate

DELIVERABLE 8 fixture slate with ~20% null `conviction_tier` / `margin_interval_*` lives
under `webapp/site/tests/**` (sanctioned), labeled `"fixture": true`. No writes to
`webapp/fixtures/`. Sort coverage extends `tests/this-week-sort.test.ts` (W7-SORTFIX
null-last). Not implemented this pass.

---

## 4. Verification (repo present)

| # | Item | Result |
|---|------|--------|
| **V1** | Which component renders `/` interval | **`IntervalBand`**, used by `GameRow` line 60. Text-only N1 μ + N2 `[lo, hi]`. When bounds are null, the range `Figure` is omitted (μ only). Game Detail `ForecastBlock` shows "Interval not computed" in a separate `<p>`, not a Primary "—". |
| **V2** | `/about` body color | **`--text-primary`** (`.body` in `AboutPage.module.css`). Not `--semantic-stale`. Warm rust/amber is not leaking into About body. |
| **V3** | `tabular-nums lining-nums` | **`Figure`** always applies it (`typography.module.css` `.figure`). Formatted numbers on `/`, Game Detail, Results metrics go through `Figure`. Team names are T3 headings, not figures. |
| **V4** | §6.1 / §6.2 string bytes | **Not mutated this task** (no copy edits). Source: `DISCLAIMER_TEMPLATE`, `RESPONSIBLE_GAMBLING_COPY` in `lib/about/copy.ts`. |
| **V5** | `sessionStorage` dismissal | **Works as specified** (Option 0 clean). Site-wide until dismiss. See §2.4. |
| **V6** | Which `/results` paragraph is §1.4 | **Recorded finding = `verdict.plain_language`.** Lay paragraph is `VERDICT_LAY_SUMMARY`. STOP #4, second case. |

---

## 5. Evidence

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
| 2.1 (A) | Game row team names T3 → B2; N1 forecast unchanged |
| 2.2 (C) | `IntervalBand` stacked typographic band; brackets tertiary, bounds secondary |
| §1.8 | Null interval renders `—` at Primary weight; fixed `min-height` on interval line |
| D3 | `SlateGroupHeader` hairline + space (no filled slab) |
| 2.4 | `ridge-disclaimer-seen` on mount; banner first page of session only |
| 2.5 | `verdict.plain_language` primary; lay summary in `<details>` |
| D8 | `tests/fixtures/week_predictions_null_heavy.json` (11/56 null rows) |

### Required screenshot matrix (pending)

Screenshots not captured in this session — harness ready at
`tests/capture-this-week-screenshots.mjs` et al.

| Surface | 390 light | 390 dark | Desktop light | Desktop dark |
|---------|-----------|----------|---------------|--------------|
| `/` | PENDING | PENDING | PENDING | PENDING |
| `/game/<id>` | PENDING | PENDING | PENDING | PENDING |
| `/results` (Tab A) | PENDING | PENDING | PENDING | PENDING |
| `/results` (Tab B) | PENDING | PENDING | PENDING | PENDING |
| `/about` | PENDING | PENDING | PENDING | PENDING |
| Zero-banner state | PENDING | — | PENDING | — |
| Null-heavy slate | PENDING | — | PENDING | — |
| `/?order=conviction` with nulls | PENDING | — | PENDING | — |

Betting-language grep gate: PENDING (manual). `git diff --stat` vs SANCTIONED EDITS: green.
---

*End of W10-UI notes.*
