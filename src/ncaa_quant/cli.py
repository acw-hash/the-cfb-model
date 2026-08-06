"""Typer CLI mirroring Makefile pipeline verbs."""

from __future__ import annotations

from pathlib import Path

import pandas as pd  # type: ignore[import-untyped]
import typer

from ncaa_quant.utils.logging import configure_logging, get_logger

app = typer.Typer(
    name="ncaa-quant",
    help="NCAA football prediction system CLI.",
    no_args_is_help=True,
)

ingest_app = typer.Typer(help="Ingest raw data from external sources.")
quality_app = typer.Typer(help="Data quality validation and quarantine.")
features_app = typer.Typer(help="Build and materialize features.")
roster_app = typer.Typer(help="Roster / QB-status manual entry.")
ratings_app = typer.Typer(help="Update team ratings.")
train_app = typer.Typer(help="Train prediction models.")
predict_app = typer.Typer(help="Generate predictions.")
backtest_app = typer.Typer(help="Run walk-forward backtests.")
diag_app = typer.Typer(help="Read-only diagnostics (no model/config changes).")

app.add_typer(ingest_app, name="ingest")
app.add_typer(quality_app, name="quality")
app.add_typer(features_app, name="features")
app.add_typer(roster_app, name="roster")
app.add_typer(ratings_app, name="ratings")
app.add_typer(train_app, name="train")
app.add_typer(predict_app, name="predict")
app.add_typer(backtest_app, name="backtest")
app.add_typer(diag_app, name="diag")


@ingest_app.callback(invoke_without_command=True)
def ingest(ctx: typer.Context) -> None:
    """Ingest raw data from external sources."""
    if ctx.invoked_subcommand is None:
        raise NotImplementedError("ingest is not implemented")


@ingest_app.command("odds")
def ingest_odds(
    once: bool = typer.Option(
        False,
        "--once",
        help="Run a single snapshot pull (required for manual/smoke runs).",
    ),
) -> None:
    """Capture The Odds API NCAAF snapshot (raw archive + staged Parquet)."""
    if not once:
        typer.echo("Pass --once for a manual run, or serve the Prefect ingest_odds deployment.")
        raise typer.Exit(code=2)
    configure_logging()
    log = get_logger("ncaa_quant.cli")
    from ncaa_quant.ingestion.odds_api import run_odds_ingest

    result = run_odds_ingest()
    log.info(
        "cli_ingest_odds_complete",
        rows_written=result.rows_written,
        rows_fetched=result.rows_fetched,
        raw_path=str(result.raw_path),
    )
    typer.echo(
        f"wrote {result.rows_written} new rows "
        f"(fetched {result.rows_fetched}) raw={result.raw_path}"
    )


@ingest_app.command("odds-historical")
def ingest_odds_historical(
    seasons: str = typer.Option(
        ...,
        "--seasons",
        help="Season or inclusive range, e.g. 2023 or 2021-2025.",
    ),
    estimate: bool = typer.Option(
        False,
        "--estimate",
        help="Print request/credit estimate only; no API spend.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Re-fetch completed units and/or exceed credit ceiling.",
    ),
    reconcile: bool = typer.Option(
        False,
        "--reconcile",
        help="Print CFBD-close vs slot_close reconciliation after backfill.",
    ),
) -> None:
    """Estimate or run The Odds API historical snapshot backfill."""
    configure_logging()
    log = get_logger("ncaa_quant.cli")
    from ncaa_quant.config import load_config
    from ncaa_quant.data.storage import ParquetStore
    from ncaa_quant.ingestion.cfbd import parse_seasons_arg
    from ncaa_quant.ingestion.odds_api import (
        HistoricalBudgetCeilingError,
        coverage_report,
        estimate_historical_credits,
        reconcile_cfbd_close_vs_slot_close,
        run_historical_backfill,
    )

    season_tuple = parse_seasons_arg(seasons)
    cfg = load_config()
    staged = Path(cfg.paths.staged_dir)

    with ParquetStore(staged) as store:
        plan, lines = estimate_historical_credits(store, season_tuple, config=cfg)
        for line in lines:
            typer.echo(line)
        if estimate:
            if plan.over_ceiling and not force:
                raise typer.Exit(code=2)
            return

        if plan.over_ceiling and not force:
            typer.echo("Refusing spend over ceiling without --force.")
            raise typer.Exit(code=2)

        try:
            result = run_historical_backfill(
                seasons=season_tuple,
                config=cfg,
                force=force,
            )
        except HistoricalBudgetCeilingError as exc:
            typer.echo(str(exc))
            raise typer.Exit(code=2) from exc

        log.info(
            "cli_ingest_odds_historical_complete",
            units_written=result.units_written,
            units_skipped=result.units_skipped,
            requests_made=result.requests_made,
            credits_spent=result.credits_spent,
            rows_written=result.rows_written,
            calibration_last=result.calibration_last,
        )
        typer.echo(
            f"units_written={result.units_written} "
            f"units_skipped={result.units_skipped} "
            f"requests_made={result.requests_made} "
            f"credits_spent={result.credits_spent} "
            f"rows_written={result.rows_written} "
            f"calibration_last={result.calibration_last}"
        )
        for line in coverage_report(store, season_tuple, config=cfg):
            typer.echo(line)
        if reconcile:
            report = reconcile_cfbd_close_vs_slot_close(store, season_tuple)
            for line in report.summary_lines():
                typer.echo(line)


