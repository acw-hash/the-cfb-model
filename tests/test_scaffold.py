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
# Groups whose bare invoke still raises NotImplementedError (no real subcommands yet).
UNIMPLEMENTED_GROUPS = ["ratings", "train", "predict"]

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


@pytest.mark.parametrize("group", UNIMPLEMENTED_GROUPS)
def test_cli_group_not_implemented(group: str) -> None:
    result = runner.invoke(app, [group])
    assert result.exit_code != 0
    assert isinstance(result.exception, NotImplementedError)
    assert "not implemented" in str(result.exception).lower()


def test_cli_backtest_help() -> None:
    result = runner.invoke(app, ["backtest", "--help"])
    assert result.exit_code == 0
    assert "plan" in result.output
    assert "run" in result.output


def test_cli_app_is_typer() -> None:
    assert callable(app)
    # Touch a private attribute so mypy/coverage see the app object used.
    meta: Any = app
    assert meta.info.name == "ncaa-quant"
