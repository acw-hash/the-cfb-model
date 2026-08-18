/**
 * Lay-reader verdict body — faithful to track_record.verdict / 23-reval §7,
 * without inventing or softening numbers. DESIGN §5.3 also requires the artifact
 * plain_language paragraph on the page (shown separately as the recorded finding).
 */
export const VERDICT_LAY_SUMMARY = [
  "Point predictions are credible: the rating engine learns in-season, and the recorded error scores are in a sane range.",
  "No betting edge has been demonstrated against the closing line.",
  "Against-the-spread results are 48.9% [47.5%, 50.5%] on snapshots and 49.9% [46.9%, 52.3%] in 2019; both intervals include 50%.",
  "Log-loss is 0.78–0.93 versus the market baseline 0.693. CLV and honest over/under remain unmeasurable.",
].join(" ");

/** Explicit on-page statement that there is no single accuracy headline. */
export const NO_SINGLE_NUMBER_COPY =
  "There is no single accuracy number for this model. Each metric below stands on its own, with its sample size and confidence interval when one was recorded.";

/** Lockbox / scope copy — deliberate absence of 2025 and empty live start. */
export const SCOPE_COPY =
  "These recorded numbers cover walk-forward evaluation on seasons 2019 and 2021–2024. Season 2025 is held as a lockbox and is never evaluated here. Live publishing begins in 2026; the live graded record starts empty until Week 1 games are final and graded.";

export const LOCKBOX_NO_AGGREGATE_COPY =
  "This page does not compute any aggregate accuracy statistic from graded games for seasons 2025 or earlier — no overall percentages, no interval-coverage totals. Per-game rows only. That absence is deliberate.";

export const FIXTURE_GRADES_COPY =
  "The graded games below are development fixture data (season 2024, allow_historical_fixture). They are not Ridge’s published live track record. The live record begins with 2026 grades.";

export const EMPTY_LIVE_COPY =
  "Results available after Week 1 completes. Live grading for 2026 has not started yet — this is the empty launch state, not a missing file.";

export const MISSING_METRIC_COPY = "Not in the recorded artifact";