@ingest_app.command("weather")
def ingest_weather(
    seasons: str | None = typer.Option(
        None,
        "--seasons",
        help="Season or inclusive range for historical weather, e.g. 2023 or 2021-2025.",
    ),
    forecast_upcoming: bool = typer.Option(
        False,
        "--forecast-upcoming",
        help="Pull Open-Meteo forecasts for upcoming staged games.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Re-fetch historical actuals even when already stored.",
    ),
) -> None:
    """Enrich venues and attach Open-Meteo weather (historical and/or forecast)."""
    configure_logging()
    log = get_logger("ncaa_quant.cli")
    from ncaa_quant.config import load_config
    from ncaa_quant.data.storage import ParquetStore
    from ncaa_quant.ingestion.weather import (
        MissingVenueCoordsError,
        coverage_report,
        parse_seasons_arg,
        run_weather_forecast_upcoming,
        run_weather_historical,
    )

    if not seasons and not forecast_upcoming:
        typer.echo("Pass --seasons YYYY[-YYYY] and/or --forecast-upcoming.")
        raise typer.Exit(code=2)

    try:
        if seasons:
            season_tuple = parse_seasons_arg(seasons)
            result = run_weather_historical(seasons=season_tuple, force=force)
            log.info(
                "cli_ingest_weather_historical_complete",
                seasons=list(result.seasons),
                venues_written=result.venues_written,
                rows_written=result.rows_written,
                rows_skipped=result.rows_skipped,
                gaps=len(result.gaps),
            )
            typer.echo(
                f"historical seasons={list(result.seasons)} "
                f"venues_written={result.venues_written} "
                f"rows_written={result.rows_written} "
                f"rows_skipped={result.rows_skipped} "
                f"raw_files={len(result.raw_paths)} "
                f"gaps={len(result.gaps)}"
            )
            for gap in result.gaps[:50]:
                typer.echo(f"  gap: {gap}")
            cfg = load_config()
            with ParquetStore(cfg.paths.staged_dir) as store:
                for season in season_tuple:
                    for line in coverage_report(store, season):
                        typer.echo(line)

        if forecast_upcoming:
            result = run_weather_forecast_upcoming()
            log.info(
                "cli_ingest_weather_forecast_complete",
                seasons=list(result.seasons),
                rows_written=result.rows_written,
                gaps=len(result.gaps),
            )
            typer.echo(
                f"forecast seasons={list(result.seasons)} "
                f"venues_written={result.venues_written} "
                f"rows_written={result.rows_written} "
                f"raw_files={len(result.raw_paths)} "
                f"gaps={len(result.gaps)}"
            )
            for gap in result.gaps[:50]:
                typer.echo(f"  gap: {gap}")
    except MissingVenueCoordsError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=2) from exc


