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
features_app = typer.Typer(help="Build and materialize features. NOT WIRED — see `backtest run`.")
roster_app = typer.Typer(help="Roster / QB-status manual entry.")
ratings_app = typer.Typer(help="Update team ratings. NOT WIRED — see `backtest run`.")
train_app = typer.Typer(help="Train prediction models. NOT WIRED — see `backtest run`.")
predict_app = typer.Typer(help="Generate predictions. NOT WIRED — see `backtest run`.")
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


def _not_wired(verb: str) -> None:
    """Exit with a pointer instead of a traceback.

    `ratings`, `train`, `predict` and `features` are declared in DESIGN §10 as
    production verbs but have no standalone implementation. The walk-forward
    path (`backtest run`) drives ratings, training and prediction internally, and
    is the only entry point whose output carries a verifiable manifest. These
    verbs get wired when the weekly production loop is built; until then they say
    so rather than raising.
    """
    typer.echo(
        f"`{verb}` is not wired as a standalone verb. The walk-forward path drives "
        f"ratings, training and prediction together:\n"
        f"  ncaa-quant backtest plan --config <config>   # cost, spends nothing\n"
        f"  ncaa-quant backtest run  --config <config>   # writes a verifiable manifest"
    )
    raise typer.Exit(code=2)


@ingest_app.callback(invoke_without_command=True)
def ingest(ctx: typer.Context) -> None:
    """Ingest raw data from external sources."""
    if ctx.invoked_subcommand is None:
        typer.echo("Usage: ncaa-quant ingest [cfbd|odds|weather|...] — see --help")
        raise typer.Exit(code=2)


