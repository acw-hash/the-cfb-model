# W8-B — About attribution line

**Date:** 2026-08-17  
**Status:** Locally complete; production closure is operator-run after Vercel deploys this commit  
**Authority:** `docs/webapp/DESIGN.md` §5.4, §6.3; `docs/notes/webapp-w6.md` (W6-4); `docs/notes/webapp-w8a.md` (L1/L2)  
**Closes:** W6-4 placeholder

String swap plus the Attribution heading. No layout, route, grep-gate, `noindex`, analytics, R2, or domain change.

---

## Approved copy (operator decision; verbatim)

```
Ridge is an independent research project. It is not affiliated with any school or conference.
```

Rendered in the Attribution section (`data-testid="about-attribution"`). CFBD attribution wording is unchanged.

**Heading amendment (pre-push).** The section heading is `ATTRIBUTION_HEADING = "Attribution"` in `src/lib/about/copy.ts`. It replaced W6's `"Attribution and contact"` because the operator decision is no contact method and the prior heading survived from the placeholder era. No other heading, sentence, or ordering changed.

### AboutPage.tsx — post-hoc (outside the originally sanctioned set)

W6 hard-coded the heading on the Attribution `h2` in `src/components/About/AboutPage.tsx`. The string was not in `copy.ts`, so a heading-only change in `copy.ts` would not have rendered. Amendment 1 added `AboutPage.tsx` post-hoc: that existing `h2` now interpolates `{ATTRIBUTION_HEADING}`. No layout, ordering, or other copy on the page changed. This is recorded as a post-hoc widening, not as a claim that AboutPage was never edited.

The CFBD sentences and the approved two-sentence copy are untouched.

---

## Operator decisions (not agent inferences)

These arrived as operator constraints with the approved string. They are recorded as operator decisions:

- **Entity, not personal name.** No individual is named anywhere.
- **No contact method.** No email, form URL, phone, social handle, or issue-tracker link.
- **No repository link on the page.** The repository's public/private status is out of scope for this task and is not referenced in copy.

W6 export name `ATTRIBUTION_PLACEHOLDER` is retained as an alias of `ATTRIBUTION_COPY` for the body copy. The literal placeholder string is gone.

---

## Rejected candidates

1. **Personal-name attribution** (naming an individual as author). Rejected — operator decision: entity, not personal name. The rejected wording is not restated here so this file does not invent a name.
2. **Attribution that includes a contact method** (email, form URL, or similar). Rejected — operator decision: no contact method.
3. **Approved affiliation sentence extended with “or sportsbook.”** Dropped — grep-gate status unverifiable until the W0 list reconciliation in open item 4 (`W9-1`); §6.1 and “What Ridge does not show” already carry the betting posture.

---

## L3 reopening conditions — none triggered

No L3 reopening condition is triggered. This task introduced no monetization, published no line or edge figure, attached no custom domain, and left `noindex` unchanged.

---

## Positive evidence — Attribution section as extracted from the test render

`renderToStaticMarkup(<AboutPage year={2026} />)`, section `data-testid="about-attribution"`.

Heading + body text after stripping tags:

```
Attribution Schedule, score, and team-name data displayed on Ridge are derived from CollegeFootballData (collegefootballdata.com). Ridge is not affiliated with CollegeFootballData. Ridge is an independent research project. It is not affiliated with any school or conference.
```

Paragraph text only (exact-equality target — four approved sentences, two CFBD then two operator, in order):

```
Schedule, score, and team-name data displayed on Ridge are derived from CollegeFootballData (collegefootballdata.com). Ridge is not affiliated with CollegeFootballData. Ridge is an independent research project. It is not affiliated with any school or conference.
```

Inner HTML of that section:

```
<h2 class="_sectionTitle_1duaw_31">Attribution</h2><p class="_attribution_1duaw_100">Schedule, score, and team-name data displayed on Ridge are derived from CollegeFootballData (collegefootballdata.com). Ridge is not affiliated with CollegeFootballData.</p><p class="_placeholder_1duaw_90" data-testid="attribution-placeholder">Ridge is an independent research project. It is not affiliated with any school or conference.</p>
```

---

## Negative evidence — placeholder eliminated

From `webapp/site`:

```
rg -n --fixed-strings "[Operator to supply" src tests
placeholder_literal_exit=1
```

Empty stdout. Ripgrep exit 1 = no matches.

```
rg -n -i --pcre2 "\[[^\[\]]*Operator to supply[^\[\]]*\]" src tests
placeholder_class_exit=1
```

Empty stdout.

```
rg -n -i --pcre2 "\[[^\[\]]*[Oo]perator[^\[\]]*\]" src tests
bracketed_operator_exit=1
```

Empty stdout.

No other operator placeholder of this class was found on a public page.

---

## Grep gate (changed copy)

The W0 list and the W8-D set do **not** agree.

| Set | Pattern |
|-----|---------|
| W0 only | `\bplay\b`, `\bunits\b` |
| Shared | `best bet`, `yes bet`, `edge vs market` |
| W8-D only | `lock it in`, `must bet`, `recommended bet` |

What is true: **neither set flags the new copy.**

W0-only patterns (`\bplay\b`, `\bunits\b`) were run against `copy.ts` only, never repo-wide. The W8-D set was run against `copy.ts` and against `webapp/site/src`.

