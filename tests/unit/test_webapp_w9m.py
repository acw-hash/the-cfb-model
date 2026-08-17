"""W9-M: serialize production ensemble; cold-load predict contract (no full fit)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from ncaa_quant.evaluation.production_stack import (
    ProductionEnsemblePredictor,
    ProductionStack,
)
from ncaa_quant.evaluation.walkforward import (
    PredictionQualityGateResult,
    WalkForwardConfig,
    WalkForwardResult,
)
from ncaa_quant.models.heads.base import HeadTrainConfig
from ncaa_quant.models.heads.elasticnet import ElasticNetMuHead
from ncaa_quant.models.heads.margin import LightGBMMuHead
from ncaa_quant.models.heads.quantile import LightGBMQuantileHead
from ncaa_quant.models.heads.sigma import LightGBMSigmaHead
from ncaa_quant.pipelines.gates import evaluate_promotion_gate
from ncaa_quant.pipelines.predict import LockboxSeasonError, load_production_prediction_rows
from ncaa_quant.registry.bundle import (
    ENSEMBLE_FILENAME,
    FEATURES_FILENAME,
    FIT_PROCESS_FILENAME,
    BundleError,
    load_production_ensemble,
    save_production_ensemble,
)
from ncaa_quant.registry.champion_serialize import (
    _CaptureFeatureProvider,
    _inventory,
    _live_predict_fn,
    _max_abs_delta,
    _team_ids_from_snapshot,
    _truncate_games_through_oracle,
    hash_isolation_paths,
    isolation_state_paths,
    main,
    run_fit,
    run_verify,
    sha256_file,
)
from ncaa_quant.registry.store import INDEX_FILENAME, ModelRegistry, NoChampionError
from ncaa_quant.utils.seeding import SeedManifest

_FAST = HeadTrainConfig(n_estimators=15, learning_rate=0.1, num_leaves=8, max_depth=3)
REPO_ROOT = Path(__file__).resolve().parents[2]


def _xy(n: int = 48, *, seed: int = 0) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    gids = np.arange(1, n + 1)
    x1 = rng.normal(0, 1, size=n)
    features = pd.DataFrame(
        {
            "game_id": gids,
            "rating_diff_off_epa": x1,
            "home_off_epa": x1,
            "away_off_epa": -x1,
            "home_def_epa": rng.normal(0, 0.2, size=n),
            "away_def_epa": rng.normal(0, 0.2, size=n),
            "home_st_value": rng.normal(0, 0.1, size=n),
            "away_st_value": rng.normal(0, 0.1, size=n),
            "home_pace": rng.normal(70, 3, size=n),
            "away_pace": rng.normal(70, 3, size=n),
            "rating_uncertainty": np.abs(rng.normal(0.5, 0.1, size=n)),
        }
    )
    labels = pd.DataFrame(
        {
            "game_id": gids,
            "realized_margin": x1 * 4 + rng.normal(0, 2, size=n),
            "realized_total": rng.normal(52, 8, size=n),
            "season": np.full(n, 2022),
            "week": np.tile(np.arange(1, 7), n // 6 + 1)[:n],
        }
    )
    return features, labels


def _fitted_predictor() -> ProductionEnsemblePredictor:
    cfg = WalkForwardConfig(
        test_seasons=(2022,),
        continuity_seasons=(),
        mapping_layer="ensemble",
        seed=42,
        model_version="test-w9m",
        enforce_prediction_quality_gate=False,
    )
    pred = ProductionEnsemblePredictor(
        config=cfg,
        model_version="test-w9m",
        margin_head=LightGBMMuHead(target="margin", train=_FAST, seed=42),
        total_head=LightGBMMuHead(target="total", train=_FAST, seed=42),
        enet_margin=ElasticNetMuHead(target="margin", top_k=6, seed=42),
        sigma_margin_head=LightGBMSigmaHead(target="sigma_margin", train=_FAST, seed=42),
        sigma_total_head=LightGBMSigmaHead(target="sigma_total", train=_FAST, seed=42),
        quantile_margin_head=LightGBMQuantileHead(target="margin", train=_FAST, seed=42),
        n_mc_draws=32,
        n_epistemic_draws=0,
        seed=42,
    )
    features, labels = _xy()
    pred.fit(features, labels)
    assert pred.is_fitted
    return pred


def test_lockbox_season_error_still_only_refuses_2025_predictions() -> None:
    with pytest.raises(LockboxSeasonError, match="lockbox"):
        load_production_prediction_rows(2025, 1)


def test_cannot_serialize_unfitted_predictor(tmp_path: Path) -> None:
    cfg = WalkForwardConfig(test_seasons=(2022,), seed=42)
    pred = ProductionEnsemblePredictor(config=cfg, n_mc_draws=8, n_epistemic_draws=0, seed=42)
    with pytest.raises(BundleError, match="unfitted"):
        save_production_ensemble(pred, tmp_path / "x.pkl")


def test_ensemble_pickle_roundtrip_preserves_predict(tmp_path: Path) -> None:
    pred = _fitted_predictor()
    features, _labels = _xy()
    before = pred.predict(features)
    path = save_production_ensemble(pred, tmp_path / "ensemble.pkl")
    loaded = load_production_ensemble(path)
    after = loaded.predict(features)
    assert list(before["game_id"]) == list(after["game_id"])
    np.testing.assert_allclose(
        before["pred_margin"].to_numpy(dtype=float),
        after["pred_margin"].to_numpy(dtype=float),
        equal_nan=True,
        rtol=0.0,
        atol=0.0,
    )
    np.testing.assert_allclose(
        before["sigma_m"].to_numpy(dtype=float),
        after["sigma_m"].to_numpy(dtype=float),
        equal_nan=True,
        rtol=0.0,
        atol=0.0,
    )


def test_evaluate_promotion_gate_force_false_requires_pass_and_manual() -> None:
    blocked = evaluate_promotion_gate(
        candidate_version="1",
        gate_passed=False,
        manual_approve=True,
        force=False,
    )
    assert blocked.approved is False
    assert blocked.force is False
    approved = evaluate_promotion_gate(
        candidate_version="1",
        gate_passed=True,
        manual_approve=True,
        force=False,
    )
    assert approved.approved is True
    assert approved.force is False
    assert "manually approved" in approved.reason


def test_truncate_games_keeps_2024_week_5_and_drops_later() -> None:
    games = pd.DataFrame(
        {
            "season": [2023, 2024, 2024, 2024],
            "week": [15, 5, 6, 10],
            "game_id": [1, 2, 3, 4],
        }
    )
    out = _truncate_games_through_oracle(games)
    assert sorted(out["game_id"].tolist()) == [1, 2]
    assert int(out.loc[out["season"] == 2024, "week"].max()) == 5


def test_isolation_paths_include_w9p_state_files() -> None:
    paths = {p.as_posix().replace("\\", "/") for p in isolation_state_paths(REPO_ROOT)}
    joined = "\n".join(sorted(paths))
    assert "data/webapp/tier_state.json" in joined
    assert "data/webapp/tier_changes.jsonl" in joined
    assert "data/pipeline_state/idempotency.json" in joined
    assert "data/artifacts/state_space/filter_history.parquet" in joined
    assert "data/artifacts/expected_possessions/live.json" in joined
    assert "season=2024_week=5.parquet" in joined


def test_registry_index_schema_roundtrip(tmp_path: Path) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    assert (tmp_path / "registry" / INDEX_FILENAME).is_file()
    payload = (tmp_path / "registry" / INDEX_FILENAME).read_text(encoding="utf-8")
    assert '"model_name"' in payload
    assert '"versions"' in payload
    assert '"champion_history"' in payload
    with pytest.raises(NoChampionError, match="no champion"):
        registry.resolve_champion()


def test_capture_provider_snapshots_matching_week_only() -> None:
    class _Inner:
        def compute_game_features(
            self,
            games: pd.DataFrame,
            as_of: datetime,
            *,
            rating_state: dict[str, Any],
            market_features: bool,
        ) -> pd.DataFrame:
            del as_of, rating_state, market_features
            return pd.DataFrame({"game_id": games["game_id"].tolist(), "x": [1.0] * len(games)})

    cap = _CaptureFeatureProvider(_Inner(), season=2024, week=5)
    as_of = datetime(2024, 9, 24, 10, tzinfo=UTC)
    cap.compute_game_features(
        pd.DataFrame({"season": [2024], "week": [4], "game_id": [1]}),
        as_of,
        rating_state={"skip": 1},
        market_features=False,
    )
    assert cap.features is None
    cap.compute_game_features(
        pd.DataFrame({"season": [2024], "week": [5], "game_id": [9]}),
        as_of,
        rating_state={"1:off_epa": 0.2},
        market_features=False,
    )
    assert cap.features is not None
    assert cap.features["game_id"].tolist() == [9]
    assert cap.rating_snapshot == {"1:off_epa": 0.2}
    assert cap.as_of == as_of


def test_capture_getattr_forwards_to_inner() -> None:
    class _Inner:
        snapshots = "forwarded"

        def compute_game_features(self, *args: Any, **kwargs: Any) -> pd.DataFrame:
            del args, kwargs
            return pd.DataFrame()

    cap = _CaptureFeatureProvider(_Inner(), season=2024, week=5)
    assert cap.snapshots == "forwarded"


def test_hash_isolation_paths_and_sha256(tmp_path: Path) -> None:
    present = tmp_path / "a.txt"
    present.write_text("hello\n", encoding="utf-8")
    missing = tmp_path / "absent.txt"
    hashed = hash_isolation_paths([present, missing])
    assert hashed[str(present.as_posix())] == sha256_file(present)
    assert hashed[str(missing.as_posix())] == "ABSENT"


def test_team_ids_inventory_and_delta_helpers() -> None:
    ids = _team_ids_from_snapshot({"12:off_epa": 1.0, "7:def_epa": 0.0, "": 0})
    assert ids == ["7", "12"]
    pred = _fitted_predictor()
    inv = _inventory(
        predictor=pred,
        snapshot={"12:off_epa": 1.0},
        features=pd.DataFrame({"game_id": [1, 2]}),
        artifact_sizes={"production_ensemble.pkl": {"bytes": 8, "sha256": "ab"}},
    )
    assert inv["oracle_season"] == 2024
    assert inv["oracle_week"] == 5
    assert inv["state"]["predict_features"]["n_rows"] == 2
    assert inv["adr_0014_thresholds"]["MAX_CREDIBLE_MARGIN_PRED"] == 80.0
    assert _max_abs_delta(None, None) == 0.0
    assert _max_abs_delta(1.5, 1.0) == 0.5
    assert _max_abs_delta(None, 1.0) is None
    assert _max_abs_delta("x", 1.0) is None


def test_live_predict_fn_stamps_champion_identity() -> None:
    pred = _fitted_predictor()
    features, _labels = _xy(n=8)
    rows = _live_predict_fn(features, pred)(object())
    assert len(rows) == 8
    assert {int(r["season"]) for r in rows} == {2024}
    assert {int(r["week"]) for r in rows} == {5}
    assert {r["run_id"] for r in rows} == {"task23_fundamental_reduced_v2"}
    assert {r["model_version"] for r in rows} == {"production-v0_reduced_v2"}
    assert "pred_margin" in rows[0] or "mu_margin" in rows[0]


def test_main_requires_fit_or_verify() -> None:
    assert main([]) == 2
    assert main(["nope"]) == 2


def test_run_verify_requires_fit_process_record(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="fit process record missing"):
        run_verify(registry_root=tmp_path / "registry")


def test_mocked_run_fit_promotes_without_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    iso = tmp_path / "keep.txt"
    iso.write_text("stable\n", encoding="utf-8")
    registry_root = tmp_path / "registry"
    cfg = WalkForwardConfig(
        test_seasons=(2024,),
        continuity_seasons=(),
        seed=42,
        run_id="task23_fundamental_reduced_v2",
        ablation_id="full",
        model_version="production-v0_reduced_v2",
        enforce_prediction_quality_gate=False,
        max_zero_mu_rate=0.001,
        min_train_games=50,
    )

    class _Inner:
        snapshots = None
        possessions_artifacts: dict[str, Any] = {}

        def compute_game_features(
            self,
            games: pd.DataFrame,
            as_of: datetime,
            *,
            rating_state: dict[str, Any],
            market_features: bool,
        ) -> pd.DataFrame:
            del as_of, rating_state, market_features
            feats, _labels = _xy(n=len(games) if len(games) else 1)
            out = feats.iloc[: len(games)].copy()
            out["game_id"] = games["game_id"].to_numpy()
            return out.reset_index(drop=True)

    fitted = _fitted_predictor()
    fitted.cfbd_lines = None
    stack = ProductionStack(
        kind="fundamental",
        config=cfg,
        feature_provider=_Inner(),  # type: ignore[arg-type]
        rating_engine=object(),  # type: ignore[arg-type]
        predictor=fitted,
    )

    class _FakeHarness:
        def __init__(
            self,
            *,
            config: WalkForwardConfig,
            predictor: ProductionEnsemblePredictor,
            feature_provider: Any,
            rating_engine: Any,
        ) -> None:
            self.config = config
            self.predictor = predictor
            self.feature_provider = feature_provider
            self.rating_engine = rating_engine

        def run(
            self,
            games: pd.DataFrame,
            snapshots: Any = None,
            cfbd_lines: Any = None,
        ) -> WalkForwardResult:
            del snapshots, cfbd_lines
            week = games.loc[
                (games["season"].astype(int) == 2024) & (games["week"].astype(int) == 5)
            ].copy()
            as_of = datetime(2024, 9, 24, 10, tzinfo=UTC)
            self.feature_provider.compute_game_features(
                week,
                as_of,
                rating_state={"1:off_epa": 0.1, "2:def_epa": -0.2},
                market_features=False,
            )
            quality = PredictionQualityGateResult(
                n_scored=1,
                zero_mu_rate=0.0,
                n_null_mu=0,
                n_zero_sd_blocks=0,
                zero_sd_blocks=(),
                min_n_train_games=125,
                max_zero_mu_rate=0.001,
                min_train_games_required=50,
                passed=True,
                failures=(),
            )
            preds = pd.DataFrame(
                {
                    "game_id": week["game_id"].tolist(),
                    "season": [2024] * len(week),
                    "week": [5] * len(week),
                    "pred_margin": [1.0] * len(week),
                }
            )
            return WalkForwardResult(
                predictions=preds,
                feature_log=pd.DataFrame(),
                config=self.config,
                seed_manifest=SeedManifest(42, "42", 42, 42, 42),
                quality_gate=quality,
            )

    games = pd.DataFrame(
        {
            "season": [2023, 2024, 2024],
            "week": [15, 5, 6],
            "game_id": [10, 20, 30],
        }
    )
    monkeypatch.setattr(
        "ncaa_quant.registry.champion_serialize.isolation_state_paths",
        lambda repo_root=None: [iso],
    )
    monkeypatch.setattr(
        "ncaa_quant.registry.champion_serialize.load_backtest_config",
        lambda _name: {},
    )
    monkeypatch.setattr(
        "ncaa_quant.registry.champion_serialize.walkforward_config_from_mapping",
        lambda _payload: cfg,
    )
    monkeypatch.setattr(
        "ncaa_quant.registry.champion_serialize._load_stack_inputs",
        lambda **_kwargs: stack,
    )
    monkeypatch.setattr(
        "ncaa_quant.registry.champion_serialize.load_staged_games",
        lambda *_args, **_kwargs: games,
    )
    monkeypatch.setattr(
        "ncaa_quant.registry.champion_serialize.WalkForwardHarness",
        _FakeHarness,
    )

    out = run_fit(registry_root=registry_root)
    assert out["promotion"]["approved"] is True
    assert out["promotion"]["force"] is False
    assert Path(out["artifact_dir"]).is_dir()
    assert (Path(out["artifact_dir"]) / ENSEMBLE_FILENAME).is_file()
    assert (registry_root / FIT_PROCESS_FILENAME).is_file()
    champ = ModelRegistry(registry_root, tracking_uri=None).resolve_champion()
    assert champ.version == 1
    assert champ.stage == "champion"


def test_mocked_run_verify_cold_load_and_oracle_diff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    iso = tmp_path / "keep.txt"
    iso.write_text("stable\n", encoding="utf-8")
    registry_root = tmp_path / "registry"
    art = registry_root / "artifacts" / "v1"
    art.mkdir(parents=True)
    pred = _fitted_predictor()
    save_production_ensemble(pred, art / ENSEMBLE_FILENAME)
    features, _labels = _xy(n=2)
    features.to_parquet(art / FEATURES_FILENAME, index=False)
    ModelRegistry(registry_root, tracking_uri=None)
    index_path = registry_root / INDEX_FILENAME
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["champion_history"] = [1]
    index["versions"] = [
        {
            "version": 1,
            "stage": "champion",
            "run_id": "task23_fundamental_reduced_v2",
            "artifact_dir": str(art),
            "registered_at": "2026-08-17T00:00:00Z",
            "manifest": {},
            "metrics": {},
            "notes": "",
            "feature_signature": None,
            "prior_champion_version": None,
        }
    ]
    index_path.write_text(json.dumps(index), encoding="utf-8")
    (registry_root / FIT_PROCESS_FILENAME).write_text(
        json.dumps({"pid": -1, "ended_at": "2026-08-17T18:28:31Z"}),
        encoding="utf-8",
    )

    fixture = {
        "model_identity": {
            "champion_version": 3,
            "run_id": "task23_fundamental_reduced_v2",
            "model_version": "production-v0_reduced_v2",
        },
        "games": [
            {
                "game_id": "1",
                "mu_margin": 0.0,
                "sigma_margin": 1.0,
                "margin_interval_lo": -2.0,
                "margin_interval_hi": 2.0,
                "mu_total": 50.0,
                "sigma_total": 10.0,
                "p_win_home": 0.5,
                "p_favored": 0.5,
                "conviction_tier": "toss_up",
            }
        ],
    }
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")

    def _fake_export(**kwargs: Any) -> dict[str, Any]:
        out_dir = Path(kwargs["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        produced = {
            "model_identity": fixture["model_identity"],
            "games": [
                {
                    "game_id": "1",
                    "mu_margin": 2.0,
                    "sigma_margin": 1.0,
                    "margin_interval_lo": -2.0,
                    "margin_interval_hi": 2.0,
                    "mu_total": 50.0,
                    "sigma_total": 10.0,
                    "p_win_home": 0.5,
                    "p_favored": 0.5,
                    "conviction_tier": "lean",
                }
            ],
        }
        path = out_dir / "week_predictions.json"
        path.write_text(json.dumps(produced), encoding="utf-8")
        return {
            "written": {"week_predictions.json": str(path)},
            "export_enabled": False,
        }

    monkeypatch.setattr(
        "ncaa_quant.registry.champion_serialize.isolation_state_paths",
        lambda repo_root=None: [iso],
    )
    monkeypatch.setattr(
        "ncaa_quant.registry.champion_serialize.run_isolated_week_export",
        _fake_export,
    )
    monkeypatch.setattr(
        "ncaa_quant.registry.champion_serialize.FIXTURE_PATH",
        fixture_path,
    )

    report = run_verify(registry_root=registry_root, output_dir=tmp_path / "oracle")
    assert report["n_games"] == 1
    assert report["game_id_sets_identical"] is True
    assert report["max_abs_delta"]["mu_margin"] == 2.0
    assert report["tier_agree"] == 0
    assert report["export_enabled"] is False