@ingest_app.command("odds")
def ingest_odds(
    once: bool = typer.Option(
        False,
        "--once",
        help="Run a single snapshot pull (required for manual/smoke runs).",
    ),
) -> None:
    """Capture The Odds API NCAAF snapshot: raw archive → normalize → stage."""
    if not once:
        typer.echo("Pass --once for a manual run, or serve the Prefect ingest_odds deployment.")
        raise typer.Exit(code=2)
    configure_logging()
    log = get_logger("ncaa_quant.cli")
    from ncaa_quant.ingestion.odds_api import run_odds_ingest

    result = run_odds_ingest()
    log.info(
        "cli_ingest_odds_complete",
        raw_path=str(result.raw_path),
        rows_written=result.rows_written,
        rows_fetched=result.rows_fetched,
        captured_at=result.captured_at.isoformat(),
    )
    typer.echo(
        f"wrote {result.rows_written} new rows (fetched {result.rows_fetched}) "
        f"raw={result.raw_path}"
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


@quality_app.command("pit-audit")
def quality_pit_audit(
    seasons: str = typer.Option(
        ...,
        "--seasons",
        help="Season or inclusive range, e.g. 2023 or 2014-2025.",
    ),
    staged_dir: str = typer.Option("data/staged", "--staged-dir"),
) -> None:
    """Full staged-set temporal PIT audit (event_time ≤ ingested_at)."""
    configure_logging()
    from ncaa_quant.ingestion.cfbd import parse_seasons_arg
    from ncaa_quant.quality.pit_audit import run_staged_pit_audit

    season_tuple = parse_seasons_arg(seasons)
    result = run_staged_pit_audit(season_tuple, staged_dir=Path(staged_dir))
    for line in result.summary_lines():
        typer.echo(line)
    if not result.passed:
        raise typer.Exit(code=1)


@ingest_app.command("odds-backup")
def ingest_odds_backup(
    dest: str = typer.Option(
        ...,
        "--dest",
        help=(
            "Off-machine backup root for this source only (required). "
            "Live and historical archives must use separate dest roots — "
            "see docs/runbooks/odds_archive_backup.md."
        ),
    ),
    source: str = typer.Option("data/raw/odds_api", "--source"),
    restore_drill_flag: bool = typer.Option(
        False,
        "--restore-drill",
        help="After backup, copy current/ to a restore dir and verify digests.",
    ),
) -> None:
    """Replicate a raw Odds API archive off-machine (DESIGN §10 / E-1)."""
    configure_logging()
    log = get_logger("ncaa_quant.cli")
    from ncaa_quant.ops.odds_backup import (
        OddsBackupError,
        assert_backup_fresh,
        replicate_odds_archive,
        restore_drill,
    )

    try:
        manifest = replicate_odds_archive(source, dest)
        log.info(
            "odds_backup_complete",
            n_files=manifest.n_files,
            total_bytes=manifest.total_bytes,
            dest=manifest.dest_root,
        )
        typer.echo(
            f"odds backup n_files={manifest.n_files} bytes={manifest.total_bytes} "
            f"dest={manifest.dest_root} created_at={manifest.created_at}"
        )
        assert_backup_fresh(manifest.dest_root)
        if restore_drill_flag:
            drill = restore_drill(manifest.dest_root, source_root=source)
            typer.echo(f"restore drill ok n_files={drill.n_files} out={drill.dest_root}")
    except OddsBackupError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc


@features_app.callback(invoke_without_command=True)
def features() -> None:
    """Build and materialize features — not wired as a standalone verb."""
    _not_wired("features")


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
    """Update team ratings — not wired as a standalone verb."""
    _not_wired("ratings")


@train_app.callback(invoke_without_command=True)
def train() -> None:
    """Train prediction models — not wired as a standalone verb."""
    _not_wired("train")


@predict_app.callback(invoke_without_command=True)
def predict() -> None:
    """Generate predictions — not wired as a standalone verb."""
    _not_wired("predict")


# Task 15 fitted-prior artifacts (same seam the acceptance harness writes).
_DEFAULT_PRIORS_CACHE = Path("data/tmp/priors_acceptance_15/week1_priors.parquet")
_DEFAULT_PRIORS_WEIGHTS = Path("data/tmp/priors_acceptance_15/summary.json")
_ARTIFACTS_CONFIG = Path("configs/artifacts.yaml")
_SUPERSEDED_FILTER_HISTORY_MARKERS: tuple[str, ...] = (
    "state_space_acceptance_14",
    "data/tmp/state_space_acceptance_14",
)


def load_artifact_paths(config_path: Path | None = None) -> dict[str, Path]:
    """Load non-tmp artifact paths from ``configs/artifacts.yaml``."""
    from omegaconf import OmegaConf

    path = config_path if config_path is not None else _ARTIFACTS_CONFIG
    if not path.is_file():
        return {
            "expected_possessions_live": Path("data/artifacts/expected_possessions/live.json"),
            "filter_history": Path("data/artifacts/state_space/filter_history.parquet"),
        }
    raw = OmegaConf.to_container(OmegaConf.load(path), resolve=True)
    block = raw.get("artifacts", raw) if isinstance(raw, dict) else {}
    if not isinstance(block, dict):
        msg = f"artifacts config must be a mapping: {path}"
        raise TypeError(msg)
    return {
        "expected_possessions_live": Path(
            str(
                block.get(
                    "expected_possessions_live", "data/artifacts/expected_possessions/live.json"
                )
            )
        ),
        "filter_history": Path(
            str(block.get("filter_history", "data/artifacts/state_space/filter_history.parquet"))
        ),
    }


def resolve_filter_history_path(path: Path | str | None = None) -> Path:
    """Resolve filter-history path; refuse SUPERSEDED Task 14 cache locations."""
    resolved = load_artifact_paths()["filter_history"] if path is None else Path(path)
    normalized = str(resolved).replace("\\", "/")
    for marker in _SUPERSEDED_FILTER_HISTORY_MARKERS:
        if marker in normalized:
            msg = (
                f"SUPERSEDED filter-history path is not reachable from production config: "
                f"{resolved}. Use configs/artifacts.yaml → artifacts.filter_history "
                f"(GT-active promoted copy under data/artifacts/)."
            )
            raise ValueError(msg)
    if "data/tmp/" in normalized or normalized.startswith("data/tmp"):
        msg = f"production filter_history must not live under data/tmp/: {resolved}"
        raise ValueError(msg)
    return resolved


def load_staged_odds_snapshots(
    staged_root: Path | str,
    seasons: tuple[int, ...] | list[int],
) -> pd.DataFrame | None:
    """Load staged ``odds_snapshots`` for ``seasons``, excluding lockbox 2025.

    Raises when any requested season is the lockbox, or when loaded rows include
    lockbox season (assert, not assume).
    """
    from ncaa_quant.data.storage import ParquetStore
    from ncaa_quant.evaluation.lockbox import LOCKBOX_SEASON, assert_lockbox_excluded

    season_list = [int(s) for s in seasons]
    assert_lockbox_excluded(season_list, context="backtest odds_snapshots load")
    store = ParquetStore(staged_root)
    frames: list[pd.DataFrame] = []
    for season in season_list:
        for path in store._matching_paths("odds_snapshots", {"season": int(season)}):  # noqa: SLF001
            frames.append(pd.read_parquet(path))
    if not frames:
        return None
    snapshots = pd.concat(frames, ignore_index=True)
    if "season" in snapshots.columns:
        lockbox_rows = snapshots.loc[snapshots["season"].astype(int) == LOCKBOX_SEASON]
        if not lockbox_rows.empty:
            msg = (
                f"odds_snapshots load included lockbox season {LOCKBOX_SEASON} "
                f"({len(lockbox_rows)} rows); refuse unconditionally"
            )
            raise AssertionError(msg)
    return snapshots


def load_fitted_priors_frame_for_backtest(
    staged_root: Path,
    seasons: tuple[int, ...] | list[int],
    *,
    priors_path: Path | None = None,
    weights_path: Path | None = None,
    history_path: Path | None = None,
) -> pd.DataFrame | None:
    """Load the Task 15 fitted ``priors_frame`` for ``backtest run`` injection.

    Preference order:
    1. Explicit ``priors_path`` parquet (must carry team/season/dim/prior_mean).
    2. Acceptance-cache ``week1_priors.parquet``.
    3. Rebuild from staged prior-family tables + fitted weights in ``summary.json``
       (and GT-active filter history when present).

    Returns ``None`` only when no artifact and no rebuild inputs exist — A1's
    precondition then fails loud rather than silently using league-mean.
    """
    season_list = [int(s) for s in seasons]
    if not season_list:
        return None

    path = priors_path if priors_path is not None else _DEFAULT_PRIORS_CACHE
    if path.is_file():
        frame = pd.read_parquet(path)
        if frame.empty:
            return None
        if "season" in frame.columns:
            frame = frame.loc[frame["season"].isin(season_list)].copy()
        return frame if not frame.empty else None

    wpath = weights_path if weights_path is not None else _DEFAULT_PRIORS_WEIGHTS
    if not wpath.is_file():
        return None

    import json

    from ncaa_quant.data.storage import ParquetStore
    from ncaa_quant.features.builders.roster import build_roster_frame
    from ncaa_quant.ratings.priors import (
        FittedPriorWeights,
        build_preseason_priors_frame,
    )

    payload = json.loads(wpath.read_text(encoding="utf-8"))
    fitted_by_dim: dict[str, FittedPriorWeights] = {}
    for dim in ("off_epa", "def_epa", "st_value", "pace"):
        block = payload.get(dim)
        if not isinstance(block, dict) or "weights" not in block:
            continue
        fitted_by_dim[dim] = FittedPriorWeights(
            dim=dim,
            weights={str(k): float(v) for k, v in dict(block["weights"]).items()},
            std_errors={
                str(k): float(v) if v is not None else 0.0
                for k, v in dict(block.get("std_errors") or {}).items()
            },
            r_squared=float(block.get("in_sample_r2") or 0.0),
            n_obs=int(block.get("n_obs") or 0),
            seasons_train=tuple(int(s) for s in (block.get("seasons_train") or [])),
            intercept=float(block.get("intercept") or 0.0),
            intercept_se=float(block.get("intercept_se") or 0.0),
        )
    if not fitted_by_dim:
        return None

    store = ParquetStore(staged_root)
    # Need S-1 for last-posterior lookup when history is present.
    load_seasons = sorted(set(season_list) | {min(season_list) - 1})

    def _read_table(name: str) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        for season in load_seasons:
            for p in store._matching_paths(name, {"season": int(season)}):  # noqa: SLF001
                frames.append(pd.read_parquet(p))
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    teams = _read_table("teams")
    if teams.empty:
        return None
    roster = build_roster_frame(
        teams=teams,
        returning=_read_table("returning_production"),
        talent=_read_table("talent"),
        recruiting=_read_table("recruiting"),
        portal=_read_table("portal"),
        coaches=_read_table("coaches"),
        seasons=load_seasons,
    )
    hpath = resolve_filter_history_path(history_path)
    history = pd.read_parquet(hpath) if hpath.is_file() else pd.DataFrame()

    frames: list[pd.DataFrame] = []
    for season in season_list:
        pf = build_preseason_priors_frame(
            history=history,
            roster=roster,
            teams=teams,
            season=int(season),
            fitted_by_dim=fitted_by_dim,
            dims=tuple(fitted_by_dim.keys()),
        )
        if not pf.empty:
            frames.append(pf)
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


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
    priors_path: str = typer.Option(
        "",
        "--priors-path",
        help="Fitted priors parquet (Task 15 seam). Default: acceptance cache if present.",
    ),
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
    from ncaa_quant.evaluation.lockbox import assert_lockbox_excluded
    from ncaa_quant.evaluation.production_stack import (
        build_observations_from_staged,
        build_production_stack,
    )
    from ncaa_quant.features.possessions import build_possessions_training_from_staged

    staged_path = Path(staged_dir)
    output_path = Path(output_root)
    payload = load_backtest_config(config)
    cfg = walkforward_config_from_mapping(payload)

    from ncaa_quant.evaluation.inert import InertComponentError, assert_prior_family_staged

    try:
        assert_prior_family_staged(cfg.all_replay_seasons(), staged_root=staged_path)
    except InertComponentError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=2) from exc

    replay_seasons = cfg.all_replay_seasons()
    assert_lockbox_excluded(replay_seasons, context=f"backtest run --config {config}")

    games = load_staged_games(staged_path, replay_seasons)
    if games.empty:
        typer.echo(f"No staged games for seasons {replay_seasons} under {staged_path}")
        raise typer.Exit(code=1)

    store = ParquetStore(staged_path)
    advanced_frames: list[pd.DataFrame] = []
    plays_frames: list[pd.DataFrame] = []
    lines_frames: list[pd.DataFrame] = []
    teams_frames: list[pd.DataFrame] = []
    drives_frames: list[pd.DataFrame] = []
    for season in replay_seasons:
        for path in store._matching_paths("advanced_box", {"season": int(season)}):  # noqa: SLF001
            advanced_frames.append(pd.read_parquet(path))
        for path in store._matching_paths("plays", {"season": int(season)}):  # noqa: SLF001
            plays_frames.append(pd.read_parquet(path))
        for path in store._matching_paths("lines_historical", {"season": int(season)}):  # noqa: SLF001
            lines_frames.append(pd.read_parquet(path))
        for path in store._matching_paths("teams", {"season": int(season)}):  # noqa: SLF001
            teams_frames.append(pd.read_parquet(path))
        for path in store._matching_paths("drives", {"season": int(season)}):  # noqa: SLF001
            drives_frames.append(pd.read_parquet(path))

    advanced = pd.concat(advanced_frames, ignore_index=True) if advanced_frames else None
    plays = pd.concat(plays_frames, ignore_index=True) if plays_frames else None
    cfbd_lines = pd.concat(lines_frames, ignore_index=True) if lines_frames else None
    teams = pd.concat(teams_frames, ignore_index=True) if teams_frames else pd.DataFrame()
    drives = pd.concat(drives_frames, ignore_index=True) if drives_frames else pd.DataFrame()
    obs, n_on, n_off = build_observations_from_staged(
        plays=plays,
        games=games,
        advanced=advanced,
        garbage_time_filter=cfg.garbage_time_filter,
    )
    play_counts = (n_on, n_off) if n_off > 0 else None

    # Snapshot ladder for ≥2021; lockbox 2025 excluded unconditionally.
    snapshots = load_staged_odds_snapshots(staged_path, replay_seasons)

    # Task 15 seam: inject fitted priors so A1's precondition sees a real frame.
    priors_frame = load_fitted_priors_frame_for_backtest(
        staged_path,
        replay_seasons,
        priors_path=Path(priors_path) if priors_path else None,
    )

    # Point-in-time possessions training (GT-filtered pace inputs). Walk-forward
    # refits at each retrain; the live artifact under configs/artifacts.yaml is
    # never loaded here.
    possessions_training = (
        build_possessions_training_from_staged(
            plays=plays if plays is not None else pd.DataFrame(),
            games=games,
            teams=teams,
            drives=drives,
            garbage_time_filter=cfg.garbage_time_filter,
        )
        if plays is not None and not plays.empty and not drives.empty
        else None
    )

    production = build_production_stack(
        cfg,
        kind=stack,  # type: ignore[arg-type]
        observations=obs,
        priors_frame=priors_frame,
        snapshots=snapshots,
        cfbd_lines=cfbd_lines,
        possessions_training=possessions_training,
        play_counts=play_counts,
        enforce_ablation_preconditions=bool(payload.get("enforce_ablation_preconditions", True)),
    )

    result = run_backtest(
        config,
        games=games,
        stack=production,
        snapshots=snapshots,
        cfbd_lines=cfbd_lines,
        observations=obs,
        priors_frame=priors_frame,
        play_counts=play_counts,
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


@backtest_app.command("verify")
def backtest_verify(
    output_root: str = typer.Option(
        "data/backtests",
        "--output-root",
        help="Scan every manifest.json beneath this root.",
    ),
    run_dir: str = typer.Option("", "--run-dir", help="Verify a single run directory instead."),
) -> None:
    """Report whether each run's results may be cited as evidence (ADR 0005).

    A run is citable only when its recorded git SHA resolves to a commit in this
    repository and the working tree was clean when it ran. Exits non-zero if any
    scanned run fails, so this can gate publication.
    """
    from ncaa_quant.registry.manifest import read_manifest, verify_provenance

    roots = [Path(run_dir)] if run_dir else sorted(Path(output_root).rglob("manifest.json"))
    paths = [p if p.name == "manifest.json" else p / "manifest.json" for p in roots]
    if not paths:
        typer.echo(f"no manifests found under {run_dir or output_root}")
        raise typer.Exit(code=2)

    failures = 0
    for path in paths:
        if not path.is_file():
            typer.echo(f"MISSING  {path}")
            failures += 1
            continue
        report = verify_provenance(read_manifest(path))
        label = "CITABLE " if report.citable else "REJECTED"
        typer.echo(f"{label} {path.parent}")
        for problem in report.problems:
            typer.echo(f"         - {problem}")
        failures += 0 if report.citable else 1

    typer.echo(f"{len(paths) - failures}/{len(paths)} runs citable")
    if failures:
        raise typer.Exit(code=1)


@backtest_app.callback(invoke_without_command=True)
def backtest(ctx: typer.Context) -> None:
    """Run walk-forward backtests."""
    if ctx.invoked_subcommand is None:
        typer.echo("Usage: ncaa-quant backtest [plan|run|verify] --config <name>")
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
