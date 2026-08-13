import { RevisedMarker } from "@/components/RevisedMarker/RevisedMarker";
import { TierChip } from "@/components/TierChip/TierChip";
import type { ConvictionTier } from "@/lib/artifacts/types";
import { TIER_GROUP_LABEL } from "@/lib/this-week/sort";

import styles from "./RevisionBlock.module.css";

interface RevisionBlockProps {
  convictionTier: ConvictionTier | null;
  convictionLabel: string | null;
  tierPrimary: ConvictionTier | null;
  tierRevisedSincePrimary: boolean;
}

/**
 * Current tier verbatim from conviction_label, plus Tuesday-primary category
 * when revised. Does not invent intermediate tiers (W1A-FIX §2.3 two-band jump).
 */
export function RevisionBlock({
  convictionTier,
  convictionLabel,
  tierPrimary,
  tierRevisedSincePrimary,
}: RevisionBlockProps): React.ReactElement {
  const showPrimary = tierRevisedSincePrimary && convictionTier != null && tierPrimary != null;

  return (
    <section className={styles.block} data-testid="revision-block">
      <h2 className={styles.label}>Conviction</h2>
      <div className={styles.row}>
        <TierChip convictionTier={convictionTier} convictionLabel={convictionLabel} />
        <RevisedMarker
          tierRevisedSincePrimary={tierRevisedSincePrimary}
          convictionTier={convictionTier}
          tierPrimary={tierPrimary}
        />
        {convictionTier == null ? <span className={styles.suppressed}>Tier not shown</span> : null}
      </div>
      {showPrimary ? (
        <p className={styles.primary} data-testid="tuesday-primary">
          Tuesday primary: {TIER_GROUP_LABEL[tierPrimary]}
        </p>
      ) : null}
    </section>
  );
}
