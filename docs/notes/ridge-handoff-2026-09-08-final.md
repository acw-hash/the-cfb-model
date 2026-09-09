# Ridge — session handoff, 2026-09-08 (final; supersedes both earlier 09-08 handoffs)

Project: the-cfb-model / Ridge. Workstation
c:\Users\alecw\Projects\the-cfb-model. Read DESIGN.md and TASKS.md in
project knowledge before acting. (`ridge-launch-execution-guide.md` is in
project knowledge but not in the repo.)

## READ THIS FIRST — three retractions

Everything below the line was investigated on 2026-09-08 and did **not**
hold. Do not reopen any of it without new evidence.

1. **"MAE 16.29 is worse than a home-field baseline."** False, and it was
   the premise driving the whole investigation. The ~13–14 figure is a
   season-long number; week 1 is not a season. On this actual slate (n=99,
   mean actual margin 27.89, SD 22.64): model **16.29**, constant home +3
   **28.38**, home +5 **27.05**, best constant obtainable with hindsight
   (+20) **19.67**. The model beats home +3 by 12.10 points per game, paired
   95% CI [+8.16, +16.02], winning 71 of 99. It also beats the best possible
   constant. Week-1 margin accuracy is fine.

2. **"Forecast quality degrades with snapshot age."** Not supported. All 99
   week-1 rows grade from one snapshot, so age is collinear with kickoff
   date, and the date series does not trend: 08-29 n=7 MAE 9.29 (2.2–2.5 d);
   09-03 n=6 MAE 21.70 (7.4–7.5 d); 09-04 n=8 MAE 10.91 (7.5–8.5 d); 09-05
   n=60 MAE 17.81 (8.5–9.5 d); 09-06 n=16 MAE 14.85; ≥10 d bucket is the
   best on the board at 4.01. Sept 3 and Sept 4 are the same age and differ
   twofold. What varies is slate composition.

3. **"The pooled FCS prior is inflating the error."** No. Both sides in
   `filter_history`: n=50, MAE 16.99 [13.68, 20.51], coverage 40/50 = 0.800.
   Either side absent: n=49, MAE 15.56 [12.39, 19.01], coverage 25/34 =
   0.735. The pooled-prior games are marginally *better*. CIs overlap almost
   entirely.

Also retracted: a hypothesis that in-season rating updates had stalled.
0 of 91 forecasts were identical between Aug 27 and Sept 1; median move
0.397 pts. Ratings are updating.

## What the re-grade actually showed, and its limit

Paired re-grade, Aug 27 `daily_refresh` (baseline, 7.4–11.5 d out) vs
Sept 1 `tuesday_primary` (candidate, 2.2–6.2 d out), 91 identical games:
baseline MAE 16.82, candidate 17.10, mean paired delta −0.28,
95% CI [−0.77, 0.16], candidate wins 46/91. No difference.

**Read this narrowly.** Only 8 games were played between the two snapshots,
so the fresher arm had almost no new information to exploit. The test is
close to null by construction. It does not establish that snapshot age is
harmless in general — it establishes that it was harmless across one
information-poor five-day window. A real test needs snapshots straddling a
full slate; week 2 onward will provide one.

Consequence for §1.3 precedence: no accuracy argument for changing
`REFRESH_KIND_PRECEDENCE`. The rule still contradicts its own documented
purpose and that is a docs fix, not a code fix. **Do not change the
constant.**

## Verified live week-1 numbers

From the published artifact (`published_at` 2026-09-08T15:34:11Z), `--week 1`:
MAE 16.29 CI [13.99, 18.72]; median AE 14.63; margin interval coverage
65/84 = 0.774 CI [0.674, 0.850] (contains nominal 0.80); Brier 0.0982
CI [0.0565, 0.1452] n=99.

Brier is **not** evidence of calibration quality — a slate with mean margin
27.9 is full of near-certain binaries. Keep it out of copy.

