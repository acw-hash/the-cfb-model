import { Figure } from "@/components/Figure/Figure";
import { formatAbsoluteUtc, formatRelativeAge } from "@/lib/formatting/time";

import styles from "./PublishedAtStamp.module.css";

interface PublishedAtStampProps {
  publishedAt: string;
  now?: Date;
}

/** Prominent published_at display — relative + absolute (§3, §4). */
export function PublishedAtStamp({ publishedAt, now }: PublishedAtStampProps): React.ReactElement {
  const relative = formatRelativeAge(publishedAt, now);
  const absolute = formatAbsoluteUtc(publishedAt);

  return (
    <div className={styles.stamp} aria-label={`Published ${absolute}`}>
      <Figure variant="c2" className={styles.relative}>
        Updated {relative}
      </Figure>
      <Figure variant="c2" className={styles.absolute}>
        {absolute}
      </Figure>
    </div>
  );
}
