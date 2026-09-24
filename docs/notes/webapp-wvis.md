# W-VIS — `/visual` model walkthrough

**Date:** 2026-09-24  
**Status:** Integrated under site toolchain; published-copy guard green; ratchet pin
pre-existing drift on HEAD (see below)  
**Authority:** handoff INTEGRATION.md (folded here, then deleted); DESIGN §4 tokens;
W7 published_at posture; W9-1 betting-language guard

---

## What was built

Public route `/visual` ("How a forecast is made"): interactive 8-step walkthrough
of how Ridge turns college football data into a forecast with uncertainty. Static
and illustrative — no artifact fetch.

### Placement (flattened paste under `webapp/`)

Operator pasted 16 files flat at `webapp/` root. After SHA-256 MATCH on all 16:

| Source | Final path |
|--------|------------|
| `webapp/page.tsx` | `webapp/site/src/app/visual/page.tsx` |
| `webapp/ModelWalkthrough.{tsx,module.css}` | `webapp/site/src/components/ModelWalkthrough/` |
| `webapp/{shared,InputsFigure,PriorFigure,KalmanFigure,EnsembleFigure,UncertaintyFigure,RangeFigure,ConvictionFigure,PublishedFigure}.tsx` | `…/figures/` |
| `webapp/{copy,math,example}.ts` | `webapp/site/src/lib/visual/` |
| `webapp/INTEGRATION.md` | folded into this note, then deleted |

`webapp/site/src/lib/about/copy.ts` and `lib/results/copy.ts` untouched
(`git diff --stat` empty).

### Sanctioned edits

1. **SiteHeader** — `Visual` → `/visual` after About (four links).
2. **`scripts/check_betting_language.py`** — added
   `"webapp/site/src/lib/visual/copy.ts"` to `PUBLISHED_COPY_SURFACES`.
   `BASELINE_*` pins unchanged.
3. **`tests/cross-page-nav.test.ts`** — asserts fourth header href.
4. **`tests/visual.test.tsx`** — happy-dom client tests for step 1 default,
   `?step=4`, Next/Back, ←/→, Start over, example tag on every step, both
   models off → "Forecast unavailable".
5. **Dev dependency:** `happy-dom` `^15.11.7` (lockfile resolves 15.11.7);
   file-local `@vitest-environment happy-dom` on `visual.test.tsx` only.

---

## Mechanical fixes (file:line)

Lint (`ridge/require-figure-for-numbers`) forbids `toFixed` in
`src/components/**`. Display/SVG formatting routed through `lib/visual`:

| Change | Location |
|--------|----------|
| Added `svgCoord` (ASCII `toFixed` for SVG path data) | `lib/visual/math.ts:257-260` |
| Import `fmtNum`, `svgCoord` | `figures/PriorFigure.tsx:3-10` |
| SVG curve coords → `svgCoord` | `PriorFigure.tsx:29` |
| SVG band coords → `svgCoord` | `PriorFigure.tsx:54` |
| aria-label `sd.toFixed(2)` → `fmtNum(sd, 2)` | `PriorFigure.tsx:68` |
| readout `±sd` / variance → `fmtNum` | `PriorFigure.tsx:138-142` |
| Import `fmtNum` | `figures/KalmanFigure.tsx:5` |
| aria-label `sd.toFixed(2)` → `fmtNum(sd, 2)` | `KalmanFigure.tsx:72` |
| rating readout `sd.toFixed(2)` → `fmtNum(sd, 2)` | `KalmanFigure.tsx:244` |
| Import `svgCoord` | `figures/RangeFigure.tsx:5` |
| SVG curve point → `svgCoord` | `RangeFigure.tsx:45` |
| Prettier `--write` on ModelWalkthrough sources (whitespace only) | 9 files under `components/ModelWalkthrough/` |

No copy rewording. No constant value changes in `PRIOR` / `KALMAN` / `TIERS` /
`example.ts`.

---

## Constant-drift table (`math.ts` vs backend)

| `math.ts` | Value | Backend source | Backend value | Status |
|-----------|-------|----------------|---------------|--------|
| `PRIOR.baseVar` | 0.02 | `PriorConfig.base_var` (`ratings/priors.py`) | 0.02 | MATCH |
| `PRIOR.turnoverScale` | 2.5 | `PriorConfig.turnover_scale` | 2.5 | MATCH |
| `PRIOR.missingVarPenalty` | 0.015 | `PriorConfig.missing_var_penalty` | 0.015 | MATCH |
| `PRIOR.confRegression` | 0.3 | `PriorConfig.conf_regression` | 0.30 | MATCH |
| `KALMAN.q` | 0.0025 | `_default_q_diag()["off_epa"]` (`state_space.py`) | 0.0025 | MATCH |
| `KALMAN.rEpaBase` | 0.12 | `StateSpaceConfig.r_epa_base` | 0.12 | MATCH |
| `KALMAN.refSnaps` | 70 | `StateSpaceConfig.ref_plays` | 70.0 | MATCH |
| `KALMAN.winsorSigma` | 2.5 | `StateSpaceConfig.residual_winsor_sigma` | 2.5 | MATCH |
| `TIERS.lean` | 0.575 | DESIGN §2.2 Lean enter | 0.575 | MATCH |
| `TIERS.clear` | 0.7 | DESIGN §2.2 Clear lean enter | 0.70 | MATCH |
| `TIERS.strong` | 0.85 | DESIGN §2.2 Strong lean enter | 0.85 | MATCH |
| `TIERS.holdBand` | 0.03 | DESIGN §2.3 ±0.03 hysteresis | 0.03 | MATCH |

