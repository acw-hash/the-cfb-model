# W4-4b — social render bugs (defer)

**Date:** 2026-09-23  
**Scope:** hand-edited `out/social/2026_w4/thread_w4.md` tonight; **no**
`src/ncaa_quant/social/render.py` changes.

## Bugs observed

1. **UTC day labels.** `kick_et_label` formats `datetime.fromisoformat(kick_utc)`
   with `%a` in UTC, not `America/New_York`. Northwestern @ Indiana
   (`2026-09-26T00:00:00Z` = Thu 20:00 EDT) rendered as **Sat**; Massachusetts
   @ Sacramento State (`2026-09-27T01:00:00Z` = Sat 21:00 EDT) rendered as
   **Sun**. Fix: convert to `DECISION_POINT_TZ` before `%a`.

2. **Unrounded model lines.** Thread Ridge lines use `fmt_line` / raw
   `model_line` floats (e.g. `-0.0407666`, `+11.482`). Operator card wants
   1-decimal rounding; `|model_line| ≤ 0.5` → copy as **pick'em**.

## Tonight

Hand-fixed in `thread_w4.md` only. Revisit render in a dedicated task.
