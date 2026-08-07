"""Preseason prior builder tests (Task 15)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ncaa_quant.features.builders.efficiency import resolve_priors
from ncaa_quant.ratings.priors import (
    LATE_TARGET_COLUMN,
    PREDICTOR_NAMES,
    PriorConfig,
    attach_late_target,
    blend_prior_mean,
    build_design_frame,
    build_predictors,
    build_preseason_priors_frame,
    build_preseason_states,
    build_team_season_prior,
    count_missing_inputs,
    efficiency_prior_lookup,
    fit_prior_weights,
    gaussian_state_from_priors,
    out_of_sample_r2,
    prior_evidence_crossover_games,
    prior_evidence_weight,
    prior_variance,
    regress_to_conference_mean,
    store_week1_predictions,
)
from ncaa_quant.ratings.state_space import StateSpaceConfig


def _ts(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


def test_conf_regression_blend() -> None:
    assert regress_to_conference_mean(1.0, 0.0, alpha=0.30) == pytest.approx(0.70)
    assert regress_to_conference_mean(1.0, 0.0, alpha=0.0) == pytest.approx(1.0)


def test_turnover_variance_monotonicity() -> None:
    """Lower returning production → strictly wider prior variance."""
    cfg = PriorConfig(base_var=0.02, turnover_scale=2.5, missing_var_penalty=0.0)
    v40 = prior_variance(0.40, n_missing=0, config=cfg)
    v85 = prior_variance(0.85, n_missing=0, config=cfg)
    assert v40 > v85
    assert v40 / v85 > 1.5  # materially wider


def test_missing_input_widens_variance() -> None:
    cfg = PriorConfig(base_var=0.02, turnover_scale=2.5, missing_var_penalty=0.015)
    base = prior_variance(0.70, n_missing=0, config=cfg)
    wider = prior_variance(0.70, n_missing=2, config=cfg)
    assert wider > base
    assert wider - base == pytest.approx(2 * cfg.missing_var_penalty)

    n = count_missing_inputs(
        returning_pct=0.7,
        talent=float("nan"),
        portal_net=float("nan"),
        portal_era=1.0,  # in-era null portal counts
        new_hc_flag=0.0,
        qb_carryover=float("nan"),
        last_posterior=0.1,
    )
    assert n == 3  # talent + portal + qb


def test_pre_portal_era_does_not_penalize_null_portal() -> None:
    n = count_missing_inputs(
        returning_pct=0.8,
        talent=0.5,
        portal_net=float("nan"),
        portal_era=0.0,
        new_hc_flag=0.0,
        qb_carryover=1.0,
        last_posterior=0.1,
    )
    assert n == 0


def test_weight_fitting_reproducible_under_fixed_seed() -> None:
    rng = np.random.default_rng(0)
    n = 80
    rows = []
    for i in range(n):
        last = float(rng.normal(0, 0.15))
        ret = float(rng.uniform(0.3, 0.95))
        talent = float(rng.normal(0, 1))
        preds = build_predictors(
            last_posterior=last,
            conference_mean=0.0,
            returning_pct=ret,
            talent_z=talent,
            portal_net=0.0,
            portal_era=0.0,
            new_hc_flag=0.0,
            qb_carryover=1.0,
        )
        y = 0.9 * preds["last_regressed"] + 0.2 * preds["talent"] + float(rng.normal(0, 0.02))
        rows.append(
            {
                "team_id": i,
                "season": 2018 + (i % 5),
                "dim": "off_epa",
                # Generated from the predictors independently of any prior, so this
                # stands in for a diffuse-run late rating (audit A-2), not an
                # early prior-dominated posterior.
                LATE_TARGET_COLUMN: y,
                "returning_pct": ret,
                "n_missing": 0,
                **preds,
            }
        )
    design = pd.DataFrame(rows)
    a = fit_prior_weights(design, dim="off_epa", seed=42)
    b = fit_prior_weights(design, dim="off_epa", seed=42)
    assert a.weights == b.weights
    assert a.r_squared == b.r_squared
    for name in PREDICTOR_NAMES:
        sa, sb = a.std_errors[name], b.std_errors[name]
        if sa != sa and sb != sb:
            continue  # both NaN (singular column)
        assert sa == pytest.approx(sb, nan_ok=True)
    assert a.weights["last_regressed"] == pytest.approx(0.9, abs=0.15)
    assert a.n_obs == n


def test_new_hc_prior_pulled_toward_talent() -> None:
    """New-HC discontinuity moves prior from weak last-season toward talent."""
    cfg = PriorConfig(conf_regression=0.30, talent_rating_scale=0.08)
    weights = {
        "last_regressed": 0.5,
        "returning_adj": 0.0,
        "talent": 0.3,
        "portal_net": 0.0,
        "coaching_adj": 0.8,
        "qb_carryover": 0.0,
    }
    roster_base = {
        "returning_offense_pct": 0.7,
        "returning_defense_pct": 0.7,
        "talent_composite": 150.0,
        "portal_net_rating": float("nan"),
        "portal_era": 0.0,
        "new_hc_flag": 0.0,
    }
    same_hc = build_team_season_prior(
        team_id=1,
        season=2023,
        dim="off_epa",
        last_posterior=-0.20,
        conference_mean=0.0,
        roster_row=roster_base,
        fitted=weights,
        qb_carryover=1.0,
        config=cfg,
        talent_z=2.0,
    )
    new_hc = build_team_season_prior(
        team_id=1,
        season=2023,
        dim="off_epa",
        last_posterior=-0.20,
        conference_mean=0.0,
        roster_row={**roster_base, "new_hc_flag": 1.0},
        fitted=weights,
        qb_carryover=1.0,
        config=cfg,
        talent_z=2.0,
    )
    talent_level = 0.08 * 2.0
    assert abs(new_hc.mean - talent_level) < abs(same_hc.mean - talent_level)
    assert new_hc.mean > same_hc.mean


def test_crossover_high_vs_low_continuity() -> None:
    cfg = PriorConfig(base_var=0.02, turnover_scale=2.5, obs_var_eff=0.20, missing_var_penalty=0.0)
    v_hi = prior_variance(0.85, n_missing=0, config=cfg)
    v_lo = prior_variance(0.40, n_missing=0, config=cfg)
    c_hi = prior_evidence_crossover_games(v_hi, config=cfg)
    c_lo = prior_evidence_crossover_games(v_lo, config=cfg)
    assert c_hi > c_lo
    # §9.6: ~5–7 on average with real variation between continuity levels.
    assert 5.0 <= c_hi <= 10.0
    assert 3.0 <= c_lo <= 6.0
    w = prior_evidence_weight(v_hi, int(round(c_hi)), config=cfg)
    assert w == pytest.approx(0.5, abs=0.05)


def test_efficiency_seam_lookup() -> None:
    priors = pd.DataFrame(
        [
            {"team_id": 10, "season": 2023, "dim": "off_epa", "prior_mean": 0.12},
            {"team_id": 20, "season": 2023, "dim": "off_epa", "prior_mean": -0.05},
        ]
    )
    lookup = efficiency_prior_lookup(priors, dim="off_epa", season=2023)
    resolved = resolve_priors(["10", "20", "99"], league_mean=0.0, prior_lookup=lookup)
    assert resolved["10"] == pytest.approx(0.12)
    assert resolved["20"] == pytest.approx(-0.05)
    assert resolved["99"] == pytest.approx(0.0)


def test_gaussian_state_wiring() -> None:
    cfg = StateSpaceConfig()
    state = gaussian_state_from_priors(
        {
            "off_epa": (0.1, 0.03),
            "def_epa": (-0.05, 0.04),
            "st_value": (0.0, 0.02),
            "pace": (0.01, 0.02),
        },
        config=cfg,
    )
    assert state.mean[cfg.dim_index("off_epa")] == pytest.approx(0.1)
    assert state.cov[cfg.dim_index("def_epa"), cfg.dim_index("def_epa")] == pytest.approx(0.04)


def test_build_design_and_states_roundtrip() -> None:
    teams = pd.DataFrame(
        [
            {"team_id": 1, "season": 2021, "conference": "X", "school": "A"},
            {"team_id": 2, "season": 2021, "conference": "X", "school": "B"},
            {"team_id": 1, "season": 2022, "conference": "X", "school": "A"},
            {"team_id": 2, "season": 2022, "conference": "X", "school": "B"},
        ]
    )
    history_rows: list[dict[str, object]] = []
    t0 = _ts(2021, 9, 1)
    for season, base in ((2021, 0.2), (2022, 0.25)):
        for g in range(1, 6):
            for tid, sign in ((1, 1.0), (2, -1.0)):
                history_rows.append(
                    {
                        "team_id": tid,
                        "season": season,
                        "week": g,
                        "game_id": season * 100 + g * 10 + tid,
                        "event_time": t0 + timedelta(days=7 * g + (season - 2021) * 365),
                        "kind": "postgame",
                        "off_epa": sign * base + 0.01 * g,
                        "def_epa": -sign * 0.1,
                        "st_value": 0.0,
                        "pace": 0.0,
                        "sd_off_epa": 0.1,
                        "sd_def_epa": 0.1,
                        "sd_st_value": 0.1,
                        "sd_pace": 0.1,
                    }
                )
                history_rows.append(
                    {
                        "team_id": tid,
                        "season": season,
                        "week": g,
                        "game_id": None,
                        "event_time": t0 + timedelta(days=7 * g + (season - 2021) * 365),
                        "kind": "weekly",
                        "off_epa": sign * base + 0.01 * g,
                        "def_epa": -sign * 0.1,
                        "st_value": 0.0,
                        "pace": 0.0,
                        "sd_off_epa": 0.1,
                        "sd_def_epa": 0.1,
                        "sd_st_value": 0.1,
                        "sd_pace": 0.1,
                    }
                )
    history = pd.DataFrame(history_rows)
    roster = pd.DataFrame(
        [
            {
                "team_id": 1,
                "season": 2022,
                "returning_offense_pct": 0.8,
                "returning_defense_pct": 0.75,
                "talent_composite": 800.0,
                "blue_chip_ratio": 0.3,
                "recruiting_4yr_weighted": 200.0,
                "portal_net_rating": float("nan"),
                "portal_era": 1.0,
                "new_hc_flag": 0.0,
            },
            {
                "team_id": 2,
                "season": 2022,
                "returning_offense_pct": 0.4,
                "returning_defense_pct": 0.45,
                "talent_composite": 600.0,
                "blue_chip_ratio": 0.1,
                "recruiting_4yr_weighted": 100.0,
                "portal_net_rating": float("nan"),
                "portal_era": 1.0,
                "new_hc_flag": 1.0,
            },
        ]
    )
    design = build_design_frame(
        history=history,
        roster=roster,
        teams=teams,
        seasons=[2022],
        dim="off_epa",
    )
    assert len(design) == 2
    # The design frame's own `early_rating` is prior-dominated, so the fit needs a
    # prior-free target attached (audit A-2). Stand in for the diffuse filter run
    # here; `test_prior_circularity.py` covers why this substitution matters.
    late = pd.DataFrame(
        {
            "team_id": [1, 2],
            "season": [2022, 2022],
            LATE_TARGET_COLUMN: [0.26, -0.21],
        }
    )
    design = attach_late_target(design, late)
    fitted = fit_prior_weights(design, dim="off_epa", seed=7)
    assert fitted.n_obs == 2
    assert fitted.target_column == LATE_TARGET_COLUMN
    assert fitted.target_is_circular is False
    assert set(fitted.weights) == set(PREDICTOR_NAMES)

    priors = build_preseason_priors_frame(
        history=history,
        roster=roster,
        teams=teams,
        season=2022,
        fitted_by_dim={"off_epa": fitted},
        dims=["off_epa"],
    )
    assert len(priors) == 2
    states = build_preseason_states(priors, season=2022)
    assert set(states) == {1, 2}
    assert states[1].mean.shape == (4,)


def test_store_week1_predictions(tmp_path: Path) -> None:
    path = tmp_path / "week1.parquet"
    priors = pd.DataFrame(
        [
            {
                "team_id": 1,
                "season": 2023,
                "dim": "off_epa",
                "prior_mean": 0.1,
                "prior_var": 0.02,
            },
            {
                "team_id": 1,
                "season": 2024,
                "dim": "off_epa",
                "prior_mean": 0.2,
                "prior_var": 0.02,
            },
            {
                "team_id": 1,
                "season": 2022,
                "dim": "off_epa",
                "prior_mean": 0.0,
                "prior_var": 0.02,
            },
        ]
    )
    out = store_week1_predictions(priors, path, seasons=[2023, 2024])
    loaded = pd.read_parquet(out)
    assert set(loaded["season"].astype(int)) == {2023, 2024}


def test_blend_prior_mean_matches_dot_product() -> None:
    preds = {n: 1.0 for n in PREDICTOR_NAMES}
    weights = {n: 0.1 for n in PREDICTOR_NAMES}
    assert blend_prior_mean(preds, weights, intercept=0.5) == pytest.approx(1.1)


def test_oos_r2_helper() -> None:
    rng = np.random.default_rng(1)
    rows = []
    for i in range(40):
        last = float(rng.normal(0, 0.1))
        preds = build_predictors(
            last_posterior=last,
            conference_mean=0.0,
            returning_pct=0.75,
            talent_z=float(rng.normal()),
            portal_net=0.0,
            portal_era=0.0,
            new_hc_flag=0.0,
            qb_carryover=1.0,
        )
        y = 0.85 * preds["last_regressed"] + 0.1 * preds["talent"]
        rows.append(
            {
                **preds,
                LATE_TARGET_COLUMN: y,
                "season": 2022 if i < 30 else 2023,
                "dim": "off_epa",
            }
        )
    design = pd.DataFrame(rows)
    fitted = fit_prior_weights(design.loc[design["season"] == 2022], dim="off_epa", seed=1)
    r2 = out_of_sample_r2(design, fitted, seasons_test=[2023])
    assert r2 > 0.9
