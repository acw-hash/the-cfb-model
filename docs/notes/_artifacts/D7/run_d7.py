"""D7: resolve stability (RE meta + week interaction), close diagnostic phase."""

from __future__ import annotations

import json
import time
from pathlib import Path

from ncaa_quant.evaluation.d4_eval import (
    CANONICAL_V2_SHA,
    load_canonical_v2_frame,
    verify_canonical_v2_sha,
)
from ncaa_quant.evaluation.d6_eval import (
    join_cfbd_closes_for_evaluation,
    load_cfbd_lines,
)
from ncaa_quant.evaluation.d7_eval import preregister_holdout, run_d7_diagnostics

ROOT = Path(__file__).resolve().parents[4]
ART = ROOT / "docs" / "notes" / "_artifacts" / "D7"
D6_ART = ROOT / "docs" / "notes" / "_artifacts" / "D6" / "d6_results.json"
PRED = (
    ROOT
    / "data"
    / "backtests"
    / "task23_fundamental"
    / "fundamental"
    / "predictions_enriched.parquet"
)
STAGED = ROOT / "data" / "staged"


def main() -> None:
    ART.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    # Pre-register holdout BEFORE any fitting (written to disk first).
    plan = preregister_holdout()
    (ART / "holdout_preregistration.json").write_text(
        json.dumps(plan, indent=2), encoding="utf-8"
    )
    print("pre-registered holdout:", json.dumps(plan), flush=True)

    sha = verify_canonical_v2_sha(
        ROOT / "docs" / "notes" / "_artifacts" / "D3" / "canonical_v2.json"
    )
    assert sha == CANONICAL_V2_SHA

    d6 = json.loads(D6_ART.read_text(encoding="utf-8"))
    enc = d6["encompassing"]

    frame = load_canonical_v2_frame(PRED, exclude_2019_w1_4=True)
    seasons = sorted(int(s) for s in frame["season"].unique())
    lines = load_cfbd_lines(STAGED, seasons=list(range(2014, max(seasons) + 1)))
    joined, _join_meta = join_cfbd_closes_for_evaluation(frame, lines, only_fill_null=True)

    print("running D7 diagnostics…", flush=True)
    out = run_d7_diagnostics(joined, enc, n_boot=1000, seed=0)
    out["canonical_v2_sha"] = sha
    out["wall_clock_s"] = time.time() - t0

    path = ART / "d7_results.json"
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")

    opening = out["opening_summary"]
    print(
        json.dumps(
            {
                "I2": opening["i2"],
                "tau2": opening["tau2"],
                "re_b2": opening["re_b2"],
                "re_ci95": opening["re_ci95"],
                "interaction_p": opening["interaction_p"],
                "holdout": out["holdout_early_week"]["status"],
                "diagnostic_phase_closed": out["diagnostic_phase_closed"],
                "early_w": out["optimal_w_weeks_1_5"]["w"],
                "roi": out["early_week_edge_roi"]["expected_roi_per_bet"],
                "roi_ci": out["early_week_edge_roi"]["roi_ci95"],
            },
            indent=2,
            default=str,
        ),
        flush=True,
    )
    print("wrote", path, "in", out["wall_clock_s"], "s", flush=True)


if __name__ == "__main__":
    main()
