# NCAA Football Prediction System — Complete Architecture & Implementation Specification

**Document type:** Master design document (PRD + Technical Design + ML Design + Implementation Plan)
**Intended reader:** An autonomous coding agent and its human supervisor
**Hardware assumption:** Single workstation, NVIDIA RTX 4070 (12GB VRAM), local storage
**Status:** Implementation-ready

---

## 0. Executive Summary and Core Architectural Thesis

Before the individual deliverables, the single most important design decision in this document must be stated plainly, because everything else follows from it.

### 0.1 The central thesis: separate the *state* from the *mapping*

Most amateur sports-prediction systems conflate two fundamentally different learning problems:

1. **Estimating the current state of the world** — how good is each team's offense/defense *right now*, what is the QB worth, how strong is home-field advantage. This state changes weekly and must be updated after every game.
2. **Learning the mapping from state to outcomes** — given two teams' ratings and contextual factors, what is the distribution of the final score. This mapping is *stable across seasons*. The relationship between "Team A is +7 in adjusted efficiency" and expected margin does not materially change between Week 1 and Week 10, or even between 2019 and 2025.

The naive approach — retrain one monolithic model every week on all data including the new week — mixes these problems. It is statistically wasteful (the mapping doesn't need weekly relearning), risky (weekly retraining on ~60 new games invites variance in model behavior), and slow to adapt (a monolithic model dilutes one week's information across millions of parameters).

**The recommended architecture is therefore two-stage:**

- **Stage 1 — Dynamic State Layer.** A Bayesian state-space rating system (Kalman-filter family) that maintains posterior distributions over each team's latent offensive, defensive, and special-teams strength, plus pace and home-field parameters. This layer updates *after every game* via a closed-form (or approximate) Bayesian update. It is where all in-season learning lives. Preseason priors (returning production, recruiting, transfers, prior-season ratings) initialize the state; the Kalman gain governs how fast beliefs move; process noise governs how much teams can drift within a season. This is the same structural idea behind Glickman & Stern's state-space model of NFL scores (JASA, 1998), FiveThirtyEight's Elo systems, and the dynamic component of Bill Connelly's SP+.
- **Stage 2 — Supervised Mapping Layer.** Gradient-boosted decision tree models (LightGBM primary, XGBoost/CatBoost as ensemble diversity) that consume Stage-1 ratings *as features*, along with matchup, situational, market, and roster features, and output **full predictive distributions** of home score and away score (equivalently margin and total). This layer is retrained on a slow cadence (2–4 times per season, plus a full offseason retrain), because it learns season-invariant structure. Between retrains it automatically produces better predictions each week simply because its *inputs* (the ratings) have absorbed the new games.

This decomposition directly satisfies the "adaptive but not overreactive" requirement in a statistically principled way: adaptation happens through Bayesian filtering with explicit uncertainty, not through ad-hoc weekly refits.

### 0.2 The second thesis: model the market, not just the game

For *betting* performance (as opposed to pure forecasting), the strongest known result in sports analytics is that **the closing line is the best publicly available single predictor of outcomes**, and beating it consistently (positive Closing Line Value) is the most reliable indicator of long-run profitability — far more reliable than short-run ROI, which is dominated by variance. Consequences:

- The system must produce two prediction modes: **fundamental** (no market features; used for rating integrity, totals structure, and honest self-evaluation) and **market-aware** (opening line, line movement, and market-implied probabilities as features; the model effectively learns the *residual* between market and truth). The market-aware model is what generates bets.
- The primary evaluation metric for the betting layer is **CLV** (did our bet beat the closing number), with ROI/profit tracked as secondary, high-variance confirmation.
- Bet selection uses edge thresholds and **fractional Kelly** staking, never full Kelly (Section 7).

### 0.3 The third thesis: respect the data regime

College football produces ~800 FBS games per season with high scoring variance (single-game margin SD ≈ 15–16 points even conditioning on true team strength), ~134 FBS teams, massive roster turnover, and enormous talent spread. Across 2014–2025 that is roughly **~10,000 FBS games**, not 20k. This is a **small-to-medium tabular data problem with heavy noise**, not a deep-learning problem. The empirical literature is unambiguous here: on tabular datasets of this size, gradient-boosted trees match or beat deep tabular architectures (Grinsztajn, Oyallon & Varoquaux, NeurIPS 2022; Shwartz-Ziv & Armon, 2021), while costing orders of magnitude less to tune and being far more robust. Deep models (FT-Transformer, TabNet, GNNs, TFT) are catalogued honestly in Section 4 and assigned to the research track (Section 13) — they are not in the production critical path. The RTX 4070 is well used for parallel Optuna sweeps, NGBoost/quantile ensembles, Monte Carlo simulation, and research experiments — not for forcing transformers onto ~10,000 rows.

### 0.4 System at a glance

```
                        ┌─────────────────────────────────────────────┐
                        │              ORCHESTRATOR (Prefect)         │
                        └─────────────────────────────────────────────┘
   ┌──────────┐   ┌──────────────┐   ┌───────────────┐   ┌───────────────────┐
   │ INGESTION │→ │ DATA QUALITY │→ │ FEATURE STORE │→ │  STATE LAYER       │
   │ CFBD API  │  │ Great Expect.│  │ (DuckDB +     │  │  Kalman ratings    │
   │ Odds API  │  │ + custom     │  │  Parquet,     │  │  off/def/st/pace   │
   │ Weather   │  │  validators  │  │  point-in-time│  │  posterior + var   │
   │ Rosters   │  └──────────────┘  │  correct)     │  └─────────┬─────────┘
   └──────────┘                     └───────┬───────┘            │
                                            ▼                    ▼
                                    ┌──────────────────────────────────┐
                                    │  MAPPING LAYER (retrained slowly)│
                                    │  LGBM margin μ,σ │ LGBM total    │
                                    │  quantile heads  │ NGBoost dist  │
                                    │  calibration (PIT / distributional)│
                                    └───────────────┬──────────────────┘
                                                    ▼
                        ┌────────────────────────────────────────────┐
                        │ SIMULATION & BETTING LAYER                 │
                        │ bivariate score simulation → ATS/ML/OU     │
                        │ probs → edge vs lines → EV → Kelly stakes  │
                        └───────────────────┬────────────────────────┘
                                            ▼
                        ┌────────────────────────────────────────────┐
                        │ REPORTING / MONITORING / CLV TRACKING      │
                        └────────────────────────────────────────────┘
```

---

## 1. Product Requirements Document (PRD)

### 1.1 Purpose

Build a fully automated, continuously learning NCAA FBS football prediction and betting-analysis system that, for every FBS game each week, produces calibrated predictions of: game winner, moneyline win probability, expected score for each team, expected total, full joint score distribution, ATS cover probability against any given spread, over/under probability against any given total, prediction intervals, model confidence, edge versus current sportsbook lines, expected value per bet, and recommended stake sizing — and that measurably improves as the season progresses by incorporating each completed week's evidence without overreacting to noise.

### 1.2 Users and use cases

- **U1 — Operator (you):** reviews weekly prediction reports, bet recommendations, and monitoring dashboards; approves/overrides bets; triggers manual retrains.
- **U2 — Coding agent / developers:** extend the system; require reproducible pipelines, tests, docs.
- **U3 — The system itself:** automated jobs consume upstream artifacts (ratings, features, models) with versioned contracts.

### 1.3 Functional requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| F1 | Ingest schedules, results, play-by-play, and advanced box scores for all FBS games from ≥2014 (PBP-quality era) to present, updating within 12h of game completion | P0 |
| F2 | Ingest opening lines, current lines, closing lines (spread, total, moneyline) from ≥2 books; snapshot line movement at ≥4 points in the week | P0 |
| F3 | Maintain Bayesian dynamic ratings (offense, defense, special teams, pace, HFA) with posterior uncertainty, updated after every completed game | P0 |
| F4 | Recompute all opponent-adjusted features weekly with strict point-in-time correctness | P0 |
| F5 | Produce joint (margin, total) predictive distributions per game; derive all bet-type probabilities from the same distribution (internal consistency) | P0 |
| F6 | Produce calibrated probabilities (post-hoc calibration validated on held-out seasons) | P0 |
| F7 | Compute edge, EV, and fractional-Kelly stake vs. live lines; apply configurable bet filters | P0 |
| F8 | Full walk-forward backtesting engine reproducing exactly what the system would have known at any historical (season, week, timestamp) | P0 |
| F9 | Weekly automated pipeline (Sec. 10) with failure recovery, alerting, and manual-review gates | P0 |
| F10 | Experiment tracking (MLflow), data versioning (DVC), model registry with promotion/rollback | P0 |
| F11 | Injury/roster ingestion and QB-out adjustments | P1 |
| F12 | Weather ingestion and totals adjustments | P1 |
| F13 | CLV tracking per bet; weekly CLV report | P0 |
| F14 | Preseason prior generation from returning production, recruiting, transfers, coaching changes | P0 |
| F15 | HTML/Markdown weekly report with predictions, edges, confidence, and rating movements | P1 |
| F16 | Research harness for candidate models/features with statistical significance testing vs. champion | P1 |

### 1.4 Non-functional requirements

- **Reproducibility:** bit-for-bit reproducibility applies to **inference and walk-forward replay given fixed model artifacts** (pinned by content hash), together with (git SHA, DVC data hash, config hash, seed). Training and HPO are **reproducible-to-artifacts**: every trial's params and seed are logged; final champion refits run with deterministic settings (CPU or framework deterministic mode); the artifact hash is the reproducibility anchor. Asynchronous parallel TPE is not run-order deterministic — the search itself is not the unit of bit-for-bit replay (see §6). All randomness is seeded and logged.
- **Point-in-time correctness:** *the* cardinal rule. No feature may use information unavailable at prediction time. Enforced by construction (as-of joins on timestamps) and by automated leakage tests.
- **Performance:** full weekly pipeline < 2h wall-clock on the workstation; full historical backtest (10 seasons) < 8h; single-week inference < 5 min.
- **Reliability:** any ingestion failure degrades gracefully (predictions still produced from last-good data, flagged as stale).
- **Extensibility:** adding a new feature family, model, or data source touches only its own module plus registry entries.
- **Auditability:** every published bet recommendation stores model version, feature vector hash, line at bet time, and closing line.

### 1.5 Explicit non-goals (v1)

Player-level tracking data; FCS-only games (FCS opponents modeled via a pooled FCS-tier rating); live in-game betting; automated bet placement (recommendations only — also keeps the project clearly legal/ToS-safe); multi-user web service.

### 1.6 Success criteria

- **Primary:** mean same-book, line-translated CLV of recommended bets > 0 with 95% CI excluding 0 over ≥300 bets settled at `clv_settlement=same_book` (probability space when `clv_method` is probability-valued; see §2.7). Fallback-consensus CLV rows (`clv_settlement=fallback_consensus`) are reported separately and never enter this criterion. **`line_shopping_capture` is excluded from the skill criterion** — it measures execution/shopping value at bet time, not model skill vs the closing market.
- **Secondary:** fundamental-model ATS accuracy ≥ 51.5% and totals ≥ 51.5% over multi-season walk-forward backtest (52.4% is breakeven at −110; the fundamental model alone is not expected to clear breakeven every season — the market-aware layer is); Brier score on moneyline ≤ market-implied Brier − ε on backtest or within noise of it; calibration slope in [0.9, 1.1].
- **Process:** zero leakage-test failures; pipeline completes unattended ≥ 90% of weeks.
- **Confirmatory instrument:** live forward performance via paper-trade (§16 item 2) is the confirmatory check on these success criteria — backtest/promotion gates are necessary but not sufficient; a full (or half) season of paper-traded CLV is what confirms the criteria under live information flow.

