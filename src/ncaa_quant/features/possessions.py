"""Expected-possessions load / point-in-time fit entrypoints (DESIGN §4.5).

The regression math lives in :mod:`ncaa_quant.features.builders.tempo`. This
module only exposes the production seams:

* **Live inference** loads a promoted artifact from the configured non-tmp path.
* **Walk-forward** refits at each retrain gate on strictly-prior games' GT-filtered
  pace inputs — never loads the live / globally-fitted artifact.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd  # type: ignore[import-untyped]

from ncaa_quant.features.builders.tempo import (
    EXPECTED_POSSESSIONS_FEATURE_NAMES,
    ExpectedPossessionsArtifact,
    build_expected_possessions_training_frame,
    build_tempo_observations,
    fit_expected_possessions,
    load_expected_possessions_artifact,
)
from ncaa_quant.features.epa import classify_play_type

# Config key (configs/artifacts.yaml) for the LIVE-only promoted artifact.
LIVE_EXPECTED_POSSESSIONS_CONFIG_KEY: str = "artifacts.expected_possessions_live"
DEFAULT_LIVE_EXPECTED_POSSESSIONS_PATH: Path = Path("data/artifacts/expected_possessions/live.json")


class PossessionsFitError(ValueError):
    """Raised when a point-in-time possessions fit would leak future games."""


def prior_to_retrain_mask(
    frame: pd.DataFrame,
    *,
    season: int,
    week: int,
) -> pd.Series:
    """Boolean mask: rows strictly before retrain point ``(season, week)``.

    ``week <= 0`` is the offseason gate: only ``season < season`` rows qualify.
    Otherwise a row qualifies when ``season < S`` or ``(season == S and week < W)``.
    """
    if "season" not in frame.columns or "week" not in frame.columns:
        msg = "possessions training frame requires season and week columns"
        raise PossessionsFitError(msg)
    s = frame["season"].astype(int)
    w = frame["week"].astype(int)
    if int(week) <= 0:
        return s < int(season)
    return (s < int(season)) | ((s == int(season)) & (w < int(week)))


def assert_possessions_fit_is_point_in_time(
    used: pd.DataFrame,
    *,
    season: int,
    week: int,
) -> None:
    """Assert every fitted row is strictly prior to ``(season, week)``."""
    if used.empty:
        return
    leaked = used.loc[~prior_to_retrain_mask(used, season=season, week=week)]
    if leaked.empty:
        return
    sample = leaked[["season", "week"]].drop_duplicates().head(5)
    sample_records = sample.to_dict(orient="records")
    msg = (
        f"expected-possessions fit at retrain (season={season}, week={week}) "
        f"saw {len(leaked)} game(s) at or after that point; sample={sample_records}"
    )
    raise PossessionsFitError(msg)


def fit_expected_possessions_at_retrain(
    training: pd.DataFrame,
    *,
    season: int,
    week: int,
) -> ExpectedPossessionsArtifact | None:
    """Fit possessions regression on games strictly prior to ``(season, week)``.

    Returns ``None`` when no prior rows exist (cold start). Never reads a
    globally-fitted live artifact.
    """
    if training.empty:
        return None
    mask = prior_to_retrain_mask(training, season=season, week=week)
    prior = training.loc[mask].copy()
    assert_possessions_fit_is_point_in_time(prior, season=season, week=week)
    if prior.empty:
        return None
    seasons = (
        tuple(sorted({int(s) for s in prior["season"].dropna().tolist()}))
        if "season" in prior.columns
        else ()
    )
    return fit_expected_possessions(prior, train_seasons=seasons)


def load_live_expected_possessions(
    path: Path | str | None = None,
) -> ExpectedPossessionsArtifact:
    """Load the promoted LIVE-only artifact (never for walk-forward)."""
    target = Path(path) if path is not None else DEFAULT_LIVE_EXPECTED_POSSESSIONS_PATH
    return load_expected_possessions_artifact(target)


def _annotate_play_types(plays: pd.DataFrame) -> pd.DataFrame:
    """Ensure rush/pass flags exist for tempo observation construction."""
    work = plays.copy()
    if "is_rush" in work.columns and "is_pass" in work.columns:
        return work
    flags = work["play_type"].map(lambda t: classify_play_type(None if pd.isna(t) else str(t)))
    work["is_rush"] = flags.map(lambda x: bool(x[0]))
    work["is_pass"] = flags.map(lambda x: bool(x[1]))
    work["is_special_teams"] = flags.map(lambda x: bool(x[2]))
    work["is_penalty"] = flags.map(lambda x: bool(x[3]))
    return work


def build_possessions_training_from_staged(
    *,
    plays: pd.DataFrame,
    games: pd.DataFrame,
    teams: pd.DataFrame,
    drives: pd.DataFrame,
    garbage_time_filter: bool = True,
) -> pd.DataFrame:
    """Build GT-filtered pace-input training rows for point-in-time fits.

    Units: pace = season-to-date plays/game before each kickoff; pass_rate =
    season-to-date offensive pass fraction; possessions = drive count (target).
    """
    if plays.empty or games.empty or drives.empty:
        return pd.DataFrame()
    work = _annotate_play_types(plays)
    for col in ("clock", "score_margin", "offense_score", "defense_score"):
        if col not in work.columns:
            work[col] = pd.Series([pd.NA] * len(work), dtype="Int64")
    tempo_obs = build_tempo_observations(
        work,
        games,
        teams,
        drop_garbage=garbage_time_filter,
    )
    return build_expected_possessions_training_frame(tempo_obs, games, drives)


def predict_expected_possessions_rows(
    artifact: ExpectedPossessionsArtifact,
    rows: pd.DataFrame,
) -> list[float]:
    """Apply ``artifact`` to rows carrying :data:`EXPECTED_POSSESSIONS_FEATURE_NAMES`."""
    out: list[float] = []
    for r in rows.itertuples(index=False):
        feats: dict[str, float] = {}
        missing = False
        for name in EXPECTED_POSSESSIONS_FEATURE_NAMES:
            val = getattr(r, name, float("nan"))
            try:
                fval = float(val)
            except (TypeError, ValueError):
                fval = float("nan")
            if fval != fval:  # NaN
                missing = True
                break
            feats[name] = fval
        out.append(float("nan") if missing else float(artifact.predict_row(feats)))
    return out


__all__ = [
    "DEFAULT_LIVE_EXPECTED_POSSESSIONS_PATH",
    "EXPECTED_POSSESSIONS_FEATURE_NAMES",
    "LIVE_EXPECTED_POSSESSIONS_CONFIG_KEY",
    "PossessionsFitError",
    "assert_possessions_fit_is_point_in_time",
    "build_possessions_training_from_staged",
    "fit_expected_possessions_at_retrain",
    "load_live_expected_possessions",
    "predict_expected_possessions_rows",
    "prior_to_retrain_mask",
]
