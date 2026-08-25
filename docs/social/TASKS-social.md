# Ridge — Social Layer Task Breakdown (S-series)

**Authority:** `docs/social/X_PLAYBOOK.md` (this series), `docs/webapp/DESIGN.md` (upstream data contracts).
**Scope:** Session-sized tasks (one agent session each), dependency order.

**Product constraint — SCOPE AMENDMENT (read before starting any S task):**

TASKS.md states: *"Ridge is not a betting-recommendations product. No picks,
lines, or edge claims in code, copy, or artifacts."* That constraint governs
**the public website and everything pushed to R2**, and is unchanged. The
S-series builds a **separate, private, local-only** social layer that does
carry lines and edges.

Hard boundary — an S task **fails review** if it:

- writes bet/edge/line data into any artifact under `v*/` or `latest/` on R2;
- adds picks, lines, or edge copy to any Next.js page or site string;
- changes `week_predictions.json`, `meta.json`, `results_*.json`, or
  `track_record.json` schemas to carry bet fields;
- posts to any social API automatically (human-in-the-loop is required).

Everything the S-series produces lands on the workstation filesystem only.

---

## Dependency graph

```
W1 (artifact export)  ─┐
                       ├─► S1 (candidate persistence, local-only)
Task 24 predict_publish ┘        └─► S2 (ridge_social generator + tests)
                                        └─► S3 (threshold calibration backtest)
                                              └─► S4 (weekly runbook + Makefile target)
```

---

## S1 — Persist bet candidates to local social sidecar

**Goal:** `predict_publish` already computes `accepted` / `rejected` candidate
lists and discards them after alerting. Persist them to a local JSON sidecar so
the social generator has a stable input. **No R2 push.**

**Sanctioned edits (sketch):**

- `src/ncaa_quant/social/__init__.py` (new package)
- `src/ncaa_quant/social/candidates.py` (new) — `export_social_candidates()`
- `src/ncaa_quant/pipelines/predict.py` — call sidecar export after
  `apply_bet_filters`, gated on `cfg.social.enabled`; wrap in try/except and
  notify on failure exactly as `webapp_export` does (never fail the publish)
- `src/ncaa_quant/config.py` — `SocialConfig`: `enabled: bool = False`,
  `output_dir: str`, `public_min_edge_sides: float = 0.045`,
  `public_min_edge_totals: float = 0.055`, `unit_fraction: float = 0.005`
- `tests/unit/test_social_candidates.py`
- `docs/notes/social-s1.md`

**Deliverables:**

1. `export_social_candidates(publish_result, config)` writes
   `{output_dir}/{season}/w{week}/{refresh_kind}/candidates.json`:
   ```json
   {
     "season": 2026, "week": 5, "refresh_kind": "tuesday_primary",
     "published_at": "...", "fixture": false,
     "accepted": [ /* CandidateRecord[] */ ],
     "rejected": [ /* CandidateRecord[] + "reasons": [FilterReason] */ ]
   }
   ```
2. `CandidateRecord` carries: `game_id`, `market`, `side_team`, `market_line`,
   `model_line`, `edge`, `expected_value`, `american_odds`, `p_win`,
   `stake_fraction` (from `recommended_stake()`), `model_market_residual_points`.
3. `side_team` / `market_line` / `model_line` derived at export time — do **not**
   make the social script re-derive orientation. Home-side orientation follows
   the CFBD designated home anchor (per `filter_home_side_spreads`), not the
   Odds listing.
4. Sidecar inherits the `fixture` flag from the publish when present.

**Acceptance:**

- Sidecar written on a fixture-week publish; `"fixture": true` propagates.
- `cfg.social.enabled = False` (default) → no file written, no error.
- Export failure does not fail `predict_publish` (mirrors `webapp_export`).
- No new fields appear in any R2-bound artifact; existing export tests unchanged.
- Rejected records carry machine-readable `FilterReason` values, not prose.
- `make test` green; no bet data in any file under the webapp export path.

**Dependencies:** W1 complete; Task 24 `predict_publish` complete.

---

## S2 — Social thread + reply-bank generator

**Goal:** Move `ridge_social.py` into the repo as a tested script that turns
`week_predictions.json` + `candidates.json` into paste-ready X copy.

**Sanctioned edits (sketch):**

