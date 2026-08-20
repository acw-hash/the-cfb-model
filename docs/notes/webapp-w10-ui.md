# W10-UI — Visual hierarchy pass

**Date:** 2026-08-19
**Branch:** `w10-ui` (off `main`; not merged)
**Status:** **W10-D1-NAMES — Candidate A shipped (operator 2026-08-20).**
Stacked away/@ home + third-line kickoff·chip; uniform **77px**. Soft-wrap (B) deleted.
Awaiting §4.4 re-run Mon Aug 24. Not merged.
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

## 1.9 — Operator decision batch (2026-08-19)

**Code freeze:** Fri 2026-08-28 · **Live publish:** Tue 2026-09-01

### Not decisions — executed 2026-08-19

| # | Action | Result |
|---|--------|--------|
| **A1** | Set `NCAA_QUANT_WEBAPP__EXPORT_ENABLED=false` in `.env` | Done. Stops `make test` from writing R2 objects (third incident class). Durable fix: test-guard task (autouse conftest + patched S3). |
| **A2** | Extract 4 pre-W10 PNGs from HEAD before restore | Done → `docs/notes/_artifacts/webapp-w10-ui/pre-w10-baseline/` (411–415 KB each; W3 baseline from `1a944b6`). |
| **A3** | Move W9 in-flight off `w10-ui` | Done → branch `w9-d` @ `95c85e1` (27 files). `w10-ui` restored to `f39efbc`; GameDetail collision with W10-FIX avoided. |

### Decision 1 — Adopt D1/D2, or ship the re-weighted scale?

**Answer: B — D1 only.** Re-weight-only hierarchy (decision A) fails first-read and density
(see Decision 2). Adopt D1 (28/30, 600, −0.02em) for the `/` forecast figure; demote T3 team
names to 15px. Target two-line row ~72–80px. Do **not** adopt D2 (40/42 Game Detail) — vertical
room and section heading already establish rank there. Amends §4.2 only. Build on
`w10-fix-kickoff`; re-capture before Mon Aug 24 slack window.

### Decision 2 — §4.4 visual verdict

Protocol run 2026-08-19 against captures in `docs/notes/_artifacts/webapp-w10-ui/screenshots/`.

1. **First-read test** (`home-mobile-light.png`): eye lands on **team names** (left, B2 15/400)
   before N1 forecast figures (right). **FAIL** — contradicts §1 Primary hierarchy statement.
2. **Density test** (full 56-row slate): uniform **107px** rows × 56 = **5,992px** row body;
   Playwright `slateHeightPx` **6,260** includes date-group headers (~268px overhead). Reads as a
   **long list of tall cards**, not scores-app density (§4.3 benchmark ~60–70px). **FAIL**.
3. **Six anti-pattern items** — all **PASS** (named screenshot each):

| Item | Verdict | Screenshot |
|------|---------|------------|
| no default-shadcn aesthetic | PASS | `home-mobile-light.png` |
| no purple-gradient heroes | PASS | `home-mobile-light.png` |
| no emoji cards | PASS | `home-mobile-light.png` |
| no wall-of-widgets | PASS | `home-mobile-light.png` |
| no gratuitous glassmorphism | PASS | `home-mobile-light.png` |
| no filler marketing copy | PASS | `about-mobile-light.png` |

4. **Null-heavy slate** (`null-heavy-slate-mobile-light.png`): Primary-slot em-dash at N1 weight
   reads **deliberate**, not broken. **PASS**.

**Caveat recorded:** `/results` and `/game/[id]` §4.4 captures used `next dev` (fixtures) —
verdict on those surfaces is a **dev-artifact only**. PROD-500 (Decision 5) has since landed
`force-dynamic` so `next start` serves all four routes 200 post-revalidation; **D1's
re-capture on `next start` retires this caveat.**

**Verdict: CONDITIONAL — anti-patterns and null-absence PASS; first-read and density FAIL.**
Triggers Decision 1 → B. DELIVERABLE 2 (re-weight-only) **does not pass** as worded.

### Decision 3 — §5.2 copy gap: the three σ figures

