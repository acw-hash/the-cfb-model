# W7-DIAG — W6 vs W7 card logic

Read-only diagnosis. Artifacts: `docs/notes/_artifacts/w7-diag/`
(`REPORT.md` + JSON dumps). Worktree: `C:/Users/alecw/Projects/the-cfb-model-w7-diag`
@ `bcfff2b`.

## Finding
Small W7 card edges come from the **intended HFA ROBUST dual gate**
(μ_home−4.56), not a model/sigma change. Disagreement W6→W7 is flat;
drop was W5→W6. `model-hfa-v4-wip` is not reachable from main.

## Commits
- W6 card: `40cd8b5` (candidates_w6 n=6); posted n=7 adds Alabama
  `cap_override` in `bb8847f`.
- W7: `4fc5cb0` freeze + `bcfff2b` shop.
