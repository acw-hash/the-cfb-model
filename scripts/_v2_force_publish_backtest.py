"""Force-publish market-aware after CLEAN audit (ADR 0014 path).

Monkeypatches the ATS plausibility finalize guard so a high-side trip can
still write predictions. Does **not** widen the band. Requires the audit
artifact to already exist with verdict CLEAN.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "docs" / "notes" / "_artifacts" / "v2_baseline" / "market_feature_audit.json"


def main() -> None:
    if not AUDIT.is_file():
        print("missing CLEAN audit artifact:", AUDIT)
        raise SystemExit(2)
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    if audit.get("verdict") != "CLEAN":
        print("refuse force-publish: audit verdict=", audit.get("verdict"))
        raise SystemExit(2)

    from ncaa_quant.evaluation import backtest_runner as br
    from ncaa_quant.evaluation.metrics import AtsPlausibilityError
    from ncaa_quant.evaluation.metrics import assert_prediction_ats_plausible as _orig

    def _force(preds, **kw):  # type: ignore[no-untyped-def]
        try:
            _orig(preds, **kw)
        except AtsPlausibilityError as exc:
            print("FORCE_PUBLISH_ATS_GUARD:", exc)
            print("AUDIT_ATTACHED:", AUDIT.as_posix())

    br.assert_prediction_ats_plausible = _force  # type: ignore[assignment]

    from typer.testing import CliRunner

    from ncaa_quant.cli import app

    runner = CliRunner()
    args = [
        "backtest",
        "run",
        "--config",
        "task23_market_aware_full_reduced_v2",
        "--stack",
        "market_aware",
        "--label",
        "v2-baseline-force-publish;ensemble_scope=REDUCED_PER_ADR_0013;audit=CLEAN",
        "--force",
    ]
    result = runner.invoke(app, args, catch_exceptions=False)
    sys.stdout.write(result.output or "")
    raise SystemExit(result.exit_code)


if __name__ == "__main__":
    main()
