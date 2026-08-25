# RidgeCFB — X Playbook (v1)

Companion to `scripts/ridge_social.py`. The site stays forecasts-only; the X
account is where lines, edges, and Best Bets live. Bio links the site;
pinned thread explains the split.

---

## 1. Best Bet selection rule

**Source of truth:** accepted candidates from `apply_bet_filters()` in
`predict_publish` (Tuesday primary run), i.e. every candidate has already
passed the private §12 filters:

| Private filter (already applied) | Meaning |
|---|---|
| `edge >= min_edge_sides / min_edge_totals` | Positive de-vigged edge vs market |
| `expected_value > 0` | Positive EV at the actual price |
| `is_stale == false` | Odds inputs fresh (< 6h) |
| `qb_status_known == true` | No QB uncertainty |
| `model_market_residual_points <= min_model_market_agreement` | Sanity band — huge model/market gaps are rejected, not celebrated |
| Weekly / per-team exposure caps | Bankroll discipline |

**Public bar (applied on top, by the script):**

1. `edge >= PUBLIC_MIN_EDGE` — default **0.045 sides / 0.055 totals**
   (tune from the 2019–2024 walkforward so a normal week yields ~3–10).
2. Prediction row for the game has `sigma_margin_credible == true` and no
   stale stamp at post time.
3. Kickoff is in the future at post time.
4. Sorted by `edge` desc; hard cap = `betting.max_bets_per_week`.

**Count is whatever qualifies.** 3 qualify → post 3. Zero qualify → post the
No-Bet post (template T6). Never pad to hit a number.

**Public labels (map edge → rating, keep it simple):**

| Rating | Edge | Copy |
|---|---|---|
| 🅰️ | ≥ 6.0% | "one of the strongest edges of the season so far" |
| 🅱️ | 4.5–6.0% | "clear model edge" |
| (no C tier posted) | below public bar | stays private / reply-bank only |

**Units:** `units = stake_fraction / 0.005` (1u = 0.5% bankroll). Quarter-Kelly
with the 1.5% hard cap means units run 0.5–3.0. Always show units — it's the
honest answer to "how much are you on it?"

---

## 2. Weekly schedule (all times ET)

| Day | Trigger | Post |
|---|---|---|
| **Tue ~9–11 AM** | `tuesday_primary` (06:00 UTC) | Anchor Best Bets thread (T1–T5). Pin it until Sunday. |
| **Wed 12 PM** | — | Methodology / explainer single post, or a poll ("Which Best Bet are you tailing?"). |
| **Thu–Sat AM** | `daily_refresh` | Only if something changed: line moved through our number (CLV brag, T7) or a bet is pulled (stale/QB news, T8). Silence otherwise. |
| **Sat 9–10 AM** | `t_minus_6h` | Gameday recap: today's card in one post, quote-tweeting the anchor thread. |
| **Sun ~12 PM** | `grade_export` | Results post (T9): W-L, units, CLV, season running record. Misses included, always. |
| **Mon** | — | One lookahead teaser ("model's early lean list drops Tuesday") + engage replies. |

Cadence rule: **max 2 original posts/day** outside the anchor thread. Replies
are unlimited — replies are the growth engine.

---

## 3. Anchor thread anatomy (Tuesday)

**T1 — Hook (first post, does the algorithmic work):**
```
Week {W} Best Bets are in. 🏈

{N} plays cleared the model's bar this week ({record_szn} on the season, {clv_szn} avg CLV).

Every pick, the number, and why — thread 🧵👇
```

**T2..T(N+1) — One bet per post (quote-able unit):**
```
Best Bet #{i} — {rating}

{away} @ {home}
▸ Market: {side_team} {market_line}
▸ Ridge: {side_team} {model_line}
▸ Edge: {edge_pct}% | {units}u

{why_one_liner}
```
`{why_one_liner}` examples (rotate, keep < 15 words):
- "Model has this {gap} points off the market and the number's been steady."
- "Market's still pricing last month's version of this team."
- "Ratings moved after {team}'s last two; the line didn't."

**T(N+2) — Methodology-lite (trust post):**
```
How these get picked, in plain English:

▸ A statistical model projects every game (margin + uncertainty)
▸ We compare to the market after removing the vig
▸ Only games with a real gap AND sane inputs qualify
▸ Some weeks that's 3. Some weeks it's 10. We don't force it.

Full methodology → {site_url}/about
```

**T(final) — CTA / engagement engine:**
```
That's the card. Tail, fade, or tell me I'm wrong. 👇

Want the model's read on a game NOT listed? Drop the matchup below — I'll reply with the number.

Full forecasts, intervals + track record: {site_url}
```

