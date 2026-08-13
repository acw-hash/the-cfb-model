# Ridge — Build Task Breakdown

**Authority:** `docs/webapp/DESIGN.md`  
**Scope:** Session-sized tasks (one agent session each), dependency order.  
**Fixture policy:** All UI development uses fixture artifacts generated from real historical publishes (2024 fixture week). Every fixture declares `"fixture": true`. No invented numbers presented as real — on screen or in screenshots.

**Product constraint (all tasks):** Ridge is **not** a betting-recommendations product. No picks, lines, or edge claims in code, copy, or artifacts.

---

## Dependency graph

```
W1 (artifact export + R2 push + grade export)
  └─► W2 (Next.js scaffold + design system)
        ├─► W3 (This Week)
        ├─► W4 (Game Detail)
        ├─► W5 (Results / Track Record)
        └─► W6 (Methodology / About)
              └─► W7 (Deploy + freshness/staleness)
```

W3–W5 depend on W2 only (parallelizable after W2). W6 depends on W2. W7 depends on W3–W6.

---

## W1 — Artifact export, grade export, and R2 push

**Goal:** Wire `predict_publish` → JSON artifacts → Cloudflare R2 push on the workstation.

**Sanctioned edits (sketch):**

- `src/ncaa_quant/pipelines/publish.py` (new) — `artifact_export()`, `r2_push()`
- `src/ncaa_quant/pipelines/predict.py` — call export + push as final step
- `src/ncaa_quant/config.py` — R2 credentials via pydantic-settings (workstation `.env` only)
- `scripts/export_fixture_week.py` — generate labeled fixture artifacts from 2024 w5 publish
- `tests/unit/test_publish_export.py`, `tests/integration/test_publish_r2.py` (mock R2)
- `docs/notes/webapp-w1.md`

**Deliverables:**

1. **`artifact_export`** — maps `ProductionEnsemblePredictor` output + schedule + tier state → §1 JSON schemas (`week_predictions.json`, `meta.json`, `team_ratings_<season>.json`, `track_record.json` from frozen 23-readout template).
2. **`grade_export` seam** — builds/updates `results_<season>.json` per §1.3 grading rule (last pre-kickoff publish). Runs after postgame ingest or on demand. **Required by W5; not assumed to exist before this task.**
3. **`r2_push`** — uploads versioned keys + `latest/` alias; POST Vercel revalidation webhook.
4. Column rename layer: `pred_margin` → `mu_margin`, `sigma_m` → `sigma_margin`, etc.
5. Conviction tier computation with hysteresis state file on workstation.

**Acceptance:**

- Fixture week export produces valid JSON against §1 schemas; `"fixture": true` set.
- `StampedPrediction` / `StaleContext` / `RefreshKind` correctly mapped.
- Chaos test: STALE publish → `stale_stamp` and `is_stale` in exported JSON.
- σ-refused rows → null probabilities, `sigma_margin_credible: false`.
- `grade_export` produces at least one graded row from fixture historical data.
- `make test` green; no secrets in exported JSON.
- **No webapp code in this task.**

**Dependencies:** Task 24 pipelines (`predict_publish`) complete.

---

## W2 — Next.js scaffold and design system

**Goal:** App Router project with DESIGN §4 tokens, typography, and core components.

**Sanctioned edits (sketch):**

- `webapp/` — Next.js 14+ App Router project root
- `webapp/app/layout.tsx`, `webapp/app/globals.css`
- `webapp/components/{GameRow,IntervalBand,TierChip,StaleBadge,RevisedBadge,PublishedAt}.tsx`
- `webapp/lib/{tokens,format}.ts` — tabular nums, margin/interval formatting
- `webapp/fixtures/` — committed fixture JSON from W1 script (labeled)
- `webapp/package.json`, `webapp/tsconfig.json`
- `docs/notes/webapp-w2.md`

**Acceptance:**

- `npm run build` succeeds.
- Design tokens match §4.1 exact values (light + dark).
- Tabular numerals on all figure components.
- Game row, interval band, tier chip, stale/revised badges match §4.3 patterns.
- **Visual review against §4** — page with fixture game rows reviewed; anti-pattern list verified absent.
- Anti-pattern list present verbatim in PR description / task notes.
- No data fetching beyond fixtures in this task.

**Dependencies:** W1 fixture artifacts available (or checked-in copies).

---

## W3 — This Week page

**Goal:** Sortable/groupable current-week slate per §5.1.

**Sanctioned edits (sketch):**

- `webapp/app/page.tsx`
- `webapp/lib/data.ts` — fetch `week_predictions.json` + `meta.json`
- `webapp/components/WeekList.tsx`, `WeekControls.tsx` (sort/group)

**Acceptance:**

