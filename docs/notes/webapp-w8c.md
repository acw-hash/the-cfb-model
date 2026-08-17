# W8-C — suppress market-referenced per-game probabilities

**Date:** 2026-08-17  
**Status:** Complete (code); production `latest/` still 1.1.0 until first live publish  
**Closes:** launch-blocker on per-game cover/over vs the CFBD close  
**Authority:** `docs/webapp/DESIGN.md` §1.2, §1.7, §3.1, §5.1–§5.2, §6.3; ADR 0015; Amendment 1  
**Depends on:** W8-A (`e20cad5`) and W8-D (`4a0dd4a`) committed at W8-COMMIT before this change

No model, calibrator, simulation, or numeric-output change. `p_ats_home`,
`p_ou_over`, `_lookup_closes`, `spread_cover_probs`, `total_probs`, and the
`ats_close` / `ou_close` PIT maps are untouched. Aggregate ATS metrics and the
NOT CURRENTLY FIT TO BET verdict stay on `/results`. `p_win_home` stays.
No R2 write.

---

## Operator L3 (as given)

A per-game cover probability against the close is the model's ATS disagreement
with the market. Relabeling it to name the close would still publish that
figure (product decision 1). Withdrawal, not honest copy. Internal calibrators
stay for evaluation. Affiliation L3 remains the existing operator-accepted
risk in `docs/notes/webapp-w6.md`.

---

## Phase 0 inventory (executed before edits)

Rendered to readers: **yes.** Game Detail ProbabilityList on `/game/401628373`
showed `Cover (model ref) 43%` and `Over (model ref) 45%` on live production
and on the W8-D production-build HTML. Gated by `sigma_margin_credible` and
the field `_credible` flags.

1. **Export** — `export.py` remapped `p_ats_home` → `p_cover_home` and
   `p_ou_over` → `p_over` plus `_credible` companions. Removed in this task.
2. **R2 (read-only)** — all nine `week_predictions.json` objects, including
   `latest/` and every `v1/*` week file, carried the four keys. None carried
   `p_ats_home` / `p_ou_over`.
3. **RSC** — live `/` leaked 56× `p_cover_home` / `p_over` (full GamePrediction
   flight, pre-W8-A projection). After W8-A, `/` is 0. `/game/401628373`
   still had labels (2) plus `data-field` / RSC copies (3). `/results` and
   `/about`: 0.
4. **UI** — `ProbabilityList.tsx` rows `Cover (model ref)` / `Over (model ref)`.
5. **Consumers** — conviction tiering uses `p_favored` ← `p_win_home` only.
   Sort uses `p_favored`. Interval rounding uses `sigma_margin`. Cover/over
   not read for tiering or sort.
6. **Fixtures** — only `week_predictions.json` carried the keys. Results /
   track_record / meta / ratings did not.

Reference line: CFBD close (median of staged `lines_historical`,
`line_type==close`). Published `sigma_margin` is the same `sigma_m` used to
assemble the bivariate; inversion of Φ is approximate (key-number kernel,
two-way, optional PIT).

---

## Schema (Amendment 1)

`SCHEMA_VERSION` **1.1.0 → 1.2.0** (minor). `SUPPORTED_SCHEMA_MAJOR` remains **1**.
No major bump. W7-CLOSE-2 / `schema-version.test.ts` still uses `2.0.0` as the
unsupported major — left in place. A real future major-2 bump must move that
fixture to `3.0.0` first.

§1.7 now distinguishes **WITHDRAWAL** (field removed in the same change that
removes every consumer — minor) from **REMOVAL** (consumer still reads it —
major). Cover/over is the first WITHDRAWAL (ADR 0015).

The JSON Schema never required the four keys. The loader `JSON.parse`s and
does not reject unknown keys. Both 1.1.0 and 1.2.0 load without a maintenance
page (proven by `game-detail-probability.test.tsx`).

---

## Fixtures (Amendment 1; no R2 write)

