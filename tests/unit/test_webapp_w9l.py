"""W9-L Amendment 2: live champion-method path, lockbox split, kickoff filter."""

from __future__ import annotations

import pickle
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from ncaa_quant.config import AppConfig, PathsConfig, WebappConfig
from ncaa_quant.evaluation.lockbox import LOCKBOX_SEASON, LockboxViolation, assert_lockbox_excluded
from ncaa_quant.evaluation.production_stack import StateSpaceRatingEngine
from ncaa_quant.evaluation.walkforward import WalkForwardConfig
from ncaa_quant.pipelines.notifications import AlertKind, RecordingNotifier
from ncaa_quant.pipelines.predict import (
    LockboxSeasonError,
    RefreshKind,
    exclude_games_kicked_off_before,
    execute_predict_publish,
    live_observation_seasons,
    live_predict_rows,
    load_champion_walkforward_config,
    rating_snapshot_digest,
)
from ncaa_quant.pipelines.stale import StaleContext


def test_live_observation_seasons_include_2025_for_2026() -> None:
    seasons = live_observation_seasons(2026)
    assert 2025 in seasons
    assert 2019 in seasons
    assert 2026 in seasons
    assert seasons == tuple(range(2019, 2027))


def test_live_observation_seasons_2024_stop_before_lockbox() -> None:
    seasons = live_observation_seasons(2024)
    assert 2025 not in seasons
    assert seasons[-1] == 2024


def test_champion_walkforward_config_excludes_2025_replay() -> None:
    wf = load_champion_walkforward_config()
    replay = wf.all_replay_seasons()
    assert LOCKBOX_SEASON not in replay
    wf.validate_ablations()


def test_2025_as_replay_season_refused() -> None:
    for field in ("test_seasons", "continuity_seasons", "warmup_seasons"):
        cfg = WalkForwardConfig(**{field: (LOCKBOX_SEASON,)})  # type: ignore[arg-type]
        with pytest.raises(LockboxViolation, match="lockbox season 2025"):
            cfg.validate_ablations()
    with pytest.raises(LockboxViolation, match="lockbox season 2025"):
        assert_lockbox_excluded(
            [2019, LOCKBOX_SEASON],
            context="live predict WalkForwardConfig",
        )


def test_live_predict_refuses_lockbox_season() -> None:
    with pytest.raises(LockboxSeasonError, match="lockbox"):
        live_predict_rows(LOCKBOX_SEASON, 1)


def test_missing_prior_season_does_not_cold_start() -> None:
    """2025 absent from priors_frame → empty injection (season-regress), not 0-mean."""
    priors = pd.DataFrame(
        [
            {
                "team_id": 10,
                "season": 2024,
                "dim": dim,
                "prior_mean": 0.2,
                "prior_var": 0.04,
            }
            for dim in ("off_epa", "def_epa", "st_value", "pace")
        ]
    )
    obs = pd.DataFrame(
        [
            {
                "game_id": 1,
                "season": 2024,
                "week": 1,
                "event_time": datetime(2024, 8, 31, tzinfo=UTC),
                "home_team_id": 10,
                "away_team_id": 20,
                "home_epa": 0.1,
                "away_epa": -0.1,
                "home_plays": 70.0,
                "away_plays": 65.0,
                "margin": 7.0,
                "neutral_site": False,
            }
        ]
    )
    engine = StateSpaceRatingEngine(
        observations=obs,
        config=WalkForwardConfig(test_seasons=(2024,), continuity_seasons=()),
        priors_frame=priors,
    )
    assert engine._priors_for_season(2025, [10, 20]) == {}
    filled = engine._priors_for_season(2024, [10, 20])
    assert 10 in filled
    assert 20 in filled


def test_exclude_games_kicked_off_before_as_of() -> None:
    as_of = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    games = pd.DataFrame(
        {
            "game_id": [1, 2, 3],
            "start_date": [
                datetime(2026, 8, 29, 16, 0, tzinfo=UTC),
                datetime(2026, 9, 5, 16, 0, tzinfo=UTC),
                datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
            ],
            "week": [1, 1, 1],
        }
    )
    kept, n_excluded = exclude_games_kicked_off_before(games, as_of)
    assert n_excluded == 1
    assert set(kept["game_id"].astype(int)) == {2, 3}


def test_exclude_games_kicked_off_before_empty() -> None:
    kept, n_excluded = exclude_games_kicked_off_before(
        pd.DataFrame(), datetime(2026, 9, 1, tzinfo=UTC)
    )
    assert kept.empty
    assert n_excluded == 0


def test_exclude_kickoff_filter_requires_start_date() -> None:
    games = pd.DataFrame({"game_id": [1], "event_time": [datetime(2026, 8, 29, tzinfo=UTC)]})
    with pytest.raises(ValueError, match="start_date"):
        exclude_games_kicked_off_before(games, datetime(2026, 9, 1, tzinfo=UTC))


