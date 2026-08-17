/**
 * About / Methodology copy — public-reader level.
 * §6 disclaimer and responsible-gambling text are adapted for readability only
 * (line breaks / emphasis via markup), never weakened.
 */

/** One-screen identity — what a stranger needs first. */
export const RIDGE_IDENTITY =
  "Ridge publishes college football forecasts with uncertainty. It is a team-rating engine that updates during the season, then a separate layer that turns those ratings into a predicted margin and combined score — always shown with a range where the outcome can still land.";

export const MODEL_SECTIONS = [
  {
    id: "how-it-works",
    title: "How the forecast is built",
    paragraphs: [
      "Two stages, kept separate on purpose.",
      "First, a rating engine tracks how strong each team’s offense and defense look right now. After each completed game, those ratings update. Early in the season the ratings are uncertain; as more games land, the picture usually firms up. (Technical name, if you see it elsewhere: a state-space / Kalman-style rating system — continuous beliefs about team strength, revised with new results.)",
      "Second, a mapping layer reads those ratings and related game context and produces a predicted margin (home minus away, in points) and a predicted total (combined score). That layer is trained more slowly than the ratings; week to week, most of the change in the forecast comes from the ratings moving, not from retraining the mapper.",
      "When several mapping models are combined, the site labels that as a reduced ensemble: a smaller set of members than the full experimental stack, not a claim that every research idea is in production.",
      "Uncertainty bands on the page come from the predictive distribution, with a conformal calibration layer when the export includes interval bounds. In plain terms: the band is constructed so that, historically, outcomes fall inside it at about the stated coverage rate — approximate, not a guarantee.",
    ],
  },
  {
    id: "what-numbers-mean",
    title: "What the numbers mean",
    paragraphs: [
      "A predicted margin is a central estimate — the middle of the forecast, not a promise.",
      "The interval beside it is the uncertainty band: where outcomes still look plausible under the model. Games regularly finish outside that band.",
      "A conviction tier (Strong lean, Clear lean, Lean, or Toss-up) describes how decisive that forecast looks. It is a label on the forecast. It is not a pick, not a wager, and not advice to bet.",
      "When a value cannot be computed honestly — for example when predictive uncertainty is refused — the site shows that absence (“—” or “not computed”). Missing is shown as missing.",
    ],
  },
  {
    id: "data-and-cadence",
    title: "Data sources and publish schedule",
    paragraphs: [
      "Schedule facts, scores, and team school names are ingested from CollegeFootballData (CFBD) on a private workstation. This website never calls CFBD, The Odds API, or any live sportsbook. It only reads versioned JSON artifacts that the workstation publishes.",
      "Primary publish is Tuesday 06:00 UTC. Refreshes run Thursday–Saturday 06:00 UTC. Team ratings also update after the weekend (Sunday 06:00 UTC on the workstation schedule).",
      "Market lines are used internally on the workstation for evaluation against the closing market. They are never published on this site. Ridge is a forecasts-with-uncertainty product, not a betting-tips product; showing lines would invite edge claims the public record does not support.",
    ],
  },
] as const;

export const HONESTY_COMMITMENTS = [
  "Uncertainty is always shown with the forecast when the export provides it.",
  "Missing values are shown as missing — never filled with zeros or averages.",
  "Stale forecasts are labeled when inputs are stale.",
  "No picks, no suggested wagers, no implied edge claims.",
  "The fit-to-bet verdict from the recorded track record is published on Results — currently NOT CURRENTLY FIT TO BET.",
] as const;

/**
 * §6.1 site-wide disclaimer — substantive text unchanged.
 * `{year}` is substituted at render time.
 */
export const DISCLAIMER_TEMPLATE =
  "Ridge publishes automated college football forecasts with uncertainty from a private statistical model. These are not betting recommendations. Ridge does not publish sportsbook lines, implied edges, suggested wagers, or expected profits. Forecasts can be wrong. Past interval hit rates and track-record metrics do not guarantee future performance. For entertainment and informational purposes only. © {year} Ridge.";

/** §6.2 responsible gambling — substantive text unchanged. */
export const RESPONSIBLE_GAMBLING_COPY =
  "If you or someone you know has a gambling problem, call 1-800-GAMBLER (1-800-426-2537). Help is available 24/7. Ridge does not accept wagers and is not affiliated with any sportsbook.";

/** Short footer line — same substance, discoverable without dumping the full block. */
export const FOOTER_DISCLAIMER_SHORT =
  "Forecasts with uncertainty — not betting recommendations. No lines, picks, or edge claims.";

export const CFBD_ATTRIBUTION =
  "Schedule, score, and team-name data displayed on Ridge are derived from CollegeFootballData (collegefootballdata.com). Ridge is not affiliated with CollegeFootballData.";

/**
 * W8-B operator-supplied attribution. Entity only: no personal name, no
 * contact method, no repository URL. AboutPage still imports the body copy
 * under the W6 export name.
 */
export const ATTRIBUTION_HEADING = "Attribution";

export const ATTRIBUTION_COPY =
  "Ridge is an independent research project. It is not affiliated with any school or conference.";

/** W6 export name — same string as ATTRIBUTION_COPY. Do not restore a placeholder. */
export const ATTRIBUTION_PLACEHOLDER = ATTRIBUTION_COPY;

export function disclaimerForYear(year: number): string {
  return DISCLAIMER_TEMPLATE.replace("{year}", String(year));
}
