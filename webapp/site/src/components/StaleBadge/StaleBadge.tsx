import { Figure } from "@/components/Figure/Figure";
import type { StaleSource } from "@/lib/artifacts/types";
import { formatAgeHours } from "@/lib/formatting/time";

import styles from "./StaleBadge.module.css";

interface StaleBadgeProps {
  staleStamp: string | null;
  sources: StaleSource[];
}

/** Per-game STALE badge from backend contract (§3.2, §4.3). */
export function StaleBadge({ staleStamp, sources }: StaleBadgeProps): React.ReactElement | null {
  if (!staleStamp) {
    return null;
  }

  const tooltip = sources
    .map((s) => `${s.source}: ${formatAgeHours(s.age_hours)} (last good ${s.last_good_at})`)
    .join("; ");

  return (
    <Figure variant="c1" className={styles.badge} title={tooltip || undefined}>
      {staleStamp}
    </Figure>
  );
}
