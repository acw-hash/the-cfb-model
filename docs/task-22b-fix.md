# TASK 22B-FIX — Leakage-test repair and run-set cost estimate

**Do not commit Task 22B until this passes.** Two acceptance numbers from the 22B run
do not hold up: the shifted-label test is reporting a broken pipeline as a pass, and
`backtest plan` is costing one walk-forward pass rather than the eight Task 23 needs.
Three smaller items are unevidenced. This task fixes exactly those and nothing else.

---

```
TASK 22B-FIX: Repair the Task 22B leakage test and cost estimator. Read
@docs/DESIGN.md §14, §1.4, §2.6, §5.2, §7.2, and docs/notes/22b.md.

Task 22B is not committed. Two of its acceptance results are wrong and must be fixed
before it is. This is diagnosis and repair only: no new capability, no tuning, no
touching model or feature logic.

SANCTIONED EDITS — this task may touch only:
  tests/leakage/                                   (shifted-label test)
  src/ncaa_quant/evaluation/backtest_runner.py     (plan/estimate path only)
  src/ncaa_quant/evaluation/production_stack.py    (only if the root cause is here)
  configs/                                         (run-set definition for plan)
  docs/notes/22b.md                                (append an amendment section)
Anything else is a STOP-and-report.

---

BLOCKER 1 — The shifted-label test is broken, not passing.

Reported: MAE 15348.57 against a chance baseline of 6.75, passing on the condition
"does not beat chance." Both the numbers and the condition are wrong.

1. DIAGNOSE BEFORE YOU FIX. Write the root cause in docs/notes/22b.md before changing
   any code, and show the evidence for it. Two things are independently wrong:
   (a) MAE ~15,000 on a football margin is not a model performing badly — it is a
       model receiving a degenerate input. Check, in this order: is the feature matrix
       all-NaN or all-zero at the shifted timestamps; are columns misaligned past the
       signature contract; is the head fitted at all; are the predictions in margin
       units or something else. Report which it was.
   (b) A chance baseline of 6.75 MAE is better than any real margin model should be.
       A league-mean predictor on CFB margins lands near 13-14. Whatever 6.75 is
       measuring, it is not chance on margin. Report what it was actually computing.
   If the root cause turns out to sit outside tests/leakage/, name the file and STOP
   rather than editing outside the sanctioned list.

2. Define the chance baseline by construction, not by constant. It is a predictor
   returning the training-set mean margin, scored on the exact same game set, with
   the same metric and units as the model under test. Compute it in the test; never
   hardcode it.

3. Replace the pass condition with a TWO-SIDED tolerance band around that baseline.
     - materially BETTER than chance  -> FAIL (leakage: future features are informing
       past outcomes)
     - materially WORSE than chance   -> FAIL (wiring bug: the model is not receiving
       usable features, and a broken pipeline cannot certify a working one)
     - within the band                -> PASS
   Set the band from the sampling noise of the metric on the evaluated game count, not
   from a round number chosen to make the current result pass. State the band and its
   derivation in the test docstring. The one-sided condition currently in the test
   would pass a predictor returning infinity — say so in the notes so the failure mode
   is on record.

4. Re-run the test against the production stack and report the achieved MAE and the
   baseline, both as numbers.

5. AUDIT FOR THE SAME PATTERN. Grep the test suite for any other assertion whose pass
   condition is one-sided in the "worse is acceptable" direction — parameter recovery,
   coverage, innovation variance, monotonicity, calibration. List every one found and
   whether it needs the same two-sided treatment. Fix only those inside tests/; report
   any others.

BLOCKER 2 — `backtest plan` is costing one run, not the run set.

Reported: week_units=105, retrain_points=21, estimated_wall_clock_sec=367.5 for
seasons 2019-2025. That is a single walk-forward pass, and the timing is roughly two
orders of magnitude under the §1.4 budget of 8 hours for a full historical backtest.

1. The Task 23 run set is EIGHT runs, not one:
     - fundamental, full system
     - market-aware, full system
     - A1 priors off
     - A2 rating updates frozen at Week 1
     - A3 market features off
     - A4 single LightGBM
     - A5 garbage-time filter off
     - A6 CFBD open/close vs snapshots — 2021-2025 ONLY, so this is not a clean
       multiple of the others
   Define the set in configs/ as named runs. `plan` takes a run set, not a single
   config, and reports per-run and total.

2. Fix the timing methodology. The estimate must be grounded in a MEASURED week-unit
   against the real production stack — including the members that actually cost time
   per §5.2 and §2.6: CatBoost and NGBoost, the 100k-draw Monte Carlo, and the 50
   epistemic draws through the rating posteriors. If the 367-second figure came from
   timing a path where those were absent or short-circuited, say so explicitly; that
   is a finding about the wiring, not just about the estimator.

3. Report the measurement basis alongside the estimate: which week-unit was timed,
   on what hardware, with which members active, and the extrapolation arithmetic. An
   estimate whose basis is not stated is not a cost gate.

4. Do not make the number smaller. If the honest full-set estimate exceeds the §1.4
   8-hour budget, report it as-is. That is a real input to whether all six ablations
   run at full walk-forward or some run on a reduced season set — and that is my
   decision, not yours.

UNEVIDENCED ITEMS — confirm or report missing, with file and symbol references.

5. Deliverable 6: does the registry's promotion gate resolve its comparison runs from
   logged MLflow runs, or is it still being handed metric dicts? An MLflow run ID
   proves tracking fired; it does not prove the gate reads from it.

6. Deliverable 7: does the line-source fallback ladder exist in the harness (snapshot
   at decision point -> nearest earlier within tolerance -> null with indicator,
   logged per game, CFBD open/close excluded for snapshot-backed seasons), and are
   line_source and n_books_available written on every prediction row? If not, A6 and
   the bet-layer backtest stay unmeasurable even after Task 5B, and that must be
   stated plainly in the notes.

7. The wiring-proof run produced n_predictions=910 for 2023, just above the ~800-900
   FBS band Task 5 verified. Reconcile it: confirm no FCS-vs-FCS games (a §1.5
   non-goal) and no duplicated week. Report the game count by week.

8. Per-module test coverage for evaluation/production_stack.py and
   evaluation/backtest_runner.py, reported separately. The 80.42% aggregate can pass
   while this task's new code is thinly covered.

ACCEPTANCE:
- Root-cause statement for the shifted-label failure, written before the fix
- Shifted-label test passing on a two-sided band, with achieved MAE, computed chance
  baseline, and the band's derivation all reported as numbers
- The one-sided-assertion audit list
- `backtest plan` for the eight-run set, with per-run and total wall clock, and the
  measurement basis stated
- Answers to items 5, 6, 7, 8 with references
- make lint typecheck test pass

EXPLICITLY FORBIDDEN:
- Widening a tolerance, relaxing a threshold, or reframing a pass condition to make a
  currently-failing thing pass. If something will not pass honestly, report it.
- Making the wall-clock estimate smaller by removing work from the timed path.
- Reporting any Task 23 metric, or any accuracy/CRPS/CLV number from the 2023 wiring
  proof.

Append an AMENDMENT section to docs/notes/22b.md — do not rewrite the original notes.
Record both root causes, the corrected numbers, and the fact that 22B was reported as
passing on a broken leakage test. That last point stays in the record: it is the
clearest evidence in the project that a green test suite is not the same as a working
one, and Task 23's memo will be read more carefully because of it.
```