def test_rating_snapshot_digest_deterministic() -> None:
    state = {"10:off_epa": 0.1, "10:def_epa": -0.05}
    a = rating_snapshot_digest(state)
    b = rating_snapshot_digest(dict(state))
    assert a == b
    assert a != rating_snapshot_digest({"10:off_epa": 0.2, "10:def_epa": -0.05})


def test_publish_path_emits_no_bet_candidate(tmp_path: Path) -> None:
    cfg = AppConfig(
        webapp=WebappConfig(
            export_enabled=False,
            tier_state_path=str(tmp_path / "tier_state.json"),
            tier_changes_path=str(tmp_path / "tier_changes.jsonl"),
        )
    )
    notifier = RecordingNotifier()

    def _predict(_ctx: StaleContext) -> list[dict[str, object]]:
        return [{"game_id": "401000001", "mu_margin": 3.0, "sigma_margin": 14.0}]

    result = execute_predict_publish(
        season=2026,
        week=1,
        refresh_kind=RefreshKind.TUESDAY_PRIMARY,
        predict_fn=_predict,  # type: ignore[arg-type]
        config=cfg,
        notifier=notifier,
    )
    assert result["n_candidates"] == 0
    assert result["n_accepted"] == 0
    assert result["n_rejected"] == 0
    kinds = {a.kind for a in notifier.sent}
    assert AlertKind.NEW_BET_CANDIDATE not in kinds


def _week_games() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "game_id": [401856766, 401858424],
            "season": [2026, 2026],
            "week": [1, 1],
            "home_team_id": [10, 30],
            "away_team_id": [20, 40],
            "start_date": [
                datetime(2026, 8, 29, 16, 0, tzinfo=UTC),
                datetime(2026, 9, 5, 16, 0, tzinfo=UTC),
            ],
            "event_time": [
                datetime(2026, 8, 29, 21, 0, tzinfo=UTC),
                datetime(2026, 9, 5, 21, 0, tzinfo=UTC),
            ],
            "neutral_site": [False, False],
            "conference_game": [False, False],
        }
    )


class _FakeEngine:
    last: _FakeEngine | None = None

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        type(self).last = self

    def initialize_season(self, season: int, as_of: datetime) -> None:
        self.season = season
        self.as_of = as_of

    def state_snapshot(self) -> dict[str, float]:
        return {"10:off_epa": 0.1, "30:off_epa": 0.2, "10:sd_off_epa": 0.05, "30:sd_off_epa": 0.05}


class _FakePredictor:
    def predict(self, features: pd.DataFrame) -> pd.DataFrame:
        n = len(features)
        return pd.DataFrame(
            {
                "game_id": features["game_id"].to_numpy(),
                "pred_margin": [4.0] * n,
                "sigma_m": [14.0] * n,
                "p_ml_home": [0.62] * n,
            }
        )


class _FakeChampion:
    version = 2
    run_id = "task23_fundamental_reduced_v3"
    artifact_dir = "."


class _FakeRegistry:
    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def resolve_champion(self) -> _FakeChampion:
        return _FakeChampion()


def _obs_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "game_id": [1],
            "season": [2025],
            "week": [1],
            "event_time": [datetime(2025, 8, 30, tzinfo=UTC)],
            "home_team_id": [10],
            "away_team_id": [20],
            "home_epa": [0.1],
            "away_epa": [-0.1],
            "home_plays": [70.0],
            "away_plays": [65.0],
            "margin": [7.0],
            "neutral_site": [False],
        }
    )


def _priors_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "team_id": [10],
            "season": [2024],
            "dim": ["off_epa"],
            "prior_mean": [0.1],
            "prior_var": [0.04],
        }
    )


def _patch_live_seams(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    week_games: pd.DataFrame | None = None,
    obs: pd.DataFrame | None = None,
    priors: pd.DataFrame | None = None,
    plays_preferred: bool = True,
) -> list[list[int]]:
    games = week_games if week_games is not None else _week_games()
    loaded_seasons: list[list[int]] = []

    def _load_games(_staged: object, seasons: object) -> pd.DataFrame:
        vals = [int(s) for s in seasons]
        loaded_seasons.append(vals)
        return games.copy()

    def _concat(_store: object, table: str, _seasons: object) -> pd.DataFrame:
        if table == "teams":
            return pd.DataFrame({"team_id": [10], "classification": ["fbs"]})
        if table == "plays":
            return pd.DataFrame({"game_id": [1]}) if plays_preferred else pd.DataFrame()
        if table == "advanced_box":
            return pd.DataFrame() if plays_preferred else pd.DataFrame({"game_id": [1]})
        return pd.DataFrame()

    art = tmp_path / "champ"
    art.mkdir(parents=True, exist_ok=True)
    (art / "possessions_artifacts.pkl").write_bytes(pickle.dumps({(2024, 5): {"dummy": True}}))
    _FakeChampion.artifact_dir = str(art)

    monkeypatch.setattr("ncaa_quant.evaluation.backtest_runner.load_staged_games", _load_games)
    monkeypatch.setattr(
        "ncaa_quant.evaluation.walkforward.week_decision_as_of",
        lambda *_a, **_k: datetime(2026, 9, 1, 10, tzinfo=UTC),
    )
    monkeypatch.setattr("ncaa_quant.pipelines.predict._concat_hive_table", _concat)
    monkeypatch.setattr(
        "ncaa_quant.evaluation.production_stack.build_observations_from_staged",
        lambda **_k: (_obs_frame() if obs is None else obs, 10, 12),
    )
    monkeypatch.setattr(
        "ncaa_quant.cli.load_fitted_priors_frame_for_backtest",
        lambda *_a, **_k: _priors_frame() if priors is None else priors,
    )
    monkeypatch.setattr(
        "ncaa_quant.evaluation.production_stack.StateSpaceRatingEngine",
        _FakeEngine,
    )
    monkeypatch.setattr("ncaa_quant.registry.store.ModelRegistry", _FakeRegistry)
    monkeypatch.setattr(
        "ncaa_quant.registry.bundle.load_production_ensemble",
        lambda _path: _FakePredictor(),
    )
    monkeypatch.setattr("ncaa_quant.data.storage.ParquetStore", lambda _root: object())
    return loaded_seasons


