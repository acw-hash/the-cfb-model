# TASK 23-FIX — Result validation and repair before Task 23 closes

**Do not close Task 23.** Three of its headline numbers are broken pipelines rather
than findings, two ablations are inert on real data, and an entire feature family was
missing from the run that produced every reported metric. The A2 result may survive;
everything measured against the market is on hold.

Same failure class as the shifted-label test: a number that is internally impossible
was reported as a result because nothing asserted it had to be possible.

---

```
TASK 23-FIX: Validate and repair the Task 23 results. Read @docs/DESIGN.md §1.6,
§2.6, §5.2, §7.2, §7.3, §12, §15 items 12, 19, 23, and docs/notes/23.md.

Task 23 is not closed. Work the P0 items in order — each one may invalidate the
numbers below it, so do not batch them. Report after each.

SANCTIONED EDITS:
  src/ncaa_quant/evaluation/production_stack.py    (calibration/conformal wiring)
  src/ncaa_quant/evaluation/backtest_runner.py     (provenance, CLV resolution)
  src/ncaa_quant/evaluation/metrics.py             (CI construction only)
  src/ncaa_quant/betting/clv.py                    (bet-time vs close resolution)
  src/ncaa_quant/data/schemas.py                   (roster negative-value bug)
  src/ncaa_quant/features/builders/roster.py       (only if the fix requires it)
  tests/
  scripts/_task23_backtest.py                      (deletion, see P0-3)
  docs/notes/23.md                                 (amendment section)
STOP and report for anything else. Do not touch model hyperparameters, betting
thresholds, or feature definitions under any circumstance.

---

P0-1 — CALIBRATION PATH. Log-loss 1.05 at 49.4% accuracy is internally inconsistent.

Predicting 0.5 on every game yields log-loss 0.693. The market's 0.69 is consistent
with ATS-at-close being near a coin flip. A model at chance on accuracy but at 1.05 on
log-loss is making CONFIDENT WRONG predictions — the signature of uncalibrated
probabilities, not of a weak model.

1. Determine whether models/calibrate.py (isotonic per derived market) and
   models/conformal.py (CQR) are actually invoked in the production stack's predict
   path, or whether raw head output is going straight to the metrics. Report the call
   path with file and line references either way.
2. If they are absent, wire them — this is a Task 19 seam that 22B did not catch
   because 22B never checked a probabilistic metric against a baseline.
3. Report calibration slope and intercept on the backtest predictions BEFORE and
   AFTER, per Task 19 deliverable 3. Generate the reliability diagram and PIT
   histogram on a held-out season.
4. Re-run and re-report log-loss AND CRPS against the de-vigged market baseline.
   CRPS 10.28 vs 8.61 is contaminated by the same cause if calibration was missing —
   a mis-scaled predictive distribution degrades CRPS directly.
5. If the model still loses to the market after calibration, report that plainly.
   That is a legitimate finding. An uncalibrated loss is not.

P0-2 — CLV IS DEGENERATE. Mean 0.0 with a [0.0, 0.0] CI over n=64.

Zero variance in CLV across 64 bets against real closing lines is not possible. The
likely cause is that the bet-time line and the closing line resolve to the same row,
making CLV identically zero by construction — exactly what §7.2 item 7 forbids.

1. Diagnose and report: what line did each bet resolve to at bet time, and what at
   close? Show the resolution for five sampled bets.
2. Confirm whether any distinct bet-time price exists for 2019 at all, given there
   are no stored snapshots for that season.
3. STRIKE the CLV number from the results table and replace it with NOT COMPUTED plus
   the reason. Do not report 0.0. A zero with a zero-width interval will be read as a
   finding by someone who did not read the caveat.
4. Add a guard: the CLV computation raises rather than returning a value when bet-time
   and closing prices resolve to the same source row.

P0-3 — RUN PROVENANCE. scripts/_task23_backtest.py should not exist.

Task 22B built evaluation/backtest_runner.py and the `ncaa-quant backtest` CLI so that
Task 23 would need no driver, and Task 23's prompt said no new subsystems.

1. State whether that script calls the runner or reimplements the loop.
2. If it reimplements it: none of the runner's guarantees demonstrably apply to the
   reported numbers — run_id and ablation_id stamping, the four-hash manifest,
   determinism, resumability. In that case delete the script and re-run the full set
   through the CLI, and treat every number in docs/notes/23.md as unverified until it
   has been reproduced that way.
3. If it wraps the runner thinly: keep it, but show that every prediction row carries
   run_id and ablation_id and that each run wrote a complete manifest.
4. Either way, demonstrate determinism on one run: same config, same seed,
   byte-identical prediction table.

P1-4 — A1 AND A5 ARE INERT, AND THE 22B TESTS DID NOT CATCH IT.

Both switches passed 22B's no-op mechanism test and are no-ops on real data. The
fixtures carry garbage-time flags and roster-derived priors that the staged partitions
do not. The test guarded against a world that does not exist.

1. Add an INPUT PRECONDITION assertion to each ablation switch, checked at run time
   against the actual data, not in a fixture: A5 fails loudly if no garbage-time flags
   are present on the plays it is filtering; A1 fails loudly if the priors it is
   replacing are already degenerate. An ablation that cannot do anything must error,
   never report a delta of zero.
2. Move the no-op mechanism tests onto a slice of real staged data, or — if that is
   impractical — assert the precondition inside the fixture test so fixture-production
   drift fails the test rather than hiding under it.
3. Re-run A1 and A5 after P1-5. If they are still inert, report them as NOT RUN with
   the missing input named. Do not report an inert switch's delta as an ablation
   result.

P1-5 — THE ROSTER SCHEMA BUG CAVEATS EVERY NUMBER, NOT JUST A1.

"Schemas reject CFBD negatives -> skipped those endpoints" means returning production,
talent composite, 4-year recruiting, portal net, and coach features were absent from
the FULL SYSTEM, not only from the A1 ablation. Every headline metric came from a
system missing a feature family.

1. This is a Task 12 bug. Legitimate negative values (portal net rating, returning-
   production deltas) must validate. Task 12 deliverable 7 required null-with-
   indicator and forbade zero-fill; a schema that rejects real values violates the
   spirit of both. Fix the schema constraint, not the data.
2. Re-materialize the roster feature partitions and confirm the builders produce
   null-with-indicator where CFBD genuinely has no value.
3. Re-run the full set with roster features present. Every number in the memo is
   superseded by this run.
4. Record in the amendment that the originally reported metrics came from a system
   missing this family, so the two result sets are never conflated.

P2-6 — THE CONFIDENCE INTERVAL IS TOO NARROW.

At n=865 and p=0.494 the naive normal-approximation 95% interval is roughly
[46.1%, 52.7%]. The reported [47.4%, 51.8%] is about +/-1.3 SE, nearer 80% coverage.
A block bootstrap on correlated data should come out WIDER than naive, not narrower.
Report the confidence level in use, the block construction and block length, and the
naive interval alongside the bootstrap one for every headline proportion.

P2-7 — SEPARATE A2'S COMPONENTS BY BASIS.

MAE and CRPS need no lines and can span all seasons; ATS needs lines and can only be
2019. Report the season list and n for EACH component of the A2 result separately.
As written they read as one result on one basis.

P2-8 — RE-SCOPE THE 5B ESTIMATE.

The 54,090-credit estimate was against a full 2021-2025 backfill, and the notes record
a 16,000 ceiling against an actual budget of 20,000 with 24 used. Reconcile that
figure, then run `--estimate` across a scope ladder and report a cost table:
  - 2021-2025, all books, all snapshot intervals   (the 54,090 baseline)
  - one season, one book, one decision point per game
  - two seasons, one book, one decision point per game
  - 2024-2025 only, one book, opening + decision point
Report which rungs fit under the remaining budget. Do not purchase anything. A
reduced scope that validates the bet layer on one season is worth more than an
unvalidated bet layer, and that trade is my call to make.

ACCEPTANCE:
- Calibration call path reported; slope/intercept before and after; reliability and
  PIT plots; re-run log-loss and CRPS vs the de-vigged market
- CLV struck and replaced with NOT COMPUTED plus reason; guard test passing
- Run provenance established; determinism demonstrated on one run
- A1/A5 precondition guards in place; both re-run or reported as NOT RUN
- Full set re-run with roster features present; superseding numbers reported
- CIs reported at a stated confidence level, with naive and bootstrap side by side
- A2 components reported per basis with season lists and n
- 5B scope/cost table
- make lint typecheck test pass

EXPLICITLY FORBIDDEN:
- Adjusting any hyperparameter, threshold, prior weight, or filter cutoff toward a
  §1.6 target. If the system misses, it misses.
- Reporting an inert ablation's delta as a result.
- Rewriting docs/notes/23.md. Append an AMENDMENT section; the original numbers and
  the reasons they were wrong stay in the record.

In the amendment, state the conclusion the results support. If ATS remains at chance
and the probabilistic scores still lose to the market after calibration, the honest
conclusion is that the system is NOT CURRENTLY FIT TO BET — which is a stronger and
more useful statement than "misses §1.6," and is the one that protects real money.
```