- Every displayed field maps to a named artifact field (§5.1 table).
- Sort by kickoff and by conviction tier order.
- `published_at` visible on page.
- Empty/offseason/stale states per §5.1.
- Mobile: single-column, sticky published bar.
- **Visual review against §4** — compare to Apple Sports benchmark; anti-pattern check.
- Fixture data only in dev screenshots.

**Dependencies:** W2.

---

## W4 — Game Detail page

**Goal:** Full uncertainty + rating trajectories per §5.2.

**Sanctioned edits (sketch):**

- `webapp/app/game/[gameId]/page.tsx`
- `webapp/components/{MarginBlock,TotalBlock,ProbabilityBlock,ProvenanceStrip,RatingTrajectory}.tsx`

**Acceptance:**

- All §5.2 field mappings implemented.
- Three provenance labels visible (`vintage_label`, `ensemble_scope_label`, `feature_time_label`).
- Tier + revised badge when applicable.
- Rating trajectory chart per §4.3 spec (mean line + ±1 SD band).
- σ-suppressed game renders honest absence.
- **Visual review against §4**.
- 404 for unknown `gameId`.

**Dependencies:** W2; fixture includes `team_ratings_2024.json`.

---

## W5 — Results / Track Record page

**Goal:** Graded games + 23-readout metrics table per §5.3.

**Sanctioned edits (sketch):**

- `webapp/app/results/page.tsx`
- `webapp/components/{GradedGamesTable,TrackRecordTable,VerdictBanner}.tsx`

**Acceptance:**

- Tab A: graded games from `results_<season>.json` with interval-hit columns.
- Tab B: `track_record.json` metrics with **exact** values, CIs, n, labels from 23-readout.
- Verdict banner: **NOT CURRENTLY FIT TO BET** with full plain-language paragraph — unrounded, unsoftened.
- `graded_from` provenance shown per row.
- **Visual review against §4**.
- Page documents dependency on W1 `grade_export` seam (not mocked in production).

**Dependencies:** W2; W1 `grade_export` + `track_record.json` fixture.

---

## W6 — Methodology / About + disclaimers

**Goal:** Public-reader about page with §6 copy.

**Sanctioned edits (sketch):**

- `webapp/app/about/page.tsx`
- `webapp/components/Disclaimer.tsx`, `ResponsibleGambling.tsx`
- Site-wide disclaimer in root layout footer

**Acceptance:**

- §5.4 content sections present.
- §6.1 disclaimer and §6.2 1-800-GAMBLER copy rendered verbatim.
- "What Ridge does not show" section lists no picks/lines/edge explicitly.
- **Visual review against §4** — no filler marketing copy.
- Legal flags table **not** shown to public (internal doc only).

**Dependencies:** W2.

---

## W7 — Deploy, freshness, and staleness states

**Goal:** Production Vercel deploy + R2 fetch + revalidation + failure-mode UI.

**Sanctioned edits (sketch):**

- `webapp/vercel.json` — ISR, env vars
- `webapp/app/api/revalidate/route.ts` — on-demand revalidation secret handler
- `webapp/components/{SiteStaleBanner,MaintenancePage,SchemaMismatch}.tsx`
- `webapp/lib/freshness.ts` — site staleness logic (§3.2)
- `docs/runbooks/ridge_deploy.md`
- `docs/notes/webapp-w7.md`

**Acceptance:**

- Deployed to Vercel Hobby; R2 public read configured.
- On-demand revalidation fires on fixture push test.
- Site staleness banner when `published_at` > 36 h past expected slot (§3.2).
- Schema major mismatch → maintenance page; no guess rendering.
- `published_at` on every page.
- Workstation R2 write credential **not** in Vercel env.
- **Visual review against §4** for banner/badge states.
- Zero CFBD/Odds API calls from deployed app (network audit / grep).

**Dependencies:** W3, W4, W5, W6; W1 R2 push operational.

---

## Cross-cutting acceptance (all UI tasks W2–W7)

1. **Visual review against DESIGN §4** — mandatory; cite anti-pattern list verbatim.
2. **Field provenance** — no UI element without artifact field named in spec.
3. **Fixture labeling** — `"fixture": true` in all dev/test data.
4. **No betting language** — grep gate: zero occurrences of recommendation framing outside explicit "does not" sections (see W0 acceptance grep list).
5. **`make test`** — backend tasks; `npm run build && npm run lint` — webapp tasks.

---

## Explicitly out of scope (all W tasks)

- Betting recommendations, picks, edges, Kelly, CLV UI
- Odds API or sportsbook line display
- MLflow / Prefect / workstation public exposure
- User accounts, subscriptions, paywalls
- 2025 lockbox season evaluation claims
- CFBD live API calls from the webapp

---

*End of Ridge build task breakdown (W0).*