@ingest_app.command("cfbd")
def ingest_cfbd(
    seasons: str | None = typer.Option(
        None,
        "--seasons",
        help="Season or inclusive range, e.g. 2023 or 2014-2025.",
    ),
    endpoints: str | None = typer.Option(
        None,
        "--endpoints",
        help="Comma-separated endpoint short names (default: all).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Re-fetch and overwrite completed partitions.",
    ),
    incremental: bool = typer.Option(
        False,
        "--incremental",
        help="Pull current season missing + recently-changed weeks only.",
    ),
) -> None:
    """Backfill or incrementally ingest CollegeFootballData.com endpoints."""
    configure_logging()
    log = get_logger("ncaa_quant.cli")
    from ncaa_quant.ingestion.cfbd import (
        parse_seasons_arg,
        resolve_endpoints,
        run_cfbd_backfill,
        run_cfbd_incremental,
    )

    endpoint_list = None
    if endpoints:
        endpoint_list = [part.strip() for part in endpoints.split(",") if part.strip()]

    if incremental:
        if seasons:
            typer.echo("--incremental ignores --seasons (uses current season).")
        result = run_cfbd_incremental(
            endpoints=resolve_endpoints(endpoint_list) if endpoint_list else None,
        )
    else:
        if not seasons:
            typer.echo("Pass --seasons YYYY or YYYY-YYYY, or use --incremental.")
            raise typer.Exit(code=2)
        season_tuple = parse_seasons_arg(seasons)
        result = run_cfbd_backfill(
            seasons=season_tuple,
            endpoints=resolve_endpoints(endpoint_list) if endpoint_list else None,
            force=force,
        )

    log.info(
        "cli_ingest_cfbd_complete",
        seasons=list(result.seasons),
        partitions_written=result.partitions_written,
        partitions_skipped=result.partitions_skipped,
        rows_written=result.rows_written,
    )
    typer.echo(
        f"seasons={list(result.seasons)} "
        f"partitions_written={result.partitions_written} "
        f"partitions_skipped={result.partitions_skipped} "
        f"rows_written={result.rows_written} "
        f"raw_files={len(result.raw_paths)}"
    )


@quality_app.command("run")
def quality_run(
    seasons: str = typer.Option(
        ...,
        "--seasons",
        help="Season or inclusive range, e.g. 2023 or 2014-2025.",
    ),
    report_dir: str | None = typer.Option(
        None,
        "--report-dir",
        help="Directory for markdown/HTML summary (default: docs/quality/reports).",
    ),
) -> None:
    """Run Great Expectations suites + custom validators; quarantine failures."""
    configure_logging()
    log = get_logger("ncaa_quant.cli")
    from ncaa_quant.ingestion.cfbd import parse_seasons_arg
    from ncaa_quant.quality.runner import run_quality

    season_tuple = parse_seasons_arg(seasons)
    result = run_quality(
        season_tuple,
        report_dir=Path(report_dir) if report_dir else None,
    )
    log.info(
        "cli_quality_run_complete",
        run_id=result.run_id,
        quarantined=result.partitions_quarantined,
        hard_failures=result.hard_failure_count,
    )
    for line in result.summary_lines():
        typer.echo(line)
    if result.partitions_quarantined > 0:
        raise typer.Exit(code=1)


@features_app.callback(invoke_without_command=True)
def features() -> None:
    """Build and materialize features."""
    raise NotImplementedError("features is not implemented")


