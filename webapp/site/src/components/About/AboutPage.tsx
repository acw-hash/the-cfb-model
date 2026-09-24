import type { GamePrediction, PublishSchedule } from "@/lib/artifacts/types";
import {
  ATTRIBUTION_COPY,
  ATTRIBUTION_HEADING,
  CFBD_ATTRIBUTION,
  HONESTY_COPY,
  HONESTY_TITLE,
  HOW_IT_WORKS_PARAGRAPHS_LEAD,
  HOW_IT_WORKS_TITLE,
  RIDGE_IDENTITY,
  UPDATE_SCHEDULE_TITLE,
  WHAT_RIDGE_WONT_SHOW_PARAGRAPHS,
  WHAT_RIDGE_WONT_SHOW_TITLE,
  WORKED_EXAMPLE_FALLBACK,
  WORKED_EXAMPLE_TITLE,
  disclaimerForYear,
  formatPublishScheduleCopy,
  howItWorksRangeSentence,
} from "@/lib/about/copy";
import { formatProbability } from "@/lib/formatting/numbers";
import { TIER_ENTER } from "@/lib/formatting/tier-copy";
import {
  favoredSideFromMargin,
  favoredWinProbability,
  formatFavoredWinChance,
  formatIntervalLandSentence,
  formatTeamNamedInterval,
  formatTeamNamedMargin,
  formatWinChanceSentence,
} from "@/lib/formatting/team-margin";
import { probabilityIsCredible } from "@/lib/game-detail/credibility";

import { NextUpdateLine } from "./NextUpdateLine";
import styles from "./AboutPage.module.css";

interface AboutPageProps {
  /** UTC calendar year for © line — server-rendered. */
  year: number;
  publishSchedule?: PublishSchedule | null;
  /** ISO UTC next publish; omitted from UI when null or past. */
  nextExpectedPublishUtc?: string | null;
  /** One live game for the worked example; null → fallback copy. */
  exampleGame?: GamePrediction | null;
  /** Test injection for NextUpdateLine timezone. */
  timeZone?: string;
}

function underdogUpsetSentence(game: GamePrediction): string | null {
  if (!probabilityIsCredible(game, "p_win_home")) {
    return null;
  }
  const pFav = favoredWinProbability(game.mu_margin, game.p_win_home);
  if (pFav == null) {
    return null;
  }
  const side = favoredSideFromMargin(game.mu_margin, game.p_win_home);
  if (side == null) {
    return null;
  }
  const underdog = side === "home" ? game.away_team : game.home_team;
  const pctLabel = formatProbability(pFav);
  if (pctLabel == null) {
    return null;
  }
  const underdogTimes = Math.round((1 - pFav) * 100);
  return `Even at ${pctLabel}, ${underdog} wins about ${underdogTimes} times in 100.`;
}

function pickExampleGame(game: GamePrediction): string[] {
  const paragraphs: string[] = [];
  const favorite = formatTeamNamedMargin(
    game.mu_margin,
    game.home_team,
    game.away_team,
    game.sigma_margin,
    game.p_win_home,
  );
  if (favorite == null) {
    return [WORKED_EXAMPLE_FALLBACK];
  }

  paragraphs.push(`Take ${game.away_team} at ${game.home_team}. Ridge has it ${favorite}.`);

  const side = favoredSideFromMargin(game.mu_margin, game.p_win_home);
  const favored = side === "away" ? game.away_team : game.home_team;

  if (probabilityIsCredible(game, "p_win_home") && favored != null) {
    const pct = formatFavoredWinChance(game.mu_margin, game.p_win_home);
    if (pct != null) {
      paragraphs.push(formatWinChanceSentence(favored, pct));
    }
  }

  const range = formatTeamNamedInterval(
    game.margin_interval_lo,
    game.margin_interval_hi,
    game.home_team,
    game.away_team,
    game.sigma_margin,
    "and",
  );
  if (range != null) {
    paragraphs.push(formatIntervalLandSentence(range, game.margin_interval_nominal));
  }

  const upset = underdogUpsetSentence(game);
  if (upset != null) {
    paragraphs.push(upset);
  }

  if (game.conviction_label) {
    const strong = formatProbability(TIER_ENTER.strong_lean);
    const clear = formatProbability(TIER_ENTER.clear_lean);
    const lean = formatProbability(TIER_ENTER.lean);
    paragraphs.push(
      `The tier on this game is ${game.conviction_label}. Tiers sort games by how one-sided the forecast is: Strong lean at about ${strong} or higher, Clear lean from about ${clear}, Lean from about ${lean}, and Toss-up below that. Labels are sticky, so a game sitting near a cutoff keeps its tier until the chance moves clearly past it. A tier describes the forecast. It's not a recommendation.`,
    );
  }

  return paragraphs;
}

