"""Non-circular preseason prior fitting (DESIGN §9.6 / Task 15, audit A-2).

The defect: prior weights were fit by regressing each season's *early* ratings on
the preseason predictors. But early ratings come from a filter initialized with
those very priors, so they are prior-dominated. The regression largely recovers
the weights that were assumed, and reports a high R² for exactly the wrong reason.
The priors governing Weeks 1-5 -- the softest market window, where the system
expects its edge -- were therefore never validated against anything.

The headline test here is `test_circular_target_recovers_the_assumed_weights`: on
synthetic data where the assumed weights are deliberately *wrong*, fitting against
early ratings recovers the wrong weights with a near-perfect fit, while fitting
against diffuse-run late ratings recovers the truth.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ncaa_quant.ratings.priors import (
    CIRCULAR_TARGET_COLUMNS,
    LATE_TARGET_COLUMN,
    PREDICTOR_NAMES,
    PriorConfig,
    attach_late_target,
    diffuse_filter_config,
    diffuse_late_ratings,
    fit_prior_weights,
    out_of_sample_r2,
)

N_TEAMS = 40
SEASONS = (2019, 2020, 2021, 2022)


def _synthetic_design(
    *,
    true_weights: dict[str, float],
    assumed_weights: dict[str, float],
    seed: int = 902,
) -> pd.DataFrame:
    """A design frame carrying both a circular and a prior-free target.

    ``late_rating`` is generated from ``true_weights`` plus noise: this is what a
    diffuse filter run would recover, because it learns from games rather than from
    the prior.

    ``early_rating`` is generated from ``assumed_weights``: this is what a
    prior-initialized filter reports after three games, because the prior still
    dominates the posterior. When the two weight sets differ, the two targets
    disagree about which predictors matter -- and only one of them is telling the
    truth about the world.
    """
    rng = np.random.default_rng(seed)
    rows: list[dict[str, float]] = []
    for season in SEASONS:
        for team in range(N_TEAMS):
            preds = {name: float(rng.normal()) for name in PREDICTOR_NAMES}
            late = sum(true_weights[n] * preds[n] for n in PREDICTOR_NAMES)
            early = sum(assumed_weights[n] * preds[n] for n in PREDICTOR_NAMES)
            rows.append(
                {
                    "team_id": team,
                    "season": season,
                    "dim": "off_epa",
                    **preds,
                    LATE_TARGET_COLUMN: late + float(rng.normal(scale=0.05)),
                    "early_rating": early + float(rng.normal(scale=0.01)),
                }
            )
    return pd.DataFrame(rows)


def _weights(**overrides: float) -> dict[str, float]:
    base = dict.fromkeys(PREDICTOR_NAMES, 0.0)
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# The demonstration the audit asked for
# ---------------------------------------------------------------------------


def test_circular_target_recovers_the_assumed_weights() -> None:
    """Fitting against early ratings validates the assumption, not reality.

    Truth: `talent` drives the rating and `last_regressed` does nothing.
    The (wrong) assumed prior says the opposite.

    Fitting against early ratings returns the assumed weights with R² ~ 1, which
    looks like a triumph and is worthless. Fitting against diffuse-run late
    ratings returns the truth.
    """
    true_weights = _weights(talent=0.9)
    assumed_weights = _weights(last_regressed=0.9)
    design = _synthetic_design(true_weights=true_weights, assumed_weights=assumed_weights)

    circular = fit_prior_weights(
        design,
        dim="off_epa",
        target_column="early_rating",
        allow_circular_target=True,
        config=PriorConfig(fit_intercept=False),
    )
    honest = fit_prior_weights(
        design,
        dim="off_epa",
        config=PriorConfig(fit_intercept=False),
    )

    # The circular fit reproduces the assumption almost exactly, and its R2 is
    # near 1 -- the number that would have been cited as validation.
    assert circular.weights["last_regressed"] == pytest.approx(0.9, abs=0.02)
    assert circular.weights["talent"] == pytest.approx(0.0, abs=0.02)
    assert circular.r_squared > 0.99

    # The honest fit recovers the generative truth instead.
    assert honest.weights["talent"] == pytest.approx(0.9, abs=0.05)
    assert honest.weights["last_regressed"] == pytest.approx(0.0, abs=0.05)

    # The two disagree about which predictor matters at all. That gap is the bug.
    assert abs(circular.weights["talent"] - honest.weights["talent"]) > 0.5


def test_a_high_circular_r2_is_not_evidence_of_forecasting_skill() -> None:
    """Priors with no predictive value still score ~1 against a circular target.

    Judged out of sample, because in-sample R² overstates with six predictors --
    fitting pure noise here returns roughly 0.11 in sample, which is noise-fitting,
    not signal.
    """
    design = _synthetic_design(
        true_weights=_weights(),  # nothing predicts the late rating
        assumed_weights=_weights(talent=0.8, last_regressed=0.5),
    )
    train, test = (2019, 2020, 2021), (2022,)
    cfg = PriorConfig(fit_intercept=False)

    circular = fit_prior_weights(
        design,
        dim="off_epa",
        seasons_train=train,
        target_column="early_rating",
        allow_circular_target=True,
        config=cfg,
    )
    honest = fit_prior_weights(design, dim="off_epa", seasons_train=train, config=cfg)

    # Held out, the circular target still scores ~1: the prior predicts the prior.
    assert out_of_sample_r2(design, circular, seasons_test=test) > 0.99
    # The honest target correctly reports that the priors forecast nothing.
    assert out_of_sample_r2(design, honest, seasons_test=test) < 0.05


def test_the_recorded_target_says_whether_an_r2_is_interpretable() -> None:
    design = _synthetic_design(
        true_weights=_weights(talent=0.5),
        assumed_weights=_weights(talent=0.5),
    )

    honest = fit_prior_weights(design, dim="off_epa")
    circular = fit_prior_weights(
        design,
        dim="off_epa",
        target_column="early_rating",
        allow_circular_target=True,
    )

    assert honest.target_column == LATE_TARGET_COLUMN
    assert honest.target_is_circular is False
    assert circular.target_is_circular is True


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


def test_circular_target_is_refused_by_default() -> None:
    design = _synthetic_design(
        true_weights=_weights(talent=0.5),
        assumed_weights=_weights(talent=0.5),
    )

    with pytest.raises(ValueError, match="prior-dominated"):
        fit_prior_weights(design, dim="off_epa", target_column="early_rating")


def test_early_rating_is_the_registered_circular_target() -> None:
    assert "early_rating" in CIRCULAR_TARGET_COLUMNS
    assert LATE_TARGET_COLUMN not in CIRCULAR_TARGET_COLUMNS


def test_missing_target_column_names_the_fix() -> None:
    design = _synthetic_design(
        true_weights=_weights(talent=0.5),
        assumed_weights=_weights(talent=0.5),
    ).drop(columns=[LATE_TARGET_COLUMN])

    with pytest.raises(ValueError, match="attach_late_target"):
        fit_prior_weights(design, dim="off_epa")


def test_out_of_sample_r2_follows_the_fitted_target() -> None:
    design = _synthetic_design(
        true_weights=_weights(talent=0.9),
        assumed_weights=_weights(last_regressed=0.9),
    )
    honest = fit_prior_weights(
        design,
        dim="off_epa",
        seasons_train=(2019, 2020, 2021),
        config=PriorConfig(fit_intercept=False),
    )

    r2 = out_of_sample_r2(design, honest, seasons_test=(2022,))

    assert r2 > 0.8  # generalizes, because it learned something real


# ---------------------------------------------------------------------------
# The diffuse run that produces the honest target
# ---------------------------------------------------------------------------


def test_diffuse_config_is_genuinely_uninformative() -> None:
    """A prior that still carried weight would leak straight back into the target."""
    cfg = diffuse_filter_config(PriorConfig())
    standard_prior_var = 0.04  # StateSpaceConfig default

    assert cfg.prior_var > 100.0 * standard_prior_var


def _synthetic_observations(*, n_teams: int = 10, n_weeks: int = 12) -> pd.DataFrame:
    """A round-robin-ish season where team strength is a known linear ramp."""
    rng = np.random.default_rng(77)
    strength = np.linspace(-0.25, 0.25, n_teams)
    rows: list[dict[str, object]] = []
    gid = 1
    base = pd.Timestamp("2021-09-04T18:00:00Z")
    for week in range(1, n_weeks + 1):
        for slot in range(n_teams // 2):
            home = slot
            away = (slot + week) % n_teams
            if home == away:
                continue
            edge = float(strength[home] - strength[away])
            rows.append(
                {
                    "game_id": gid,
                    "season": 2021,
                    "week": week,
                    "event_time": base + pd.Timedelta(days=7 * (week - 1)),
                    "home_team_id": home,
                    "away_team_id": away,
                    "home_epa": float(strength[home] + rng.normal(scale=0.05)),
                    "away_epa": float(strength[away] + rng.normal(scale=0.05)),
                    "home_plays": 70.0,
                    "away_plays": 68.0,
                    "margin": 80.0 * edge + float(rng.normal(scale=8.0)),
                    "neutral_site": False,
                }
            )
            gid += 1
    return pd.DataFrame(rows)


def test_diffuse_late_ratings_runs_the_filter_and_ranks_teams() -> None:
    """End-to-end: the honest target comes from observed games, not from a prior."""
    obs = _synthetic_observations()

    late = diffuse_late_ratings(obs, seasons=(2021,), dim="off_epa")

    assert not late.empty
    assert set(late.columns) >= {"team_id", "season", LATE_TARGET_COLUMN, "n_games_used"}
    assert (late["n_games_used"] >= PriorConfig().late_n_games).all()

    # The filter learned the planted ordering from the games themselves.
    ordered = late.sort_values("team_id")[LATE_TARGET_COLUMN].to_numpy(dtype=float)
    assert np.corrcoef(ordered, np.arange(ordered.size))[0, 1] > 0.8


def test_diffuse_late_ratings_drops_teams_below_the_game_threshold() -> None:
    """A team with few games still has a diffuse posterior; it must not be scored.

    In a short season only the teams that happen to play often enough qualify,
    and the rest are dropped rather than contributing a prior-shaped rating.
    """
    short = diffuse_late_ratings(_synthetic_observations(n_weeks=4), seasons=(2021,), dim="off_epa")
    full = diffuse_late_ratings(_synthetic_observations(n_weeks=12), seasons=(2021,), dim="off_epa")
    threshold = PriorConfig().late_n_games

    assert len(short) < len(full)
    assert (short["n_games_used"] >= threshold).all()
    assert (full["n_games_used"] >= threshold).all()


def test_diffuse_late_ratings_on_empty_observations_returns_an_empty_frame() -> None:
    late = diffuse_late_ratings(pd.DataFrame(), seasons=(2021,), dim="off_epa")

    assert late.empty
    assert LATE_TARGET_COLUMN in late.columns


def test_attach_late_target_leaves_unmatched_rows_missing() -> None:
    """Absent late ratings must be NaN and dropped, never filled with a guess."""
    design = pd.DataFrame(
        {
            "team_id": [1, 2],
            "season": [2021, 2021],
            "dim": ["off_epa", "off_epa"],
            **{name: [0.0, 0.0] for name in PREDICTOR_NAMES},
        }
    )
    late = pd.DataFrame({"team_id": [1], "season": [2021], LATE_TARGET_COLUMN: [0.31]})

    out = attach_late_target(design, late)

    assert out.loc[out["team_id"] == 1, LATE_TARGET_COLUMN].item() == pytest.approx(0.31)
    assert bool(np.isnan(out.loc[out["team_id"] == 2, LATE_TARGET_COLUMN].item()))


def test_attach_late_target_with_no_late_ratings_yields_an_empty_target() -> None:
    design = pd.DataFrame(
        {
            "team_id": [1],
            "season": [2021],
            "dim": ["off_epa"],
            **{name: [0.0] for name in PREDICTOR_NAMES},
        }
    )

    out = attach_late_target(design, pd.DataFrame())

    assert bool(np.isnan(out[LATE_TARGET_COLUMN].item()))
    with pytest.raises(ValueError, match="no finite"):
        fit_prior_weights(out, dim="off_epa")
