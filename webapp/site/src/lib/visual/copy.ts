/**
 * Published copy for /visual. Listed in scripts/check_betting_language.py
 * PUBLISHED_COPY_SURFACES (an unlisted copy.ts fails the guard).
 *
 * Every mechanism described here is sourced from the backend:
 * ratings/priors.py, ratings/state_space.py, evaluation/production_stack.py,
 * models/ensemble.py, models/conformal.py, distribution/simulate.py,
 * webapp/export.py, ADR 0013, ADR 0014, DESIGN §1–§3.
 */

export const PAGE = {
  title: "How a forecast is made",
  lede:
    "Follow one example game from raw data to the row you see on This Week. " +
    "Every number on this page is illustrative, not a live forecast.",
  exampleTag: "Illustrative example",
  back: "Back",
  next: "Next",
  restart: "Start over",
  stepOf: (n: number, total: number) => `Step ${n} of ${total}`,
  techHeading: "Under the hood",
  closingBefore:
    "Ridge publishes forecasts, not recommendations. To see how the model has actually " +
    "performed, including where it falls short, visit",
  closingMiddle: ". The full method is on",
  closingLinks: { results: "Results", about: "About" },
} as const;

export type StepGroup = "ratings" | "forecast" | "distribution" | "site";

export const GROUPS: Record<StepGroup, string> = {
  ratings: "Stage 1: team ratings",
  forecast: "Stage 2: score forecast",
  distribution: "Distribution",
  site: "Site",
};

export type StepCopy = {
  id: string;
  short: string;
  group: StepGroup;
  title: string;
  plain: string[];
  technical: string[];
  formula?: string;
};

