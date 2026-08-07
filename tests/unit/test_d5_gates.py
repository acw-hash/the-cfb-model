"""D5/D6 unit tests: sign gate, hypotheses, encompassing config, close join."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ncaa_quant.distribution.shape import win_equals_cover_at_zero
from ncaa_quant.evaluation.d4_eval import EncompassingResult
from ncaa_quant.evaluation.d5_eval import (
    EncompassingEvalConfig,
    audit_sigma_feature_set,
    diagnose_ats_ou_hypotheses,
    encompassing_power,
    load_encompassing_config,
    run_encompassing_evaluation,
    underpowered_verdict,
)
from ncaa_quant.evaluation.d6_eval import (
    assert_closes_eval_only,
    join_cfbd_closes_for_evaluation,
    priced_vs_pooled_season_note,
)
from ncaa_quant.evaluation.production_stack import (
    CHANCE_LOG_LOSS,
    SignInvertedMarketError,
    assert_derived_market_signs,
)


def test_sign_gate_trips_on_inverted_probability_table() -> None:
    """Acceptance: sign gate trips when LL(1−p) beats LL(p)."""
    rng = np.random.default_rng(0)
    n = 80
    y = rng.integers(0, 2, size=n).astype(float)
    # Inverted: emit 1−p relative to the true outcome signal.
    p_true = np.where(y > 0.5, 0.8, 0.2)
    p = 1.0 - p_true
    frame = pd.DataFrame(
        {
            "realized_margin": np.where(y > 0.5, 7.0, -7.0),
            "spread_close": np.zeros(n),
            "pred_margin": np.where(y > 0.5, 7.0, -7.0),
            "p_ats_home": p,
            "realized_total": np.full(n, 50.0),
            "total_close": np.full(n, 55.0),
            "pred_total": np.full(n, 55.0),
            "p_ou_over": 1.0 - np.where(rng.random(n) > 0.5, 0.75, 0.25),
            "exclude_from_headline": False,
        }
    )
    # Force OU outcomes so inverted probs beat forward.
    frame["realized_total"] = np.where(frame["p_ou_over"] > 0.5, 40.0, 70.0)
    with pytest.raises(SignInvertedMarketError, match="sign-inverted"):
        assert_derived_market_signs(frame)


def test_sign_gate_passes_healthy_above_chance_ats() -> None:
    """Reverted chance gate: ATS LL > ln2 must NOT fail a healthy-sign run."""
    rng = np.random.default_rng(1)
    n = 120
    # Weak edge + overconfident σ → LL often above chance, but sign is correct.
    mu = rng.normal(0, 3, n)
    spread = -mu + rng.normal(0, 8, n)  # large residual vs market → weak cover edge
    y = mu + rng.normal(0, 14, n)
    sig = np.full(n, 8.0)  # overconfident
    from scipy import stats as sp_stats

    p = sp_stats.norm.cdf((mu + spread) / sig)
    frame = pd.DataFrame(
        {
            "realized_margin": y,
            "spread_close": spread,
            "pred_margin": mu,
            "p_ats_home": p,
            "realized_total": 55.0 + rng.normal(0, 13, n),
            "total_close": np.full(n, 55.0),
            "pred_total": np.full(n, 55.0),
            "p_ou_over": np.full(n, 0.5),
            "exclude_from_headline": False,
        }
    )
    report = assert_derived_market_signs(frame)
    assert report["ats_close"]["sign_gate_fail"] == 0.0
    # Skill flag may be 0 when LL > chance; that must not raise.
    assert report["ats_close"]["log_loss"] > 0.0
    assert "log_loss_inverted" in report["ats_close"]
    assert report["ats_close"]["log_loss_inverted"] >= report["ats_close"]["log_loss"] - 0.01


def test_chance_level_no_longer_fails_near_chance() -> None:
    n = 60
    frame = pd.DataFrame(
        {
            "realized_margin": np.tile([7.0, -7.0], n // 2),
            "spread_close": np.zeros(n),
            "pred_margin": np.zeros(n),
            "p_ats_home": np.full(n, 0.5),
            "realized_total": np.tile([60.0, 40.0], n // 2),
            "total_close": np.full(n, 50.0),
            "pred_total": np.full(n, 50.0),
            "p_ou_over": np.full(n, 0.5),
            "exclude_from_headline": False,
        }
    )
    report = assert_derived_market_signs(frame)
    assert abs(report["ats_close"]["log_loss"] - CHANCE_LOG_LOSS) < 1e-9


def test_win_equals_cover_nonzero_spread_guards_h1() -> None:
    """Strengthened §19: MC cover matches Φ((μ+S)/σ), not the inverted sign."""
    mu = np.array([3.0, -2.0, 8.0, 0.0])
    sig = np.array([14.0, 12.0, 16.0, 13.0])
    spreads = np.array([-3.0, 7.0, -14.0, 0.0])
    result = win_equals_cover_at_zero(
        mu, sig, kernel=None, atol=0.03, n_draws=15_000, seed=1, spreads=spreads
    )
    assert result["within_tolerance"]
    assert result["path"] == "mc"
    nz = result["nonzero_spread"]
    assert nz["within_tolerance"]
    assert not nz["inverted_sign_closer"]
    assert nz["max_abs_diff_vs_gaussian"] < nz["max_abs_diff_vs_inverted_sign"]


def test_encompassing_power_scales_with_se() -> None:
    p = encompassing_power(0.1066, 559, b2_target=0.10)
    assert p.n_required > 2000
    p15 = encompassing_power(0.1066, 559, b2_target=0.15)
    assert p15.n_required < p.n_required


def test_underpowered_verdict_when_ci_covers_edge() -> None:
    fake = EncompassingResult(
        b1=0.98,
        b2=0.0629,
        se_b1=0.09,
        se_b2=0.1066,
        p_b2=0.616,
        a=0.0,
        n=559,
        verdict="old",
    )
    text = underpowered_verdict(fake)
    assert "UNDERPOWERED" in text


def test_load_encompassing_config() -> None:
    path = Path("configs/eval/encompassing.yaml")
    cfg = load_encompassing_config(path)
    # 2025 excluded: lockbox season (§7.2 item 9). The powered encompassing test
    # is development work, so it may not read it.
    assert cfg.seasons == (2019, 2021, 2022, 2023, 2024)
    assert cfg.min_games_per_season == 400
    assert cfg.substantial_b2 == 0.10
    assert cfg.stability_min_seasons_positive == 3


def test_run_encompassing_evaluation_smoke() -> None:
    rng = np.random.default_rng(1)
    n = 120
    market = rng.normal(0, 14, n)
    stack = 0.2 * market + rng.normal(0, 10, n)
    y = 0.8 * market + 0.15 * stack + rng.normal(0, 9, n)
    frame = pd.DataFrame(
        {
            "season": np.full(n, 2019),
            "week": 1 + np.arange(n) % 12,
            "realized_margin": y,
            "pred_margin": stack,
            "spread_close": -market,
        }
    )
    cfg = EncompassingEvalConfig(seasons=(2019,), n_boot=50, min_games_per_season=50)
    out = run_encompassing_evaluation(frame, cfg)
    assert out["joint"]["n"] == n
    assert "ci95" in out["joint"]
    assert "b2_0.10" in out["power"]


def test_sigma_feature_audit() -> None:
    cols = [
        "stage1_posterior_var_home",
        "stage1_posterior_var_away",
        "week",
        "rating_diff_magnitude",
        "roster_portal_null",
    ]
    audit = audit_sigma_feature_set(cols)
    assert "expected_possessions" in audit["absent"]
    assert "week" in audit["present"]


def test_diagnose_hypotheses_rejects_h1_on_healthy_sign() -> None:
    rng = np.random.default_rng(2)
    n = 200
    mu = rng.normal(0, 10, n)
    spread = -mu + rng.normal(0, 3, n)
    y = mu + rng.normal(0, 14, n)
    sig = np.full(n, 14.0)
    from scipy import stats as sp_stats

    p = sp_stats.norm.cdf((mu + spread) / sig)
    frame = pd.DataFrame(
        {
            "pred_margin": mu,
            "sigma_m": sig,
            "spread_close": spread,
            "spread_asof": spread,
            "realized_margin": y,
            "pred_total": np.full(n, 55.0),
            "sigma_t": np.full(n, 13.0),
            "total_close": np.full(n, 55.0),
            "realized_total": 55.0 + rng.normal(0, 13, n),
            "game_id": np.arange(n),
        }
    )
    diag = diagnose_ats_ou_hypotheses(frame)
    h1 = next(h for h in diag["ats"]["hypotheses"] if h["name"] == "H1_spread_sign_inverted")
    assert h1["holds"] is False
    del p


def test_join_cfbd_closes_fills_null_seasons() -> None:
    frame = pd.DataFrame(
        {
            "game_id": [1, 2, 3],
            "season": [2019, 2021, 2021],
            "spread_close": [-3.5, np.nan, np.nan],
            "total_close": [55.0, np.nan, np.nan],
            "line_source_close": ["cfbd_close", "null", "null"],
            "n_books_close": [3, 0, 0],
        }
    )
    lines = pd.DataFrame(
        {
            "game_id": [1, 2, 2, 3],
            "line_type": ["close", "close", "close", "close"],
            "book": ["a", "a", "b", "a"],
            "spread": [-3.5, -7.0, -7.5, 3.0],
            "total": [55.0, 48.0, 49.0, 60.0],
        }
    )
    out, meta = join_cfbd_closes_for_evaluation(frame, lines)
    assert meta["n_filled"] == 2
    assert out.loc[out["game_id"] == 2, "spread_close"].iloc[0] == pytest.approx(-7.25)
    assert out.loc[out["game_id"] == 2, "line_source_close"].iloc[0] == "cfbd_close_eval"
    # Existing 2019 close preserved.
    assert out.loc[out["game_id"] == 1, "spread_close"].iloc[0] == pytest.approx(-3.5)


def test_assert_closes_eval_only() -> None:
    assert_closes_eval_only(["home_off_epa", "away_def_epa", "week"])
    with pytest.raises(Exception, match="closing lines must not"):
        assert_closes_eval_only(["home_off_epa", "spread_close"])


def test_priced_vs_pooled_note_mentions_2019() -> None:
    frame = pd.DataFrame(
        {
            "season": [2019, 2019, 2021, 2021],
            "realized_margin": [10.0, -10.0, 2.0, -2.0],
            "pred_margin": [0.0, 0.0, 1.0, -1.0],
            "spread_close": [-3.0, np.nan, -7.0, -3.0],
        }
    )
    note = priced_vs_pooled_season_note(frame)
    assert "2019" in note["note"]
    assert note["unpriced_2019_n"] == 1