**Answer: B — §5.2 + `/about`.** Accept proposed copy verbatim on Game Detail; extend W6's
existing conformal / σ-refusal section on `/about`. DESIGN amendment — operator-signed. Does
not defer: visible arithmetic contradiction is unacceptable on this product.

> The range is conformal, not Gaussian: it is calibrated to cover at the stated rate and is
> typically wider than the standard deviation alone would imply. The win probability comes from a
> recalibrated predictive distribution rather than from the displayed σ. These three figures
> describe the same forecast from different calibrations and are not intended to reconcile
> arithmetically.

### Decision 4 — Retroactive authorization (operator-approved 2026-08-19)

**Answer: Authorize.** Covers what already shipped **and** the incoming Decision 1 §4.2
amendment (D1 display scale on `/`), not only the §4.1 / §4.3 edits already on disk.

| Path | Authorization |
|------|---------------|
| `docs/webapp/DESIGN.md` §4.1 use-column + §4.3 game-row diagram | **Authorized** — accurate record of what shipped (ADR 0015 precedent). |
| `docs/webapp/DESIGN.md` §4.2 (incoming D1 amendment) | **Authorized** — Decision 1 → B adopts D1 (28/30/600/−0.02em) for the `/` forecast figure; amends §4.2 only. Build on `w10-fix-kickoff`. |
| `docs/notes/_artifacts/webapp-w10-ui/**` (committed captures, grep gate, metrics) | **Authorized — commit.** Sets auditable-evidence precedent over W6 ephemerality; repo weight accepted. `screenshots/` is the **re-weight-only round**; `pre-w10-baseline/` is the extracted W3 baseline. |
| `docs/notes/webapp-w10-ui.md` (hyphenated vs `webapp-w10ui.md`) | **Authorized** — hyphenated convention canonical; `_artifacts/webapp-w10-ui/` already uses it. |

### Decision 5 — Production 500 on `/results` and `/game/[id]`

**Answer: A — Own it now.** Separate branch off `main`; bisect against W7 to establish whether
predates W10. `src/lib/artifacts/**` is forbidden in W10 branches. Two of four public routes 500
nine days before freeze — **launch blocker**. Cannot wait for W10 clearance.

### Sequence (post-batch)

1. ~~A1–A3~~ (done)
2. Clean tree → branch `w10-fix-kickoff` off `f39efbc`
3. Decision 5 task in parallel off `main`
4. Decision 1 build + re-capture (D1)
5. Decision 3 copy (§5.2 + `/about`)
6. Notes: row-height arithmetic corrected (§5); test-guard task for A1 durable fix
7. W9-D notes: stale `sandbox/latest` → versioned copy at `sandbox/v1/2026/w1/tuesday_primary/`

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

## 5. Evidence — captures recorded; §4 operator review closed (Decision 2)

**Re-weight-only round:** `docs/notes/_artifacts/webapp-w10-ui/screenshots/` (2026-08-19,
`next dev` port 3562, `ARTIFACT_SOURCE=local`, `ARTIFACT_BASE_PATH=webapp/fixtures`). These
are the captures Decision 2 judged; they are **not** the D1 remediation set.

**Pre-W10 baseline:** `docs/notes/_artifacts/webapp-w10-ui/pre-w10-baseline/` (extracted
2026-08-19 from HEAD before restore; W3 @ `1a944b6`).

`/results` and `/game/[id]` captures in the re-weight-only round used `next dev` because
production `next start` then 500'd on those routes (Decision 5 / PROD-500). That launch
blocker is fixed on `main`; D1 re-capture must use `next start` and retires the §4.4
dev-artifact caveat.

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
| Row body height (107 × 56) | **5,992px** |
| Playwright `slateHeightPx` | 6,260px (includes date-group header overhead ≈268px) |
| Pre-W10 baseline | `pre-w10-baseline/` (extracted from HEAD 2026-08-19; W3 @ `1a944b6`) |

**Operator verdict (Decision 2):** multi-line rows trade vertical space for hierarchy but
**fail density** (~2× Apple Sports benchmark). Re-weight-only **fails first-read**; D1 adoption
authorized (Decision 1 → B).

