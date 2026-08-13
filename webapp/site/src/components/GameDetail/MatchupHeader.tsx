import { Figure } from "@/components/Figure/Figure";
import { formatKickoffLocal } from "@/lib/formatting/time";

import styles from "./MatchupHeader.module.css";

interface MatchupHeaderProps {
  awayTeam: string;
  homeTeam: string;
  kickoffUtc: string;
  neutralSite: boolean;
}

/** Matchup + kickoff from artifact fields only (§5.2). */
export function MatchupHeader({
  awayTeam,
  homeTeam,
  kickoffUtc,
  neutralSite,
}: MatchupHeaderProps): React.ReactElement {
  const kickoff = formatKickoffLocal(kickoffUtc);
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
      <Figure variant="b2" className={styles.kickoff} title={`${kickoff.utc} UTC`}>
        {kickoff.local}
      </Figure>
    </header>
  );
}
