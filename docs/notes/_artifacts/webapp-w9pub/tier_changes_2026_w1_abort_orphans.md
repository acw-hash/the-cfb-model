# tier_changes.jsonl — 2026 week-1 abort orphans

**Source file:** `data/webapp/tier_changes.jsonl` (append-only; do not edit in place)

**Orphan batch:** lines **224–322** (99 lines, contiguous)

**Stamp:** `published_at = 2026-08-25T13:35:44Z`

**Cause:** First live `tuesday_primary` attempt (`data/tmp/publish_w1_live.log`) finished
predict at that instant, then export refused with slate regression (stub history still
held synthetic game `401000001`). No R2 push; no publish_history append.

**Disposition:** Orphans retained as evidence. **Do not delete** these lines.

**Success batch (for contrast):** lines **323–421**, `published_at = 2026-08-25T13:51:33Z`
(retry in `data/tmp/publish_w1_live_retry.log`).

**Tier values:** Documented in `docs/notes/webapp-w9pub.md` — abort and success batches
carry identical tier maps; no value drift.
