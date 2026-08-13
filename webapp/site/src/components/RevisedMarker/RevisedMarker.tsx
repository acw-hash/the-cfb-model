import type { ConvictionTier } from "@/lib/artifacts/types";

import styles from "./RevisedMarker.module.css";

interface RevisedMarkerProps {
  tierRevisedSincePrimary: boolean;
  convictionTier: ConvictionTier | null;
  tierPrimary: ConvictionTier | null;
}

/** Quiet "Revised" badge when tier changed since Tuesday primary (§2.5). */
export function RevisedMarker({
  tierRevisedSincePrimary,
  convictionTier,
  tierPrimary,
}: RevisedMarkerProps): React.ReactElement | null {
  if (!tierRevisedSincePrimary || convictionTier == null) {
    return null;
  }

  const tooltip =
    tierPrimary != null
      ? `Conviction tier changed since Tuesday primary publish (${tierPrimary} \u2192 ${convictionTier}).`
      : "Conviction tier changed since Tuesday primary publish.";

  return (
    <span className={styles.marker} title={tooltip}>
      Revised
    </span>
  );
}
