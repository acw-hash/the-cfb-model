# S2 — Social thread + reply-bank generator

**Date:** 2026-08-23  
**Status:** Complete  
**Authority:** `docs/social/TASKS-social.md` S2; scope amendment (site/R2 stay
forecasts-only; social layer is private, local-only, human-in-the-loop).

---

## Built

| Module | Role |
|--------|------|
| `src/ncaa_quant/social/select.py` | `BestBet`, `select_best_bets()` — public bar |
| `src/ncaa_quant/social/render.py` | Thread + reply-bank rendering |
| `scripts/ridge_social.py` | Thin CLI over select/render |
| `tests/unit/test_social_select.py` | Selection unit tests |
| `tests/unit/test_social_render.py` | Golden / N=0 / stale+σ / ≤280 / no-network |
| `tests/fixtures/social/` | 2024 w5 sidecar + golden thread/replies |
| `docs/notes/social-s2.md` | This note |

Draft `docs/social/ridge_social.py` was hardened and moved — not rewritten.
Logic lives in the package so S3 can call `select_best_bets` without the CLI.

### CLI

```
uv run python scripts/ridge_social.py \
  --predictions webapp/fixtures/week_predictions.json \
  --sidecar tests/fixtures/social/w2024_w5_candidates.json \
  --now 2024-09-24T10:00:00Z \
  --out out/social
```

Primary input is the S1 sidecar (`accepted` / `rejected` arrays). Separate
`--candidates` / `--rejected` list files remain as a fallback for hand-written
weeks. `--now` freezes post-time for dry-runs/tests.

Thresholds / units come from `load_config().social` (`public_min_edge_*`,
`unit_fraction`) and `betting.max_bets_per_week` — not module constants in the
script. S3 overrides by passing a `SocialConfig` copy into `select_best_bets`.

---

## Decisions / ambiguities

1. **`A_TIER_EDGE` (0.060)** stays a module constant in `select.py`. It is a
   display label cut from the playbook, not a SocialConfig field S3 calibrates.
2. **σ-refused reply branch** triggers on `sigma_margin_credible is False` **or**
   `mu_margin is None` (draft only checked the latter). Stale accepted
   candidates that fail the public bar appear as forecast-only when present in
   `rejected` with `stale_inputs`, else default plain English.
3. **Fixture banner** is required in both output files when
   `week_predictions.fixture` or the sidecar `fixture` flag is true (plus
   stderr warn). Banner text is ASCII-stable for golden diffs.
4. **Character counts** use Python `len` (Unicode code points), matching the
   draft. X’s grapheme counting can differ slightly for emoji badges.
5. **No `configs/social.yaml` yet** — defaults remain on `SocialConfig` (S3).

---

## Acceptance

| Criterion | Result |
|-----------|--------|
| Golden thread + reply bank on 2024 fixture week | Covered |
| Every fixture-week thread post ≤ 280 chars | Covered |
| N=0 → No-Bet post only | Covered |
| Stale + σ-refused excluded from card, correct in reply bank | Covered |
| Script performs no network calls | Covered (`socket` monkeypatch) |
| `make test` green | **979 passed**, 1 deselected (2026-08-23) |

---

## Follow-ups (not S2)

- S3: calibrate `public_min_edge_*` from 2019–2024 walkforward; write
  `configs/social.yaml`.
- S4: `make social-week` + runbook.
- Real `build_candidates` (post-S1) so live publishes produce non-empty sidecars.
