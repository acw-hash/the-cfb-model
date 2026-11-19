import styles from "./RatingsDirectionNote.module.css";

type Dimension = "offense" | "defense";

interface RatingsDirectionNoteProps {
  dimension: Dimension;
}

const COPY: Record<Dimension, { testId: string; text: string }> = {
  offense: {
    testId: "offense-direction",
    text: "Higher is better on offense. Values are relative to the league average.",
  },
  defense: {
    testId: "defense-direction",
    text:
      "Higher is better on defense. The rating measures EPA suppressed, so a larger number means a tougher defense.",
  },
};

/**
 * Direction label for every surface that plots offense/defense ratings.
 * Kept identical across game detail, Visual team walkthrough, and homepage note.
 */
export function RatingsDirectionNote({
  dimension,
}: RatingsDirectionNoteProps): React.ReactElement {
  const { testId, text } = COPY[dimension];
  return (
    <p className={styles.note} data-testid={testId}>
      {text}
    </p>
  );
}

/** Compact homepage / slate pointer — SSR so `/` HTML contains the phrase. */
export function RatingsDirectionHomeNote(): React.ReactElement {
  return (
    <p className={styles.homeNote} data-testid="ratings-direction-home">
      Game pages show Stage-1 offense and defense ratings. Higher is better.
    </p>
  );
}
