"""CLI wiring for odds archive backup (lands with cli.py)."""

from __future__ import annotations


def test_cli_odds_backup_requires_dest() -> None:
    """CLI must require --dest so historical cannot silently hit the live root."""
    from typer.testing import CliRunner

    from ncaa_quant.cli import app

    result = CliRunner().invoke(app, ["ingest", "odds-backup", "--source", "data/raw/odds_api"])
    assert result.exit_code != 0
    assert "--dest" in (result.output + result.stdout).lower() or "Missing option" in (
        result.output + str(result.exception)
    )