A candid statement of uncertainty, per the brief: **no design guarantees profitability.** CFB sides at major books are efficient enough that sustained edges are small (historically, published academic edges cluster at 1–3% ROI and decay). The realistic goals are (a) a genuinely well-calibrated forecaster, (b) positive same-book CLV through informational speed (injuries, early-week lines, smaller totals/derivative markets), and (c) an honest measurement apparatus that tells you quickly if (a)/(b) fail.

---

## 2. Part 1 — Prediction Problem Definition

### 2.1 Formal setup

For game *g* between home team *h* and away team *a* at kickoff time *t*:

- **Inputs** `x_g`: Stage-1 rating posteriors for both teams (means and variances of off/def/ST/pace), matchup features (rating differentials, tempo interaction, style crosses), situational features (rest, travel, altitude, surface, weather forecast, neutral-site flag, week-of-season, conference game flag, rivalry flag), roster features (QB status, injury-adjusted deltas, returning production, talent composite), and — in the market-aware variant — market features (opening spread/total, current line, movement, implied probabilities, cross-book dispersion).
- **Primary targets:** home points `S_h` and away points `S_a`. Everything else — winner, margin `M = S_h − S_a`, total `T = S_h + S_a`, ATS cover vs spread `s`, over vs total `τ` — is a *deterministic functional of the joint distribution* `p(S_h, S_a | x_g)`.

### 2.2 Regression vs classification: model scores, derive everything

**Decision: model the joint score distribution; never train separate binary classifiers for ATS/OU as primary models.**

Justification:

1. **Statistical efficiency.** A binary ATS label discards the magnitude of cover/non-cover. With ~800 games/season, throwing away information is unaffordable. Margin regression uses the full signal; the ATS probability at any spread falls out as `P(M > −s)` — and it works for *any* spread, including line movement after prediction, alternate lines, and teasers, without retraining.
2. **Internal consistency.** Deriving ML, ATS, and OU from one joint distribution guarantees the predictions cannot contradict each other (a classifier stack can simultaneously say "70% to win" and "45% to cover −1").
3. **Label noise.** ATS labels are defined relative to a moving market number; the label itself depends on which snapshot you use. Scores are ground truth.

**Direct classification is retained only as (a) a diagnostic baseline and (b) a stacked meta-learner input in the research track.**

### 2.3 Parameterization of the joint distribution

Compare three options:

