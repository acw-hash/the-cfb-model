/**
 * Plain-language glosses for the three provenance labels (§5.2).
 * Meanings stay on the page so a reader does not have to leave.
 */
export const PROVENANCE_GLOSS = {
  vintage: {
    field: "vintage_label" as const,
    title: "Vintage",
    meaning: "Which graded training run produced these numbers.",
  },
  ensemble: {
    field: "ensemble_scope_label" as const,
    title: "Ensemble",
    meaning:
      "Which models were combined. Reduced means a smaller set than the full experimental ensemble.",
  },
  featureTime: {
    field: "feature_time_label" as const,
    title: "Feature time",
    meaning:
      "When inputs were frozen. Tuesday decision means later information is not in this forecast.",
  },
};
