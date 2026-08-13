import type { ConvictionTier } from "@/lib/artifacts/types";

import styles from "./TierChip.module.css";

interface TierChipProps {
  convictionTier: ConvictionTier | null;
  convictionLabel: string | null;
}

/** Pill chip — label from conviction_label verbatim (§4.3, §2). */
export function TierChip({
  convictionTier,
  convictionLabel,
}: TierChipProps): React.ReactElement | null {
  if (convictionTier == null || convictionLabel == null) {
    return null;
  }

  const isTossUp = convictionTier === "toss_up";
  const className = [styles.chip, isTossUp ? styles.tossUp : styles[tierClass(convictionTier)]]
    .filter(Boolean)
    .join(" ");

  return <span className={className}>{convictionLabel}</span>;
}

function tierClass(tier: ConvictionTier): string {
  switch (tier) {
    case "strong_lean":
      return "strong";
    case "clear_lean":
      return "clear";
    case "lean":
      return "lean";
    case "toss_up":
      return "tossUp";
  }
}
