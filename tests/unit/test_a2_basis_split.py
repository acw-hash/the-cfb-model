"""Task 23-FIX-CLOSE P2-7 — A2 components reported per basis."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ncaa_quant.evaluation.metrics import (
    BasisMetricRecord,
    MetricsError,
    report_a2_components_by_basis,
)


def _mixed_basis_frame() -> pd.DataFrame:
    """2018–2019 continuous; only 2019 has closing lines for ATS."""
    rng = np.random.default_rng(7)
    rows: list[dict[str, object]] = []
    for season, n, has_line in ((2018, 40, False), (2019, 30, True)):
        for _i in range(n):
            margin = float(rng.normal(0, 14))
            pred = margin + float(rng.normal(0, 5))
            spread = float(rng.choice([-7.0, -3.0, 3.0, 7.0])) if has_line else float("nan")
            rows.append(
                {
                    "season": season,
                    "pred_margin": pred,
                    "realized_margin": margin,
                    "sigma_m": 12.0 + float(rng.normal(0, 0.5)),
                    "p_ats_home": 0.45 + 0.1 * float(rng.random()),
                    "spread_close": spread,
                }
            )
    return pd.DataFrame(rows)


def test_a2_basis_split_emits_separate_records() -> None:
    frame = _mixed_basis_frame()
    records = report_a2_components_by_basis(frame)
    by_metric = {r.metric: r for r in records}

    assert set(by_metric) == {"mae_margin", "crps_margin", "ats_accuracy"}
    assert by_metric["mae_margin"].seasons == (2018, 2019)
    assert by_metric["mae_margin"].n == 70
    assert by_metric["mae_margin"].basis == "all_seasons"
    assert by_metric["crps_margin"].seasons == (2018, 2019)
    assert by_metric["crps_margin"].basis == "all_seasons"

    ats = by_metric["ats_accuracy"]
    assert ats.seasons == (2019,)
    assert ats.n == 30
    assert ats.basis == "line_backed"
    assert all(isinstance(r, BasisMetricRecord) for r in records)


def test_a2_basis_split_has_no_pooled_render() -> None:
    """Caller must cite records separately — no pooled helper exists."""
    import ncaa_quant.evaluation.metrics as metrics_mod

    assert not hasattr(metrics_mod, "pool_a2_components")
    assert not hasattr(metrics_mod, "render_pooled_a2")
    records = report_a2_components_by_basis(_mixed_basis_frame())
    # Distinct bases must not be reducible to one n / one season list.
    bases = {r.basis for r in records}
    assert bases == {"all_seasons", "line_backed"}
    season_sets = {r.seasons for r in records}
    assert len(season_sets) == 2


def test_a2_basis_split_requires_season_column() -> None:
    with pytest.raises(MetricsError, match="season"):
        report_a2_components_by_basis(pd.DataFrame({"pred_margin": [1.0]}))