- `scripts/ridge_social.py` (provided draft — harden, don't rewrite)
- `src/ncaa_quant/social/select.py` — `select_best_bets()` moved out of the
  script so it is unit-testable and reusable by S3
- `src/ncaa_quant/social/render.py` — thread + reply-bank rendering
- `tests/unit/test_social_select.py`, `tests/unit/test_social_render.py`
- `docs/notes/social-s2.md`

**Deliverables:**

1. Public bar applied on top of already-accepted candidates: edge ≥
   `public_min_edge_*`, σ credible, not stale at post time, kickoff in future,
   sorted by edge desc, capped at `betting.max_bets_per_week`.
2. **Variable count** — N qualifying bets → N posts. N=0 → the No-Bet post.
   Never pad, never truncate to a round number.
3. Thread renderer emits numbered posts with per-post character counts and an
   over-280 warning marker.
4. Reply bank covers **every game in the publish**, with three branches:
   on-card / forecast-only (reason mapped from `FilterReason` to plain English)
   / σ-refused.
5. Refuses to render without a warning banner when `fixture: true`.

**Acceptance:**

- Golden-file tests for thread and reply bank on the 2024 fixture week.
- Every generated post ≤ 280 chars for the fixture week (test asserts this).
- N=0 fixture case renders the No-Bet post and nothing else.
- A stale-stamped game and a σ-refused game are both excluded from the card
  and both appear correctly in the reply bank.
- Script performs **no network calls** — test asserts this.
- `make test` green.

**Dependencies:** S1.

---

## S3 — Public threshold calibration backtest

**Goal:** Set `public_min_edge_*` from data, not vibes. Produces the numbers for
the account's pinned methodology thread.

**Sanctioned edits (sketch):**

- `scripts/calibrate_social_threshold.py`
- `docs/notes/social-s3.md` — the writeup **is** the deliverable

**Deliverables:**

1. Replay 2019–2024 walkforward candidates; for a grid of edge thresholds
   report per season-week: bet count, ATS/total hit rate, units, mean CLV,
   and the distribution of weekly counts.
2. Recommend thresholds where the **median week yields 3–10 bets** and the
   count distribution is reported honestly (including zero-bet weeks).
3. Explicit statement of what is and isn't measured — this is backtest
   performance of a selection rule, not a forward guarantee. Follow the
   epistemic conventions used in ADR 0013/0014 and the flap-exposure note
   (measured vs NOT MEASURED vs UNRESOLVED).

**Acceptance:**

- 2025 lockbox **not** touched (`assert_lockbox_excluded` conventions respected).
- Recommended thresholds written back to `configs/social.yaml` defaults.
- Note states measured quantities and their limits without overclaiming.

**Dependencies:** S2.

---

## S4 — Weekly runbook + operator target

**Goal:** One command on Tuesday morning; human reviews and posts.

**Sanctioned edits (sketch):**

- `Makefile` — `make social-week SEASON=.. WEEK=..`
- `docs/social/RUNBOOK.md`
- `docs/social/X_PLAYBOOK.md` (copy of the playbook doc; templates live here)

**Deliverables:**

1. `make social-week` reads the latest local publish + sidecar, writes
   `out/social/thread_w{W}.md` and `replies_w{W}.md`, prints the bet count and
   every candidate held back with its reason.
2. Runbook covering the weekly cadence (Tue anchor → Wed explainer → Thu–Sat
   revisions only if changed → Sun grading), the pulled-bet procedure, and the
   grading post built from `grade_export` + `settle_week` CLV output.
3. Guardrail checklist in the runbook: 21+ / not financial advice /
   1-800-GAMBLER in bio and pinned post; never delete a graded pick; no
   auto-posting.

**Acceptance:**

- Dry run on the fixture week produces both files and the held-back log.
- Runbook names the exact publish clock: Tue 06:00 UTC `tuesday_primary`,
  Thu–Sat 06:00 UTC `daily_refresh`, plus `t_minus_6h` / `t_minus_1h`.
- No credentials, tokens, or API keys introduced anywhere in the S-series.

**Dependencies:** S2 (S3 recommended first so thresholds are calibrated).

---

## Open items for human decision (not agent-resolvable)

| ID | Item |
|----|------|
| SL1 | Counsel reviews the X plan **alongside** the site (DESIGN §6.3 L1–L3 remain open; the social account changes the L3 posture). |
| SL2 | Account handle, bio copy, and whether the site links back to X (recommend: X → site only, not site → X, until SL1 clears). |
| SL3 | Monetization posture (subscriptions/affiliates) — out of scope for S1–S4; revisit only after a full graded season is public. |