| File | schema | four keys |
|------|--------|-----------|
| `webapp/fixtures/week_predictions.json` | 1.2.0 | absent |
| `webapp/fixtures/week_predictions.legacy-1.1.0.json` | 1.1.0 | intact (copy of pre-task fixture) |

**How `latest/` cleans itself:** the first live `predict_publish` (blocked until
this task closes) writes 1.2.0 objects without the four keys and overwrites
`latest/*`. This task does not restore R2.

**Still on the operator-cleanup list** (keys retained): `v1/*`, `v2/*` (incl. the
doctored 2.0.0 W7-CLOSE-2 object — not touched), `sandbox/*`.

---

## Visual delta

ProbabilityList: three rows → one row (`Home win`). Heading `Probabilities`
kept (not empty, not orphaned). No other layout change.

Rendered list for fixture `401628373`, **identical** on 1.1.0 and 1.2.0:

```
Probabilities
Home win    68%
```

No Cover/Over labels. No maintenance copy. No `p_cover_home` in the markup.

---

## Allowlist (value / exact keys, not a name denylist)

- Python: `PUBLISHED_GAME_PREDICTION_KEYS` + `assert_game_prediction_allowlist`
  on every `build_game_prediction` return.
- Site: `assertPublishedGameKeys` / `assertConsumedOrWithdrawn` /
  `GAME_FIELD_CONSUMERS`. A key present in a published game object must be
  a named consumer **or** `WITHDRAWN_FIELDS`.
- This Week DTO: existing `THIS_WEEK_GAME_KEYS` exact set.
- Game Detail DTO: `projectGameDetailGame` drops withdrawn 1.1.0 keys.

Bite (pytest): extra key `unsanctioned_edge` → `PublishedKeyAllowlistError`;
revert → pass. See `tests/unit/test_webapp_w8c.py`.

---

## W8-COMMIT Phase 4 correction

W8-A projection removes `p_cover_home` / `p_over` / `conviction_basis` /
`p_win_home` / `home_team_id` from **`/` only**. Phase 4's field-name loop
against `/` will read 0 for all five after the W8-A/W8-D deploy. That is **not**
closure of the leak: Game Detail still rendered Cover/Over until this task.

Required extra check (expect Cover/Over **present** on a W8-COMMIT-only deploy):

```
curl -s https://the-cfb-model.vercel.app/game/401628373 | rg -i -c 'cover|model ref'
```

Two-step push (`4a0dd4a:main` then `913312c:main`) captured that window.

**After `4a0dd4a` was production (W8-A+D, not W8-C):**

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

The five names at 0 on `/` is W8-A projection. Cover on Game Detail was still
there — that is the remaining W8-C-scoped leak.

**After `913312c` (W8-C) was production:**

```
# /
conviction_basis: 0
p_cover_home: 0
p_over: 0
p_win_home: 0
home_team_id: 0
mu_margin: 56

# /game/401628373
Cover (model ref): 0
Over (model ref): 0
Home win: 2   # "Probabilities Home win 68%"
HAS maintenance / schema warning: False

# /results
ATS: 12
NOT CURRENTLY FIT TO BET: 2
```

Loose `cover|model ref` still counts 4 after W8-C: all `ForecastBlock` **coverage**
(`nominal coverage`, CSS `ForecastBlock_coverage`). That pattern is not a close
signal. Close signal is the exact labels `Cover (model ref)` / `Over (model ref)`.

---

## W8-R2-PUBLIC

**BLOCKED** until this task is closed in production (and preferably until
`latest/` is a 1.2.0 object). Public-read of raw week_predictions would
republish whatever keys `latest/` still holds.

---

## Handoff

Verify "committed" against `git log` for a representative path, not against a
notes file. W8-A/W8-D notes said complete while the work was uncommitted;
they were committed at W8-COMMIT as `e20cad5` / `4a0dd4a`.

---

## Acceptance commands

```
$ make test                    # pytest -m "not live"
$ cd webapp/site && npm test   # 129 passed + token/contrast guard
$ npm run build                # ARTIFACT_SOURCE=fixtures
```

`npm run typecheck` may still fail TS2540 on `gallery-gate.test.ts` (W9-1).
