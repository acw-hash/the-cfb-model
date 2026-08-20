# W9-CQR — refit CQR on the real quantile heads

**Date:** 2026-08-19  
**Status:** Named successor. Not started. Post-launch.  
**Authority:** W9-D Amendment 2; `docs/notes/webapp-w9int.md`;
`docs/notes/webapp-w9d.md` Amendment 1 § Training / CQR support.

This is a record, not an implementation. No model, calibrator, CQR
constant, quantile head, export, schema, or `/results` change lives here.

---

## Defect

The champion CQR 80% add (**6.837**) was fit on **placeholder Gaussian
bands around OOF μ**, then applied at predict time to the LightGBM
**q10/q90** heads. The constant cannot encode quantile-head skew. W9-D
week 1 showed `q90 < μ` on 19 / 91 cupcake blowouts; the export gate
(W9-D Amendment 2) nulls those bands rather than inventing a new
threshold.

## Job (when opened)

Refit CQR against the real quantile heads. Re-measure coverage. Do not
adopt a new constant because it looks closer to 0.80 on one table.

## Baseline (W9-INT, n=4,743, N_2025=0, thr=6.837)

| construction | n | hits | coverage | below lo | above hi |
|---|---:|---:|---:|---:|---:|
| **published** sorted q10/q90 ± 6.837 | 4,743 | 4,147 | **0.874** | 307 | 289 |
| raw sorted q10/q90, no CQR add | 4,743 | 3,566 | **0.752** | 601 | 576 |
| μ ± 1.28σ (Gaussian) | 4,743 | 3,867 | **0.815** | 475 | 401 |

Full breakouts: `docs/notes/webapp-w9int.md` §1 and §5.
JSON: `docs/notes/_artifacts/webapp-w9int/coverage.json`.

Empirical coverage **0.874** is also the 23-readout UNMEASURABLE cell.
It is not on `/results` until a post-week-1 restamp.

## Out of scope until this task is assigned

Fit, retrain, promotion, registry change, live CQR swap, schema bump,
or any change to the Amendment 2 coherence gate.