/** Methodology / About composition (§5.4, §6, clarity). */
export function AboutPage({
  year,
  publishSchedule = null,
  nextExpectedPublishUtc = null,
  exampleGame = null,
  timeZone,
}: AboutPageProps): React.ReactElement {
  const exampleParagraphs =
    exampleGame != null ? pickExampleGame(exampleGame) : [WORKED_EXAMPLE_FALLBACK];
  const scheduleCopy = publishSchedule
    ? formatPublishScheduleCopy(publishSchedule)
    : "Primary publish is Tuesday 06:00 UTC. Refreshes run Thursday–Saturday 06:00 UTC. Team ratings also update after the weekend (Sunday 06:00 UTC).";
  const rangeSentence = howItWorksRangeSentence(exampleGame?.margin_interval_nominal ?? null);

  return (
    <article className={styles.page} data-testid="about-page">
      <header className={styles.header}>
        <h1 className={styles.title}>About</h1>
        <p className={styles.identity} data-testid="ridge-identity">
          {RIDGE_IDENTITY}
        </p>
      </header>

      <section id="worked-example" className={styles.section} data-testid="about-worked-example">
        <h2 className={styles.sectionTitle}>{WORKED_EXAMPLE_TITLE}</h2>
        {exampleParagraphs.map((p) => (
          <p key={p.slice(0, 48)} className={styles.body}>
            {p}
          </p>
        ))}
      </section>

      <section id="how-it-works" className={styles.section} data-testid="about-how-it-works">
        <h2 className={styles.sectionTitle}>{HOW_IT_WORKS_TITLE}</h2>
        {HOW_IT_WORKS_PARAGRAPHS_LEAD.map((p) => (
          <p key={p.slice(0, 48)} className={styles.body}>
            {p}
          </p>
        ))}
        <p className={styles.body}>{rangeSentence}</p>
      </section>

      <section
        id="what-ridge-wont-show"
        className={styles.section}
        data-testid="about-what-ridge-wont-show"
      >
        <h2 className={styles.sectionTitle}>{WHAT_RIDGE_WONT_SHOW_TITLE}</h2>
        {WHAT_RIDGE_WONT_SHOW_PARAGRAPHS.map((p) => (
          <p key={p.slice(0, 48)} className={styles.body}>
            {p}
          </p>
        ))}
      </section>

      <section id="update-schedule" className={styles.section} data-testid="about-update-schedule">
        <h2 className={styles.sectionTitle}>{UPDATE_SCHEDULE_TITLE}</h2>
        <p
          className={styles.body}
          title="Schedule strings are UTC cadence labels from meta.publish_schedule"
        >
          {scheduleCopy}
        </p>
        {nextExpectedPublishUtc ? (
          <NextUpdateLine nextExpectedPublishUtc={nextExpectedPublishUtc} timeZone={timeZone} />
        ) : null}
      </section>

      <section id="honesty" className={styles.section} data-testid="about-honesty">
        <h2 className={styles.sectionTitle}>{HONESTY_TITLE}</h2>
        <p className={styles.body}>{HONESTY_COPY}</p>
      </section>

      <section id="disclaimer" className={styles.section} data-testid="about-disclaimer">
        <h2 className={styles.sectionTitle}>Disclaimer</h2>
        <p className={styles.disclaimerBlock}>{disclaimerForYear(year)}</p>
      </section>

      <section
        id="responsible-gambling"
        className={styles.section}
        data-testid="about-responsible-gambling"
      >
        <h2 className={styles.sectionTitle}>Responsible gambling</h2>
        <p className={styles.rgBlock}>
          If you or someone you know has a gambling problem, call{" "}
          <a href="tel:18004262537">1-800-GAMBLER</a> (1-800-426-2537). Help is available 24/7.
          Ridge does not accept wagers and is not affiliated with any sportsbook.
        </p>
      </section>

      <section id="attribution" className={styles.section} data-testid="about-attribution">
        <h2 className={styles.sectionTitle}>{ATTRIBUTION_HEADING}</h2>
        <p className={styles.attribution}>{CFBD_ATTRIBUTION}</p>
        <p className={styles.placeholder} data-testid="attribution-placeholder">
          {ATTRIBUTION_COPY}
        </p>
      </section>
    </article>
  );
}

/** Select one game with a readable μ for the About worked example. */
export function selectAboutExampleGame(games: GamePrediction[]): GamePrediction | null {
  const withMargin = games.find(
    (g) => g.mu_margin != null && g.sigma_margin_credible && g.p_win_home_credible,
  );
  return withMargin ?? games.find((g) => g.mu_margin != null) ?? null;
}
