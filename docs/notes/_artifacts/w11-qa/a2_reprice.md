# W11-QA A2 — W10 card reprice vs Mac

**Overall:** `PASS`

## Snapshot

- W10-3 snapshot staged here: **no**
- Snapshot used: `odds_allbooks_20261104T141200Z.json`
- Path: `docs/notes/_artifacts/social-s6-w10-card/odds_allbooks_20261104T141200Z.json`
- Newest `event_time` in window: `2026-11-04T14:11:55+00:00`
- Cutoff: Sat Nov 7 11:30 AM ET (`2026-11-07T16:30:00+00:00`)
- Week predictions: `data/webapp/publish_history/2026_w10.jsonl (record 1)` as_of `2026-11-04T14:03:14.629521+00:00`

## Method

- Production card math: `model_line_home = -μ`, `gap = |model_line_home − home_line|`, edge via seeded Monte Carlo `spread_cover_probs` + `compute_edge` (seed `42 + game_id`).
- Market terms = **posted** card line/price (Mac targets are at those lines). Opposing American from all-books two-way when present (else −110).
- FAIL if any side differs or any gap differs by > 0.25.

## Per-play

| matchup | side | book | posted | gap | Mac gap | Δgap | edge | Mac edge | verdict |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| Texas @ Missouri | Missouri | fanduel | +2.5/-104 | 6.83 | 6.83 | +0.0000 | 18.6% | — | **PASS** |
| Georgia @ Ole Miss | Ole Miss | espnbet | +3.5/-105 | 6.19 | 6.19 | +0.0000 | 14.9% | — | **PASS** |
| Alabama @ LSU | LSU | draftkings | -6/-108 | 5.62 | 5.62 | +0.0000 | 13.8% | — | **PASS** |
| North Carolina @ UConn | UConn | fanduel | +7.5/-102 | 4.77 | 4.77 | +0.0000 | 13.3% | — | **PASS** |
| Arkansas @ Auburn | Auburn | betmgm | -10/-108 | 4.25 | 4.25 | +0.0000 | 9.0% | — | **PASS** |
| Texas State @ Oregon State | Texas State | betmgm | +5.5/-108 | 3.71 | 3.71 | +0.0000 | 8.0% | — | **PASS** |
| Kentucky @ Tennessee | Kentucky | hardrockbet | +6.5/-105 | 3.44 | — | — | 8.3% | 8.3% | **PASS** |

## Notes

- Mac Nov 7 commits / `posted_calls_2026_w10.json` / `_s6_w10_sat_card.py` are not on this machine (`origin/main`).
- Cadence DK/FD/MGM continued to `2026-11-04T20:00:03.244387Z`, but espnbet/hardrockbet last appear in the W10-2 all-books stage; that file is the named snapshot.
- Allbooks quote lines for LSU/Auburn/Texas State/UConn differ from posted (Nov 4 vs Nov 7 card); gaps above use **posted** lines so they are comparable to Mac.
