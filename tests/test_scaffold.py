"""Scaffold smoke tests: package importability and CLI wiring."""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from typer.testing import CliRunner

from ncaa_quant.cli import app

PACKAGES = [
    "ncaa_quant",
    "ncaa_quant.ingestion",
    "ncaa_quant.quality",
    "ncaa_quant.quality.expectations",
    "ncaa_quant.data",
    "ncaa_quant.features",
    "ncaa_quant.features.builders",
    "ncaa_quant.ratings",
    "ncaa_quant.models",
    "ncaa_quant.models.heads",
    "ncaa_quant.distribution",
    "ncaa_quant.betting",
    "ncaa_quant.evaluation",
    "ncaa_quant.pipelines",
    "ncaa_quant.utils",
    "ncaa_quant.cli",
]

COMMAND_GROUPS = ["ingest", "features", "ratings", "train", "predict", "backtest"]
# Groups with no standalone implementation: the walk-forward path drives them.
UNWIRED_GROUPS = ["features", "ratings", "train", "predict"]

runner = CliRunner()


@pytest.mark.parametrize("package_name", PACKAGES)
def test_package_imports(package_name: str) -> None:
    module = importlib.import_module(package_name)
    assert module is not None


def test_cli_help_exits_zero() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for name in COMMAND_GROUPS:
        assert name in result.output


@pytest.mark.parametrize("group", UNWIRED_GROUPS)
def test_unwired_cli_group_points_at_the_walkforward_path(group: str) -> None:
    """An unwired verb must say so and name what does work, not raise."""
    result = runner.invoke(app, [group])
    assert result.exit_code == 2
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "not wired" in result.output.lower()
    assert "backtest run" in result.output


def test_cli_backtest_help() -> None:
    result = runner.invoke(app, ["backtest", "--help"])
    assert result.exit_code == 0
    assert "plan" in result.output
    assert "run" in result.output
    assert "verify" in result.output


def test_cli_app_is_typer() -> None:
    assert callable(app)
    # Touch a private attribute so mypy/coverage see the app object used.
    meta: Any = app
    assert meta.info.name == "ncaa-quant"
