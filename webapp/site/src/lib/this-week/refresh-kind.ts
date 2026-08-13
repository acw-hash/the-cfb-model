import type { RefreshKind } from "@/lib/artifacts/types";

/** Display labels for RefreshKind — spec §1.2 schedule names, not invented copy. */
const REFRESH_KIND_LABEL: Record<RefreshKind, string> = {
  tuesday_primary: "Tuesday primary",
  daily_refresh: "Daily refresh",
  t_minus_6h: "T\u22126h",
  t_minus_1h: "T\u22121h",
};

export function formatRefreshKind(kind: RefreshKind): string {
  return REFRESH_KIND_LABEL[kind];
}