No drift. Do not "fix" either side if they diverge later — report.

---

## `published_at` (W7 / DESIGN §3)

- Root layout loads `meta` and may show **StalenessBanner** using
  `meta.published_at` (observed on `/visual` under fixture meta: stale banner
  present). That is layout-level, not a page stamp.
- **PublishedAtStamp** is page-level on This Week (`ThisWeekHeader`) and Game
  Detail (`ProvenanceStrip`). About and Results do **not** mount it.
- `/visual` must not fetch artifacts. No page-level stamp was added; posture
  matches About/Results aside from the shared layout banners.

---

## Acceptance counts

### `webapp/site`

| Command | Result |
|---------|--------|
| `npm run typecheck` | exit 0 |
| `npm run lint` | exit 0 |
| `npm run test` | 29 files / **209** tests passed; token check PASSED |
| `npm run build` | exit 0; route **`ƒ /visual`** present (16.2 kB) |

### Repo root

| Command | Result |
|---------|--------|
| `check_betting_language.py published` | matches=**0** lines=0 files=0 surfaces=**11** (exit 0) |
| `check_betting_language.py ratchet` | matches=**813**/621 lines=**627**/468 files=**109**/104 (exit 1) |
| `make test` | **1052** passed, **4** failed, 1 deselected (exit 1) |

**`make test` failures (pre-existing on clean HEAD; identical with W-VIS staged):**

Verified by `git stash -u` → `make test` on clean HEAD → `git stash pop`.
Both runs: **1052** passed, **4** failed, 1 deselected. Same IDs:

1. `tests/unit/test_betting_language_guard.py::test_ratchet_matches_exact_pin`
2. `tests/unit/test_slot_close_capture.py::test_2026_credit_accounting_matches_v4_projection`
3. `tests/unit/test_social_candidates.py::test_social_disabled_by_default`
4. `tests/unit/test_webapp_w9r.py::test_copy_cites_amended_numbers_and_reval_memo`

**Ratchet equality:** live counts with W-VIS staged and on bare HEAD are both
**813 / 627 / 109** (pins remain 621 / 468 / 104). W-VIS sources and this notes
file add **0** union matches. Pins were not changed; re-measure/re-pin is an
operator task outside W-VIS.

### happy-dom

Added as a **devDependency** only (`"happy-dom": "^15.11.7"`, caret pin matching
other site devDependencies). Lockfile resolves **15.11.7**. Vitest stays on the
global `environment: "node"`; only `tests/visual.test.tsx` opts in via
`// @vitest-environment happy-dom`. Needed for client interactivity (Next/Back,
arrow keys, model toggles) that `renderToStaticMarkup` cannot exercise.

### Playwright evidence

Script: `webapp/site/tests/capture-visual-screenshots.mjs`  
Committed under `webapp/site/docs/screenshots/wvis/` (43 PNGs), matching the W6
`about-*.png` convention under `docs/screenshots/`.

- Steps 1–8 × {390, desktop} × {light, dark}
- Interactive: step 3 after "Run the season"; step 4 both models off; step 5
  after Simulate; step 6 "When it fails"; step 8 "Stale inputs"
- **console_errors = 0** on `/visual`

### §4.4 anti-pattern checklist

| Criterion | Verdict | Evidence |
|-----------|---------|----------|
| no default-shadcn aesthetic | **PASS** | `s44-no-default-shadcn.png` — custom tokens, no shadcn chrome |
| no purple-gradient heroes | **PASS** | `s44-no-purple-gradient-heroes.png` — flat primary bg, accent blue only |
| no emoji cards | **PASS** | `s44-no-emoji-cards.png` — no emoji, no card grid |
| no wall-of-widgets | **PASS** | `s44-no-wall-of-widgets.png` — one figure + prose per step |
| no gratuitous glassmorphism | **PASS** | `s44-no-gratuitous-glassmorphism.png` — solid surfaces |
| no filler marketing copy | **PASS** | `s44-no-filler-marketing-copy.png` — method prose + illustrative tag |

---

## Ambiguities / operator draft

### DESIGN §5 has no `/visual` entry

Draft **§5.5 Visual / How a forecast is made** for operator review (do **not**
paste into DESIGN.md from this task):

> **§5.5 Visual (`/visual`)**  
> Interactive, static walkthrough of the publish path: inputs → preseason prior →
> weekly Kalman update → ensemble forecast → uncertainty → calibrated range →
> conviction tier → published row. All figure numbers are illustrative
> (`lib/visual/example.ts`), labeled on every step. No artifact load. Nav label
> **Visual**. Query `?step=1…8` deep-links a stage. Shares root layout chrome
> (header, footer, fixture/staleness banners).

### Spec silences recorded

- Relative imports retained from handoff (repo also supports `@/`).
- See **happy-dom** under Acceptance counts for the test-environment decision.

### Deploy follow-up (post e706852)

Vercel `npm run guard` failed repeatedly on `tests/visual.test.tsx` under Linux
Vitest interop: named/`React.act` undefined, then `createRequire` not a function.
Final approach: **no act** — use `flushSync` from `react-dom` for setState from
native events, plus `settle()`/`waitFor` so `useEffect` (`?step=`) runs. Also
listed `src/lib/visual/copy.ts` in `check-published-copy.mjs`. Verified with
local `npm run guard` exit 0 before push.

---

## Out of scope (honored)

No backend, pipeline, export, fixture, R2, or config changes. No DESIGN.md /
TASKS.md edits. No `_handoff/` committed (paste lived under `webapp/`, cleaned).