### Re-weight-only hierarchy (390px)

**Self-assess (not operator PASS):** in `home-mobile-light.png`, N1 forecast figures in the
right column read before B2 team names in the left column — a modest delta (17/500 vs 15/400).
Whether that is enough to win first read is an **operator call**; captures are attached for
§2.6 D1/D2 trigger evaluation.

### §4.4 anti-pattern checklist (operator verdict 2026-08-19)

| Item | Verdict | Screenshot |
|------|---------|------------|
| no default-shadcn aesthetic | PASS | `home-mobile-light.png` |
| no purple-gradient heroes | PASS | `home-mobile-light.png` |
| no emoji cards | PASS | `home-mobile-light.png` |
| no wall-of-widgets | PASS | `home-mobile-light.png` |
| no gratuitous glassmorphism | PASS | `home-mobile-light.png` |
| no filler marketing copy | PASS | `about-mobile-light.png` |

**§4 visual review: CLOSED.** Anti-patterns PASS; first-read and density FAIL → Decision 1 B.
Full protocol in §1.9 Decision 2.

---

## 6. W10-D1 — Adopt D1, rebuild the row (2026-08-20)

**Deadline:** code + captures Fri 2026-08-21 · operator §4.4 Mon 2026-08-24 · buffer Wed
2026-08-26 · freeze Fri 2026-08-28. Abort if §4.4 not passed by Wed Aug 26 → merge
re-weight-only.

### §4.2 amendment (authorized Decisions 1 + 4)

Applied exactly as tabled: D1 28/30/600/−0.02em for `/` forecast; T3 reduced to 15/20/600;
tracking column; N1 retained for Game Detail and `/results`. **D2 not adopted.**
`git diff docs/webapp/DESIGN.md` touches §4.2 only.

### Row structure shipped

```
┌──────────────────────────────────────────────────────┐
│ Away @ Home                    (T3 15/600)  −5.0 (D1)│
│ Sat … · Lean Home (C2/C1)            [lo, hi] (N2)   │
└──────────────────────────────────────────────────────┘
```

Left = Secondary + Tertiary; right = Primary stack (scannable rail). Hierarchy: D1 μ wins
first read over T3 names; interval stays Primary (N2 band / D1 `—` when null).

### Density (390px, fixture slate, `next start`)

Source: `docs/notes/_artifacts/webapp-w10-ui/screenshots-d1/row-height-metrics.json`

| Metric | Value |
|--------|-------|
| Row count | 56 |
| Row height (each) | **75px** uniform (target 72–80) |
| Row body (75 × 56) | **4,200px** |
| Group headers (3 × 37) | **111px** stated separately |
| Playwright `slateHeightPx` | 4,311 |

### Capture command

All D1 screenshots: `node tests/capture-w10-d1.mjs` against **`next start`** (port 3101),
`ARTIFACT_SOURCE=local`. **Not `next dev`.** Directory: `screenshots-d1/`. This retires the
prior `/results` / `/game/[id]` next-dev caveat for the D1 round.

### Three-round comparison

| Round | Directory | Row height | First-read |
|-------|-----------|------------|------------|
| Pre-W10 baseline | `pre-w10-baseline/` | (W3 captures) | Team names dominant (T3 17/600) |
| Re-weight-only | `screenshots/` | 107px | Still fails — names before N1 |
| **D1** | `screenshots-d1/` | **75px** | D1 μ intended Primary; **blocked** by long-name STOP |

### STOP — longest real 2026 team-name pair

**Pair:** `Mississippi Valley State @ Sacramento State` (43 chars) from W9-I dry export
2026 week 1 (`docs/notes/_artifacts/webapp-w9i/dry_export/week_predictions.json`).

**Measurement at 390px / T3 15px / nowrap** (`long-name-probe.json`):

| Field | Value |
|-------|-------|
| clientWidth | 234px |
| scrollWidth | 298px |
| wraps | false |
| truncates | **true** (overflow into D1 rail; see `long-name-pair-mobile-light.png`) |

T3 was **not** shrunk below 15px. Options for operator (do not implement here):

