import { Figure } from "@/components/Figure/Figure";
import { PublishedAtStamp } from "@/components/PublishedAtStamp/PublishedAtStamp";
import type { RefreshKind } from "@/lib/artifacts/types";
import { formatRefreshKind } from "@/lib/this-week/refresh-kind";

import styles from "./ThisWeekHeader.module.css";

interface ThisWeekHeaderProps {
  season: number;
  week: number;
  publishedAt: string;
  refreshKind: RefreshKind;
}

/** This Week page header — season/week, prominent published_at, refresh_kind (§5.1). */
export function ThisWeekHeader({
  season,
  week,
  publishedAt,
  refreshKind,
}: ThisWeekHeaderProps): React.ReactElement {
  return (
    <header className={styles.header}>
      <div className={styles.titles}>
        <Figure variant="c2" className={styles.season}>
          {season}
        </Figure>
        <h1 className={styles.title}>
          Week <Figure variant="t1">{week}</Figure>
        </h1>
      </div>
      <div className={styles.meta}>
        <PublishedAtStamp publishedAt={publishedAt} />
        <p className={styles.refresh}>{formatRefreshKind(refreshKind)}</p>
      </div>
    </header>
  );
}
