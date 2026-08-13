import type { GradeStatus } from "@/lib/artifacts/types";

/** Explicit display copy for every grade_status — never omit, never blank-as-zero. */
export const GRADE_STATUS_LABEL: Record<GradeStatus, string> = {
  graded: "Graded",
  no_pre_kickoff_publish: "No pre-kickoff publish",
  game_not_final: "Game not final",
  postgame_missing: "Postgame missing",
};

export function formatGradeStatus(status: GradeStatus): string {
  return GRADE_STATUS_LABEL[status];
}

export const UNGRADED_STATUSES: GradeStatus[] = [
  "no_pre_kickoff_publish",
  "game_not_final",
  "postgame_missing",
];