@roster_app.command("set-qb")
def roster_set_qb(
    game: int = typer.Option(..., "--game", help="Staged game_id."),
    team: str = typer.Option(
        ...,
        "--team",
        help="Team school name or numeric team_id participating in the game.",
    ),
    status: str = typer.Option(
        ...,
        "--status",
        help="QB availability: starter | backup | unknown.",
    ),
) -> None:
    """Record prospective QB status for a game-team (versioned ``qb_status`` table)."""
    configure_logging()
    log = get_logger("ncaa_quant.cli")
    from ncaa_quant.config import load_config
    from ncaa_quant.data.storage import ParquetStore
    from ncaa_quant.features.builders.roster import resolve_team_id, set_qb_status

    cfg = load_config()
    with ParquetStore(cfg.paths.staged_dir) as store:
        games = store.read("games", filters={"game_id": game})
        if games.empty:
            typer.echo(f"game_id {game} not found in staged games")
            raise typer.Exit(code=2)
        season = int(games.iloc[0]["season"])
        teams = store.read("teams", filters={"season": season})
        try:
            team_id = resolve_team_id(team, teams, season=season)
            row = set_qb_status(store, game_id=game, team_id=team_id, status=status)
        except ValueError as exc:
            typer.echo(str(exc))
            raise typer.Exit(code=2) from exc

    log.info(
        "cli_roster_set_qb",
        game_id=game,
        team_id=int(row.iloc[0]["team_id"]),
        status=str(row.iloc[0]["status"]),
        event_time=str(row.iloc[0]["event_time"]),
    )
    typer.echo(
        f"qb_status game={game} team_id={int(row.iloc[0]['team_id'])} "
        f"status={row.iloc[0]['status']} event_time={row.iloc[0]['event_time']}"
    )


@ratings_app.callback(invoke_without_command=True)
def ratings() -> None:
    """Update team ratings."""
    raise NotImplementedError("ratings is not implemented")


@train_app.callback(invoke_without_command=True)
def train() -> None:
    """Train prediction models."""
    raise NotImplementedError("train is not implemented")


@predict_app.callback(invoke_without_command=True)
def predict() -> None:
    """Generate predictions."""
    raise NotImplementedError("predict is not implemented")


@backtest_app.command("plan")
def backtest_plan(
    config: str = typer.Option(..., "--config", help="Ablation/run config name or path."),
    staged_dir: str = typer.Option(
        "data/staged",
        "--staged-dir",
        help="Staged Parquet root for season/week discovery.",
    ),
) -> None:
    """Print the run plan (seasons, weeks, retrains, estimated wall clock). Spends nothing."""
    configure_logging()
    from ncaa_quant.evaluation.backtest_runner import (
        load_backtest_config,
        load_staged_games,
        plan_backtest,
        walkforward_config_from_mapping,
    )

    staged_path = Path(staged_dir)
    payload = load_backtest_config(config)
    cfg = walkforward_config_from_mapping(payload)
    games = load_staged_games(staged_path, cfg.all_replay_seasons())
    plan = plan_backtest(config, games=games if not games.empty else None, config_payload=payload)
    typer.echo(plan.format_text())


