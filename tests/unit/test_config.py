"""Tests for layered config loading, secrets isolation, and dumps."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ncaa_quant.config import dump_config, load_config, load_secrets


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """Minimal layered YAML tree for precedence tests."""
    (tmp_path / "base.yaml").write_text(
        "seed: 1\nlog_level: INFO\npaths:\n  data_dir: data\n",
        encoding="utf-8",
    )
    (tmp_path / "data.yaml").write_text(
        "data:\n  start_season: 2014\n  end_season: 2020\n",
        encoding="utf-8",
    )
    (tmp_path / "ratings.yaml").write_text(
        "ratings:\n  process_noise_scale: 0.01\n",
        encoding="utf-8",
    )
    (tmp_path / "betting.yaml").write_text(
        "betting:\n"
        "  min_edge_sides: 0.025\n"
        "  min_edge_totals: 0.03\n"
        "  kelly_fraction: 0.25\n"
        "  max_stake_pct: 0.015\n",
        encoding="utf-8",
    )
    (tmp_path / "pipeline.yaml").write_text(
        "pipeline:\n  odds_snapshots_per_day: 6\n",
        encoding="utf-8",
    )
    return tmp_path


def test_domain_yaml_overrides_base(config_dir: Path) -> None:
    cfg = load_config(config_dir)
    assert cfg.seed == 1
    assert cfg.data.end_season == 2020
    assert cfg.betting.min_edge_sides == 0.025
    assert cfg.ratings.process_noise_scale == 0.01


def test_env_overrides_domain_yaml(config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NCAA_QUANT_SEED", "99")
    monkeypatch.setenv("NCAA_QUANT_BETTING__MIN_EDGE_SIDES", "0.04")
    cfg = load_config(config_dir)
    assert cfg.seed == 99
    assert cfg.betting.min_edge_sides == 0.04
    # Untouched domain values remain.
    assert cfg.betting.kelly_fraction == 0.25


def test_cli_overrides_beat_env(config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NCAA_QUANT_SEED", "99")
    cfg = load_config(config_dir, overrides={"seed": 7, "betting": {"min_edge_sides": 0.05}})
    assert cfg.seed == 7
    assert cfg.betting.min_edge_sides == 0.05


def test_full_precedence_chain(config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """base < domain < env < CLI — exercise all four levels on one field."""
    # base seed=1; domain does not touch seed; env → 50; CLI → 123
    monkeypatch.setenv("NCAA_QUANT_SEED", "50")
    cfg = load_config(config_dir, overrides={"seed": 123})
    assert cfg.seed == 123


def test_repo_configs_load() -> None:
    cfg = load_config()
    assert cfg.betting.min_edge_sides == 0.025
    assert cfg.betting.min_edge_totals == 0.03
    assert cfg.betting.kelly_fraction == 0.25
    assert cfg.betting.max_stake_pct == 0.015
    assert cfg.data.garbage_wp_low == 0.02
    assert cfg.data.garbage_wp_high == 0.98


def test_secret_never_in_config_dump(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "super-secret-cfbd-key-xyz"
    monkeypatch.setenv("CFBD_API_KEY", secret)
    monkeypatch.setenv("ODDS_API_KEY", "odds-secret-abc")
    secrets = load_secrets()
    assert secrets.cfbd_api_key.get_secret_value() == secret

    cfg = load_config()
    dumped = dump_config(cfg)
    serialized = json.dumps(dumped)
    assert secret not in serialized
    assert "odds-secret-abc" not in serialized
    assert "CFBD_API_KEY" not in serialized
    assert "cfbd_api_key" not in serialized
    assert "odds_api_key" not in serialized
