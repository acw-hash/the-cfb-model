# Fix B — STOP: interval gate is coherence, not filter_history

**Date:** 2026-09-08  
**Status:** Stopped per task rule. No code change. No publish.

## What the code actually does

Margin intervals are nulled in `apply_margin_interval_coherence_gate`
(`src/ncaa_quant/webapp/export.py` lines 317–337), called from game build at
lines 730–737:

- If CQR `lo`/`hi`/`nominal` are all absent → stay null.
- If LightGBM q10/q90 heads are missing → keep the CQR bounds (no failure).
- If heads are present and **`not (q10 < mu < q90)`** (strict) → return
  `(None, None, None)` for the interval fields.
- `null_reason` is copied unchanged from the prediction row
  (`export.py` ~782: `"null_reason": _field(row, "null_reason")`).
  The coherence gate does **not** set it.

`filter_history` is used only for `build_team_ratings` (lines 1242+), not for
interval emission. There is no rating-history membership check on this path.

Unit evidence: `tests/unit/test_webapp_w9d.py` —
`test_coherence_gate_nulls_incoherent_margin_interval` (μ=47.6 outside
q10/q90 → interval null, `null_reason` left None).

## Correlation vs cause

Absent-side teams in `filter_history` may **produce** extreme μ / crossed
heads upstream, which then fail the coherence gate — but the export decision
condition is **quantile-head coherence**, not membership. Task stop rule
applies.

## String that should be present (when fixed later)

Not `no_rating_history` as the primary code-faithful value. The gate that
fires is coherence, so the exported `null_reason` that Game Detail's footnote
should show is along the lines of:

**`incoherent_margin_interval`**

(display via existing `nullReasonFootnote` → `"incoherent margin interval"`).

If a later task also wants to surface the upstream rating-history correlation,
that would be a separate reason or a documented alias — do not invent
`no_rating_history` on a path that never checked membership.

Until that populate lands, the string that is wrongly present is JSON `null`
on `null_reason` for those 15 rows.