def test_live_predict_rows_mocked_champion_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loaded = _patch_live_seams(monkeypatch, tmp_path)
    cfg = AppConfig(
        paths=PathsConfig(staged_dir=str(tmp_path / "staged"), data_dir=str(tmp_path)),
        webapp=WebappConfig(export_enabled=False),
    )
    rows = live_predict_rows(2026, 1, config=cfg)
    assert len(rows) == 1
    assert str(rows[0]["game_id"]) == "401858424"
    assert float(rows[0]["mu_margin"]) == 4.0
    assert rows[0]["rating_digest"]
    assert loaded[0] == [2026]
    assert 2025 in loaded[1]
    assert 2025 not in load_champion_walkforward_config().all_replay_seasons()
    assert _FakeEngine.last is not None
    assert _FakeEngine.last.season == 2026


def test_live_predict_rows_advanced_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_live_seams(monkeypatch, tmp_path, plays_preferred=False)
    cfg = AppConfig(
        paths=PathsConfig(staged_dir=str(tmp_path / "staged"), data_dir=str(tmp_path)),
        webapp=WebappConfig(export_enabled=False),
    )
    rows = live_predict_rows(2026, 1, config=cfg)
    assert len(rows) == 1


def test_live_predict_rows_missing_priors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_live_seams(monkeypatch, tmp_path, priors=pd.DataFrame())
    cfg = AppConfig(
        paths=PathsConfig(staged_dir=str(tmp_path / "staged"), data_dir=str(tmp_path)),
        webapp=WebappConfig(export_enabled=False),
    )
    with pytest.raises(FileNotFoundError, match="priors"):
        live_predict_rows(2026, 1, config=cfg)


def test_live_predict_rows_empty_observations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_live_seams(monkeypatch, tmp_path, obs=pd.DataFrame())
    cfg = AppConfig(
        paths=PathsConfig(staged_dir=str(tmp_path / "staged"), data_dir=str(tmp_path)),
        webapp=WebappConfig(export_enabled=False),
    )
    with pytest.raises(FileNotFoundError, match="no Kalman observations"):
        live_predict_rows(2026, 1, config=cfg)


def test_live_predict_rows_no_season_games(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ncaa_quant.evaluation.backtest_runner.load_staged_games",
        lambda *_a, **_k: pd.DataFrame(),
    )
    cfg = AppConfig(
        paths=PathsConfig(staged_dir=str(tmp_path / "staged"), data_dir=str(tmp_path)),
        webapp=WebappConfig(export_enabled=False),
    )
    with pytest.raises(FileNotFoundError, match="no staged games for season 2026"):
        live_predict_rows(2026, 1, config=cfg)


def test_live_predict_rows_all_kicked_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    early = _week_games()
    early["start_date"] = datetime(2026, 8, 29, 16, 0, tzinfo=UTC)
    _patch_live_seams(monkeypatch, tmp_path, week_games=early)
    cfg = AppConfig(
        paths=PathsConfig(staged_dir=str(tmp_path / "staged"), data_dir=str(tmp_path)),
        webapp=WebappConfig(export_enabled=False),
    )
    with pytest.raises(FileNotFoundError, match="after kickoff filter"):
        live_predict_rows(2026, 1, config=cfg)


def test_concat_hive_table_empty(tmp_path: Path) -> None:
    from ncaa_quant.pipelines.predict import _concat_hive_table

    class _Store:
        def _matching_paths(self, table: str, filters: dict[str, int]) -> list[Path]:
            del table, filters
            return []

    out = _concat_hive_table(_Store(), "plays", (2019,))
    assert out.empty


def test_fbs_team_ids_empty() -> None:
    from ncaa_quant.pipelines.predict import _fbs_team_ids

    assert _fbs_team_ids(pd.DataFrame()) == set()
    assert _fbs_team_ids(pd.DataFrame({"team_id": [1]})) == set()