1. **Allow matchup wrap** to a second line when `scrollWidth > clientWidth` — breaks uniform
   75px for those rows only; may push overflow rows above 80px.
2. **Ellipsis + `title=`** full name — Secondary text hidden; fails “no truncate.”
3. **Full-bleed matchup** (μ moves under names) — abandons the side-by-side Primary rail.
4. **Accept FCS overflow** until a later formatter/abbrev task — ship D1 for FBS-length names.
5. **Narrow the figure column** (smaller D1 tracking only) — recovers ~10–20px, not 64px; cannot
   clear 298 − 234 alone.

### Absence

`null-interval-row-mobile-light.png` (null-heavy slate, `next start`): Primary-slot `—` at D1
weight visible. `interval-absence.test.tsx` regression guard retained (fails if reason returns
to `title=` only on Game Detail).

### §4.4 checklist re-run (D1 captures — provisional; long-name STOP blocks PASS)

| Item | Verdict | Screenshot |
|------|---------|------------|
| no default-shadcn aesthetic | PASS | `screenshots-d1/home-mobile-light.png` |
| no purple-gradient heroes | PASS | `screenshots-d1/home-mobile-light.png` |
| no emoji cards | PASS | `screenshots-d1/home-mobile-light.png` |
| no wall-of-widgets | PASS | `screenshots-d1/home-mobile-light.png` |
| no gratuitous glassmorphism | PASS | `screenshots-d1/home-mobile-light.png` |
| no filler marketing copy | PASS | `screenshots-d1/about-mobile-light.png` |

**First-read / density:** density **met** (75px). First-read **cannot be claimed PASS** while
long names collide with the D1 rail — operator §4.4 Mon Aug 24 after long-name decision.

### Positive-evidence sentences (W6 format, D1 captures)

1. **The forecast figure reads as the Primary element on the `/` row** — In
   `screenshots-d1/home-mobile-light.png`, D1 28/600 μ sits on a right-aligned rail above the
   N2 interval, larger and heavier than T3 15/600 team names on the left.
2. **The slate reads as scores-app density, not a card stack** — Uniform **75px** rows
   (4,200px body + 111px group headers) replace the re-weight-only 107px / 5,992px body
   (`row-height-metrics.json`).
3. **Honest absence stays visible in the Primary interval slot** — In
   `screenshots-d1/null-interval-row-mobile-light.png`, the em-dash renders at D1 weight under
   μ with no tooltip-only fallback.
4. **Game Detail did not receive a display tier** — In
   `screenshots-d1/game-detail-mobile-light.png`, margin μ remains N1; D2 was not introduced.
5. **Production captures retire the next-dev caveat** — `/results` and `/game/[id]` shots in
   `screenshots-d1/` were taken against `next start` (`CAPTURE.md`), not `next dev`.

### Betting-language grep gate

Re-run command identical to §5; output refreshed to
`docs/notes/_artifacts/webapp-w10-ui/betting-grep-gate.txt` if dirty. Verdict expected PASS
(no recommendation framing added by D1).

### Density confirmation (clears one §4.4 failure)

Fixture slate body **4,200px** (75 × 56) vs re-weight-only **5,992px** (107 × 56) is a
**~30% reduction**. That likely clears the §4.4 **density** FAIL; **first-read** remains open
until the operator picks a long-name candidate and re-runs §4.4 Mon Aug 24.

### Sanctioned-edits path correction (operator-authorized)

`src/lib/tokens.ts` **does not exist**. Live type scale for the §4.2 amendment is
`webapp/site/src/styles/tokens.css` + `webapp/site/scripts/check-tokens.mjs`. Both were updated
for D1/T3/tracking. W10-FIX and D3 inherit these paths — not `lib/tokens.ts`.

---

## 7. W10-D1-NAMES — long-name candidates (2026-08-20)

**Switch:** URL query on `/` (client slate) — `?matchup=soft-wrap|stacked` and
`?stackedMeta=third-line|time-gutter|chip-under-interval`. Default in tree for guards:
`soft-wrap`. Losing candidate is delete-cheap (prop + CSS).

