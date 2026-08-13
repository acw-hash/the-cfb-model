import { Figure } from "@/components/Figure/Figure";
import { stalenessBannerMessage } from "@/lib/formatting/time";

import styles from "./StalenessBanner.module.css";

interface StalenessBannerProps {
  publishedAt: string;
}

/** Site-wide staleness banner (>36h past expected slot) per §3.2. */
export function StalenessBanner({ publishedAt }: StalenessBannerProps): React.ReactElement {
  return (
    <div className={styles.banner} role="status">
      <Figure variant="c1">{stalenessBannerMessage(publishedAt)}</Figure>
    </div>
  );
}
