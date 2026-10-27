# W7-BT — Which HFA rule does the backtest support?

Read-only. Artifacts: `docs/notes/_artifacts/w7-bt/REPORT.md`,
`docs/notes/_artifacts/w7-bt/part2/REPORT.md`.

## Finding (part 1)
Walk-forward with historical market lines **exists**
(`task23_fundamental_reduced_v3` grade_v2). Under the pre-registered
decision rule, **keep dual-gate C** (no change). D has better pooled ROI
but wins only 2/5 seasons.

HFA 4.56 is Enet intercept × stack weight from MODEL-HFA-3 (no 95% CI;
full overlap with scored seasons). Live posted ledgers grade **15-9-1**
(matches thread; operator 21-10-1 unexplained by ledgers).

## Finding (part 2 — signal vs market)
**Signal: no.** Grading audit PASS. Live = same `production-v0_reduced_v3`
identity as WF. Model MAE worse than market; μ coef vs market ≈0
(t=0.25); edge deciles flat/inverted; rule-A CLV mean −0.34 pts;
live posted CLV mean −0.04. Model excess HFA over market ≈ **+0.89 pts**
(CI 0.63–1.15), much smaller than the 4.56 card shift. Change nothing.