### Phase 1 — measure (`long-name-probe-2.json`)

2026 W1 dry export, 390px, T3 15px, matchup box **234px**. Slate has **99** rows (not 56).

**Overflow count: 11 / 99**

| Overflowing pair | pair scrollWidth |
|------------------|------------------|
| Mississippi Valley State @ Sacramento State | 298 |
| Charleston Southern @ Georgia Southern | 282 |
| Jacksonville State @ North Dakota State | 273 |
| Southeast Missouri State @ Iowa State | 262 |
| Sacramento State @ Eastern Michigan | 259 |
| Eastern Kentucky @ Jacksonville State | 257 |
| Northwestern State @ Louisiana Tech | 253 |
| Florida International @ South Florida | 252 |
| North Carolina A&T @ Georgia State | 248 |
| South Dakota State @ Northwestern | 247 |
| San José State @ Eastern Michigan | 236 |

**Top five + stacked single-name widths (against 234px):**

| Pair | pair SW | away SW | home SW | either name >234? |
|------|---------|---------|---------|-------------------|
| Mississippi Valley State @ Sacramento State | 298 | 156 | 119 | no |
| Charleston Southern @ Georgia Southern | 282 | 139 | 120 | no |
| Jacksonville State @ North Dakota State | 273 | 118 | 132 | no |
| Southeast Missouri State @ Iowa State | 262 | 168 | 71 | no |
| Sacramento State @ Eastern Michigan | 259 | 119 | 117 | no |

**Candidate A viable:** yes — longest single name is Southeast Missouri State at **168px**.

### Phase 2 — Candidate A meta placements (measured)

| Placement | Uniform height | ≤80? |
|-----------|----------------|------|
| third-line (kickoff·chip C2) | **77px** | yes |
| time-gutter (~55px left) | **77px** | yes (names still OK: 168 < ~179) |
| chip-under-interval | **103px** | **no** |

**Chose third-line** for A captures: tied shortest with time-gutter; keeps full matchup width
(no 55px gutter tax). Stacked rows use 8px vertical pad (soft-wrap keeps 12px).

### Phase 3 — operator comparison (`next start`, 390 light)

| | Candidate A (stacked) | Candidate B (soft-wrap) |
|---|---|---|
| Uniform / mode row height | **77px** uniform | **75px** mode |
| Rows deviating from mode | **0** | **10** at 85px |
| Slate body (rows × height) | 77 × 99 = **7,623px**; headers **222px** separate | 7,525px body (mixed); headers **222px** |
| Longest pair renders fully? | **yes** | **yes** |
| Chip/kickoff placement | third-line C2 under stacked names | line-2 under matchup (D1 default) |

Captures: `candidate-a-long-name-row-mobile-light.png`, `candidate-a-slate-mobile-light.png`,
`candidate-a-null-interval-row-mobile-light.png`, and `candidate-b-*` equivalents.
Metrics: `candidate-compare.json`. Phase 1 probe: `long-name-probe-2.json`.

**Evidence retained (same role as re-weight-only `screenshots/`):** `candidate-b-*.png` and
`long-name-probe-2.json` stay in `screenshots-d1/` as the audit trail for **why A was chosen** —
not live UI. Soft-wrap code is gone from `webapp/site/src`; only these artifacts remain.

**Cost — A:** Always three content bands (two name lines + meta); +2px vs D1 two-line fixture
row; every row pays for the worst-case name.

**Cost — B:** Keeps two-line density for 89/99 rows; 10 overflow rows jump to 85px; wrap break
can fall after `@`, so visual parse of away/home is weaker than explicit stack.

**STOP for operator:** pick A or B from captures before Mon §4.4. Do not treat either as shipped
final until chosen.

### Decision — Candidate A (operator 2026-08-20)

**Shipped:** stacked matchup + third-line kickoff·chip. Soft-wrap (B), time-gutter, and
chip-under-interval comparison switches **deleted**. Row is no longer URL-switchable.

Uniform **77px** at 390; Primary rail unchanged; long names render fully
(`candidate-a-long-name-row-mobile-light.png`).

---

*End of W10-UI notes.*