- **(A) Independent Poisson/negative-binomial per team** (soccer-style Dixon–Coles). Poor fit for football: scoring is clumpy (3s and 7s), overdispersed, and margin/total have known discreteness at key numbers.
- **(B) Bivariate normal on (M, T)** with heteroskedastic, model-predicted `μ_M, σ_M, μ_T, σ_T` and estimated correlation `ρ(M,T)` (empirically small but positive in CFB, ~0.05–0.15; estimate it, don't assume 0). Continuous scores are then discretized by rounding through a **key-number mass adjustment**: an empirical margin-distribution kernel learned **conditional on the predicted margin** — at minimum by buckets of `μ_M`; preferably a smooth model of `P(M = k | μ_M, σ_M)` — reallocates probability mass to exact margins (3, 7, 10, 14…). An unconditional residual kernel is rejected: key-number mass depends on where the continuous predictive sits (a −3 favorite is not a −20 favorite). Key numbers matter less in CFB than NFL (more variance, higher totals) but are not negligible; the conditional kernel handles this without hand-tuning. **Validation:** empirical exact-margin frequencies by predicted-spread bucket must be compared to kernel output (same buckets); material divergence is a misspecification alarm, not a silent acceptance.
- **(C) Full discrete simulation** — simulate drives/possessions. Highest fidelity ceiling, very high complexity; deferred to research track (Sec. 13).

**Decision: (B) as production system, with the conditional empirical discretization kernel.** It is tractable, produces exact probabilities for any line via 100k-draw Monte Carlo (or 2-D numerical integration), naturally yields prediction intervals, and lets the mapping layer predict *both mean and variance* (heteroskedasticity is real: mismatched-tempo games, extreme weather, and huge favorites have different variance).

### 2.4 Multi-task structure

**Decision: one multi-output system with shared features, separate heads.**

- Margin head: LightGBM regression for `μ_M`; second LightGBM (trained on squared/absolute residuals or via NGBoost) for `σ_M`.
- Total head: same structure for `μ_T, σ_T`. **ATS and totals share the feature pipeline and rating inputs but use separate boosted models** — margin is driven by *differences* in team quality, totals by *sums* and tempo, and forcing one tree ensemble to serve both empirically hurts each (their split structures differ). Sharing happens at the representation level (Stage-1 ratings), which is where multi-task transfer genuinely helps.
- Moneyline: **not a separate model.** `P(win) = P(M > 0)` from the margin distribution, then post-hoc calibrated (Sec. 2.6). A separate ML classifier would reintroduce inconsistency for no gain; backtests in the literature and in analogous NFL work show derived win probabilities from good margin models are as accurate as direct classifiers once calibrated.

### 2.5 Latent team ratings: yes, and they are the backbone

Learned latent ratings (Stage 1, full spec in Sec. 9) are strictly superior to raw rolling stats as model inputs because they (a) perform opponent adjustment coherently and jointly, (b) carry calibrated uncertainty, (c) incorporate priors, and (d) update at game granularity. Raw/rolling features are *also* fed to Stage 2 so the trees can learn corrections the linear state-space model misses (nonlinearities, style matchups).

### 2.6 Probabilistic forecasting, calibration, uncertainty

- **Probabilistic forecasting is mandatory**, not preferred — betting decisions are functions of full distributions (EV and Kelly need probabilities, not point estimates).
- **Aleatoric uncertainty** (irreducible game randomness): the predicted `σ` heads. This dominates in football.
- **Epistemic uncertainty** (rating uncertainty): Stage-1 posterior variances propagate into Stage 2 as features and inflate predictive variance early in the season and for teams with unstable situations (new QB). Implementation: for each game, draw the **two competing teams' rating blocks jointly** from the corresponding block of the Stage-1 **joint** posterior (preserving their cross-team covariance — not independent marginal draws) → push through mapping → mixture predictive distribution (cheap: 50 posterior draws).
- **Calibration:** **distributional recalibration** of the predictive distributions (margin and total separately), not separate per-market probability maps. A single monotone map is fit on the PIT values of the OOF margin predictive CDF (and one for total), then applied to the continuous distribution so that *all* derived market probabilities (ML, ATS at any line, OU at any line) recalibrate coherently and preserve the §2.2 internal-consistency guarantee. Per-market reliability diagrams and Cox slope/intercept remain **diagnostics only** — they are never the fitting target. For the **fundamental** stack, calibration targets are market-free (moneyline / distribution PIT only); ATS@close reliability is reported as a diagnostic, not fit. Fit only on out-of-sample predictions — never on training folds; refit at each scheduled retrain. Thin-data fallback: a parametric (e.g. Beta / Platt-on-PIT) monotone map when OOF n is too small for nonparametric isotonic-on-PIT.
- **Prediction intervals:** primary intervals from the parametric predictive distribution; a **conformal layer** (Vovk; Romano et al.'s CQR variant on the quantile heads) wrapped on top. Split-conformal coverage is guaranteed only under exchangeability; season-over-season drift in CFB violates that assumption, so the layer provides **approximate** coverage and is monitored weekly (empirical coverage vs nominal at 50/80/95%). **Production variant: Adaptive Conformal Inference (ACI)** — online α adjustment that tracks realized coverage under non-exchangeability. **Initializer:** trailing-2-season split conformal / CQR sets the starting conformity scores and α before ACI takes over. Report parametric and conformal intervals; alert if they diverge materially (a symptom of misspecification) or if weekly conformal coverage drifts outside tolerance.

### 2.7 Labels — precise definitions

- Scores: final official score including OT. (Model OT-inclusive scores; the market does too. A research flag exists for regulation-only modeling + explicit OT simulation — theoretically cleaner for totals, deferred.)
- ATS label (evaluation only): cover vs. the **closing consensus spread**; pushes excluded from accuracy, counted as stake-returned in ROI.
- **Closing line (definition):** the last captured snapshot strictly before kickoff from the designated reference book set, sourced from The Odds API historical/live snapshots where available (2020-06-06+) and from CFBD's `close` field otherwise. The two definitions do **not** agree exactly. Where both exist, store both and record the divergence; systematic divergence beyond a documented tolerance is a data-quality finding, not something to average away. Every reported CLV states which close definition it used.
- **CLV (Closing Line Value) — settlement book.** CLV is settled against the closing price **at the same book** whose price was used to place/recommend the bet. That book identity is stored on the recommendation. If that book's close is missing, the row is flagged `clv_settlement=fallback_consensus`, settled against consensus close for diagnostic reporting only, and **never pooled** with `clv_settlement=same_book` rows in headline CLV, §1.6 success criteria, or promotion gates. Same-book rows carry `clv_settlement=same_book`.
- **CLV — probability definition (same line).** On the bet side, de-vigged proportionally:
  `CLV = implied_prob(close_at_bet_line) − implied_prob(bet_price)`,
  where both implied probabilities refer to **the bettor's actual ticket line** (the spread or total number on the ticket), not merely the book's closing *price* at whatever line the book closed at. Settling against a moved line's price without translating to the ticket line is forbidden: it does not price the ticket that was bet.
- **CLV — line translation (spreads and totals).** When the book's closing *line* differs from the bet line, compare probabilities **of the same line**. Translate the closing market to the bet's line using, in priority order:
  1. **`alt_line_price`** — the book's captured alternate-line price at the bet line from closing (or nearest pre-kick) snapshots, if present; then de-vig and take implied prob at that price.
  2. **`model_dist`** — evaluate the model's own margin (sides) or total (OU) predictive distribution at both the bet line and the closing line; convert the line difference into a probability shift and apply it so the close is expressed at the bet line. The absolute CLV remains `implied_prob(close@bet_line) − implied_prob(bet_price)` after translation.
  3. **`line_units`** — if neither (1) nor (2) is possible, report CLV in **line units** (points of close movement toward the bet) rather than probability space, with the conversion method recorded per row. Line-unit CLV is reported separately from probability-space CLV and does not enter the §1.6 primary criterion.
  The method used is stored on every settlement row as `clv_method` ∈ {`alt_line_price`, `model_dist`, `line_units`, `same_line`} (`same_line` when bet line equals close line and no translation was required). Moneyline bets have no line to translate; they use same-book closing ML price with `clv_method=same_line`.
- **`line_shopping_capture` (separate metric, not CLV).** Defined as
  `implied_prob(best captured price at bet time) − implied_prob(consensus price at bet time)`
  on the bet side, de-vigged proportionally, both at the **bet-time** line/price snapshot (not the close). Reported alongside CLV so shopping/execution value is never conflated with model skill vs the closing market. Excluded from the §1.6 primary skill criterion.
- Garbage time: labels are *never* garbage-time adjusted (bets settle on real scores); *features* are (Sec. 4).

---
## 3. Part 2 — Data Sources Catalog

Legend: **Depth** = usable historical coverage; **Value** = expected marginal predictive value given the rest of the stack (H/M/L); **Cx** = engineering complexity.

### 3.1 Core game & efficiency data

| Source | Contents | Availability / Cost | Licensing | Depth | Update freq | Value | Cx |
|---|---|---|---|---|---|---|---|
| **CollegeFootballData.com (CFBD) API** | Schedules, results, PBP, drives, advanced box scores (EPA, success rate, explosiveness, havoc, field position, finishing drives), SP+ ratings, Elo, talent composite, returning production, recruiting, transfer portal, betting lines, venues, coaches, rosters | Free tier + Patreon (~$5–15/mo) for higher rate limits | Personal/research use permitted; check ToS for redistribution | PBP quality good 2014+, usable 2004+ | Same-day post-game | **H — this is the backbone; ~80% of the system's data** | L |
| `cfbfastR` (R) / `cfbd` (Python client) | Client libraries + EPA model over CFBD PBP | Free, open source | MIT-style | Same | Same | H (use Python client; optionally re-derive own EPA — see 3.6) | L |
| ESPN (scrape/API endpoints) | FPI, box scores, injuries (partial), depth charts (partial) | Free, unofficial endpoints | Scraping — fragile, ToS gray zone | FPI 2005+ | Daily | M (FPI is a strong prior ensemble input) | M |
| **SP+ (via CFBD)** | Connelly's opponent-adjusted ratings incl. preseason projections | Included in CFBD | As CFBD | 2005+ (preseason 2015+) | Weekly | **H — best public single rating; external benchmark always; §9.6 prior-blend component for the market-aware stack only (never fundamental priors or fundamental Stage-2 features — ADR 0003)** | L |
| Team talent composite (247 via CFBD) | Roster talent index | CFBD | As CFBD | 2015+ | Annual | H for priors | L |
| Returning production (via CFBD) | % returning offense/defense production | CFBD | As CFBD | 2014+ | Annual (preseason) | **H — the single best-documented preseason predictor delta** | L |
| Recruiting (247 Composite via CFBD) | Class rankings, blue-chip ratio | CFBD | As CFBD | 2002+ | Annual | H for priors (4-yr weighted) | L |
| Transfer portal (CFBD) | In/out transfers w/ ratings | CFBD | As CFBD | 2021+ (portal era) | Rolling | M-H, growing; short history is a real limitation — model as prior adjustment with wide uncertainty | M |

### 3.2 Market data

| Source | Contents | Cost | Depth | Update | Value | Cx |
|---|---|---|---|---|---|---|
| CFBD lines | Open/close spread, total, ML from several books | Included | 2013+ (spotty early) | Post-week | **H for backtesting** (closing lines) | L |
| **The Odds API — live** | Live odds snapshots, ~40 books (soft books only; no Pinnacle/Betfair), spread/total/ML | **20K plan, $30/mo = 20,000 credits/mo.** Credits ≠ requests: live cost = `markets × regions`. Our config (3 markets × 1 region) = 3 credits/call → ~540 credits/mo at 6×/day | From subscription start only — **live snapshots cannot be backfilled** | Real-time | **H — enables CLV measurement and live edge computation** | M |
| **The Odds API — historical** | Point-in-time snapshots of the same board, wrapped in an envelope carrying `timestamp` / `previous_timestamp` / `next_timestamp` | Same key, paid plans only. Cost = `10 × markets × regions` → **30 credits/call** at our config | **From 2020-06-06.** 10-min snapshot intervals until Sept 2022, 5-min after. Book coverage grows over time | Static (backfill) | **H — the only source of realistic bet-time prices for backtesting** | M |
| Sportsbook APIs/scrapes (Pinnacle-style sharp reference via aggregators) | Sharp closing reference | Varies; Pinnacle API restricted | Limited | Real-time | H (best closing benchmark if obtainable; else consensus close) | M-H |
| Betting splits (public bet %/money %) | Action Network / covers-style splits | Paid, redistribution restricted, historical archives poor | Shallow | Daily | L-M — weak, noisy, hard to validate historically; **defer** | H |

### 3.3 Context data

| Source | Contents | Cost | Depth | Update | Value | Cx |
|---|---|---|---|---|---|---|
| **Open-Meteo / Meteostat** | Historical + forecast weather by stadium lat/lon (temp, wind, precip) | Free | Decades | Hourly | M — matters mainly for totals (wind >15mph, heavy precip); small but real | M |
| Venue table (CFBD + manual) | Lat/lon, altitude, surface, dome, capacity | Free | Static | Static | M (enables travel, altitude, surface features) | L |
| Travel distance | Haversine from campus/venue coords + time zones crossed | Derived | Full | Derived | M (documented small effect: long west→east 12pm ET games, etc.) | L |
| Rest days | Derived from schedule | Derived | Full | Derived | M (bye weeks, short weeks, post-rivalry lookahead) | L |
| Injuries | No good free historical CFB injury feed exists. Practical v1: **QB availability only**, from depth-chart scrapes + manual weekly review; log everything you capture to build your own history | Manual + scrape | Building from now | Weekly + gameday | H *for QBs specifically*; low/unknowable for others at v1 | H |
| Coaching/coordinator DB | Head coach + OC/DC tenures, scheme | CFBD coaches + manual OC/DC table | 2000+ (HC), manual (OC/DC) | Annual+ | M (change-points for ratings; first-year discontinuities) | M |
| Referee crews | Crew assignment + penalty/pace tendencies | Scraping, inconsistent sources | Poor | Weekly | L — genuinely uncertain literature in CFB (unlike NBA); **defer, log for research** | H |
| Polls/rankings | AP/Coaches/CFP | CFBD | Deep | Weekly | L as prediction feature (subsumed by ratings); M for "ranked-team public-side" market context | L |

### 3.4 Explicit availability warnings (honesty section)

- **Odds snapshot history begins 2020-06-06, and snapshot granularity changes mid-window.** Intervals are 10 minutes until Sept 2022 and 5 minutes after, so any as-of fallback tolerance must be ≥10 min for 2020–2022 or real snapshots get silently dropped. Since 2020 is already excluded from headline metrics (§7.2 item 5), the effective snapshot-backed window is **2021–2025 — five seasons**. Season 2019 has CFBD open/close only. Market-aware results before 2021 are a distinct regime and must never be pooled with 2021+.
- **Bookmaker coverage is not constant over time.** The API added books progressively, so a 2021 snapshot contains materially fewer books than a 2025 one. This biases (a) "best available price across books" (§12), which will appear to improve over time from coverage alone, and (b) cross-book dispersion (§4.5), which is not comparable across seasons. Both carry an `n_books_available` covariate and are reported per season.
- **No sharp reference book.** The Odds API covers soft books only. Same-book closes and any `fallback_consensus` close are soft-book numbers, not a Pinnacle close. CLV measured against soft closes is a weaker signal than CLV against a sharp close, and this must be stated wherever CLV is reported. Consensus close is **not** the primary CLV settlement instrument (§2.7); it is only the flagged fallback when the bet's book close is missing.
- **Sharp vs public money:** reliable, historical, licensed sharp-money data effectively does not exist at hobbyist/prosumer prices. Anyone claiming otherwise is selling something. Design the schema to accept it; do not depend on it.
- **Injuries below QB:** historical CFB injury data is so incomplete that training on it would inject leakage-adjacent bias (injuries get recorded more for teams that lost). v1 uses QB-status only, prospectively collected.
- **Portal data pre-2021** doesn't exist in comparable form; features must handle the regime change (era indicator, interaction with season year).
- **Any live-odds feature is only as deep as your own capture history.** The single most time-sensitive action in this entire project is standing up the odds snapshot job (Coding-agent Prompt #4) — do it before anything else that can wait.

### 3.5 Storage estimate

Ten seasons of PBP + drives + features + line snapshots ≈ 15–40 GB Parquet. Trivial for local NVMe; DuckDB queries it in seconds.

### 3.6 EPA note

CFBD ships EPA from cfbfastR's model. Use it for v1. Research-track option: refit your own EP model (multinomial next-score model on down/distance/field position/era) to control the era covariates and to compute *leverage-weighted* and *garbage-time-filtered* EPA exactly to your definitions. Marginal value: modest; scientific control: high.

---

## 4. Part 3 — Feature Engineering Specification

### 4.1 Principles

1. **Point-in-time by construction.** Every feature function has signature `f(entity, as_of_timestamp) → value` and may only read data with `event_time < as_of_timestamp`. The feature store enforces as-of joins; there is no code path that joins on game_id without a timestamp.
2. **Ratings first, raw stats second.** Stage-1 posteriors are the primary signal; raw aggregates exist to give trees residual structure to exploit.
3. **Every feature has a card**: definition, formula, lookback, prior/shrinkage, null policy, owner, and the hypothesis for why it should predict. Features without a hypothesis go to the research registry, not production (this is the primary defense against p-hacking a ~10k-row dataset).

### 4.2 Garbage-time filtering (applied before all efficiency aggregates)

Exclude plays where win probability > 0.98 or < 0.02 (from the EP/WP model), or use Connelly-style score-margin-by-quarter rules as fallback. Rationale: backup-QB stat-padding and prevent-defense plays are non-predictive noise; every serious CFB efficiency system filters them. Labels are never filtered (Sec. 2.7).

### 4.3 Opponent adjustment

Two mechanisms, used together:

- **Implicit (primary):** the Stage-1 state-space model *is* the opponent adjustment — each game's observed efficiency is decomposed into own-strength minus opponent-strength plus noise, jointly across the league.
- **Explicit (secondary, for raw stat features):** ridge-regression adjustment — solve `y_play_or_game = off_i − def_j + hfa + ε` per metric (EPA/play, SR, explosiveness, havoc, finishing) with L2 shrinkage toward zero (λ tuned by walk-forward CV), weekly. This is the standard "adjusted efficiency" construction (equivalent to adjusted plus-minus in basketball literature). Iterative averaging (opponent-of-opponent) is a worse estimator of the same quantity; use the regression.

### 4.4 Temporal aggregation

For each adjusted metric, compute (a) season-to-date shrunk mean, (b) EWMA with half-life tuned per metric family (efficiency metrics: half-life ≈ 5–8 games by validation; tempo: longer, it's stable; explosiveness: longer, it's noisy), and (c) last-3 flag deltas (recent form minus season, as a *small* feature the trees can use or ignore). **Bayesian shrinkage everywhere:** season-to-date means are shrunk toward the preseason prior with weight `n/(n+k)`, `k` tuned per metric (empirically lands ~6–10 games for efficiency metrics, consistent with published stabilization-point analyses). Multi-season carryover enters only through the prior (Sec. 9.6), never through raw cross-season windows — rosters turn over too much for a naive 20-game rolling window to be meaningful in CFB.

### 4.5 Feature families (production set, ~120–180 features before selection)

> **Market-feature availability contract.** Market features follow the same null-with-indicator discipline as portal features (§3.4): where no snapshot exists at the decision timestamp, the feature is null with an `is_missing` indicator. **Never** substitute the CFBD open or close as a stand-in for a missing intra-week snapshot — that is forward-looking and is leakage. Cross-book dispersion features additionally carry `n_books_available`, without which they are not comparable across seasons.

- **Rating features (core):** off/def/ST posterior means and SDs for both teams; differentials; sums (for totals); pace posteriors; rating × pace interactions; league-relative z-scores; conference-strength estimates; rating trajectory over last 4 updates (slope); **league scoring-environment posterior mean** (the identified level state from §9.3 — Stage-2 totals/efficiency context).
- **Efficiency features:** adjusted EPA/play (off & def, rush & pass splits), success rate, explosiveness (EPA on successful plays / IsoPPP-style), havoc rate (for & against), finishing drives (pts per trip inside 40), field-position margin, third/fourth-down rates, red-zone TD%, penalty rate. All garbage-filtered, opponent-adjusted, shrunk, in season-to-date + EWMA forms.
- **Tempo/possession features:** adjusted plays/game, seconds/play (situation-neutral), **expected possessions for this matchup** (regression on both teams' pace + pass rates — the key totals feature: totals ≈ possessions × points/possession), run/pass rate over expectation.
- **Matchup crosses (hypothesis-driven, not automatic):** off explosiveness × opp def explosiveness allowed; rush-lean offense × opp rush def EPA; pace mismatch (fast off vs slow def → possession expectation interaction); havoc def × sack-prone off. Feature crosses beyond trees' native interaction capacity are limited to these curated ~15; **automatic pairwise cross generation is rejected** for this data size (combinatorial multiple-testing disaster; trees already learn interactions).
- **Situational:** rest differential, short-week flag, bye flag, travel km, time zones crossed (+ direction), altitude delta, surface × team-surface-history, neutral site, week number, month, rivalry flag, post-rivalry/pre-rivalry lookahead flags, conference game, divisional implications flag; **rule-era categorical** (at minimum: `pre_2023_clock` vs `post_2023_clock`). Hypothesis: the 2023 NCAA clock-rule change (shorter play clock after certain stops, fewer clock stoppages) raised plays-per-game and scoring environment; era must be an explicit covariate so Stage 2 does not silently absorb a structural break into team ratings or season dummies.
- **Weather (totals-focused):** forecast wind speed (and wind × pass-rate-sum), precip probability × precip amount, temperature extremes, dome flag (zeroes weather features).
- **Roster/prior features:** returning production off/def, talent composite, blue-chip ratio, 4-yr weighted recruiting, portal net rating (2021+, era-flagged), QB status (starter/backup/unknown) + QB-value estimate delta, OL returning starts, HC/OC/DC tenure years, new-coordinator flags.
- **Market features (market-aware model only):** opening spread/total, current, consensus, movement open→now, cross-book SD, implied probabilities de-vigged, model-fundamental minus market (the residual target's own coordinates).
- **Uncertainty features:** both teams' rating posterior SDs, games played, FCS-opponent count (schedule informativeness).

### 4.6 Encodings, normalization, missing values

- Teams/conferences/coaches: **no high-cardinality one-hots into trees.** Teams are represented *by their ratings* (this is the correct "embedding" for this problem). Conference as small ordinal-by-strength + P5/G5 flag. Entity embeddings are a research-track item for neural models only.
- Trees need no normalization; the linear/NGBoost/meta models get standard-scaling fit on train folds only (leakage discipline).
- Missing values: explicit `is_missing` indicator + native LightGBM missing handling; *never* impute market features (missingness is informative — untracked games are a bet filter, not an imputation problem); weather imputed to climatological normals with indicator.

### 4.7 Feature store architecture

Parquet datasets partitioned by season/week, computed by pure functions in `features/`, registered in a YAML feature registry (name, version, dependencies, as-of semantics), queried via DuckDB with as-of joins. A dedicated `pit_audit` test suite recomputes a random sample of historical feature rows using only data time-stamped before the game and asserts equality with stored values — run in CI. (A hosted feature store like Feast is overkill for one machine; this design gives the same guarantees with 5% of the ops burden.)

---

## 5. Part 4 — Modeling Strategy

### 5.1 Honest comparison of model families

| Family | Strengths | Weaknesses | Interp. | GPU use | Train cost | Inference | Expected perf. here | Verdict |
|---|---|---|---|---|---|---|---|---|
| Elastic Net (on ratings + top features) | Unbeatable baseline, stable, interpretable, near-zero variance | No interactions/nonlinearity | High | – | Seconds | Instant | Surprisingly close to trees on margin (the problem is mostly linear in rating diffs) | **Keep — baseline + ensemble member + sanity anchor** |
| Random Forest | Robust, low tuning | Dominated by GBDT on accuracy; poor extrapolation | Med | – | Min | Fast | Below GBDT | Drop (redundant with GBDT) |
| **LightGBM** | SOTA tabular accuracy, native missing/monotone constraints, quantile objective, fast | Needs careful CV; can overfit small data w/o strong regularization | Med (SHAP) | Optional (CPU fine at this size) | Min | Instant | **Best expected point/quantile accuracy** | **Primary** |
| XGBoost | ≈LightGBM, different inductive bias | Slower | Med | Yes | Min | Instant | ≈ LGBM | Ensemble member (diversity) |
| CatBoost | Ordered boosting resists target leakage on categoricals, great defaults | Slower; categorical edge less relevant given rating-based team encoding | Med | Yes | Min–hr | Fast | ≈ LGBM | Ensemble member |
| **NGBoost** | Native distributional regression (predicts full Normal params via natural gradient) | Slower, finicky, weaker point accuracy than LGBM | Med | – | Hr | Fast | Best-in-class honest σ; μ slightly worse | **σ-head member / distributional cross-check** |
| Bayesian linear regression (hierarchical) | Principled uncertainty; partial pooling by team/season; the correct *rating* machinery | Not for the mapping layer's nonlinearity | High | – | Min (PyMC/NumPyro) | Fast | Core of Stage 1, not Stage 2 | **Stage 1 uses this family (state-space form)** |
| Gaussian Processes | Gold-standard small-data UQ | O(n³); kernel design nontrivial; ~10k×150 is past the sweet spot; SVGP loses the elegance | Med | Yes | Hr | Med | No advantage demonstrated over NGBoost+conformal here | Research only |
| BART | Excellent small-data accuracy + UQ in literature | Slow; ecosystem weaker (pymc-bart) | Low-Med | – | Hrs | Slow | Plausibly ≈ GBDT with better UQ; genuinely uncertain — flagged as *worth a research sprint* | Research (promising) |
| MLP / TabNet / TabTransformer / **FT-Transformer** / SAINT | Embeddings, multi-task heads | Grinsztajn et al. 2022 & Shwartz-Ziv 2021: GBDT ≥ deep tabular at n≈10⁴; tuning cost 10–50×; variance high | Low | Yes | Hrs–days | Fast | Expect −1 to +0.3 vs LGBM; upside only via embeddings/multi-task | Research; FT-Transformer is the one worth trying |
| DeepGBM / MoE | Complexity without demonstrated tabular-small-n wins | — | Low | Yes | High | Med | Unjustified | Reject |
| **GNN (team graph over schedule)** | Elegantly encodes schedule connectivity — but the Kalman layer already propagates information through the schedule graph optimally under its model[^joint-kalman] | Tiny graphs (134 nodes), sparse early-season connectivity | Low | Yes | Hrs | Fast | Likely redundant w/ Stage 1; honest uncertainty: could add value on style-matchup propagation | Research |
| TFT / LSTM / time-series transformers | Sequence modeling of team trajectories | Season sequences are length ~12 with regime breaks at season boundaries; state-space model is the right tool and is interpretable | Low | Yes | Hrs | Med | Poor fit | Reject for v1 |
| **Stacked ensemble** | Squeezes 0.5–2% from diverse members; standard in every winning tabular effort | Leakage risk if OOF discipline slips | Low | – | Cheap | Fast | Reliable small gain | **Production** |

[^joint-kalman]: "Optimally under its model" holds **only because the filter is joint** (§9.2): a single league state with full cross-team covariance, updated with both teams' measurements in one step. Independent per-team Kalmans do **not** propagate schedule information optimally — opponent adjustments would not flow through the rest of the league graph.

### 5.2 Recommended production ensemble

**Level 0 (all trained on walk-forward out-of-fold predictions only):**
1. LightGBM μ_M (margin mean) — with monotone constraints on rating differentials (a rating increase may never decrease predicted margin: cheap regularization + sanity guarantee).
2. LightGBM quantile set for margin (q ∈ {5,10,25,50,75,90,95}) → CQR conformal layer.
3. LightGBM μ_T, quantile set for total.
4. XGBoost and CatBoost μ_M, μ_T (diversity members).
5. Elastic Net μ_M, μ_T on the 30 strongest features.
6. NGBoost Normal(μ,σ) for margin and total (distribution cross-check + σ input).
7. σ-models: LightGBM heteroskedasticity head — see **Predictive variance** below (not a free-form residual fit).

**Level 1 (meta):** Simplex-constrained least squares stacking of level-0 μs, fit on OOF predictions per target: weights \(w_k \ge 0\), \(\sum_k w_k = 1\), solved as a constrained quadratic program (not plain NNLS with post-hoc renormalization — post-hoc norm does not recover the constrained optimum when the unconstrained NNLS solution lies off the simplex face). No intercept: the stack must remain a convex combination of member predictions so the ensemble cannot invent a location shift absent from every member; any residual systematic bias is the job of Level-2 distributional recalibration, not of a free intercept that would also destroy the soft-model-selection interpretation and chase noise with 4–6 correlated members on small \(n\) (Breiman 1996 stacking rationale, strengthened). Probability-space stacking (if ever used diagnostically) is likewise simplex-constrained, not unconstrained logistic.

**Predictive variance (specified exactly once here):**
(a) The σ-head is trained on **absolute residuals of the Level-1 stacked mean**, \(|y - \mu_{\mathrm{stack}}|\), on OOF rows, then multiplied by \(\sqrt{\pi/2}\) to yield an unbiased σ estimate under a Normal residual assumption (\(E[|Z|]=\sqrt{2/\pi}\) for \(Z\sim\mathcal{N}(0,\sigma^2)\)). Squared-residual / σ² targeting is rejected for v1 (heavier outlier sensitivity on CFB margins).
(b) Total predictive variance decomposes as
\(\sigma^2_{\mathrm{pred}} = \sigma^2_{\mathrm{aleatoric}} + \sigma^2_{\mathrm{members}} + \sigma^2_{\mathrm{Stage\text{-}1}}\),
where \(\sigma^2_{\mathrm{aleatoric}}\) is the squared σ-head output, \(\sigma^2_{\mathrm{members}} = \mathrm{Var}_k(\mu_k)\) is disagreement across Level-0 members (weighted by stacking weights), and \(\sigma^2_{\mathrm{Stage\text{-}1}}\) is the variance of the mapping-layer mean across Stage-1 posterior draws (§2.6 epistemic mixture). The σ-head is fit on residuals that **exclude** the epistemic components already counted: specifically on OOF residuals of the stacked mean against the realized outcome **net of the member-mean** — i.e. labels are \(|y - \mu_{\mathrm{stack}}|\) where \(\mu_{\mathrm{stack}}\) is the Level-1 combination of member means at fixed Stage-1 point estimates (no posterior draws in the σ-head training labels). Member-disagreement and Stage-1 draw variance are added afterward; they must not be double-counted inside the σ-head target.
(c) After Level-1 / variance assembly, conformal-check as before (CQR on quantile heads).

**Level 2:** Distributional recalibration per §2.6 — one monotone PIT map on the OOF margin predictive CDF and one on total — so every derived market probability recalibrates coherently; then bivariate assembly with estimated ρ; key-number kernel; Monte Carlo to bet probabilities. Per-market reliability diagrams (ML, ATS@close, OU@close) are diagnostics only. Fundamental-stack calibration targets are market-free (distribution / moneyline PIT); ATS@close reliability is never a fitting target for the fundamental stack.

**Two parallel instances** of this whole stack: **Fundamental** (no market features; no SP+ in priors — ADR 0003) and **Market-aware** (adds market features; SP+ allowed in the §9.6 prior blend; equivalently learns market residuals — see Sec. 13.1 for why this is the betting workhorse).

---

## 6. Part 5 — Hyperparameter Optimization Framework

- **Engine: Optuna** with TPE sampler (multivariate=True) + Hyperband/ASHA pruning via `LightGBMPruningCallback` analogs. Bayesian optimization over ~15-dim GBDT spaces is exactly Optuna's sweet spot; grid search is rejected (exponential waste), pure random kept as the null baseline Optuna must beat.
- **Objective:** mean walk-forward validation loss (CRPS for distributional models, pinball for quantile heads, MSE for μ-heads, log-loss for calibration-sensitive selection) averaged over the last 3 validation seasons — never a single season (season-level variance would select noise).
- **Nested structure:** HPO runs inside the training window only; the walk-forward test seasons are *never* visible to Optuna (this is the nested-CV requirement adapted to time series: outer loop = rolling origin, inner loop = HPO on trailing seasons within the training window).
- **Budget & parallelism:** LGBM CPU-parallel across trials (`n_jobs` per trial × 4 concurrent trials via Optuna's process-based parallelism, SQLite/journal storage); XGBoost/CatBoost trials on GPU (`device=cuda`) — the 4070 comfortably fits these models and enables 2× trial throughput; NGBoost CPU. Typical budget: 300–500 trials per head at seasonal retrains, 50-trial refresh at mid-season retrain gates. **Asynchronous parallel TPE is not run-order deterministic** (trial suggestions depend on which trials finished first); do not treat a re-run of the same study as bit-for-bit identical to a prior search. The reproducibility unit is the **logged champion artifact** (params + seed + deterministic refit → content hash), not the search trajectory — matching §1.4.
- **Reliability:** every study persisted (resume-safe), seeds = f(study_name, trial_number) logged to MLflow; each trial logs params, per-season losses, and feature-importance snapshot; early stopping on 200 rounds no-improvement inside each fit; global study stop via Optuna's `MaxTrialsCallback` + wall-clock guard.
- **Overfitting-to-validation guard:** final selection compares top-5 Optuna configs on a *quarantined* season not used in the study; if ranking is unstable, prefer the more regularized config (explicit tie-break rule, codified). The quarantine-tiebreak season is **2024** — it must remain distinct from the lockbox season (§7.2 item 9).

---

## 7. Part 6 — Validation Strategy & Part 7 — Evaluation Metrics

### 7.1 The validation contract

**Rule zero: no random K-fold, ever.** Games are temporally and cross-sectionally dependent (shared teams, shared season context, market co-movement); random folds leak future team-strength information into the past and overstate skill dramatically. Every number reported by this system comes from **simulated real-time forecasting**.

### 7.2 Validation layers

1. **Rolling-origin (walk-forward) season evaluation — the primary harness.** For each test season `Y` in {2019, 2021, 2022, 2023, 2024}: train mapping layer on seasons < `Y` (with the HPO nesting of Sec. 6), then replay season `Y` week by week: initialize Stage-1 priors from information available before Week 1 of `Y`; for each week, compute features as-of Tuesday, predict, record vs. lines-as-of-Tuesday and closing lines, then reveal results and update ratings. This reproduces exactly the production information set. Statistical validity: it is an honest estimate of the deployed system's risk because train/test respect the arrow of time and the *state* evolves exactly as it would live (Bergmeir & Benítez 2012 on temporal CV; Tashman 2000 on rolling-origin evaluation). **Season 2025 is excluded from this harness** — it is the lockbox (§7.2 item 9).
2. **Within-season weekly curves.** Report error by week-of-season to verify the core promise (Week 10 < Week 4 error) and to characterize early-season behavior (expect Weeks 1–3 to lean on priors with wider intervals).
3. **Slice holdout analyses (diagnostic, not selection):** by conference, P5 vs G5, favorites vs dogs, totals buckets, ranked games, bowls, playoffs, rivalry games, weather games. Purpose: detect systematic bias pockets (e.g., G5 totals miscalibration), which become bet filters — *not* separate models unless a slice shows persistent, multi-season, significance-tested failure.
4. **Bowls/playoffs:** evaluated but flagged as a distinct regime (opt-outs, layoffs, motivation). v1 ships with a bowl-uncertainty inflation factor + opt-out roster adjustment; bowl bets are gated behind a stricter edge threshold. Honest note: bowl opt-out effects post-2018 are real but the historical sample to model them is thin — this is stated uncertainty, handled with wider σ rather than invented point adjustments.
5. **2020 (COVID):** include for Stage-1 continuity (ratings must not have a hole) but **exclude from mapping-layer training loss and from headline metrics** (canceled games, empty stadiums with measurably altered HFA, opt-outs). Sensitivity run with-and-without to confirm the exclusion isn't doing hidden work.
6. **Preseason evaluation:** each walk-forward season also scores the Week-1 predictions in isolation — a direct audit of the prior-construction system.
7. **Backtesting of the betting layer:** bets simulated with realistic frictions — bet at the line snapshot actually stored (never the close), −110 unless captured otherwise, stake by fractional Kelly, limits ignored (flagged as optimism), CLV recorded per bet per §2.7 (same-book, line-translated; `clv_settlement` / `clv_method` on every row; `line_shopping_capture` reported alongside and never pooled into CLV). Output: equity curves, drawdowns, and bootstrap CIs on ROI and CLV (block bootstrap by week to respect intra-week correlation); CLV CIs and means stratified by `clv_settlement`, never mixing same-book with fallback-consensus.

8. **Line-source regime.** The harness records, per game and per decision timestamp, which source supplied the line (`odds_api_snapshot` vs `cfbd_close`) and how many books were available. Metrics that depend on bet-time price (CLV, bet-layer ROI, edge distributions, `line_shopping_capture`) are reported **separately** for snapshot-backed seasons (2021+) and CFBD-only seasons (2019), never pooled — pooling would silently mix two different measurement instruments. The line lookup resolves via as-of join with an explicit, per-game-logged fallback ladder: snapshot at the decision point → nearest earlier snapshot within tolerance → null with indicator. The CFBD open/close never enters this ladder for snapshot-backed seasons; substituting it would be a forward-looking substitution.

9. **Lockbox season (2025).** Season **2025** — the most recent completed FBS season at the time of writing (Aug 2026) — is a **lockbox**. It is excluded from **all** development, HPO, ablation, and promotion evaluations. It may be read **at most once per calendar year** for a confirmatory report only. Every lockbox read is logged in `docs/lockbox_access.md` (date, reader, purpose, git SHA of the code/config used, and a one-line summary of what was reported). The Task 18 quarantine-tiebreak season (**2024**) is a **different** season from the lockbox and must remain so if either designation is ever revised.

### 7.3 Metric hierarchy (which metrics matter most, and why)

**Tier 1 — decision metrics (what we optimize the system toward):**
- **CLV (mean, and % of bets with positive CLV)** — same-book, line-translated CLV per §2.7; the highest-signal, lowest-variance indicator of real edge; converges orders of magnitude faster than ROI. Headline figures use `clv_settlement=same_book` and probability-valued `clv_method` only; fallback-consensus and `line_units` rows are reported separately. **`line_shopping_capture`** is reported alongside CLV as a Tier-1 companion metric but is **not** a skill / promotion / §1.6 criterion.
- **CRPS** on margin and total distributions — proper scoring rule for the full predictive distribution; the single best "is the forecaster good" number.
- **Log loss / Brier** on ML, ATS@close, OU@close probabilities, *always reported alongside the market-implied baseline* (de-vigged closing probabilities) — the market is the yardstick, not a constant-probability strawman.

**Tier 2 — calibration & sharpness:** reliability diagrams (10 bins + LOESS), calibration slope/intercept, PIT histograms for the continuous distributions, interval coverage (50/80/95%) vs nominal, interval width (sharpness subject to calibration).

**Tier 3 — point diagnostics:** MAE/RMSE on margin & total (report MAE primarily; margin errors are heavy-tailed), ATS/OU/SU accuracy vs close.

**Tier 4 — economic simulation:** flat-stake ROI, fractional-Kelly (¼ and ½) bankroll simulation with bootstrap CIs and max-drawdown distribution; Sharpe-like ratio per bet.

**Anti-metric note:** raw win% on tiny bet samples is explicitly labeled noise in every report (at 52% true skill, even 500 bets give a ±4.4pp 95% CI); the reporting layer prints the CI next to every rate to enforce this culturally.

---

## 8. Part 8 — Training Pipeline

Directed acyclic flow (Prefect deployment `train_full`):

1. **Ingest** (`ingestion/`): idempotent pull of CFBD endpoints (schedules, games, PBP, drives, adv box, lines, talent, returning production, portal, coaches, venues), Odds API snapshots, weather. Raw JSON archived immutably (`data/raw/{source}/{date}/`), then normalized to typed Parquet (`data/staged/`). Every record carries `ingested_at` and `source_version`.
2. **Validate** (`quality/`): Great Expectations suites + custom checks — schema, ranges (0 ≤ points ≤ 100), referential integrity (every PBP game exists in schedule), completeness vs expected game count, line sanity (|spread| < 70, totals in [20, 100]), duplicate detection, **temporal sanity** (no `event_time > ingested_at` on **completed** rows; unplayed `games` rows keep `event_time = kickoff + duration`, which may post-date ingest — ADR 0016). Failures: hard-fail the affected partition, soft-continue others, alert.
3. **Feature build** (`features/`): registry-driven, incremental by (season, week), DVC-tracked outputs; feature-drift stats (PSI vs trailing distribution) computed and logged.
4. **Stage-1 fit/refit** (`ratings/`): full historical filter re-run (cheap: seconds per season) to regenerate rating history; hyperparameters of the state-space model (process noise, obs noise, prior weights) re-tuned only at offseason retrain via marginal-likelihood/walk-forward CRPS.
5. **Mapping-layer training** (`models/`): OOF walk-forward fits → Optuna (per Sec. 6) → final fits per head → stacking on OOF → calibration on OOF → conformal calibration set → package.
6. **Evaluation** (`evaluation/`): full Sec. 7 harness; auto-generated HTML report (metrics tables w/ CIs, reliability plots, weekly curves, slice tables, equity curves, SHAP summaries).
7. **Registry & promotion** (`registry/`): MLflow model registry; candidate promoted to `champion` only if it beats the incumbent on the pre-registered metric set (CRPS + log-loss + CLV-backtest) on the same walk-forward seasons (lockbox excluded — §7.2 item 9) with a paired block-bootstrap test whose significance threshold is **Bonferroni-adjusted for promotion multiplicity**, *and* passes calibration and leakage gates. Exact rule: the registry maintains an append-only **promotion-attempt ledger**; within each calendar year, let \(k\) be the number of promotion attempts recorded that year **including the current attempt**, and let \(\alpha_0 = 0.10\) be the nominal per-comparison alpha. The attempt is significant only if the paired block-bootstrap \(p < \alpha_0 / k\). The comparison report **must** print \(k\), \(\alpha_0\), and the adjusted threshold \(\alpha_0 / k\). Otherwise archived with the comparison report. One-command rollback to any prior champion. Live forward paper-trade (§16 item 2; named in §1.6) remains the confirmatory instrument for success criteria after any promotion.
8. **Artifacts:** every run pinned by (git SHA, DVC hash, config hash, seed manifest, environment lockfile) in MLflow; models stored as versioned ONNX/pickle bundles with feature-signature contracts (inference refuses mismatched feature schemas).

---

## 9. Part 9 — Weekly Updating & Continual Learning Framework (the core of the system)

### 9.1 Updating philosophy — comparison and selection

The requirement: beliefs about team quality must update after every game, proportionally to the *informativeness* of the observation, anchored by priors, with principled forgetting. Candidates:

| Method | Assessment for CFB |
|---|---|
| **Elo / Glicko** | Simple, robust; but scalar (no off/def split), margin handling is bolted on, K-factor is a blunt instrument, and Glicko's rating deviation is a special case of what a Kalman filter gives you anyway. Keep Elo as a *benchmark and sanity feature*, not the engine. |
| **EWMA / rolling windows on stats** | No opponent adjustment, no uncertainty, window length is a fudge. Used only as secondary raw features (Sec. 4.4), never as the belief system. |
| **Kalman filter / linear-Gaussian state-space model** | **Selected.** Exactly the right tool: latent multivariate team state, per-game measurement update with gain proportional to prior uncertainty and inversely to observation noise, process noise = principled within-season drift, closed-form, fast, interpretable, and the canonical method in the sports-rating literature (Glickman & Stern 1998; Harville 1980's mixed-model margins are the static ancestor). |
| Dynamic Bayesian networks / full MCMC state-space | Statistically strictly more flexible (non-Gaussian obs, t-tails); cost: hours of sampling weekly, fragility. **Adopted in hybrid form:** production filter is Kalman with robustification (Sec. 9.5); a full NumPyro seasonal re-fit runs *offline monthly* as a bias check on the filter (if they diverge, investigate). |
| Online/incremental learning of the mapping layer (SGD-style) | Wrong layer. The mapping is stable; streaming updates to it would inject variance for no benefit. Rejected with the argument of Sec. 0.1. |
| Adaptive ensemble weighting | Adopted in damped form (Sec. 9.7). |

### 9.2 State definition (joint league state)

The Stage-1 filter maintains a **single joint league state vector** `x`, not a bank of independent per-team filters. Per-team blocks and league-level blocks live in one vector with a **full joint covariance** `P` (including all cross-team blocks).

- **Per-team block** for team `i`: `x_i = [off_epa, def_epa, st_value, pace, off_rush_bias, off_explos, def_explos]` — 7-dim latent (v1-minimal uses the first 4 only; the last 3 are v1.1). Extending from 4→7 dims is a config change, not a rewrite.
- **League-level blocks:** `hfa_global`, `hfa_team_deviation` (small, heavily shrunk), and a **single season scoring-environment** state (the identified league-mean efficiency/scoring level — see §9.3).
- **Dimension (v1):** ~134 FBS teams × 4 state dims + a handful of league-level states ≈ **~540**. At this size a dense Kalman update is trivial (milliseconds per game); do not approximate with independent or sparse-per-team filters for computational reasons.

**Per-game update is joint:** when home `h` plays away `a`, both teams' measurement equations enter **one** update of `(x, P)` (the four primary observations for the two sides — off/def efficiency interactions for each offense — plus ST/pace/margin as specified in §9.3). Information therefore flows across the schedule graph through the cross-team covariance blocks; that is what justifies the "optimal schedule propagation" claim in §5.1.

### 9.3 Measurement model (per game)

Observations per game: garbage-filtered offensive EPA/play for each side, plays run, ST EPA. Measurement equations, e.g. for home offense: `obs_epa_h = off_h − def_a + hfa_off + scoring_env + ε`, with **observation noise σ_obs scaled by informativeness**: fewer plays → larger σ; FCS opponents → opponent state pinned to a pooled FCS-tier prior with large variance (their game tells you little); blowout tails winsorized (Sec. 9.5). Using per-play efficiency rather than final margin as the observation is a deliberate, evidence-backed choice: EPA/SR-based team quality is more stable and more predictive of *future* margins than past margins themselves (the entire premise of SP+/FEI-class systems), because it strips out turnover luck, field-position luck, and red-zone variance. Margin still enters as a secondary observation with high noise (it carries finishing-drives info).

**Identifiability (mandatory).** The contrast `off_h − def_a` identifies only *differences*: adding a constant `c` to every team's offense and every team's defense leaves all measurements unchanged, and that null direction is collinear with the league scoring-environment state. Therefore, after every measurement update:

1. **Constraint projection:** project the offensive block onto the subspace with league-mean zero, and the defensive block onto the subspace with league-mean zero. Concretely, subtract the cross-sectional mean of `off_epa` from every team's `off_epa`, and likewise for `def_epa`; apply the matching linear projection to the joint covariance (`P ← (I − M) P (I − M)ᵀ` where `M` is the mean-centering operator on the off and def index sets). Do **not** use a soft / zero-noise pseudo-observation as the primary method — use explicit projection so the constraint is exact after every update.
2. **Scoring environment carries the level:** the single league `scoring_env` state absorbs the absolute efficiency/scoring level and is thereby identified as the league-mean environment. Team off/def posteriors remain relative (zero-sum within the league).

**The Kalman gain is the "don't overreact" mechanism, derived rather than hand-tuned:** update size = prior_variance / (prior_variance + obs_variance). Early-season (high prior variance from roster turnover) → bigger moves; a stable November team after 10 games → small moves; a noisy 40-play weather game → smaller moves than a 90-play shootout. This single equation replaces every ad-hoc "minimum sample size" heuristic requested in the brief, and *is* the requested Bayesian shrinkage/regression-to-mean/noise-filtering, unified.

### 9.4 Process noise (within-season drift)

Weekly `x ← x + w, w ~ N(0, Q)`. Q tuned by maximizing one-step-ahead predictive likelihood over historical seasons (per state dimension; pace drifts less than efficiency). Event-triggered Q inflation: starting-QB change (largest single injury effect in CFB — inject variance, let games resolve it), coordinator firing mid-season, documented mass injuries. This is how the system "knows it doesn't know" after a regime change without pretending to know the new level.

### 9.5 Robustness / preventing overreaction (beyond the gain)

- Winsorize observation residuals at ±2.5σ (a 63-point blowout updates like a 35-point one — the tail is not linearly informative about strength; equivalently approximates a t-likelihood filter).
- **Clipped-observation noise inflation (covariance consistency):** when a standardized innovation is clipped, \(|z| > 2.5\) where \(z = \nu / \sqrt{S}\) for innovation \(\nu\) and innovation variance \(S\), the *state* update uses the winsorized residual but the *covariance* update must inflate effective observation noise so the Joseph/Kalman \(P\) update stays consistent with the dampened information content: \(R_{\mathrm{eff}} = R \cdot (|z|/2.5)^2\). Without this, clipped blowouts shrink posterior variance as if the full tail were informative — overconfident November ratings after early-season landslides.
- Diagnostics: standardized innovation monitoring. Primary alert is a **per-team CUSUM on standardized innovations**, tuned so expected flags are ~1–2 per week league-wide (possible true regime change vs data issue) — not an automatic mega-update. The former **3 consecutive same-signed >2σ** rule is retained as a loud tier on top of CUSUM.
- Regression to the mean is emergent: predictions combine posterior mean (already shrunk) with mapping-layer structure; no additional ad-hoc regression is layered on (double-shrinkage bias).
- Distinguishing signal from randomness, operationally: signal = persistent innovation direction + corroborating covariates (new QB's recruit rating, line movement) → captured by Q-inflation events and multi-game accumulation; randomness = single-game outliers → absorbed by winsorization + \(R_{\mathrm{eff}}\) + gain.

### 9.6 Preseason priors & historical knowledge retention

Prior mean per state = weighted blend of preseason predictors:
`prior = w1·(last-season final posterior, regressed 25–35% to conference mean) + w2·returning-production adjustment (published elasticities as starting point, refit) + w3·recruiting talent (4-yr weighted composite, blue-chip ratio) + w4·portal net (2021+, wide σ) + w5·coaching-change adjustment (new-HC discontinuity: partial reset toward talent-implied level) + w6·QB-specific carryover (returning starter vs new)` — and, **for the market-aware stack only**, `+ w7·SP+ preseason rating` (ADR 0003). Fundamental-stack priors omit SP+ entirely. Prior variance = base + turnover-scaled inflation (a team returning 40% of production starts the season with a much wider posterior than one returning 85%).

**Weight fitting (no circularity):** Do **not** fit weights by regressing next-season *early* (prior-initialized) posterior ratings on the prior components — early ratings are prior-dominated, so that regression recovers the *assumed* blend weights rather than the true generative relationship (circular). Instead, weights are fit by regressing each season's **late-season (≥8 games played) posterior ratings from a diffuse-initialization filter run** (wide, nearly flat preseason \(P_0\); no informative prior blend) on the preseason predictors, over 2015–2024. The diffuse-run late ratings are dominated by within-season observations, so the regression recovers how well the preseason covariates predict eventual team strength — not how the prior was constructed. **Preferred upgrade** (replace the late-rating regression when the likelihood plumbing is ready): maximize Weeks 1–4 one-step-ahead predictive likelihood of the *game observations* with respect to the prior weights (and optionally prior variance hyperparameters), still using time-ordered seasons only. Out-of-sample acceptance must score fitted priors against **realized game observations** or against diffuse-run late ratings — never against prior-initialized early posteriors.

**How much do prior seasons matter?** Empirically (and this matches SP+/FPI behavior): priors dominate Weeks 1–3, reach ~50/50 with current-season evidence around games 5–7, and are largely (not entirely) washed out by game 9–10 — but in this architecture that schedule is *not hard-coded*; it is the emergent consequence of prior variance vs accumulated observation precision, and it self-adjusts per team (high-continuity teams' priors persist longer, correctly).

### 9.7 What updates on what cadence (with justification)

| Component | Cadence | Justification |
|---|---|---|
| Line/odds snapshots | 4–6×/day automated | Perishable; CLV measurement requires it |
| Stage-1 ratings | After every completed game (batch Sat night + stragglers Sun) | Closed-form, instant, the designed learning channel |
| Opponent-adjusted features, SoS, conference strength | Weekly (Sun) | Inputs complete after the week's games |
| Injury/QB status | Daily Thu–Sat + manual gameday review | Highest-value perishable info |
| Weather forecasts | Daily from T−3 | Forecast skill horizon |
| Mapping-layer models | **Scheduled: offseason full retrain + mid-season retrain gate at Weeks ~5 and ~10.** Gate: retrain candidate on all data incl. current season; promote only if it beats champion on trailing OOF by the Sec. 8.7 test. | Weekly retraining rejected: ~60 games add ~0.6% (<1%) to a ~10k-game training set; measured week-over-week improvement flows through rating inputs (verified in ablation A2, Prompt #23); weekly refits add model-churn variance and destroy comparability of monitoring baselines. |
| Ensemble weights | Monthly gate, damped (new = 0.7·old + 0.3·fit) | Adaptive weighting on small windows chases noise; damping bounds regret (standard online-learning practice) |
| Calibration (PIT) maps | With each model retrain; monitored weekly | Calibration drift is a monitored alarm, not a weekly refit |
| State-space hyperparams (Q, σ_obs, prior weights) | Offseason only | Structural; needs full-season likelihoods |

### 9.8 Weekly automated cycle (production week)

- **Sat 23:30 & hourly to 03:00** — ingest finals + PBP as posted; quality gates.
- **Sun 06:00** — full validation; Stage-1 updates for all completed games; regenerate adjusted features, SoS, conference strength; rating-movement report (innovation diagnostics, flags). *Automated; flags → manual review queue.*
- **Mon 06:00** — (retrain-gate weeks only) candidate retrain + comparison; otherwise integrity checks of champion against Sunday's features. *Promotion is manual-approve on the auto-generated comparison.*
- **Tue 06:00** — generate all game predictions (fundamental + market-aware) vs current lines; compute edges/EV/Kelly; publish internal report. *Automated.*
- **Wed** — publish weekly report; bet-candidate list w/ thresholds. *Bet list reviewed manually (roster news the system can't see).* 
- **Thu–Sat** — daily refresh: injuries, weather, line moves → re-price edges; final pre-kick refresh at T−6h and T−1h. *Automated; new bet candidates above threshold trigger push notification for manual confirm.*
- **Continuous** — odds snapshots; post-game CLV settlement of the week's bets Sun.

**Live publish construction (ADR 0017).** Tuesday uses one `as_of` per CFBD week, matching the validated walk-forward. The published forecast artifact omits any game whose kickoff (`start_date`) precedes that instant — those games are not This Week / Game Detail forecasts; they reach Results once graded. Stage-1 ratings for the publish are a champion-method `run_filter` of staged plays-preferred observations with `event_time < as_of` and fitted Task 15 priors (recomputed each publish; no cache). Task 14 `filter_history` is hygiene, not a production input. Kalman observations for a 2026+ predict include 2025; `WalkForwardConfig` replay/test/warmup must not.

> The historical backfill (§15 item 5b) pulls snapshots at timestamps that **mirror these production decision points exactly**. Backfilling at convenient-but-different timestamps would price a market the live system never sees, invalidating every backtested CLV number. Changing this schedule later invalidates backtest comparability with runs made before the change.

Manual-vs-automated principle: **everything computational is automated; the two human gates are model promotion and bet confirmation** — the points of highest irreversible cost and highest value of human context.

---

## 10. Part 10 — Automation Architecture

- **Orchestrator: Prefect 2** (chosen over Airflow: single-machine friendly, no scheduler DB ops burden, native Python, good retries/observability; over cron: dependency graphs, state, retries; Dagster is a fine alternative — asset-graph model is arguably nicer for the feature DAG — pick one, this doc standardizes on Prefect).
- **Deployments:** `ingest_odds` (cron 6×/day), `postgame_ingest` (Sat/Sun schedule), `weekly_update` (Sun), `retrain_gate` (gated), `predict_publish` (Tue + daily refresh), `settle_clv` (Sun).
- **Failure recovery:** idempotent tasks keyed by (source, partition); exponential-backoff retries (3×); dead-letter queue for poisoned partitions; every flow resumable from last successful task; stale-data mode — if ingestion fails, prediction flow runs on last-good snapshot and stamps every output `STALE(source, age)`.
- **Notifications:** ntfy/Telegram/email for: flow failure, quality-gate failure, rating-innovation flags, cadence shortfall (snapshot count below expected − tolerance within 24h), new bet candidates, calibration alarm, CLV weekly summary.
- **Logging:** structlog JSON logs, per-flow-run IDs, shipped to local Loki or plain files + `logcli`; every prediction row carries lineage IDs.
- **MLflow backend:** local **file store** (or a single-writer logging pattern). Do **not** use SQLite under multi-process / 4-way parallel trial logging — SQLite locking under concurrent writers is unreliable here.
- **CI / live tests:** tests that require a live network are marked `pytest.mark.live` and are **excluded from CI**. Default `make test` / CI runs the offline suite only.
- **Data-quality cadence:** per (game, decision window), captured snapshot count must be ≥ expected minus a configured tolerance; alert within 24h of a cadence shortfall.
- **Raw odds archive durability:** replicate the raw odds archive off-machine to a versioned S3-class target within 24h of capture; run a quarterly restore drill. Request metadata in the raw archive is scrubbed of API keys (enforced by test).
- **UI exposure:** MLflow and Prefect UIs must **never** be exposed off-host without authentication.
- **Model promotion/rollback:** registry stages `candidate → challenger → champion → archived`; promotion writes an immutable comparison report; rollback = one CLI command re-pinning the prior version; inference always resolves `champion` at runtime.
- **Artifact versioning:** DVC for data/features (remote = local NAS or S3-compatible bucket), MLflow for models/experiments, git for code/config; a `manifest.json` per weekly run binds all three hashes.

## 11. Part 11 — Repository Architecture

```
ncaa-quant/
├── pyproject.toml            # uv/poetry; single source of deps
├── uv.lock                   # locked env
├── Dockerfile                # CUDA base; dev == prod image
├── docker-compose.yml        # app + mlflow + prefect + duckdb vol
├── Makefile                  # make ingest / features / train / predict / backtest / test
├── configs/
│   ├── base.yaml             # hydra-style layered config
│   ├── data.yaml  ratings.yaml  models/{lgbm_margin,...}.yaml
│   ├── betting.yaml          # thresholds, kelly fraction, filters
│   └── pipeline.yaml         # schedules, gates
├── src/ncaa_quant/
│   ├── ingestion/            # cfbd.py, odds_api.py, weather.py, rosters.py
│   ├── quality/              # expectations/, validators.py, pit_audit.py
│   ├── data/                 # schemas.py (pandera), storage.py (duckdb/parquet)
│   ├── features/             # registry.yaml, builders/{efficiency,tempo,situational,market,roster}.py
│   ├── ratings/              # state_space.py, priors.py, diagnostics.py, elo_baseline.py
│   ├── models/               # heads/{margin,total,sigma,quantile}.py, ensemble.py, calibrate.py, conformal.py
│   ├── distribution/         # bivariate.py, key_numbers.py, simulate.py
│   ├── betting/              # edges.py, kelly.py, filters.py, clv.py
│   ├── evaluation/           # walkforward.py, metrics.py, reports.py, significance.py
│   ├── pipelines/            # prefect flows
│   ├── cli.py                # typer CLI mirroring Makefile verbs
│   └── utils/                # seeding.py, timeutils.py, logging.py
├── tests/                    # unit/ integration/ leakage/ golden/
├── notebooks/                # research only; nothing imports from here
├── data/                     # DVC-managed: raw/ staged/ features/ predictions/
├── docs/                     # mkdocs; ADRs in docs/adr/
└── .github/workflows/ci.yml  # lint(ruff)+typecheck(mypy)+tests+leakage suite+expectations dry-run
```

Standards: Python 3.11+, ruff + mypy(strict on src), pandera schemas on every DataFrame boundary, pydantic-settings for secrets via `.env` (never committed; template checked in), conventional commits, ADR required for any architectural change, every module owns its docstring-level spec, pytest coverage gate ≥ 80% on `src`, golden-file tests pin known historical predictions to detect silent behavior change.

## 12. Part 7 addendum — Betting layer specifics

- **Edge:** `edge = p_model_calibrated − p_market_devigged` per side, computed against best available captured price across books. The shopping component of that best price vs consensus at bet time is measured separately as `line_shopping_capture` (§2.7), not folded into CLV.
- **Bet filters (configs/betting.yaml):** min edge (start 2.5% sides / 3% totals — deliberately above backtest-optimal to pay for optimism bias), min model-market agreement checks, no-bet on STALE inputs, no-bet on QB-status=unknown, bowl stricter threshold, max bets/week, max exposure/team.
- **Staking: fractional Kelly at 25%** of full Kelly, capped at 1.5% bankroll/bet. Justification: Kelly is growth-optimal only under exactly-known probabilities; with estimated probabilities, full Kelly systematically overbets (estimation error is asymmetric in growth terms — well-established result), and quarter-Kelly sacrifices little growth for large drawdown reduction. Simultaneous same-week bets are near-independent (different games) but correlated through model error → the cap plus a weekly aggregate exposure limit handles it; full correlated-Kelly optimization is research-track.
- **CLV settlement:** every recommendation stores book identity, line-at-recommendation, bet-time price (and consensus price at bet time for `line_shopping_capture`), and the same-book close (plus consensus close for fallback diagnostics). Weekly CLV report settles per §2.7: same-book, line-translated CLV with `clv_settlement` / `clv_method` on every row; fallback-consensus and line-unit rows reported separately, never pooled into the headline; `line_shopping_capture` alongside. Cumulative distribution and CI on this page are the system's real report card.

## 13. Part 13 — Advanced Research Opportunities (ranked honestly)

**Worth implementing (ordered):**
1. **Market residual modeling** — already in production design (market-aware stack). The purest form — target = closing-line error directly — is Research Sprint R1; likely the single highest-ROI research line, because it converts the problem from "beat the market from scratch" to "find the market's small systematic biases" (e.g., documented-in-literature candidates to test: G5 totals, large-dog inflation, ranked-team overpricing, early-week stale lines).
2. **Conformal prediction (CQR)** — in production design already; cheap, rigorous.
3. **Monte Carlo simulation-based forecasting** — v1 simulates from the parametric joint; R2 upgrades to possession-level simulation (drive-outcome model per matchup → simulate 10k games). Highest-fidelity path to totals, alt-lines, and derivative markets; meaningful build cost; clear go/no-go test: must beat bivariate-normal CRPS out-of-sample.
4. **Bayesian hierarchical season model (NumPyro, GPU)** — the monthly offline cross-check (Sec. 9.1) graduating to prior-generation duty; also the right machinery for coach/QB partial pooling.
5. **QB value modeling / player aggregation** — QB-specific EPA state with backup deltas; the highest-leverage single-player effect in CFB.
6. **Dynamic ensemble weighting (damped)** — in design; research the regret-bounded variants (Hedge/EWA) properly.
7. **BART and FT-Transformer bake-offs** — bounded experiments with pre-registered success criteria (beat champion CRPS on walk-forward, p<0.1); expectation honestly stated: likely null result, worth one week each.
**Likely overengineering for this data regime (documented reasons):** GNNs over the schedule graph (Stage 1 already propagates schedule information optimally under its *joint* model — §9.2 / footnote in §5.1; revisit only for style-matchup propagation), TFT/LSTM (12-length sequences with regime breaks), foundation models for sports / self-supervised pretraining (no suitable pretraining corpus at CFB scale), RL for staking (Kelly already solves the formal problem; RL adds estimation risk), causal inference for prediction (causal identification is not required for forecasting; useful only for targeted questions like coaching-change effects), LLM-assisted feature engineering (fine as an ideation tool offline; never in the pipeline), synthetic data generation (would launder the small-n problem, not solve it).

## 14. Monitoring Plan, Testing Plan, Risk Assessment, Roadmap

**Monitoring (weekly dashboard + alarms):** ingestion freshness; feature drift (PSI > 0.2 warn / 0.3 alarm); rating innovation z-scores; rolling CRPS/log-loss vs market baseline (CUSUM change detection); calibration drift (slope outside [0.85, 1.15] over trailing 150 games); CLV trend; bankroll sim vs realized; error-by-slice heatmap; model/feature version dashboard.

**Testing:** unit (feature functions with hand-computed fixtures; Kalman update against analytic 1-D cases; Kelly math; de-vig math), property-based (hypothesis: probabilities in [0,1], distribution integrates to 1, monotone-constraint respect), **leakage suite** (pit_audit random-row recomputation; **within-week label permutation** — models trained on labels permuted within week must score ≈ chance out-of-sample; **planted prophecy** — a deliberately future-leaking feature is added in a test fixture and both pit_audit and the information-set audit must catch it; feature-timestamp static analysis), integration (end-to-end on a frozen mini-season fixture), golden predictions, backtest determinism test (pinned model artifact hashes → identical inference/walk-forward outputs per §1.4; async HPO search is not bit-for-bit), chaos tests (missing partitions → STALE mode engages). The former "shifted-label" null (future features predicting past games must score at chance) is **deleted**: strength persistence makes future features legitimately predictive of past games, so that null is wrong.

**Risk register (top items):** CFBD API change/ToS shift (mitigate: raw archival, thin client isolation, second-source plan); odds-history shallowness (mitigate: start capture immediately); overfitting-to-backtest (mitigate: nested HPO, quarantine season, pre-registered promotion tests, edge thresholds above optimal); silent leakage (mitigate: test suite, as-of-only joins); regime change — rule changes, portal/NIL escalation (mitigate: era features, Q-inflation, monitoring alarms); market efficiency — no edge exists at your books (mitigate: CLV apparatus gives a fast, honest verdict; the forecaster remains valuable as a forecaster); operator risk — betting is risky, jurisdiction-dependent, and this system is decision support, not a profit guarantee; bankroll must be money you can lose.

**Roadmap:** v1.0 (Prompts 1–24, fundamental + market-aware stack, weekly automation) → v1.1 (QB state, weather-totals, explosiveness states) → v1.2 (R1 market-residual target, CQR hardening) → v2.0 (possession simulator, hierarchical Bayes priors, derivative markets).

## 15. Part 12 — Ordered Coding-Agent Implementation Prompts

Each prompt = one PR-sized unit; the agent must: implement only the named module(s), run `make test`, add tests reaching the coverage gate for new code, update `docs/`, and append an implementation-notes entry to `docs/notes/NN.md`. Acceptance criteria are stated per prompt; none may modify unrelated files.

1. **Repo scaffold** — create the Sec. 11 tree, pyproject (uv), ruff/mypy/pytest config, Dockerfile, Makefile, CI workflow, pre-commit. *Accept:* `make test` green on empty suite; CI passes; container builds.
2. **Config & utilities** — layered YAML config loader (pydantic-settings + OmegaConf), `seeding.py` (global seed manifest), structlog setup, timeutils (all times UTC, `as_of` helpers). *Accept:* unit tests; config override precedence tested.
3. **Storage & schemas** — DuckDB/Parquet storage layer, pandera schemas for games, PBP, drives, lines, teams, venues; partition conventions. *Accept:* round-trip tests; schema violations raise.
4. **Odds snapshot ingester (DO THIS EARLY — data is unbackfillable)** — Odds API client w/ rate limiting, snapshot schema `(book, market, line, price, captured_at)`, Prefect deployment 6×/day, raw archival. *Accept:* live smoke test; idempotency test; dedupe test.
5b. **Historical odds backfill** — The Odds API historical endpoint; pre-registered snapshot schedule mirroring §9.8 decision points; cost estimator and dry-run gate before any spend; separate credit budget bucket from the live capture. *Accept:* `--estimate` prints credit cost before any spend; coverage and book-count trajectory reported per season; CFBD-close reconciliation distribution reported.
5. **CFBD ingestion** — full client (schedules, games, PBP, drives, adv box, historical lines, talent, returning production, recruiting, portal, coaches, venues, rosters), backfill CLI for 2014–2025, incremental mode. *Accept:* backfill of one season verified against known game counts; retry/idempotency tests.
6. **Weather & venue enrichment** — venue lat/lon/altitude table, Open-Meteo historical + forecast client, stadium-hour matching. *Accept:* spot-check fixtures; dome handling test.
7. **Data quality layer** — Great Expectations suites + custom validators + pit temporal checks; quarantine flow. *Accept:* seeded corrupt fixtures are caught; clean season passes.
8. **EPA/WP acquisition & garbage-time filter** — normalize CFBD EPA/WP fields; implement WP-threshold and fallback GT rules; play-weighting utilities. *Accept:* GT filter matches hand-labeled fixture drives.
9. **Feature registry & as-of engine** — registry YAML schema, builder base class `f(entity, as_of)`, DuckDB as-of join helpers, incremental (season, week) materialization + DVC hooks. *Accept:* pit_audit passes on synthetic data with planted future rows (must exclude them).
10. **Efficiency feature builders** — adjusted EPA/SR/explosiveness/havoc/finishing via ridge opponent adjustment; shrinkage; EWMA variants. *Accept:* ridge adjustment recovers planted synthetic team strengths; shrinkage math unit-tested.
11. **Tempo/possession + situational builders** — pace, expected possessions model, rest/travel/altitude/rivalry/week features. *Accept:* fixtures; expected-possessions regression sanity bounds.
12. **Roster/prior data builders** — returning production, talent, recruiting 4-yr weights, portal net, coach tenure/change flags, QB-status table (manual-entry CLI + scrape stub). *Accept:* fixtures per source; era flags tested.
13. **Elo baseline** — margin-aware Elo with tuned K, autumn regression; report harness hookup. *Accept:* reproduces published-order rankings on a past season (rank correlation vs SP+ > 0.85 sanity check).
14. **State-space rating engine** — joint-league Kalman filter per Sec. 9.2–9.5 (~540-dim v1 state, full cross-team covariance, joint per-game update; off/def mean-zero projection + identified scoring_env; informativeness-scaled noise, winsorization with \(R_{\mathrm{eff}}\) inflation on clipped obs, FCS pooling, Q inflation events, diagnostics). *Accept:* recovers planted parameters in simulation; 1-D analytic Kalman test; **identifiability/invariance test** (constant shift of initial states leaves constrained ratings and predictions unchanged); clipped-update variance test; innovation stats unit-tested; full 2014–2025 filter run < 5 min.
15. **Preseason prior builder** — Sec. 9.6 blend with weights fit on diffuse-init late-season ratings (or Weeks 1–4 predictive likelihood upgrade); turnover-scaled prior variance; SP+ term market-aware only. *Accept:* weight-fitting reproducible; circularity regression test; OOS scored vs games / diffuse late ratings; Week-1 2023/2024 predictions stored.
16. **Walk-forward harness** — the Sec. 7.2 replay engine (information-set correct weekly loop), OOF prediction storage. *Accept:* determinism test; information-set audit (spot weeks recomputed from raw pass equality); within-week label-permutation and planted-prophecy leakage hooks per §14.
17. **Mapping-layer heads** — LGBM μ/quantile heads for margin & total, σ-heads (abs residual × \(\sqrt{\pi/2}\), net of member-mean), XGB/CatBoost/ENet/NGBoost members, monotone constraints, feature-signature contracts. *Accept:* OOF pipeline runs end-to-end on 3 train seasons; σ unbiasedness on synthetic heteroskedastic data; constraint tests.
18. **Optuna HPO** — Sec. 6 framework (nested walk-forward objective, pruning, persistence, GPU trials for XGB/CatBoost, quarantine-season tiebreak on **2024**, distinct from lockbox **2025**). *Accept:* resumability test; nested-CV isolation test (test seasons unreadable inside objective — enforced by API).
19. **Ensemble, calibration, conformal, distribution assembly** — simplex-constrained QP stacking on OOF, distributional PIT recalibration (not per-market maps), ACI conformal layer (trailing-2-season split-conformal initializer), bivariate assembly + ρ estimation + **conditional** key-number kernel + MC engine. *Accept:* post-calibration consistency property tests; stacking weights on simplex by construction; approximate coverage tests on held-out seasons within tolerance; kernel-vs-empirical margin frequencies by predicted-spread bucket.
20. **Betting layer** — de-vig, edge, EV, fractional Kelly, filters, exposure limits, CLV settlement. *Accept:* hand-computed fixtures; filter config tests.
21. **Evaluation & reporting** — full metric suite w/ block-bootstrap CIs, reliability/PIT plots, slice tables, equity curves, HTML weekly + backtest reports, market-baseline comparisons everywhere. *Accept:* golden report on fixture season.
22. **MLflow + registry + promotion** — tracking integration, champion/challenger workflow, Bonferroni-adjusted promotion alpha via promotion-attempt ledger, significance-tested promotion, rollback CLI. *Accept:* promotion blocked on failing gate in test; ledger \(k\) and \(\alpha_0/k\) printed on comparison report; rollback restores golden predictions.
23. **Full backtest & ablations** — run 2019–2024 walk-forward (lockbox 2025 excluded per §7.2 item 9); ablation suite: A1 priors off, A2 rating-updates frozen at Week 1 (quantifies the continual-learning gain — the system's core claim), A3 market features off, A4 ensemble vs single LGBM, A5 GT-filter off. *Accept:* ablation report generated; results archived to MLflow.
24. **Prefect production flows** — all Sec. 10 deployments, STALE mode, notifications, runbooks in docs. *Accept:* end-to-end dry run on fixture week; chaos test (killed ingestion → STALE predictions still publish).
25+ **Research sprints** (each gated, pre-registered): R1 closing-line-residual target; R2 possession simulator; R3 QB state; R4 BART; R5 FT-Transformer; R6 hierarchical Bayes priors.

---

## 16. Additional recommendations a professional shop would insist on

1. **Pre-registration discipline:** before each season, freeze and commit the metric definitions, promotion tests, bet thresholds, and research hypotheses. The dominant failure mode of retail quant sports projects is unconscious backtest iteration; pre-registration is the cheap vaccine.
2. **Paper-trade a full season** (or at minimum half) before real stakes: the CLV apparatus makes paper trading genuinely informative, unlike ROI-only tracking.
3. **Bet the number, not the team:** the pipeline prices *lines*; a recommendation is void if the line moves past its threshold — enforce in the bet-confirmation UI.
4. **Track your own market impact and limits** even at small scale; log every rejected/limited bet.
5. **Line shopping is alpha:** 2–3 books' best price is worth roughly as much as most modeling improvements; the odds-capture layer should cover every book you can legally access.
6. **Totals and derivatives before sides:** market efficiency ordering (sides sharpest → totals → team totals/alt lines/derivatives) means early real-money focus should tilt to the softer markets the same joint distribution already prices.
7. **Season-boundary postmortem** as a standing artifact: what the monitoring caught, what it missed, which pre-registered hypotheses survived.
8. **Legal/compliance note:** confirm sports betting legality and book ToS in your jurisdiction; automated *placement* is intentionally out of scope partly for this reason. Bankroll must be strictly discretionary funds.

*Uncertainties deliberately left open (as instructed, stated rather than papered over):* true magnitude of bowl opt-out effects; referee-crew predictive value in CFB; portal-era prior weights (3 seasons of data); whether BART/FT-Transformer add anything at this n; correlated-Kelly refinements; and, above all, whether the accessible markets currently leave positive expectation on the table at all — the system is built to answer that last question honestly and fast.

— End of specification —
