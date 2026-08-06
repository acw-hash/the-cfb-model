"""Unit tests for TASK D1 margin-μ diagnostics (read-only)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ncaa_quant.evaluation.diagnostics_mu import (
    block_a,
    block_b_join_integrity,
    block_b_target_contract,
    block_c_layer_ladder,
    block_d_ensemble_health,
    block_f_feature_health,
    block_g_calibration,
    block_h_slices,
    detect_structural_stop,
    load_prediction_frame,
    reconcile_history,
    regress_y_on_yhat,
    render_notes,
    run_mu_diagnostics,
    score_predictor,
    train_mean_margin,
)


def _toy_frame(n: int = 40, *, mu_scale: float = 1.0, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    home = rng.integers(10, 45, size=n)
    away = rng.integers(10, 45, size=n)
    y = home.astype(float) - away.astype(float)
    noise = rng.normal(0, 8.0, size=n)
    mu = mu_scale * y + noise
    return pd.DataFrame(
        {
            "game_id": np.arange(n),
            "game_key": [f"k{i}" for i in range(n)],
            "season": np.full(n, 2023),
            "week": (np.arange(n) % 12) + 1,
            "home_points": home,
            "away_points": away,
            "realized_margin": y,
            "pred_margin": mu,
            "exclude_from_headline": False,
            "p_ml_home": np.clip(0.5 + mu / 40.0, 0.05, 0.95),
            "p_ml_home_raw": np.clip(0.5 + mu / 40.0, 0.05, 0.95),
            "spread_close": np.full(n, np.nan),
            "neutral_site": np.arange(n) % 5 == 0,
        }
    )


def _feature_bank(n_train: int = 80, n_test: int = 40, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows: list[dict[str, object]] = []
    gid = 0
    for season, n in ((2021, n_train // 2), (2022, n_train // 2), (2023, n_test)):
        for i in range(n):
            off = float(rng.normal(0, 0.2))
            y = float(30.0 * off + rng.normal(0, 10))
            rows.append(
                {
                    "game_id": gid,
                    "season": season,
                    "week": (i % 12) + 1,
                    "realized_margin": y,
                    "rating_diff_off_epa": off,
                    "rating_diff_def_epa": float(rng.normal(0, 0.1)),
                    "off_epa_diff": off,
                    "def_epa_diff": float(rng.normal(0, 0.1)),
                    "rating_uncertainty": float(abs(rng.normal(1.0, 0.2))),
                }
            )
            gid += 1
    return pd.DataFrame(rows)


def test_score_predictor_perfect() -> None:
    y = np.array([1.0, 2.0, 3.0])
    sc = score_predictor("id", y, y)
    assert sc.mae == 0.0
    assert sc.r2 == 1.0
    assert sc.mean_signed_bias == 0.0


def test_regress_slope_near_one() -> None:
    rng = np.random.default_rng(1)
    yhat = rng.normal(0, 10, size=200)
    y = 2.0 + 1.0 * yhat + rng.normal(0, 0.01, size=200)
    sl = regress_y_on_yhat("cal", y, yhat)
    assert sl.b == pytest.approx(1.0, abs=0.05)
    assert sl.a == pytest.approx(2.0, abs=0.05)


def test_regress_negative_slope_detected_as_stop() -> None:
    frame = _toy_frame(mu_scale=-1.0)
    a = block_a(frame, train_mean=0.0)
    b1 = block_b_target_contract(frame, raw_games=None, n_sample=10)
    stop = detect_structural_stop(a, b1)
    assert stop.kind == "sign_inversion"


def test_block_a_stack_r2_positive_on_signal() -> None:
    frame = _toy_frame(mu_scale=0.8)
    a = block_a(frame, train_mean=float(frame["realized_margin"].mean()))
    assert a["stack_r2_le_zero"] is False
    assert a["stack_r2"] > 0.0
    assert "constant_0" in a["scores"]
    assert "constant_train_mean" in a["scores"]


def test_block_b_target_contract_matches() -> None:
    frame = _toy_frame()
    out = block_b_target_contract(frame, raw_games=frame, n_sample=15, seed=1)
    assert out["stored_y_matches_points"] is True
    assert out["raw_points_match"] is True
    assert out["n_sampled"] == 15


def test_block_b_join_integrity_one_to_one() -> None:
    frame = _toy_frame()
    outcomes = frame[["game_id", "game_key", "realized_margin", "home_points", "away_points"]]
    j = block_b_join_integrity(frame, outcomes)
    assert j["one_to_one"] is True
    assert j["pred_orphans"] == 0


def test_render_notes_first_line_yes_or_no(tmp_path: Path) -> None:
    frame = _toy_frame()
    rng = np.random.default_rng(99)
    frame = frame.copy()
    frame["pred_margin"] = rng.normal(0, 1, size=len(frame))
    a = block_a(frame, train_mean=0.0)
    result = {
        "predictions_path": "toy.parquet",
        "n_predictions": len(frame),
        "n_headline": len(frame),
        "test_seasons": [2023],
        "block_a": a,
        "structural_stop": {"kind": "none", "message": "ok"},
        "stopped_early": False,
        "block_c": {"rows": [], "failing_step": None, "elo_mae": 13.0, "signal_ceiling": {}},
    }
    text = render_notes(result)
    first = text.splitlines()[0].lower()
    assert first.startswith("yes") or first.startswith("no")
    out = tmp_path / "D1.md"
    out.write_text(text, encoding="utf-8")
    assert out.is_file()


def test_load_prediction_frame_requires_columns(tmp_path: Path) -> None:
    bad = tmp_path / "bad.parquet"
    pd.DataFrame({"game_id": [1]}).to_parquet(bad)
    with pytest.raises(Exception, match="missing columns"):
        load_prediction_frame(bad)


def test_reconcile_history_has_both_mae_sources() -> None:
    hist = reconcile_history()
    assert "mae_13_65_source" in hist
    assert "mae_16_60_source" in hist
    assert hist["mae_16_60_source"]["value"] == pytest.approx(16.604688787660955)


def test_train_mean_margin() -> None:
    games = pd.DataFrame(
        {
            "season": [2021, 2021, 2023],
            "home_points": [30, 20, 40],
            "away_points": [20, 10, 10],
        }
    )
    assert train_mean_margin(games, [2023]) == pytest.approx(10.0)


def test_block_c_layer_ladder_smoke() -> None:
    bank = _feature_bank()
    test = bank.loc[bank["season"] == 2023].copy()
    eval_frame = pd.DataFrame(
        {
            "game_id": test["game_id"].to_numpy(),
            "season": 2023,
            "week": test["week"].to_numpy(),
            "home_points": 28,
            "away_points": 21,
            "realized_margin": test["realized_margin"].to_numpy(),
            "pred_margin": test["realized_margin"].to_numpy() * 0.5,
            "exclude_from_headline": False,
        }
    )
    elo = {
        int(g): float(m)
        for g, m in zip(test["game_id"], test["realized_margin"] * 0.4, strict=True)
    }
    pub = {
        int(g): float(m) for g, m in zip(test["game_id"], eval_frame["pred_margin"], strict=True)
    }
    out = block_c_layer_ladder(
        eval_frame=eval_frame,
        feature_bank=bank,
        elo_by_game=elo,
        published_mu=pub,
        test_seasons=[2023],
    )
    assert out["rows"]
    layers = {r["layer"] for r in out["rows"]}
    assert "L0_elo" in layers
    assert "L7_published_mu" in layers
    assert "L1_ols_rating_diff" in layers
    assert out["signal_ceiling"].get("in_sample_lgbm_mae") is not None


def test_block_d_assert_oof_executes() -> None:
    out = block_d_ensemble_health(nnls_weights={"2023": {"lgbm": 1.0}}, oof_frame=None)
    assert out["assert_oof_only_executes"] is True
    oof = pd.DataFrame(
        {
            "lgbm": [1.0, 2.0, 3.0, 4.0],
            "enet": [1.1, 2.1, 2.9, 4.2],
            "realized_margin": [2.0, 3.0, 4.0, 5.0],
            "is_out_of_fold": [True, True, True, True],
        }
    )
    out2 = block_d_ensemble_health(nnls_weights=None, oof_frame=oof)
    assert "condition_number" in out2["oof_audit"]


def test_block_f_psi_and_nulls() -> None:
    rng = np.random.default_rng(0)
    train = pd.DataFrame(
        {
            "game_id": np.arange(50),
            "f1": rng.normal(0, 1, 50),
            "f2": rng.normal(0, 1, 50),
            "season": 2022,
        }
    )
    test = pd.DataFrame(
        {
            "game_id": np.arange(50, 80),
            "f1": rng.normal(3, 1, 30),  # drifted
            "f2": np.full(30, np.nan),  # null at test
            "season": 2023,
        }
    )
    out = block_f_feature_health(train, test)
    assert out["flagged_train_populated_test_null"]
    assert isinstance(out["psi_above_0_3"], list)


def test_block_g_and_h() -> None:
    frame = _toy_frame(60)
    g = block_g_calibration(frame)
    assert "logloss_calibrated" in g
    assert len(g["worst10"]) <= 10
    h = block_h_slices(frame, games=frame)
    assert "by_week" in h
    assert "by_neutral" in h


def test_run_mu_diagnostics_skip_heavy(tmp_path: Path) -> None:
    frame = _toy_frame(30)
    pred_path = tmp_path / "preds.parquet"
    frame.to_parquet(pred_path)
    notes = tmp_path / "D1.md"
    art = tmp_path / "art"
    result = run_mu_diagnostics(
        predictions_path=pred_path,
        staged_dir=tmp_path / "missing_staged",
        artifact_dir=art,
        notes_path=notes,
        skip_heavy=True,
    )
    assert result["block_a"]["n"] == 30
    assert notes.is_file()
    assert (art / "diag_mu.json").is_file()
    assert result["structural_stop"]["kind"] in {
        "none",
        "sign_inversion",
        "target_orientation_mismatch",
    }


def test_shifted_label_probe_and_signature_helpers(tmp_path: Path) -> None:
    from datetime import UTC, datetime, timedelta

    from ncaa_quant.evaluation.diagnostics_mu import (
        _run_shifted_label_probe,
        block_b_feature_signature_contract,
        block_b_neutral_slopes,
        block_b_row_order_test,
        block_e_stage1,
    )
    from ncaa_quant.models.heads.margin import LightGBMMuHead

    shifted = _run_shifted_label_probe(skip_heavy=False)
    assert shifted["status"] == "ok"
    assert "model_mae" in shifted

    frame = _toy_frame(24)
    neut = block_b_neutral_slopes(frame, games=frame)
    assert "neutral" in neut and "non_neutral" in neut

    rng = np.random.default_rng(0)
    x = pd.DataFrame(
        {
            "game_id": np.arange(40),
            "rating_diff_off_epa": rng.normal(0, 1, 40),
            "rating_diff_def_epa": rng.normal(0, 1, 40),
        }
    )
    y = x["rating_diff_off_epa"] * 10 + rng.normal(0, 1, 40)
    head = LightGBMMuHead(target="margin", model_version="d1-test")
    head.fit(x, pd.DataFrame({"game_id": x["game_id"], "realized_margin": y}))
    b4 = block_b_row_order_test(head, x)
    assert b4["elementwise_equal_after_unshuffle"] is True
    b5 = block_b_feature_signature_contract(head, x)
    assert b5["raised"] is True

    t0 = datetime(2021, 9, 4, tzinfo=UTC)
    obs_rows = []
    for week in range(1, 7):
        obs_rows.append(
            {
                "game_id": week,
                "season": 2021,
                "week": week,
                "event_time": t0 + timedelta(days=7 * (week - 1)),
                "home_team_id": 0,
                "away_team_id": week,
                "home_epa": 0.12,
                "away_epa": -0.05,
                "home_plays": 70.0,
                "away_plays": 65.0,
                "home_st_epa": np.nan,
                "away_st_epa": np.nan,
                "pace_obs": 67.5,
                "margin": 14.0,
                "neutral_site": False,
                "home_is_fcs": False,
                "away_is_fcs": False,
            }
        )
    obs = pd.DataFrame(obs_rows)
    bank = _feature_bank(n_train=40, n_test=20)
    elo = {
        int(g): float(m)
        for g, m in zip(
            bank.loc[bank["season"] == 2023, "game_id"],
            bank.loc[bank["season"] == 2023, "realized_margin"] * 0.3,
            strict=True,
        )
    }
    eval_frame = pd.DataFrame(
        {
            "game_id": bank.loc[bank["season"] == 2023, "game_id"].to_numpy(),
            "season": 2023,
            "week": 1,
            "home_points": 24,
            "away_points": 17,
            "realized_margin": bank.loc[bank["season"] == 2023, "realized_margin"].to_numpy(),
            "pred_margin": 3.0,
            "exclude_from_headline": False,
        }
    )
    e = block_e_stage1(
        observations=obs,
        feature_bank=bank,
        elo_by_game=elo,
        eval_frame=eval_frame,
        artifact_dir=tmp_path,
    )
    assert e["filter_wall_clock_sec"] is not None
    assert "innovation_stats_per_season" in e
    assert "e3_rating_diff" in e