@backtest_app.command("run")
def backtest_run(
    config: str = typer.Option(..., "--config", help="Ablation/run config name or path."),
    force: bool = typer.Option(
        False, "--force", help="Re-run completed (run_id, season, week) units."
    ),
    staged_dir: str = typer.Option("data/staged", "--staged-dir"),
    output_root: str = typer.Option("data/backtests", "--output-root"),
    tracking_uri: str = typer.Option("file:./mlruns", "--tracking-uri"),
    label: str = typer.Option("", "--label", help="Optional run label (e.g. WIRING PROOF)."),
    stack: str = typer.Option("fundamental", "--stack", help="fundamental | market_aware"),
) -> None:
    """Execute one named walk-forward backtest end to end (resumable)."""
    configure_logging()
    from ncaa_quant.data.storage import ParquetStore
    from ncaa_quant.evaluation.backtest_runner import (
        load_backtest_config,
        load_staged_games,
        run_backtest,
        walkforward_config_from_mapping,
    )
    from ncaa_quant.evaluation.production_stack import build_observations_from_staged

    staged_path = Path(staged_dir)
    output_path = Path(output_root)
    payload = load_backtest_config(config)
    cfg = walkforward_config_from_mapping(payload)
    games = load_staged_games(staged_path, cfg.all_replay_seasons())
    if games.empty:
        typer.echo(f"No staged games for seasons {cfg.all_replay_seasons()} under {staged_path}")
        raise typer.Exit(code=1)

    store = ParquetStore(staged_path)
    advanced_frames: list[pd.DataFrame] = []
    plays_frames: list[pd.DataFrame] = []
    lines_frames: list[pd.DataFrame] = []
    for season in cfg.all_replay_seasons():
        for path in store._matching_paths("advanced_box", {"season": int(season)}):  # noqa: SLF001
            advanced_frames.append(pd.read_parquet(path))
        for path in store._matching_paths("plays", {"season": int(season)}):  # noqa: SLF001
            plays_frames.append(pd.read_parquet(path))
        for path in store._matching_paths("lines_historical", {"season": int(season)}):  # noqa: SLF001
            lines_frames.append(pd.read_parquet(path))

    advanced = pd.concat(advanced_frames, ignore_index=True) if advanced_frames else None
    plays = pd.concat(plays_frames, ignore_index=True) if plays_frames else None
    cfbd_lines = pd.concat(lines_frames, ignore_index=True) if lines_frames else None
    obs, _n_on, _n_off = build_observations_from_staged(
        plays=plays,
        games=games,
        advanced=advanced,
        garbage_time_filter=cfg.garbage_time_filter,
    )

    result = run_backtest(
        config,
        games=games,
        snapshots=None,
        cfbd_lines=cfbd_lines,
        observations=obs,
        output_root=output_path,
        force=force,
        tracking_uri=tracking_uri,
        label=label,
        config_payload=payload,
        stack_kind=stack,  # type: ignore[arg-type]
    )
    typer.echo(
        f"backtest complete run_id={result.run_id} ablation_id={result.ablation_id} "
        f"n_predictions={len(result.predictions)} out={result.output_dir} "
        f"mlflow={result.mlflow_run_id} label={result.label or '-'}"
    )
    if label:
        typer.echo(f"LABEL={label}")


@backtest_app.callback(invoke_without_command=True)
def backtest(ctx: typer.Context) -> None:
    """Run walk-forward backtests."""
    if ctx.invoked_subcommand is None:
        typer.echo("Usage: ncaa-quant backtest [plan|run] --config <name>")
        raise typer.Exit(code=2)


@diag_app.command("mu")
def diag_mu(
    predictions: str = typer.Option(
        "data/backtests/task23_fix_smoke/wiring_proof_2023/full/predictions.parquet",
        "--predictions",
        help="Walk-forward predictions parquet (default: FIX-DIAG smoke that produced MAE 16.60).",
    ),
    staged_dir: str = typer.Option("data/staged", "--staged-dir"),
    notes: str = typer.Option("docs/notes/D1.md", "--notes"),
    artifact_dir: str = typer.Option("docs/notes/_artifacts/D1", "--artifact-dir"),
    skip_heavy: bool = typer.Option(
        False,
        "--skip-heavy",
        help="Skip feature-bank / Stage-1 / shifted-label rebuilds (Block A + artifact-only).",
    ),
) -> None:
    """TASK D1: locate where the margin signal dies (read-only)."""
    configure_logging()
    from ncaa_quant.evaluation.diagnostics_mu import run_mu_diagnostics

    result = run_mu_diagnostics(
        predictions_path=predictions,
        staged_dir=staged_dir,
        artifact_dir=artifact_dir,
        notes_path=notes,
        skip_heavy=skip_heavy,
    )
    a = result.get("block_a") or {}
    stop = result.get("structural_stop") or {}
    typer.echo(
        f"D1 complete stack_r2={a.get('stack_r2')} r2_le_zero={a.get('stack_r2_le_zero')} "
        f"stop={stop.get('kind')} notes={notes}"
    )
    if result.get("stopped_early"):
        typer.echo(f"STOPPED EARLY: {stop.get('message')}")
        raise typer.Exit(code=0)
    c = result.get("block_c") or {}
    if c.get("failing_step"):
        typer.echo(f"failing_step={c.get('failing_step')}")


@diag_app.callback(invoke_without_command=True)
def diag(ctx: typer.Context) -> None:
    """Read-only diagnostics."""
    if ctx.invoked_subcommand is None:
        typer.echo("Usage: ncaa-quant diag mu [--predictions PATH]")
        raise typer.Exit(code=2)


if __name__ == "__main__":
    app()