Canonical W0 acceptance list over `src/lib/about/copy.ts`:

```
rg -n -i --pcre2 "best bet|yes bet|\bplay\b|edge vs market|\bunits\b" src/lib/about/copy.ts
W0_copy_exit=1
```

Empty stdout.

Ad-hoc W8-D pattern set over the same file, then over `src`:

```
rg -n -i --pcre2 "best bet|yes bet|edge vs market|lock it in|must bet|recommended bet" src/lib/about/copy.ts
W8D_copy_exit=1

rg -n -i --pcre2 "best bet|yes bet|edge vs market|lock it in|must bet|recommended bet" src
W8D_src_exit=1
```

Empty stdout.

**W9-1 starting point (union):**

```
best bet|yes bet|\bplay\b|edge vs market|\bunits\b|lock it in|must bet|recommended bet
```

---

## Local acceptance output (re-run on amendment 2 tree)

### 1. `npm run typecheck`

```
> ridge-site@0.1.0 typecheck
> tsc --noEmit

tests/gallery-gate.test.ts(8,17): error TS2540: Cannot assign to 'NODE_ENV' because it is a read-only property.
tests/gallery-gate.test.ts(10,17): error TS2540: Cannot assign to 'NODE_ENV' because it is a read-only property.
tests/gallery-gate.test.ts(15,17): error TS2540: Cannot assign to 'NODE_ENV' because it is a read-only property.
tests/gallery-gate.test.ts(17,17): error TS2540: Cannot assign to 'NODE_ENV' because it is a read-only property.
```

Pre-existing W8-D gallery-gate `NODE_ENV` assignability (four TS2540s). Not introduced here; `tests/gallery-gate.test.ts` is outside the sanctioned edit set. `next build` typechecks application code and passed (item 4).

### 2. `npm run test`

```
 Test Files  19 passed (19)
      Tests  121 passed (121)
Token diff-check PASSED — all §4.1/§4.2 values match tokens.css
```

Prior W8-D total: **119**. This task: **121** (+2). The +2 covers deliverable 3 (positive Attribution render; replaces the old placeholder-presence test, so not an extra count) and deliverable 5 (placeholder-export regression). Deliverable 4 was a modification, not a new test.

Amendment 2 replaced the personal-name / company-suffix regex with exact-equality on the attribution body. Current deliverable-4 assertions:

```
expect(attributionParagraphText(section)).toBe(`${CFBD_ATTRIBUTION} ${ATTRIBUTION_COPY}`);
expect(html).not.toMatch(/mailto:/i);
expect(html).not.toMatch(/[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}/i);
expect(html).not.toMatch(/(^|[^a-z0-9/])@[a-z0-9_]+/i);
expect(html).not.toMatch(/github\.com/i);
expect(withoutResponsibleGambling).not.toMatch(/tel:/i);
```

Heading assertions (amendment 2):

```
expect(ATTRIBUTION_HEADING).toBe("Attribution");
expect(attributionHeadingText(section)).toBe("Attribution");
for (const heading of renderedHeadingTexts(html)) {
  expect(heading).not.toMatch(/contact/i);
}
```

`withoutResponsibleGambling` is the About markup with the `data-testid="about-responsible-gambling"` section removed, so `tel:` is forbidden outside §6.2. No personal name or company suffix appears in `tests/about.test.tsx`.

### 3. `npm run lint`

```
> ridge-site@0.1.0 lint
> eslint src && prettier --check .

Checking formatting...
All matched files use Prettier code style!
```

Exit 0. The W6-known prettier warning on `tests/capture-about-screenshots.mjs` did not fire.

### 4. `npm run build`

`ARTIFACT_SOURCE=fixtures npm run build` — succeeded. Route table includes:

```
├ ○ /about                                 375 B         103 kB          6h      1y
```

---

## Production closure (operator-run, after Vercel deploys this commit)

W8-B is a production defect. A green local suite does not close it. No R2 publish and no revalidation POST is required: this is static copy; the deploy rebuilds `/about`. If the string is absent after deploy, STOP AND REPORT; do not POST `/api/revalidate` as a workaround without recording why it was needed.

Commands (run after deploy):

```
curl -s  https://the-cfb-model.vercel.app/about | rg -o 'Ridge is an independent research project\. It is not affiliated with any school or conference\.'
curl -s  https://the-cfb-model.vercel.app/about | rg -c 'Operator to supply'
curl -sI https://the-cfb-model.vercel.app/about | rg -i 'x-robots-tag'
```

Expect: the sentence pair present; zero placeholder matches; `noindex` still present.

**Outputs (pending operator post-deploy run):**

```
(sentence pair)
(placeholder count)
(x-robots-tag)
```

---

## Named successors

- **W8-B-HISTORY** — git-history secret scan for CFBD and R2 key patterns before/after the repository public flip. CFBD ToU §2 is one of the two constraints W8-A left live; rotation closes the security exposure without removing the string from history.
- **W9-1** — grep-list reconciliation. Starting point is the union recorded above (W0 `\bplay\b`/`\bunits\b` plus W8-D `lock it in`/`must bet`/`recommended bet`). W0-only patterns in this task were run against `copy.ts` only, never repo-wide.

---

*End of W8-B.*
