"""Grade export for Ridge ``results_<season>.json`` (docs/webapp/DESIGN.md §1.3)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, cast

from ncaa_quant.webapp.export import (
    REFRESH_KIND_PRECEDENCE,
    SCHEMA_VERSION,
    _iso_utc,
    _json_safe,
    _optional_float,
    schedule_lookup,
)

LIVE_PUBLISH_MIN_SEASON = 2026


class GradeExportError(ValueError):
    """Raised when grade export violates lockbox or input contracts."""


GradeStatus = str


def assert_live_season(season: int) -> None:
    """Refuse grading for lockbox / pre-live seasons (2025 and earlier)."""
    if season < LIVE_PUBLISH_MIN_SEASON:
        msg = (
            f"grade export refused for season {season}: live publish begins "
            f"{LIVE_PUBLISH_MIN_SEASON}+ (lockbox guard)"
        )
        raise GradeExportError(msg)


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).astimezone(UTC)
    except ValueError:
        return None


def select_pre_kickoff_publish(
    *,
    game_id: str,
    kickoff_utc: datetime,
    publish_history: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any] | None, dict[str, Any] | None]:
    """Select winning pre-kickoff snapshot per §1.3 grading rule.

    Tie-break among same ``refresh_kind``: latest ``published_at`` wins.
    Among different kinds: higher precedence wins; then latest ``published_at``.
    """
    candidates: list[tuple[int, datetime, Mapping[str, Any], Mapping[str, Any]]] = []
    for artifact in publish_history:
        refresh_kind = str(artifact.get("refresh_kind", ""))
        file_published = _parse_dt(artifact.get("published_at"))
        for game in artifact.get("games") or []:
            if str(game.get("game_id")) != str(game_id):
                continue
            row_published = _parse_dt(game.get("published_at")) or file_published
            kickoff = kickoff_utc
            if row_published is None or row_published >= kickoff:
                continue
            precedence = REFRESH_KIND_PRECEDENCE.get(refresh_kind, 0)
            candidates.append(
                (
                    precedence,
                    row_published,
                    game,
                    {"refresh_kind": refresh_kind, "published_at": _iso_utc(row_published)},
                )
            )

    if not candidates:
        return None, None

    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    _prec, _ts, game_row, graded_from = candidates[0]
    return game_row, dict(graded_from)


def _optional_int(value: Any) -> int | None:
    parsed = _optional_float(value)
    return int(parsed) if parsed is not None else None


def interval_hit(
    lo: float | None,
    hi: float | None,
    actual: float | int | None,
) -> bool | None:
    if lo is None or hi is None or actual is None:
        return None
    return float(lo) <= float(actual) <= float(hi)


def build_graded_game_row(
    *,
    schedule: Mapping[str, Any],
    published_game: Mapping[str, Any] | None,
    graded_from: Mapping[str, Any] | None,
    grade_status: GradeStatus,
) -> dict[str, Any]:
    home_pts = schedule.get("home_points")
    away_pts = schedule.get("away_points")
    actual_margin: int | None = None
    actual_total: int | None = None
    home_win: bool | None = None
    p_win_realized: float | None = None

    if home_pts is not None and away_pts is not None:
        home_f = _optional_float(home_pts)
        away_f = _optional_float(away_pts)
        if home_f is not None and away_f is not None:
            actual_margin = int(home_f) - int(away_f)
            actual_total = int(home_f) + int(away_f)
            home_win = actual_margin > 0
            p_win_realized = 1.0 if home_win else 0.0

    row: dict[str, Any] = {
        "game_id": str(schedule.get("game_id", "")),
        "week": int(schedule.get("week", 0)),
        "kickoff_utc": _iso_utc(schedule.get("kickoff_utc") or schedule.get("start_date")),
        "home_team": schedule.get("home_team"),
        "away_team": schedule.get("away_team"),
        "home_points": _optional_int(home_pts),
        "away_points": _optional_int(away_pts),
        "actual_margin": actual_margin,
        "actual_total": actual_total,
        "graded_from": graded_from,
        "mu_margin": None,
        "sigma_margin": None,
        "margin_interval_lo": None,
        "margin_interval_hi": None,
        "margin_interval_nominal": None,
        "mu_total": None,
        "total_interval_lo": None,
        "total_interval_hi": None,
        "total_interval_nominal": None,
        "p_win_home": None,
        "conviction_tier": None,
        "conviction_team": None,
        "conviction_label": None,
        "margin_interval_hit": None,
        "total_interval_hit": None,
        "home_win": home_win if home_win is not None else False,
        "p_win_home_realized": p_win_realized,
        "grade_status": grade_status,
    }

    if published_game is not None and grade_status == "graded":
        row.update(
            {
                "mu_margin": published_game.get("mu_margin"),
                "sigma_margin": published_game.get("sigma_margin"),
                "margin_interval_lo": published_game.get("margin_interval_lo"),
                "margin_interval_hi": published_game.get("margin_interval_hi"),
                "margin_interval_nominal": published_game.get("margin_interval_nominal"),
                "mu_total": published_game.get("mu_total"),
                "total_interval_lo": published_game.get("total_interval_lo"),
                "total_interval_hi": published_game.get("total_interval_hi"),
                "total_interval_nominal": published_game.get("total_interval_nominal"),
                "p_win_home": published_game.get("p_win_home"),
                "conviction_tier": published_game.get("conviction_tier"),
                "conviction_team": published_game.get("conviction_team"),
                "conviction_label": published_game.get("conviction_label"),
                "margin_interval_hit": interval_hit(
                    _optional_float(published_game.get("margin_interval_lo")),
                    _optional_float(published_game.get("margin_interval_hi")),
                    actual_margin,
                ),
                "total_interval_hit": interval_hit(
                    _optional_float(published_game.get("total_interval_lo")),
                    _optional_float(published_game.get("total_interval_hi")),
                    actual_total,
                ),
            }
        )
    return cast(dict[str, Any], _json_safe(row))


def build_results_season(
    *,
    season: int,
    published_at: datetime,
    completed_games: Any,
    schedule_by_game: Mapping[str, Mapping[str, Any]],
    publish_history: Sequence[Mapping[str, Any]],
    fixture: bool = False,
    allow_historical_fixture: bool = False,
) -> dict[str, Any]:
    """Build ``results_<season>.json`` for one season."""
    if not allow_historical_fixture:
        assert_live_season(season)

    games_out: list[dict[str, Any]] = []
    for _gid, sched in schedule_by_game.items():
        sched = {**sched, "week": sched.get("week", 0)}
        kickoff = _parse_dt(sched.get("kickoff_utc") or sched.get("start_date"))
        completed = bool(sched.get("completed", False))
        home_pts = sched.get("home_points")
        away_pts = sched.get("away_points")

        if not completed:
            games_out.append(
                build_graded_game_row(
                    schedule=sched,
                    published_game=None,
                    graded_from=None,
                    grade_status="game_not_final",
                )
            )
            continue

        if (
            home_pts is None
            or away_pts is None
            or _optional_float(home_pts) is None
            or _optional_float(away_pts) is None
        ):
            games_out.append(
                build_graded_game_row(
                    schedule=sched,
                    published_game=None,
                    graded_from=None,
                    grade_status="postgame_missing",
                )
            )
            continue

        if kickoff is None:
            games_out.append(
                build_graded_game_row(
                    schedule=sched,
                    published_game=None,
                    graded_from=None,
                    grade_status="postgame_missing",
                )
            )
            continue

        published, graded_from = select_pre_kickoff_publish(
            game_id=str(sched.get("game_id")),
            kickoff_utc=kickoff,
            publish_history=publish_history,
        )
        if published is None:
            games_out.append(
                build_graded_game_row(
                    schedule=sched,
                    published_game=None,
                    graded_from=None,
                    grade_status="no_pre_kickoff_publish",
                )
            )
            continue

        games_out.append(
            build_graded_game_row(
                schedule=sched,
                published_game=published,
                graded_from=graded_from,
                grade_status="graded",
            )
        )

    artifact: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "season": season,
        "published_at": _iso_utc(published_at),
        "grading_rule": "last_pre_kickoff_publish",
        "games": games_out,
    }
    if fixture:
        artifact["fixture"] = True
    return artifact


def grade_export(
    *,
    season: int,
    published_at: datetime | None = None,
    publish_history: Sequence[Mapping[str, Any]] | None = None,
    config: Any = None,
) -> dict[str, Any]:
    """On-demand grade export entry (2026+ only)."""
    from ncaa_quant.config import load_config
    from ncaa_quant.webapp.export import load_schedule_frame, load_teams_frame

    assert_live_season(season)
    cfg = config or load_config()
    clock = published_at or datetime.now(tz=UTC)

    games_frames = []
    sched: dict[str, dict[str, Any]] = {}
    for week in range(1, 16):
        try:
            gf = load_schedule_frame(season=season, week=week, config=cfg)
        except FileNotFoundError:
            continue
        if gf.empty:
            continue
        games_frames.append(gf)
        teams = load_teams_frame(season=season, config=cfg)
        sched.update(schedule_lookup(gf, teams))

    if not games_frames:
        msg = f"no staged games for season {season}"
        raise GradeExportError(msg)

    import pandas as pd  # type: ignore[import-untyped]

    completed = pd.concat(games_frames, ignore_index=True)
    history = list(publish_history or [])
    return build_results_season(
        season=season,
        published_at=clock,
        completed_games=completed,
        schedule_by_game=sched,
        publish_history=history,
    )