export const STEPS: StepCopy[] = [
  {
    id: "inputs",
    short: "Inputs",
    group: "ratings",
    title: "What the model knows, and when",
    plain: [
      "Every forecast starts from college football data gathered on a private workstation: " +
        "the result of every snap in every FBS game, the schedule and venues, and preseason " +
        "roster information.",
      "The rule is strict. A run may only use what was already known at the moment it ran. " +
        "Pick a run to see where that line falls during a week.",
    ],
    technical: [
      "Every record carries an event_time, and every feature, rating and schedule lookup is " +
        "bounded by event_time < as_of. An audit rebuilds feature vectors from raw history under " +
        "the same cut and checks they match exactly.",
      "Garbage-time snaps are removed before efficiency is measured. Non-FBS opponents are " +
        "pooled into a single FCS entity with a wide prior. Ratings are recomputed from scratch " +
        "on every publish, so a Thursday night result is in Friday's numbers, and any game that " +
        "has kicked off is dropped from the slate.",
    ],
    formula: "use(record) ⇔ event_time < as_of",
  },
  {
    id: "prior",
    short: "Preseason",
    group: "ratings",
    title: "A starting point before any games",
    plain: [
      "Before week 1, each team gets a preseason rating built from six things: last season's " +
        "rating pulled 30% back toward its conference average, returning production, roster " +
        "talent, transfer portal gains and losses, coaching changes, and whether the starting " +
        "quarterback returns.",
      "Just as important is how sure the model is. A team that lost most of its roster starts " +
        "with a wider range, so its early games move it more. Try the slider.",
    ],
    technical: [
      "The prior mean is a weighted blend of the six predictors. The weights are fit by least " +
        "squares against late-season ratings from a separate run that ignores the prior, so the " +
        "fit can't simply confirm itself.",
      "Prior variance grows with roster turnover and with every missing input. Missing data " +
        "widens the prior; it is never filled with a default that would fake confidence.",
    ],
    formula: "var₀ = 0.02 · (1 + 2.5 · (1 − returning))\n     + 0.015 · missing",
  },
  {
    id: "update",
    short: "Weekly update",
    group: "ratings",
    title: "Updating ratings as games are played",
    plain: [
      "Each team is tracked on four dimensions: offensive efficiency, defensive efficiency, " +
        "special teams and pace. After every game the model compares what happened with what " +
        "it expected.",
      "A surprising result moves the rating, but only in proportion to how unsure the model " +
        "was. In a week with no game, uncertainty grows a little. Walk one team's offense " +
        "through a season below.",
    ],
    technical: [
      "Stage 1 is a Bayesian state-space model updated with a Kalman filter. One joint state " +
        "holds every FBS team with full cross-team covariance, which is how a result also " +
        "informs past opponents. Ratings are re-centered to a league mean of zero after each update.",
      "The gain K sets how far a rating moves toward the observed efficiency. Observation noise " +
        "R shrinks when a game has more snaps. Surprises beyond 2.5 standard deviations are " +
        "capped so one lopsided game can't dominate. This view shows one dimension of one team; " +
        "the real filter updates all of them together.",
    ],
    formula: "K = P⁻ / (P⁻ + R)\nm = m⁻ + K · cap(y − m⁻)\nP = (1 − K) · P⁻",
  },
  {
    id: "forecast",
    short: "Forecast",
    group: "forecast",
    title: "From ratings to a score forecast",
    plain: [
      "Ratings alone don't give a score. The mapping layer turns the matchup into an expected " +
        "margin and combined score, using both teams' ratings plus tempo, rest, travel, time " +
        "zones, altitude and recent form.",
      "Two different models each make an estimate, and a blend weighted by past accuracy " +
        "becomes the forecast. A model that fails its health checks is left out. If none pass, " +
        "Ridge shows no forecast rather than a made-up one. Switch a model off to see.",
    ],
    technical: [
      "Margin members are LightGBM and Elastic Net. Their weights are fit on out-of-fold " +
        "predictions with w ≥ 0, Σw = 1 and no intercept, so the forecast is always a blend of " +
        "real member predictions. The combined score currently uses a single LightGBM model. " +
        "This reduced ensemble is recorded in ADR 0013; the full design adds more members.",
      "Under ADR 0014 a member is used only if its fit completed and its predictions actually " +
        "vary. A failed member is excluded and recorded, never replaced with a constant.",
    ],
    formula: "μ = Σ wₖ μₖ,   wₖ ≥ 0,   Σ wₖ = 1",
  },
  {
    id: "uncertainty",
    short: "Uncertainty",
    group: "distribution",
    title: "How uncertain is the forecast?",
    plain: [
      "A forecast of Home by 4.2 isn't a promise. Football is noisy, the ratings themselves are " +
        "uncertain, and the models don't agree perfectly. Ridge combines all three into one " +
        "number, written σ.",
      "It then simulates the game many times. Final margins bunch up on 3 and 7 because points " +
        "arrive as field goals and touchdowns, so the simulation puts extra weight there.",
    ],
    technical: [
      "The σ-head is a model trained on the size of past errors, so σ differs game to game. " +
        "Rating uncertainty comes from pushing 50 draws of the Stage 1 posterior through the " +
        "mapping layer. Member disagreement is how far the member estimates sit from their blend.",
      "Margin and total are drawn jointly, with their correlation fit on residuals, over " +
        "100,000 seeded draws. Margins land on whole numbers, reweighted by a key-number kernel " +
        "fit from past residuals rather than set by hand.",
    ],
    formula: "σ² = σ²_head + Var_ratings(μ)\n     + Var_members(μₖ)",
  },
  {
    id: "range",
    short: "Range",
    group: "distribution",
    title: "The range around the forecast",
    plain: [
      "Instead of a single number, Ridge publishes a range meant to contain the real margin in " +
        "about 8 of 10 games.",
      "It starts from models that predict the low and high ends directly, then widens them by " +
        "however much similar ranges missed over the last two seasons. If those ends land on the " +
        "wrong side of the forecast itself, the range is withheld rather than shown wrong.",
    ],
    technical: [
      "Conformalized quantile regression: LightGBM q10 and q90 heads, widened by a quantile of " +
        "the conformity scores E = max(q_lo − y, y − q_hi) from the trailing two seasons. " +
        "Adaptive conformal inference then steers the miss rate toward 20% as results arrive.",
      "Coverage is approximate, not guaranteed, because seasons drift. Publish gate: the range " +
        "is written only if q10 < μ < q90 before widening; otherwise the fields are null.",
    ],
    formula: "range = [q₁₀ − Q̂, q₉₀ + Q̂]",
  },
  {
    id: "conviction",
    short: "Win chance",
    group: "distribution",
    title: "Win chance and conviction",
    plain: [
      "Win chance is the share of simulated games each team wins. The conviction label simply " +
        "describes that number: how clearly the model favors one side.",
      "It's a statement about the forecast, not a recommendation. Move the sliders to see how " +
        "the expected margin and its uncertainty change the label.",
    ],
    technical: [
      "p_favored is the win chance of the side with μ ≥ 0, with exact-tie draws set aside. " +
        "Tiers enter at 57.5% (Lean), 70% (Clear lean) and 85% (Strong lean). To stop labels " +
        "flickering between refreshes, a tier is held until p_favored falls 3 points below its " +
        "entry threshold, and a toss-up is promoted only at 60.5%.",
      "Labels are hidden when σ was refused, when the win chance isn't credible, or when " +
        "inputs are more than 6 hours stale.",
    ],
    formula: "p_win = P(M > 0) / (P(M > 0) + P(M < 0))",
  },
  {
    id: "published",
    short: "Published",
    group: "site",
    title: "What reaches the site",
    plain: [
      "The final numbers are written to versioned files that this site reads. The site never " +
        "talks to the model or to any data provider.",
      "The primary forecast publishes Tuesday at 06:00 UTC and refreshes Thursday through " +
        "Saturday. When something is missing or unreliable, the row says so instead of " +
        "guessing. Switch states to see how a row changes.",
    ],
    technical: [
      "Each publish writes week_predictions.json and meta.json with a shared published_at, " +
        "model identity and provenance labels. A completed game is graded against the last " +
        "publish before its kickoff.",
      "Null means not computed. The site shows a dash or an explanation, never a zero, an " +
        "interpolation or a league average.",
    ],
  },
];