**T6 — No-Bet week (replaces thread when N=0):**
```
Week {W}: the model found ZERO bets that clear our bar.

Yes, really. The market priced this slate tight.

Most accounts would post 10 picks anyway. We'd rather be flat than wrong on purpose. Forecasts for every game still live at {site_url}.
```
(This post historically outperforms pick posts on engagement. Do not skip it.)

**T7 — CLV move (Thu–Sat, optional):**
```
Posted {side_team} {posted_line} Tuesday. It's {current_line} now.

That's closing line value — the market moved toward our number. Doesn't guarantee a win Saturday, but it's how you know the process is real.
```

**T8 — Pulled bet (integrity post):**
```
Pulling Best Bet #{i} ({matchup}).

{reason — e.g. "QB status went from probable to questionable and the model's edge assumed he plays."}

Down to {N-1} plays this week. We'd rather pull it than pretend.
```

**T9 — Sunday grading:**
```
Week {W} Best Bets: {w}-{l} ({units_net:+.1f}u)

{one line per bet: ✅/❌ {side} {line} — final {score}}

Season: {szn_w}-{szn_l} ({szn_units:+.1f}u) | Avg CLV: {clv:+.2f}

Every result, graded and public: {site_url}/results
```

---

## 4. Reply macros ("what does the model say about X?")

The script writes `replies_w{W}.md` with a ready blurb for **every game on
the slate**. Three cases:

**R1 — Game is a Best Bet:**
```
It's on the card 👀 — Best Bet #{i}: {side_team} {market_line} (model: {model_line}, {edge_pct}% edge). Full thread pinned.
```

**R2 — Forecast only, no bet (uses FilterReason honestly):**
```
Model: {favored_team} by {mu:.1f} (80% range: {lo:+.0f} to {hi:+.0f}).

No bet though — {reason_plain}. Forecast ≠ edge.
```
`reason_plain` mapping:
| FilterReason | Plain copy |
|---|---|
| `edge_too_small` | "the market has this priced about right" |
| `non_positive_ev` | "no positive EV at current prices" |
| `model_market_disagree` | "model and market are so far apart it usually means the market knows something we don't — auto no-play" |
| `qb_status_unknown` | "QB situation is unclear and we don't bet through that" |
| `stale_inputs` | "our odds feed was stale at decision time — no bet on stale data" |
| tier suppressed / σ refused | "model won't even give a confident number here (not enough signal)" |

**R3 — Game not in publish (already kicked, FCS, etc.):**
```
That one's outside this week's publish (kicked off before our Tuesday decision point / not in the slate). Everything we do forecast is at {site_url}.
```

Reply SLA: within ~2h during Tue–Sat daytime. Reply to *every* game question
in week 1–4; it trains the algorithm and the audience.

---

## 5. Engagement mechanics (why each piece exists)

- **One bet per post** → each is independently quotable/shareable into that
  fanbase's conversation. A single card image can't be.
- **Numbers in every post** (edge %, units, CLV) → differentiates from
  vibes-based pick accounts; screenshots age well when you're right.
- **Public grading with misses** → the Sunday post is the retention product.
  Accounts die from hiding losses, not from having them.
- **The "ask me about your game" CTA** → converts passive readers into
  repliers; replies are weighted heavily by the X algorithm and the reply
  bank makes each answer 20 seconds of work.
- **No-Bet weeks** → the single strongest brand post available. Scarcity =
  credibility = follows.
- **Polls on Wednesday** → cheapest engagement of the week; also market
  research on which bets your audience cares about.
- **Pinned post** → season track record + methodology link + disclaimers.
  Update after each grading week.

---

## 6. Guardrails (non-negotiable)

- Bio and pinned post carry: **21+ | not financial advice | 1-800-GAMBLER**.
- Never DM picks, never sell picks, never do "🔒 lock" language — the brand
  is the anti-tout.
- Never edit/delete a graded pick. Pulled bets get a T8 post, not silence.
- No injury speculation about named players beyond "status unclear."
- The site remains picks-free; counsel reviews the X plan alongside the site
  (DESIGN §6.3 L1–L3 still open).

---

## 7. Script I/O quick reference

```
python scripts/ridge_social.py \
  --predictions artifacts/latest/week_predictions.json \
  --candidates artifacts/latest/accepted_candidates.json \
  --rejected artifacts/latest/rejected_candidates.json \
  --record artifacts/social/season_record.json \
  --site-url https://ridge.example.com \
  --out out/social/
```

Outputs: `thread_w{W}.md` (anchor thread, ready to paste) and
`replies_w{W}.md` (reply bank for the whole slate).

One small backend addition needed: `predict_publish` already computes
`accepted` / `rejected` candidate lists — persist them to JSON next to the
webapp export (5-line change alongside `export_publish_artifacts`) with the
market/model lines and `stake_fraction` attached. Until then the script
accepts a hand-written candidates file with the same fields.
