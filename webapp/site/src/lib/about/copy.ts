/**
 * About / Methodology copy — public-reader level (clarity rewrite).
 * §6.1 disclaimer and §6.2 responsible-gambling text stay verbatim.
 */

import { formatNominalCoveragePhrase } from "@/lib/formatting/team-margin";

/** Opening: what Ridge does, in plain sentences. */
export const RIDGE_IDENTITY = [
  "I built Ridge to publish college football forecasts with the uncertainty left in.",
  "Every game gets a predicted margin, a predicted combined score, and a range showing how far off the result could reasonably land.",
  "What you do with that is up to you.",
].join(" ");

export const HOW_IT_WORKS_TITLE = "How the model works";

/**
 * Static how-it-works body. The range sentence is completed at render time from
 * the worked-example game's margin_interval_nominal when one is available.
 */
export const HOW_IT_WORKS_PARAGRAPHS_LEAD = [
  "I keep a rating for every team's offense and defense. After each completed game, those ratings update. Early in the season they wobble. By midseason they usually settle.",
  "A second step turns those ratings into a predicted margin (who's favored and by how many points) and a predicted total for both teams combined. Week to week, most of the change comes from the ratings moving.",
] as const;

/** Fallback range sentence when no example game / nominal is available. */
export const HOW_IT_WORKS_RANGE_FALLBACK =
  "The range is where the final margin is likely to land. Some games finish outside it, and the Results page shows which ones did.";

export const WHAT_RIDGE_WONT_SHOW_TITLE = "What Ridge won't show you";

/**
 * Explicit exception to the betting-language grep gate: this section states
 * the product refusal (no picks, lines, or betting advice).
 */
export const WHAT_RIDGE_WONT_SHOW_PARAGRAPHS = [
  "Ridge doesn't publish picks, sportsbook lines, or betting advice. I use lines behind the scenes to check the model after games are played, but they stay off the site. Ridge is about what's likely to happen on the field. Betting is a different question, and this site doesn't try to answer it.",
] as const;

export const UPDATE_SCHEDULE_TITLE = "When forecasts update";

export const HONESTY_TITLE = "Honesty commitments";

export const HONESTY_COPY =
  "Every forecast shows its uncertainty when there's enough data to measure it. If something's missing, it stays blank. I never fill gaps with zeros or averages. If the data behind a forecast is out of date, the game is marked. There are no suggested wagers anywhere on the site.";

/** @deprecated Prefer HONESTY_COPY — kept as a one-element list for map-style callers. */
export const HONESTY_PARAGRAPHS = [HONESTY_COPY] as const;

export const WORKED_EXAMPLE_TITLE = "A game, read out loud";

export const WORKED_EXAMPLE_FALLBACK =
  "When a week of forecasts is available, this section walks through one game with live numbers. Right now there is no slate to read, so here is the shape: a favored team and margin, a win chance, a range for the final margin, and a conviction tier that only labels how decisive the forecast looks.";

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

/**
 * Format publish_schedule fields for the About cadence paragraph.
 *
 * The artifact strings are day-of-week cadence labels ("Tue 06:00 UTC"), not
 * absolute ISO timestamps. They cannot be converted to viewer-local time
 * without inventing a calendar date, so they are shown as published (UTC).
 */
export function formatPublishScheduleCopy(schedule: {
  primary: string;
  refresh: string;
  postgame_ratings: string;
}): string {
  return `Primary publish is ${schedule.primary}. Refreshes run ${schedule.refresh}. Team ratings also update after the weekend (${schedule.postgame_ratings}).`;
}

/**
 * How-it-works range sentence from the worked-example nominal.
 * Uses the same coverage formatter as game summaries (N times in 10 or p%).
 */
export function howItWorksRangeSentence(nominal: number | null | undefined): string {
  const coverage = formatNominalCoveragePhrase(nominal);
  if (coverage.kind === "absent") {
    return HOW_IT_WORKS_RANGE_FALLBACK;
  }
  if (coverage.kind === "n_in_10") {
    return `The range is set so the final margin should land inside it about ${coverage.label}. Some games finish outside it, and the Results page shows which ones did.`;
  }
  return `The range is set so the final margin should land inside it about ${coverage.pctLabel} of the time. Some games finish outside it, and the Results page shows which ones did.`;
}
