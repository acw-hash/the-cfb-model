"""Unit tests for canonical evaluation helpers (D2)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ncaa_quant.evaluation.canonical_eval import (
    build_comparison_rows,
    compose_canonical_set,
    file_sha256,
    fit_l1_ols,
    load_canonical_config,
    nnls_from_member_columns,
    score_predictor,
    sigma_diagnostics,
    write_canonical_artifact,
)
from ncaa_quant.evaluation.walkforward import PredictionQualityGateResult


def _frame(n: int = 40) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    seasons = np.array([2019, 2021, 2022, 2023])[(np.arange(n) % 4)]
    return pd.DataFrame(
        {
            "game_id": np.arange(n),
            "season": seasons,
            "week": (np.arange(n) % 10) + 1,
            "pred_margin": rng.normal(3, 8, n),
            "realized_margin": rng.normal(3, 16, n),
            "spread_close": np.where(np.arange(n) % 3 == 0, rng.normal(-3, 7, n), np.nan),
            "sigma_m": rng.uniform(10, 18, n),
            "exclude_from_headline": False,
            "home_team_id": 10 + (np.arange(n) % 5),
            "away_team_id": 20 + (np.arange(n) % 5),
        }
    )


def test_load_canonical_config() -> None:
    cfg = load_canonical_config()
    assert cfg["name"] == "canonical_v2"
    assert 2020 not in cfg["walkforward"]["test_seasons"]
    assert cfg["inclusion"]["fcs_opponent_games"] == "include"
    assert cfg["inclusion"]["exclude_season_weeks"][0]["season"] == 2019


def test_compose_and_score() -> None:
    frame = _frame()
    comp = compose_canonical_set(frame, fcs_rule="include")
    assert comp.n_total == 40
    assert comp.sd_y_full > 0
    assert "2019" in {str(k) for k in comp.n_by_season} or 2019 in comp.n_by_season
    y = frame["realized_margin"].to_numpy()
    mu = frame["pred_margin"].to_numpy()
    row = score_predictor(frame, mu, name="pub", sigma=frame["sigma_m"].to_numpy())
    assert row["n"] == 40
    assert np.isfinite(row["mae"])
    l1 = fit_l1_ols(mu[:30], y[:30], mu[30:])
    assert l1.shape == (10,)


def test_comparison_sigma_nnls_and_artifact(tmp_path: Path) -> None:
    frame = _frame(60)
    elo = frame["pred_margin"].to_numpy() * 0.8
    l1 = fit_l1_ols(elo[:40], frame["realized_margin"].to_numpy()[:40], elo)
    rows = build_comparison_rows(
        frame,
        elo_mu=elo,
        l1_mu=l1,
        lgbm_mu=frame["pred_margin"].to_numpy(),
        nnls_mu=frame["pred_margin"].to_numpy(),
        nnls_weights={"lgbm_mu_margin": 0.7, "enet_mu_margin": 0.3},
    )
    names = {r["predictor"] for r in rows}
    assert "constant_train_mean" in names
    assert "devigged_market" in names
    sig = sigma_diagnostics(frame)
    assert sig["n"] > 0
    assert "coverage" in sig
    oof = pd.DataFrame(
        {
            "lgbm_mu_margin": frame["pred_margin"],
            "enet_mu_margin": elo,
            "realized_margin": frame["realized_margin"],
            "is_out_of_fold": True,
        }
    )
    nnls = nnls_from_member_columns(oof, ["lgbm_mu_margin", "enet_mu_margin"])
    assert "weights" in nnls
    assert nnls["condition_number"] > 0
    gate = PredictionQualityGateResult(
        n_scored=60,
        zero_mu_rate=0.0,
        n_null_mu=0,
        n_zero_sd_blocks=0,
        zero_sd_blocks=(),
        min_n_train_games=100,
        max_zero_mu_rate=0.001,
        min_train_games_required=50,
        passed=True,
        failures=(),
    )
    src = tmp_path / "preds.parquet"
    frame.to_parquet(src)
    out, digest = write_canonical_artifact(
        composition=compose_canonical_set(frame),
        comparison=rows,
        gate=gate.as_dict(),
        sigma=sig,
        nnls_folds=[nnls],
        source_predictions=src,
        out_dir=tmp_path / "art",
    )
    assert out.is_file()
    assert len(digest) == 64
    assert file_sha256(src) == file_sha256(src)


def test_sigma_diagnostics_missing_sigma() -> None:
    frame = _frame(20).drop(columns=["sigma_m"])
    out = sigma_diagnostics(frame)
    assert out["n"] == 0