Coverage splits meaningfully and honestly: **0.800 exactly on the 50 games
with full rating history**, 0.735 on pooled-prior games plus 15 with no
interval at all. Reporting the split on `/results` would be more informative
than the blended 0.774, and the 0.800 half is defensible.

## Shipped 2026-09-08 (uncommitted at time of writing; not published)

- **Fix A** — `/results` no longer renders 789 blanks. Graded rows render as
  before; a single count line follows, stating how many games are not yet
  final ("kept in the artifact, listed here so the season schedule is not
  omitted. Final score not available; not treated as a miss"). Rationale:
  the existing not-final copy said "row kept visible," so filtering would
  have reversed a deliberate anti-cherry-pick guarantee; the count discharges
  it without the wall. `no_pre_kickoff_publish` and `postgame_missing` still
  render as full rows — they are outcomes, not schedule. Mixed-status test
  added. 175 tests pass, lint and build clean.
- **Fix B** — `apply_margin_interval_coherence_gate` (export.py 317–337) now
  sets `null_reason = "incoherent_margin_interval"` when it nulls the
  interval. `null_reason` is `string | null`, not a closed enum;
  `NULL_REASON_LABELS` added in `lib/results/copy.ts`, Game Detail falls back
  via `nullReasonFootnote(...) ?? MARGIN_INTERVAL_ABSENT_REASON`. Ships on the
  next scheduled publish. `make test` 1052 passed / 1 deselected, no
  deletions.
- **Fix C** — DESIGN §1.3 corrected: four statuses, full-schedule
  placeholders, kind-first/recency-second semantics stated. Constant
  unchanged. §1.7 classification recommended as patch (documenting enum
  values that already ship); operator decides any artifact bump.

## Open work, priority order

0. **ROOT CAUSE FOUND — `rating_uncertainty` in the ENet member.** Written up
   in `docs/notes/finding-rating-uncertainty.md`; read that, not this summary.
   The ENet carries coef_z +4.05 on `rating_uncertainty` (sum of both teams'
   Kalman posterior SDs on off_epa). On pooled-prior games it contributes the
   majority of published μ — +59.6 of BYU–Utah Tech's +76.7 — pushing μ above
   its own q90. Stack weights LGBM 0.384 / ENet 0.616; the ENet is the excess
   on 17/17 failures, and the epistemic mix reduces it rather than causing it.
   **Structural flaw:** all 555 absent-side training rows have the *visitor*
   missing (home-absent n=0), so the model fused "uncertainty is high" with
   "the home team wins big." The feature is symmetric but was fitted on
   one-sided evidence. **Not a bug to remove:** dropping it fixes 17/17
   coherences and moves week-1 MAE 16.27 → 24.39. Week-1 values run ~6× the
   training mean, so production is extrapolating an unconstrained linear term
   past its fitted range. Options and rejected approaches are in the note.
   Decision required; do not patch.

   Ruled out en route (do not reopen): epistemic mix as cause, sparse tail
   data, unstable quantile boosters, monotone constraints on the μ head.
   `ensemble_weight_dampen` is unrelated — a monthly weight EMA, still
   unimplemented, still item 12.

0b. ~~The mean head disagrees with the quantile heads~~ — root cause above.
   Original framing:  the mean head disagrees with the quantile heads, only on
   pooled-prior games.** The interval-suppression gate is
   `not (q10 < mu < q90)`: the point estimate falls outside its own 10th–90th
   percentile band. That fires on **15 of 49** games with a `filter_history`-
   absent side and **0 of 50** full-history games (Fisher exact
   p ≈ 7×10⁻⁶). So the pooled prior does not hurt point accuracy — MAE 15.56
   vs 16.99, in its favour — but it breaks the uncertainty machinery outright
   on roughly a third of those games. The gate behaved correctly: it caught
   the incoherence and refused. The only defect was that it refused silently,
   and that is now fixed. **Why the heads disagree is unexamined and is the
   most substantive open question on this list.**

1. ~~`/results` renders all 888 rows~~ — **fixed, see above.**
   Original diagnosis retained for context:
   `components/Results/GradedGamesSection.tsx` calls `results.games.map` with
   no filter. `results_2026.json` carries 99 `graded` rows plus 789
   `game_not_final` placeholders (weeks 2–15, every field null). The page is
   showing 99 real rows followed by 789 blanks. Not a bug in one place:
   `GradedGameRow` handles all four statuses deliberately, the section renders
   what it is given, the exporter ships the full schedule. The empty branch
   keys on `games.length === 0`, which is why launch verification passed —
   the mixed state had never existed until week 1 got graded. Webapp-only
   change; no publish and no export gate needed.

2. ~~15 rows violate §1.8~~ — **`null_reason` fixed, see above.** The
   suppression itself is item 0, which remains open. Original diagnosis: `margin_interval_lo`/`hi` null while
   `sigma_margin`, `p_win_home` and `conviction_tier` are present, and
   `null_reason` is null on all 99 rows. Not σ-refusals (those null sigma and
   gate probabilities). The gate is **deterministic and identified**: all 15
   are in the `filter_history`-absent set, zero outside, and the same 84/99
   split appears in both week-1 snapshots five days apart (72/91 on Sept 1).
   `null_reason` is a per-game field in `publish_history` rows and is being
   carried through unset — nothing populates it. Find the interval gate, set
   a reason (e.g. `no_rating_history`). Ships on the next publish; do not
   publish to fix it.

3. **Checks that exist but never fire.** `verify_published_artifacts.py`
   validated none of today's findings, and its check 3 hardcodes week-1
   game IDs so it will fail every week. Same class: the betting-language
   ratchet pin was **already stale on HEAD** on 2026-09-08 (618 actual
   against a pin of 617) — a union hit entered at some earlier commit and
   the guard never fired. Re-pinned to 618/465/103 during the Fix A–C
   work, which added zero union hits of its own. Worth identifying the
   commit that introduced it. Reframe both from "stale hardcode" to "no
   enforcement." Original diagnosis: Items 1, 2 and 4
   all reached production without anything firing. Its check 3 also hardcodes
   week-1 game IDs and will fail every week. Reframe from "stale hardcode" to
   "no contract validation."

4. ~~DESIGN §1.3 is stale~~ — **corrected, see above.** Original: It documents `grade_status` as
   `graded | no_pre_kickoff_publish`; the system has four — the webapp's
   `GradeStatus` covers `graded`, `game_not_final`, `no_pre_kickoff_publish`,
   `postgame_missing`, with a `Record<GradeStatus, string>` of display copy.
   §1.3 also says ungraded games are excluded from results; they are not. And
   it claims the precedence ladder "ensures grades reflect what Ridge would
   have shown before kickoff" — it does not, since a 9-day-old
   `daily_refresh` outranks a 4-day-old `tuesday_primary`. Fix the spec, not
   the artifact.

5. **Prefect keep-alive.** Worker + API server are foreground shells; they die
   on reboot and nothing alerts. Needs a service or Task Scheduler job.

6. **Register config-only deployments:** predict_publish (tuesday + refresh),
   postgame_ingest, weekly_update, settle_clv, capture_slot_close. Only
   ingest_odds is registered. Registering predict_publish means it can fire
   unattended. Registering the refresh deployments is what makes a real
   snapshot-age test possible later.

7. **W-RATINGS-WIRE.** `execute_predict_publish` never passes `filter_history`
   to `export_publish_artifacts`, so `team_ratings_2026.json` ships
   `teams: {}` (107 bytes) and Game Detail rating trajectories (§5.2) have
   never rendered. The exporter works when fed history — `v2/2024/w5`
   team_ratings is 991,889 bytes against 107-byte stubs elsewhere. The defect
   is the caller.

8. **Read-only R2 token.** No public read path: `lib/artifacts/r2.ts` signs a
   GET with a server-side credential. `webapp/site/.env.local` holds
   placeholders and points at `ridge-artifacts-preview`; production bucket is
   `ridge-artifacts`. Every production artifact read on this workstation is
   currently a write-key operation.

9. **No retention of published artifacts, and versioned keys overwrite.**
   R2 `latest/` was the only record of what shipped until
   `data/results/latest_results_2026.json` was saved manually this session.
   §1.1 keys are `v{schema_version}/{season}/w{week}/{refresh_kind}/…` with no
   timestamp, so two publishes of the same (week, kind) collide —
   **confirmed**: `v1/2026/w1/tuesday_primary/week_predictions.json` holds
   Sept 1, and the Aug 25 launch object was overwritten in place (it survives
   only under `sandbox/v1/…`). Local `publish_history` is append-only and
   intact with per-game rows, so grading is unaffected; the loss is the
   published-artifact archive.

10. **Quarantine not enforced.** `is_quarantined` has zero call sites under
    features/ratings/evaluation. Quality flags partitions and the rating
    engine reads them anyway. Decide: refuse, warn, or rescope.

11. **build_meta** silently substitutes literal `"2024-08-01T12:00:00Z"` when
    `identity["registered_at"]` is missing (export.py ~line 978). Should raise.

12. **`ensemble_weight_dampen = 0.7`** documented in §9.7, unimplemented in
    `src/`. Unverified — §9.7 is in the main design doc, not the webapp
    DESIGN.md, which ends at §6.

## Facts established this session (don't re-derive)

- Webapp lives at `webapp/site`, not `webapp/`.
- `filter_history` membership: `data/artifacts/state_space/filter_history.parquet`,
  137 team_ids, seasons 2014–2025. 49 of 99 week-1 games have an absent side;
  names in `pooled_prior_teams.txt`.
- Two 2026 `classification=fbs` teams are absent from filter_history:
  16 Sacramento State, 2449 North Dakota State.
- `data/webapp/publish_history/2026_w1.jsonl`: 3 lines — Aug 25
  tuesday_primary (99), Aug 27 daily_refresh (99), Sept 1 tuesday_primary
  (91). Per-game rows, 39 fields, post-rename names. Nothing Aug 28 or Aug 29.
- `grade_export` in `src/ncaa_quant/webapp/grade.py` (288–339) →
  `history_records_for_grade` → local jsonl. Selection in
  `select_pre_kickoff_publish` (51–88), sorts by
  (REFRESH_KIND_PRECEDENCE, published_at) desc. Precedence in export.py
  233–238. Cannot observe R2 overwrites; history is local append-only.
- Week 14 is absent from `results_2026.json` entirely (weeks 1–13 and 15
  present). Unexplained — CFBD schedule quirk or an export gap. Unchecked.
- Aug 27 daily_refresh **did** run. An earlier handoff said Thu/Fri/Sat were
  all missed; only Fri Aug 28 and Sat Aug 29 were.

## Tooling (untracked, read-only, stdlib-only)

- `scripts/analyze_grading.py` — per-week diagnostics on a results file:
  kickoff histogram, MAE by date and snapshot age with bootstrap CIs,
  Wilson-interval coverage, null audit, top misses, `--fcs-teams` split.
- `scripts/regrade_paired.py` — paired snapshot comparison from
  publish_history. `--inspect` first to see snapshot shapes.
- Baselines on disk: `w1_live.json`, `w1_split.json`, `regrade_w1.json`.

## Positioning, undecided — do not resolve incidentally

`/results` renders "NOT CURRENTLY FIT TO BET" verbatim from the frozen
23-readout: no edge demonstrated vs the close, ATS straddles 50%, CLV
unmeasurable. **Nothing today touches that.** Beating a home-field constant
on margin is not an edge against a market price, and the verdict rests on
CLV and ATS, neither of which was measured today. The social/Best Bets track
(ridge_social.py, select.py, render.py, golden fixtures) is fully built and
tested but has never run live — n_candidates=0 on every 2026 publish.
Publishing a Best Bets thread contradicts the verdict on the site it links
to. That is a deliberate decision requiring a call on the verdict banner,
not something to fold into a script run.
