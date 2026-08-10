"""Great Expectations suites per staged table (DESIGN §8 step 2).

Suites cover schema presence, value ranges, nullability of keys, and
duplicate detection on natural keys. Cross-table and temporal checks live in
``validators.py`` / ``pit_audit.py`` — they are not expressible cleanly as
single-table GE expectations.
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass, field
from typing import Any

import great_expectations as gx
import pandas as pd  # type: ignore[import-untyped]
from great_expectations.expectations import (  # type: ignore[attr-defined]
    ExpectColumnPairValuesAToBeGreaterThanB,
    ExpectColumnValuesToBeBetween,
    ExpectColumnValuesToBeInSet,
    ExpectColumnValuesToBeUnique,
    ExpectColumnValuesToNotBeNull,
    ExpectCompoundColumnsToBeUnique,
    ExpectTableColumnsToMatchSet,
)

# Tables validated by GE suites in Task 7.
TABLE_SUITES: tuple[str, ...] = (
    "games",
    "plays",
    "drives",
    "advanced_box",
    "lines_historical",
    "venues",
    "odds_snapshots",
)


@dataclass(frozen=True)
class SuiteFailure:
    """One unsuccessful expectation from a GE validation result."""

    expectation: str
    message: str
    sample_rows: list[dict[str, Any]] = field(default_factory=list)
    unexpected_count: int = 0


@dataclass(frozen=True)
class SuiteResult:
    """Outcome of running a named suite against a DataFrame."""

    table: str
    suite_name: str
    success: bool
    failures: list[SuiteFailure]
    evaluated: int
    successful: int


def build_suite(table: str) -> gx.ExpectationSuite:
    """Return the GE ExpectationSuite for ``table``.

    Requires an active GE data context (``gx.get_context``) — call
    :func:`run_suite_on_dataframe` for the usual path.
    """
    builders = {
        "games": _games_suite,
        "plays": _plays_suite,
        "drives": _drives_suite,
        "advanced_box": _advanced_box_suite,
        "lines_historical": _lines_suite,
        "venues": _venues_suite,
        "odds_snapshots": _odds_suite,
    }
    try:
        builder = builders[table]
    except KeyError as exc:
        msg = f"no GE suite registered for table {table!r}"
        raise KeyError(msg) from exc
    return builder()


def run_suite_on_dataframe(table: str, df: pd.DataFrame) -> SuiteResult:
    """Validate ``df`` with the suite for ``table`` using an ephemeral GE context."""
    if df.empty:
        return SuiteResult(
            table=table,
            suite_name=table,
            success=True,
            failures=[],
            evaluated=0,
            successful=0,
        )

    # GE is chatty on ephemeral context creation; keep CLI output readable.
    logging.getLogger("great_expectations").setLevel(logging.ERROR)
    warnings.filterwarnings("ignore", category=UserWarning, module="great_expectations")

    # GE 1.x requires an active context before ExpectationSuite.add_expectation.
    ctx = gx.get_context(mode="ephemeral")
    suite = build_suite(table)
    datasource = ctx.data_sources.add_pandas(name=f"pandas_{table}")
    asset = datasource.add_dataframe_asset(name=table)
    batch_def = asset.add_batch_definition_whole_dataframe("whole")
    batch = batch_def.get_batch(batch_parameters={"dataframe": df})
    result = batch.validate(suite)

    failures: list[SuiteFailure] = []
    for item in result.results:
        if item.success:
            continue
        cfg = item.expectation_config
        exp_type = str(cfg.type) if cfg is not None else "unknown"
        kwargs = dict(cfg.kwargs) if cfg is not None else {}
        res = item.result or {}
        unexpected = list(res.get("partial_unexpected_list") or [])
        sample_rows = _samples_from_unexpected(df, kwargs.get("column"), unexpected)
        failures.append(
            SuiteFailure(
                expectation=exp_type,
                message=_format_failure_message(exp_type, kwargs, res),
                sample_rows=sample_rows,
                unexpected_count=int(res.get("unexpected_count") or 0),
            )
        )

    stats = result.statistics or {}
    return SuiteResult(
        table=table,
        suite_name=suite.name,
        success=bool(result.success),
        failures=failures,
        evaluated=int(stats.get("evaluated_expectations") or 0),
        successful=int(stats.get("successful_expectations") or 0),
    )


def _games_suite() -> gx.ExpectationSuite:
    suite = gx.ExpectationSuite(name="games")
    cols = {
        "game_id",
        "season",
        "week",
        "season_type",
        "start_date",
        "home_team_id",
        "away_team_id",
        "home_points",
        "away_points",
        "neutral_site",
        "conference_game",
        "venue_id",
        "completed",
        "event_time_estimated",
        "source_version",
        "event_time",
        "ingested_at",
    }
    suite.add_expectation(ExpectTableColumnsToMatchSet(column_set=sorted(cols), exact_match=True))
    for col in ("game_id", "season", "week", "home_team_id", "away_team_id", "completed"):
        suite.add_expectation(ExpectColumnValuesToNotBeNull(column=col))
    suite.add_expectation(ExpectColumnValuesToBeUnique(column="game_id"))
    suite.add_expectation(
        ExpectColumnValuesToBeBetween(column="home_points", min_value=0, max_value=100)
    )
    suite.add_expectation(
        ExpectColumnValuesToBeBetween(column="away_points", min_value=0, max_value=100)
    )
    suite.add_expectation(
        ExpectColumnValuesToBeInSet(column="season_type", value_set=["regular", "postseason"])
    )
    suite.add_expectation(ExpectColumnValuesToBeBetween(column="week", min_value=0, max_value=25))
    return suite


def _plays_suite() -> gx.ExpectationSuite:
    suite = gx.ExpectationSuite(name="plays")
    cols = {
        "play_id",
        "game_id",
        "drive_id",
        "season",
        "week",
        "offense_id",
        "defense_id",
        "period",
        "down",
        "distance",
        "yards_to_goal",
        "play_type",
        "yards_gained",
        "epa",
        "wp",
        "offense_score",
        "defense_score",
        "clock",
        "score_margin",
        "success",
        "scoring",
        "source_version",
        "event_time",
        "ingested_at",
    }
    suite.add_expectation(ExpectTableColumnsToMatchSet(column_set=sorted(cols), exact_match=True))
    for col in ("play_id", "game_id", "season", "week", "offense_id", "defense_id", "period"):
        suite.add_expectation(ExpectColumnValuesToNotBeNull(column=col))
    # GT-FIX: score/clock must be present on nearly all rows. WP may be 100% null
    # (CFBD /plays archives do not ship it); do not require wp non-null.
    for col in ("offense_score", "defense_score", "clock", "score_margin"):
        suite.add_expectation(ExpectColumnValuesToNotBeNull(column=col, mostly=0.95))
    suite.add_expectation(ExpectCompoundColumnsToBeUnique(column_list=["game_id", "play_id"]))
    suite.add_expectation(ExpectColumnValuesToBeBetween(column="period", min_value=1, max_value=8))
    suite.add_expectation(ExpectColumnValuesToBeBetween(column="wp", min_value=0.0, max_value=1.0))
    suite.add_expectation(
        ExpectColumnValuesToBeBetween(column="yards_to_goal", min_value=0, max_value=100)
    )
    suite.add_expectation(
        ExpectColumnValuesToBeBetween(column="offense_score", min_value=0, max_value=100)
    )
    suite.add_expectation(
        ExpectColumnValuesToBeBetween(column="defense_score", min_value=0, max_value=100)
    )
    suite.add_expectation(ExpectColumnValuesToBeBetween(column="clock", min_value=0, max_value=900))
    suite.add_expectation(
        ExpectColumnValuesToBeBetween(column="score_margin", min_value=-100, max_value=100)
    )
    return suite


def _drives_suite() -> gx.ExpectationSuite:
    suite = gx.ExpectationSuite(name="drives")
    cols = {
        "drive_id",
        "game_id",
        "season",
        "week",
        "offense_id",
        "defense_id",
        "start_period",
        "end_period",
        "plays",
        "yards",
        "scoring",
        "start_yards_to_goal",
        "end_yards_to_goal",
        "points",
        "source_version",
        "event_time",
        "ingested_at",
    }
    suite.add_expectation(ExpectTableColumnsToMatchSet(column_set=sorted(cols), exact_match=True))
    for col in ("drive_id", "game_id", "season", "week", "offense_id", "defense_id"):
        suite.add_expectation(ExpectColumnValuesToNotBeNull(column=col))
    suite.add_expectation(ExpectCompoundColumnsToBeUnique(column_list=["game_id", "drive_id"]))
    suite.add_expectation(
        ExpectColumnValuesToBeBetween(column="points", min_value=0, max_value=100)
    )
    return suite


def _advanced_box_suite() -> gx.ExpectationSuite:
    suite = gx.ExpectationSuite(name="advanced_box")
    cols = {
        "game_id",
        "team_id",
        "season",
        "week",
        "offense_epa",
        "defense_epa",
        "success_rate",
        "explosiveness",
        "havoc_rate",
        "finishing_drives",
        "field_position",
        "points",
        "source_version",
        "event_time",
        "ingested_at",
    }
    suite.add_expectation(ExpectTableColumnsToMatchSet(column_set=sorted(cols), exact_match=True))
    for col in ("game_id", "team_id", "season", "week"):
        suite.add_expectation(ExpectColumnValuesToNotBeNull(column=col))
    suite.add_expectation(ExpectCompoundColumnsToBeUnique(column_list=["game_id", "team_id"]))
    suite.add_expectation(
        ExpectColumnValuesToBeBetween(column="points", min_value=0, max_value=100)
    )
    suite.add_expectation(
        ExpectColumnValuesToBeBetween(column="success_rate", min_value=0.0, max_value=1.0)
    )
    return suite


def _lines_suite() -> gx.ExpectationSuite:
    suite = gx.ExpectationSuite(name="lines_historical")
    cols = {
        "game_id",
        "season",
        "week",
        "book",
        "line_type",
        "spread",
        "total",
        "home_ml",
        "away_ml",
        "source_version",
        "event_time",
        "ingested_at",
    }
    suite.add_expectation(ExpectTableColumnsToMatchSet(column_set=sorted(cols), exact_match=True))
    for col in ("game_id", "season", "week", "book", "line_type"):
        suite.add_expectation(ExpectColumnValuesToNotBeNull(column=col))
    suite.add_expectation(
        ExpectColumnValuesToBeInSet(column="line_type", value_set=["open", "close", "other"])
    )
    # DESIGN §8: |spread| < 70, totals in [20, 100]
    suite.add_expectation(
        ExpectColumnValuesToBeBetween(column="spread", min_value=-69.999, max_value=69.999)
    )
    suite.add_expectation(
        ExpectColumnValuesToBeBetween(column="total", min_value=20.0, max_value=100.0)
    )
    return suite


def _venues_suite() -> gx.ExpectationSuite:
    suite = gx.ExpectationSuite(name="venues")
    cols = {
        "venue_id",
        "season",
        "name",
        "city",
        "state",
        "latitude",
        "longitude",
        "elevation_m",
        "capacity",
        "grass",
        "dome",
        "surface",
        "timezone",
        "source_version",
        "event_time",
        "ingested_at",
    }
    suite.add_expectation(ExpectTableColumnsToMatchSet(column_set=sorted(cols), exact_match=True))
    for col in ("venue_id", "season", "name"):
        suite.add_expectation(ExpectColumnValuesToNotBeNull(column=col))
    suite.add_expectation(ExpectColumnValuesToBeUnique(column="venue_id"))
    suite.add_expectation(
        ExpectColumnValuesToBeBetween(column="latitude", min_value=-90.0, max_value=90.0)
    )
    suite.add_expectation(
        ExpectColumnValuesToBeBetween(column="longitude", min_value=-180.0, max_value=180.0)
    )
    return suite


def _odds_suite() -> gx.ExpectationSuite:
    suite = gx.ExpectationSuite(name="odds_snapshots")
    cols = {
        "snapshot_id",
        "game_key",
        "game_id",
        "season",
        "week",
        "book",
        "market",
        "side",
        "line",
        "price",
        "home_team",
        "away_team",
        "captured_at",
        "source_version",
        "snapshot_source",
        "decision_point",
        "n_books_available",
        "event_time",
        "ingested_at",
    }
    suite.add_expectation(ExpectTableColumnsToMatchSet(column_set=sorted(cols), exact_match=True))
    for col in (
        "snapshot_id",
        "game_key",
        "book",
        "market",
        "price",
        "captured_at",
        "snapshot_source",
        "n_books_available",
        "event_time",
        "ingested_at",
    ):
        suite.add_expectation(ExpectColumnValuesToNotBeNull(column=col))
    suite.add_expectation(ExpectColumnValuesToBeUnique(column="snapshot_id"))
    suite.add_expectation(
        ExpectColumnValuesToBeInSet(column="market", value_set=["spread", "total", "h2h"])
    )
    suite.add_expectation(
        ExpectColumnValuesToBeInSet(column="snapshot_source", value_set=["live", "historical"])
    )
    # Temporal pair check as a GE expressible form (also covered by pit_audit).
    suite.add_expectation(
        ExpectColumnPairValuesAToBeGreaterThanB(
            column_A="ingested_at",
            column_B="event_time",
            or_equal=True,
        )
    )
    return suite


def _format_failure_message(exp_type: str, kwargs: dict[str, Any], result: dict[str, Any]) -> str:
    unexpected = result.get("unexpected_count")
    col = kwargs.get("column") or kwargs.get("column_list") or kwargs.get("column_set")
    return f"{exp_type} failed for {col!r} (unexpected_count={unexpected})"


def _samples_from_unexpected(
    df: pd.DataFrame,
    column: Any,
    unexpected: list[Any],
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    if not unexpected or column is None or not isinstance(column, str) or column not in df.columns:
        return _records_json_safe(df.head(limit))
    mask = df[column].isin(unexpected)
    sample = df.loc[mask].head(limit)
    if sample.empty:
        return _records_json_safe(df.head(limit))
    return _records_json_safe(sample)


def _records_json_safe(df: pd.DataFrame) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in df.to_dict(orient="records"):
        cleaned: dict[str, Any] = {}
        for key, value in row.items():
            if pd.isna(value):
                cleaned[key] = None
            elif hasattr(value, "isoformat"):
                cleaned[key] = value.isoformat()
            else:
                cleaned[key] = value
        out.append(cleaned)
    return out
