"""Task 22B — production wiring, ablation no-op flags, resume, manifest."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from ncaa_quant.evaluation.backtest_runner import (
    plan_backtest,
    require_complete_manifest,
    run_backtest,
    walkforward_config_from_mapping,
)
from ncaa_quant.evaluation.leakage import (
    assert_no_prophecy_features,
    audit_prophecy_features,
)
from ncaa_quant.evaluation.production_stack import (
    assert_feature_signature,
    build_production_stack,
)
from ncaa_quant.evaluation.walkforward import (
    WalkForwardConfig,
    WalkForwardError,
    WalkForwardHarness,
    audit_information_set,
    predictions_bytes,
    run_shifted_label_test,
    week_decision_as_of,
)
from ncaa_quant.models.heads.base import FeatureSignatureError
from ncaa_quant.registry.manifest import ManifestError, build_manifest
from ncaa_quant.utils.seeding import set_global_seed
from tests.fixtures.walkforward_stubs import (
    LeagueAverageMarginPredictor,
    RunningMarginRatingEngine,
)
from tests.unit.test_walkforward import (
    PitMeanMarginFeatureProvider,
    build_multi_season_games,
    build_team_history,
)


def _kickoff(season: int, week: int, slot: int = 0) -> datetime:
    tuesday = week_decision_as_of(season, week, WalkForwardConfig())
    return tuesday + timedelta(days=4, hours=slot)


def _synth_games(
    seasons: Sequence[int] = (2023,),
    weeks: Sequence[int] = (1, 2, 5, 10),
    games_per_week: int = 2,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    gid = 5000
    rng = np.random.default_rng(7)
    for season in seasons:
        for week in weeks:
            for slot in range(games_per_week):
                home = 10 + slot
                away = 20 + slot
                start = _kickoff(season, week, slot)
                rows.append(
                    {
                        "game_id": gid,
                        "game_key": f"{season}:{home}:{away}:{start.date()}",
                        "season": season,
                        "week": week,
                        "event_time": start,
                        "home_team_id": home,
                        "away_team_id": away,
                        "home_points": int(24 + rng.integers(0, 21)),
                        "away_points": int(21 + rng.integers(0, 21)),
                        "neutral_site": False,
                    }
                )
                gid += 1
    return pd.DataFrame(rows)


def _synth_observations(games: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for g in games.itertuples(index=False):
        # Team-specific EPA so Stage-1 / μ heads are not constant across games.
        home_epa = 0.02 * (int(g.home_team_id) % 7) - 0.05
        away_epa = 0.02 * (int(g.away_team_id) % 7) - 0.05
        rows.append(
            {
                "game_id": int(g.game_id),
                "season": int(g.season),
                "week": int(g.week),
                "event_time": g.event_time,
                "home_team_id": int(g.home_team_id),
                "away_team_id": int(g.away_team_id),
                "home_epa": home_epa,
                "away_epa": away_epa,
                "home_plays": 70.0,
                "away_plays": 68.0,
                "margin": float(g.home_points) - float(g.away_points),
                "neutral_site": False,
            }
        )
    return pd.DataFrame(rows)


def _synth_cfbd_lines(games: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for g in games.itertuples(index=False):
        for book in ("a", "b"):
            rows.append(
                {
                    "game_id": int(g.game_id),
                    "book": book,
                    "line_type": "close",
                    "spread": -3.5,
                    "total": 55.0,
                }
            )
            rows.append(
                {
                    "game_id": int(g.game_id),
                    "book": book,
                    "line_type": "open",
                    "spread": -3.0,
                    "total": 54.0,
                }
            )
    return pd.DataFrame(rows)


def _run_stack(
    cfg: WalkForwardConfig,
    games: pd.DataFrame,
    *,
    priors_frame: pd.DataFrame | None = None,
) -> Any:
    from dataclasses import replace

    # Thin synth OOF cannot fit NNLS; tests that exercise the ensemble path
    # must opt into an explicit equal-weight fallback (stamped, never silent).
    if cfg.mapping_layer == "ensemble" and not cfg.nnls_equal_weight_fallback:
        cfg = replace(cfg, nnls_equal_weight_fallback=True)
    obs = _synth_observations(games)
    stack = build_production_stack(
        cfg,
        kind="market_aware" if cfg.market_features_available else "fundamental",
        observations=obs,
        priors_frame=priors_frame,
        cfbd_lines=_synth_cfbd_lines(games),
        play_counts=(80, 100),
        n_mc_draws=400,
        n_epistemic_draws=2,
    )
    harness = WalkForwardHarness(
        config=stack.config,
        predictor=stack.predictor,
        feature_provider=stack.feature_provider,
        rating_engine=stack.rating_engine,
    )
    result = harness.run(games, cfbd_lines=_synth_cfbd_lines(games))
    return result, stack


def _fitted_priors_frame(games: pd.DataFrame) -> pd.DataFrame:
    """Non-league priors so A1 fitted vs league_mean is not a silent no-op."""
    tids = sorted(
        set(int(x) for x in games["home_team_id"]) | set(int(x) for x in games["away_team_id"])
    )
    rows: list[dict[str, Any]] = []
    for i, tid in enumerate(tids):
        for dim in ("off_epa", "def_epa", "st_value", "pace"):
            rows.append(
                {
                    "team_id": tid,
                    "season": 2023,
                    "dim": dim,
                    "prior_mean": 0.15 + 0.01 * i,
                    "prior_var": 0.05,
                }
            )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Placeholder still works from fixtures
# ---------------------------------------------------------------------------


def test_task16_placeholder_still_passes() -> None:
    games = build_multi_season_games(seasons=(2021,), weeks=(1, 2), games_per_week=2)
    history = build_team_history(games)
    cfg = WalkForwardConfig(
        test_seasons=(2021,),
        continuity_seasons=(),
        retrain_weeks=(),
        market_features_available=False,
        seed=0,
        model_version="placeholder-league-avg-v0",
    )
    harness = WalkForwardHarness(
        config=cfg,
        predictor=LeagueAverageMarginPredictor(model_version=cfg.model_version),
        feature_provider=PitMeanMarginFeatureProvider(history),
        rating_engine=RunningMarginRatingEngine(),
    )
    result = harness.run(games)
    assert len(result.predictions) == 4


# ---------------------------------------------------------------------------
# A1–A6 no-op flag tests (mechanism assertions)
# ---------------------------------------------------------------------------


def test_a1_league_mean_priors_mechanism() -> None:
    games = _synth_games(weeks=(1, 2, 5, 10))
    base = WalkForwardConfig(
        test_seasons=(2023,),
        continuity_seasons=(),
        retrain_weeks=(5,),
        market_features_available=False,
        preseason_priors="fitted",
        seed=1,
        run_id="a1_base",
        ablation_id="full",
    )
    abl = WalkForwardConfig(
        test_seasons=(2023,),
        continuity_seasons=(),
        retrain_weeks=(5,),
        market_features_available=False,
        preseason_priors="league_mean",
        seed=1,
        run_id="a1_abl",
        ablation_id="A1",
    )
    priors = _fitted_priors_frame(games)
    from ncaa_quant.evaluation.production_stack import (
        ProductionStackError,
        assert_a1_priors_precondition,
    )

    assert_a1_priors_precondition(priors)
    with pytest.raises(ProductionStackError, match="missing/empty|already degenerate"):
        assert_a1_priors_precondition(None)
    r_base, _ = _run_stack(base, games, priors_frame=priors)
    r_abl, stack = _run_stack(abl, games, priors_frame=priors)
    assert not r_base.predictions["pred_margin"].equals(r_abl.predictions["pred_margin"])

    # Mechanism: after initialize_season, every team mean equals league prior_mean.
    as_of = week_decision_as_of(2023, 1, abl) - timedelta(seconds=1)
    stack.rating_engine.initialize_season(2023, as_of)
    snap = stack.rating_engine.state_snapshot()
    league = float(stack.rating_engine.ss_config.prior_mean)
    means = [v for k, v in snap.items() if k.endswith(":off_epa") and not k.startswith("_")]
    assert means, "expected team off_epa means in snapshot"
    assert all(abs(m - league) < 1e-9 for m in means)


def test_a2_frozen_rating_state_mechanism() -> None:
    games = _synth_games(weeks=(1, 2, 5, 10))
    cfg = WalkForwardConfig(
        test_seasons=(2023,),
        continuity_seasons=(),
        retrain_weeks=(5,),
        market_features_available=False,
        rating_updates="frozen_after_week_1",
        seed=2,
        run_id="a2",
        ablation_id="A2",
    )
    continual = WalkForwardConfig(
        test_seasons=(2023,),
        continuity_seasons=(),
        retrain_weeks=(5,),
        market_features_available=False,
        rating_updates="continual",
        seed=2,
        run_id="a2_base",
        ablation_id="full",
    )
    r_c, _ = _run_stack(continual, games)
    r_f, stack = _run_stack(cfg, games)
    assert not r_c.predictions["pred_margin"].equals(r_f.predictions["pred_margin"])

    week1 = stack.rating_engine.week1_state_snapshot()
    assert week1 is not None
    # After full run under freeze, live snapshot must match Week-1 freeze.
    live = stack.rating_engine.state_snapshot()
    for k, v in week1.items():
        assert abs(float(live[k]) - float(v)) < 1e-9, f"A2 freeze broken on {k}"


def test_a3_market_features_off_mechanism() -> None:
    games = _synth_games()
    on = WalkForwardConfig(
        test_seasons=(2023,),
        continuity_seasons=(),
        retrain_weeks=(),
        market_features_available=True,
        market_feature_source="cfbd_open_close",
        seed=3,
        run_id="a3_on",
        ablation_id="full",
    )
    off = WalkForwardConfig(
        test_seasons=(2023,),
        continuity_seasons=(),
        retrain_weeks=(),
        market_features_available=False,
        market_feature_source="cfbd_open_close",
        seed=3,
        run_id="a3_off",
        ablation_id="A3",
    )
    r_on, s_on = _run_stack(on, games)
    r_off, s_off = _run_stack(off, games)
    assert predictions_bytes(r_on.predictions) != predictions_bytes(r_off.predictions)

    # Mechanism: no market feature is non-null under A3.
    feat_cols = [c for c in r_off.feature_log.columns if c.startswith("feat__mkt_")]
    for c in feat_cols:
        vals = r_off.feature_log[c]
        assert vals.isna().all() or (vals.astype(str) == "null").all()
    # Provider must omit market columns when flag is False.
    as_of = week_decision_as_of(2023, 1, off)
    feats = s_off.feature_provider.compute_game_features(
        games.loc[games["week"] == 1],
        as_of,
        rating_state={},
        market_features=False,
    )
    assert not any(c.startswith("mkt_") or c == "market_provenance" for c in feats.columns)


def test_a4_single_lgbm_mechanism() -> None:
    games = _synth_games()
    ens = WalkForwardConfig(
        test_seasons=(2023,),
        continuity_seasons=(),
        retrain_weeks=(5,),
        market_features_available=False,
        mapping_layer="ensemble",
        seed=4,
        run_id="a4_ens",
        ablation_id="full",
    )
    single = WalkForwardConfig(
        test_seasons=(2023,),
        continuity_seasons=(),
        retrain_weeks=(5,),
        market_features_available=False,
        mapping_layer="single_lgbm",
        seed=4,
        run_id="a4_single",
        ablation_id="A4",
    )
    r_e, s_e = _run_stack(ens, games)
    r_s, s_s = _run_stack(single, games)
    assert not r_e.predictions["pred_margin"].equals(r_s.predictions["pred_margin"])
    weights = s_s.predictor.ensemble_weights
    assert weights == {"lgbm_mu_margin": 1.0}
    assert abs(sum(weights.values()) - 1.0) < 1e-12


def test_a5_garbage_time_filter_mechanism() -> None:
    games = _synth_games()
    on = WalkForwardConfig(
        test_seasons=(2023,),
        continuity_seasons=(),
        retrain_weeks=(),
        market_features_available=False,
        garbage_time_filter=True,
        seed=5,
        run_id="a5_on",
        ablation_id="full",
    )
    off = WalkForwardConfig(
        test_seasons=(2023,),
        continuity_seasons=(),
        retrain_weeks=(),
        market_features_available=False,
        garbage_time_filter=False,
        seed=5,
        run_id="a5_off",
        ablation_id="A5",
    )
    _, s_on = _run_stack(on, games)
    _, s_off = _run_stack(off, games)
    n_on = s_on.feature_provider.play_count_entering_efficiency
    n_off = s_off.feature_provider.play_count_entering_efficiency
    assert n_off > n_on
    # Expected garbage-time share from play_counts=(80, 100) → +20 / 80 = 0.25
    share = (n_off - n_on) / n_on
    assert abs(share - 0.25) < 1e-9
    # Fixture-production drift: equal counts must fail the precondition.
    from ncaa_quant.evaluation.production_stack import (
        ProductionStackError,
        assert_a5_garbage_time_precondition,
        build_production_stack,
    )

    assert_a5_garbage_time_precondition(n_plays_gt_on=n_on, n_plays_gt_off=n_off)
    with pytest.raises(ProductionStackError, match="inert"):
        build_production_stack(
            off,
            kind="fundamental",
            observations=_synth_observations(games),
            play_counts=(100, 100),
            n_mc_draws=200,
            n_epistemic_draws=1,
            enforce_ablation_preconditions=True,
        )


def test_a6_cfbd_market_source_mechanism() -> None:
    games = _synth_games()
    cfg = WalkForwardConfig(
        test_seasons=(2023,),
        continuity_seasons=(),
        retrain_weeks=(),
        market_features_available=True,
        market_feature_source="cfbd_open_close",
        seed=6,
        run_id="a6",
        ablation_id="A6",
        nnls_equal_weight_fallback=True,
    )
    # Snapshots present but must not be read under A6.
    snaps = pd.DataFrame(
        {
            "game_id": games["game_id"],
            "event_time": games["event_time"] - pd.Timedelta(hours=1),
            "spread": -99.0,
            "total": 99.0,
            "book": "snap",
        }
    )
    obs = _synth_observations(games)
    stack = build_production_stack(
        cfg,
        kind="market_aware",
        observations=obs,
        snapshots=snaps,
        cfbd_lines=_synth_cfbd_lines(games),
        n_mc_draws=400,
        n_epistemic_draws=2,
    )
    # Force provider to hold snaps so a bug would leak -99 spreads.
    stack.feature_provider.snapshots = snaps
    harness = WalkForwardHarness(
        config=stack.config,
        predictor=stack.predictor,
        feature_provider=stack.feature_provider,
        rating_engine=stack.rating_engine,
    )
    result = harness.run(games, snapshots=snaps, cfbd_lines=_synth_cfbd_lines(games))
    prov = result.feature_log["feat__market_provenance"]
    assert (prov == "cfbd").all()
    # No snapshot sentinel spread leaked into market features.
    assert not (result.feature_log["feat__mkt_spread"] == -99.0).any()


def test_a6_outside_window_hard_error() -> None:
    cfg = WalkForwardConfig(
        test_seasons=(2019,),
        continuity_seasons=(),
        market_feature_source="cfbd_open_close",
    )
    with pytest.raises(WalkForwardError, match="2021-2025"):
        cfg.validate_ablations()


# ---------------------------------------------------------------------------
# Adapter / manifest / resume
# ---------------------------------------------------------------------------


def test_adapter_signature_mismatch_names_columns() -> None:
    frame = pd.DataFrame({"game_id": [1], "a": [1.0], "b": [2.0]})
    with pytest.raises(FeatureSignatureError, match="missing=\\['c'\\].*unexpected=\\['b'\\]"):
        assert_feature_signature(frame, ["a", "c"])


def test_manifest_completeness_requires_ablations() -> None:
    seeds = set_global_seed(0)
    bad = build_manifest(config={"x": 1}, seed_manifest=seeds, extra={})
    with pytest.raises(ManifestError, match="ablation"):
        require_complete_manifest(bad)
    good = build_manifest(
        config={"x": 1},
        seed_manifest=seeds,
        extra={"ablation_settings": "{}"},
    )
    require_complete_manifest(good)


def test_resumability_no_duplicate_weeks(tmp_path: Path) -> None:
    games = _synth_games(weeks=(1, 2, 5), games_per_week=4)
    payload = {
        "run_id": "resume_demo",
        "ablation_id": "full",
        "walkforward": {
            "test_seasons": [2023],
            "continuity_seasons": [],
            "retrain_weeks": [2],
            "market_features_available": False,
            "seed": 9,
            "run_id": "resume_demo",
            "ablation_id": "full",
            "nnls_equal_weight_fallback": True,
            "enforce_prediction_quality_gate": False,
            "min_train_games": 1,
        },
    }
    obs = _synth_observations(games)
    stack = build_production_stack(
        walkforward_config_from_mapping(payload),
        kind="fundamental",
        observations=obs,
        n_mc_draws=400,
        n_epistemic_draws=2,
    )
    r1 = run_backtest(
        "resume_demo",
        games=games,
        stack=stack,
        cfbd_lines=_synth_cfbd_lines(games),
        observations=obs,
        output_root=tmp_path,
        force=False,
        tracking_uri=f"file:{tmp_path / 'mlruns'}",
        config_payload=payload,
    )
    n1 = len(r1.predictions)
    # Restart — should load completed units, not duplicate.
    stack2 = build_production_stack(
        walkforward_config_from_mapping(payload),
        kind="fundamental",
        observations=obs,
        n_mc_draws=400,
        n_epistemic_draws=2,
    )
    r2 = run_backtest(
        "resume_demo",
        games=games,
        stack=stack2,
        cfbd_lines=_synth_cfbd_lines(games),
        observations=obs,
        output_root=tmp_path,
        force=False,
        tracking_uri=f"file:{tmp_path / 'mlruns'}",
        config_payload=payload,
    )
    assert len(r2.predictions) == n1
    assert r2.predictions["game_id"].duplicated().sum() == 0
    assert r2.resumed_units >= 1 or r2.completed_units >= 1


def test_plan_task23_includes_wall_clock() -> None:
    plan = plan_backtest("task23_full")
    text = plan.format_text()
    assert "estimated_wall_clock_sec" in text
    assert plan.n_week_units > 0
    assert 2019 in plan.seasons


# ---------------------------------------------------------------------------
# Leakage suite vs production stack
# ---------------------------------------------------------------------------


def test_production_infoset_determinism_and_leakage_suite() -> None:
    """A-8: information-set + prophecy audit gate; shifted-label is diagnostic only."""
    games = _synth_games(weeks=(1, 2, 3, 4, 5))
    cfg = WalkForwardConfig(
        test_seasons=(2023,),
        continuity_seasons=(),
        retrain_weeks=(5,),
        market_features_available=False,
        seed=11,
        run_id="leak",
        ablation_id="full",
    )
    r1, stack = _run_stack(cfg, games)

    # Rebuild per-week rating snapshots for the information-set audit.
    obs = _synth_observations(games)
    engine = build_production_stack(
        cfg,
        kind="fundamental",
        observations=obs,
        play_counts=(80, 100),
        n_mc_draws=400,
        n_epistemic_draws=2,
    ).rating_engine
    rating_snapshots: dict[tuple[int, int], dict[str, Any]] = {}
    weeks = sorted(int(w) for w in games["week"].unique())
    first_as_of = week_decision_as_of(2023, weeks[0], cfg)
    engine.initialize_season(2023, first_as_of - timedelta(seconds=1))
    for week in weeks:
        rating_snapshots[(2023, week)] = engine.state_snapshot()
        week_games = games.loc[games["week"] == week]
        engine.update_after_games(week_games)

    r2, _ = _run_stack(cfg, games)
    assert predictions_bytes(r1.predictions) == predictions_bytes(r2.predictions)

    audit = audit_information_set(
        r1.feature_log,
        stack.feature_provider,
        games,
        rating_snapshots=rating_snapshots,
        market_features=False,
    )
    assert audit.n_week_points >= 4
    assert audit.passed, audit.mismatches[:3]

    early = games.loc[games["week"] < 5]
    labels = early.copy()
    labels["realized_margin"] = labels["home_points"].astype(float) - labels["away_points"].astype(
        float
    )
    labels["realized_total"] = labels["home_points"].astype(float) + labels["away_points"].astype(
        float
    )
    as_of = week_decision_as_of(2023, 4, cfg)
    feats = stack.feature_provider.compute_game_features(
        early,
        as_of,
        rating_state=rating_snapshots[(2023, 4)],
        market_features=False,
    )
    stack.predictor.fit(feats, labels)

    # Gate: honest features must not be outcome copies (prophecy audit).
    prophecy = audit_prophecy_features(feats, labels)
    assert_no_prophecy_features(prophecy)

    # Diagnostic only — null is invalid per audit A-8 / amended §14.
    past = games.loc[games["week"] <= 2].copy()
    shifted_as_of = datetime(2024, 1, 15, tzinfo=UTC)
    shifted = run_shifted_label_test(
        stack.predictor,
        past,
        stack.feature_provider,
        shifted_as_of,
        rating_state=rating_snapshots[(2023, 2)],
        market_features=False,
        tolerance=0.25,
    )
    assert shifted.n > 0
    assert shifted.null_is_invalid
    print(
        f"SHIFTED_LABEL_DIAGNOSTIC model={shifted.model_score} "
        f"chance={shifted.chance_score} (not a gate)"
    )
