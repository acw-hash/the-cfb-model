"""Registry CLI: promote / rollback / resolve (DESIGN §8.7 / §10).

Invoked as ``python -m ncaa_quant.registry`` (kept inside the registry package
so Task 22 does not modify ``cli.py``).
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from ncaa_quant.registry.store import ModelRegistry
from ncaa_quant.utils.logging import configure_logging, get_logger

app = typer.Typer(
    name="ncaa-quant-registry",
    help="Model registry: promote, rollback, resolve champion.",
    no_args_is_help=True,
)


def _registry(root: str, tracking_uri: str | None) -> ModelRegistry:
    return ModelRegistry(Path(root), tracking_uri=tracking_uri)


@app.command("promote")
def promote_cmd(
    version: int = typer.Option(..., "--version", help="Candidate version to promote."),
    root: str = typer.Option(..., "--root", help="Registry root directory."),
    metrics_json: str = typer.Option(
        ...,
        "--metrics-json",
        help="JSON file with crps/log_loss/clv paired series + blocks + seasons.",
    ),
    tracking_uri: str | None = typer.Option(None, "--tracking-uri"),
    force: bool = typer.Option(
        False,
        "--force",
        help="Human override of a failing gate (writes an override record).",
    ),
    force_reason: str = typer.Option("", "--force-reason", help="Required with --force."),
    force_actor: str = typer.Option("", "--force-actor", help="Required with --force."),
    n_boot: int = typer.Option(1999, "--n-boot"),
    seed: int = typer.Option(0, "--seed"),
) -> None:
    """Run the promotion gate; archive on failure unless --force."""
    configure_logging()
    log = get_logger("ncaa_quant.registry.cli")
    from ncaa_quant.registry.promote import (
        MetricComparisonInput,
        PromotionError,
        promote,
    )

    metrics_path = Path(metrics_json)
    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    try:
        metrics = [
            MetricComparisonInput(
                name="crps",
                champion=payload["crps"]["champion"],
                candidate=payload["crps"]["candidate"],
                blocks=payload["crps"]["blocks"],
                direction="lower",
            ),
            MetricComparisonInput(
                name="log_loss",
                champion=payload["log_loss"]["champion"],
                candidate=payload["log_loss"]["candidate"],
                blocks=payload["log_loss"]["blocks"],
                direction="lower",
            ),
            MetricComparisonInput(
                name="clv",
                champion=payload["clv"]["champion"],
                candidate=payload["clv"]["candidate"],
                blocks=payload["clv"]["blocks"],
                direction="higher",
            ),
        ]
    except KeyError as exc:
        typer.echo(f"metrics JSON missing key: {exc}")
        raise typer.Exit(code=2) from exc

    registry = _registry(root, tracking_uri)
    try:
        result = promote(
            registry,
            version,
            metrics,
            seasons=list(payload.get("seasons") or []),
            calibration_slope=float(payload["calibration_slope"]),
            leakage_gate_passed=bool(payload["leakage_gate_passed"]),
            force=force,
            force_reason=force_reason,
            force_actor=force_actor,
            calibration_slope_low=float(payload.get("calibration_slope_low", 0.85)),
            calibration_slope_high=float(payload.get("calibration_slope_high", 1.15)),
            n_boot=n_boot,
            seed=seed,
        )
    except PromotionError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=2) from exc

    log.info(
        "cli_promote",
        promoted=result.promoted,
        version=version,
        force=force,
    )
    typer.echo(f"promoted={result.promoted} force={result.report.force_override}")
    typer.echo(f"reason={result.report.reason}")
    typer.echo(f"champion_version={result.champion_version}")
    if result.archived_version is not None:
        artifact = registry.get_version(result.archived_version).artifact_dir
        typer.echo(f"archived_version={result.archived_version}")
        typer.echo(f"comparison_report={Path(artifact) / 'comparison_report.html'}")
    if not result.promoted:
        raise typer.Exit(code=1)


@app.command("rollback")
def rollback_cmd(
    root: str = typer.Option(..., "--root", help="Registry root directory."),
    to_version: int | None = typer.Option(
        None,
        "--to",
        help="Prior champion version to re-pin (default: previous in history).",
    ),
    tracking_uri: str | None = typer.Option(None, "--tracking-uri"),
) -> None:
    """One-command rollback: re-pin any prior champion."""
    configure_logging()
    log = get_logger("ncaa_quant.registry.cli")
    from ncaa_quant.registry.promote import PromotionError, rollback

    registry = _registry(root, tracking_uri)
    try:
        record = rollback(registry, target_version=to_version)
    except PromotionError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=2) from exc

    log.info("cli_rollback", to_version=record.version)
    typer.echo(f"champion=v{record.version} stage={record.stage}")


@app.command("resolve-champion")
def resolve_champion_cmd(
    root: str = typer.Option(..., "--root", help="Registry root directory."),
    tracking_uri: str | None = typer.Option(None, "--tracking-uri"),
) -> None:
    """Print the current champion version (fails loudly if none)."""
    from ncaa_quant.registry.store import NoChampionError

    registry = _registry(root, tracking_uri)
    try:
        champ = registry.resolve_champion()
    except NoChampionError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=2) from exc
    typer.echo(f"champion=v{champ.version} run_id={champ.run_id} stage={champ.stage}")


if __name__ == "__main__":
    app()
