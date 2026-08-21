import { KickoffTime } from "@/components/KickoffTime/KickoffTime";

import styles from "./MatchupHeader.module.css";

interface MatchupHeaderProps {
  awayTeam: string;
  homeTeam: string;
  kickoffUtc: string | null;
  neutralSite: boolean;
  /** Test injection — production resolves visitor TZ inside KickoffTime. */
  timeZone?: string;
}

/** Matchup + kickoff from artifact fields only (§5.2). */
export function MatchupHeader({
  awayTeam,
  homeTeam,
  kickoffUtc,
  neutralSite,
  timeZone,
}: MatchupHeaderProps): React.ReactElement {
  const vs = `${awayTeam} @ ${homeTeam}`;

  return (
    <header className={styles.header}>
      <h1 className={styles.matchup}>
        {vs}
        {neutralSite ? (
          <span className={styles.neutral} title="Neutral site">
            N
          </span>
        ) : null}
      </h1>
      <KickoffTime
        kickoffUtc={kickoffUtc}
        variant="b2"
        className={styles.kickoff}
        timeZone={timeZone}
      />
    </header>
  );
}
