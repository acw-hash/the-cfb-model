"""CFBD AP rankings for situational-angle research (H23).

Research storage only: ``data/research/rankings`` is not a production staged
table and is not in ``ENDPOINT_SPECS``. ``event_time`` is the poll release
instant; a poll is never attached to a game whose kickoff is at or before
that instant.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pandas as pd  # type: ignore[import-untyped]
import pandera.pandas as pa
from pandera.engines.pandas_engine import DateTime
from pandera.typing import Series as PaSeries

from ncaa_quant.utils.timeutils import DECISION_POINT_TZ, to_utc

DEFAULT_RANKINGS_ROOT: Final[Path] = Path("data/research/rankings")
AP_POLL_SEASONS: Final[tuple[int, ...]] = tuple(range(2014, 2027))

_UTC_DT = {"tz": UTC}


class ApRankingSchema(pa.DataFrameModel):
    """One AP ballot row. ``event_time`` is the poll release (UTC)."""

    season: PaSeries[int]
    week: PaSeries[int]
    season_type: PaSeries[str]
    poll: PaSeries[str]
    team_id: PaSeries[int]
    school: PaSeries[str]
    rank: PaSeries[int] = pa.Field(ge=1, le=25)
    event_time: PaSeries[DateTime] = pa.Field(dtype_kwargs=_UTC_DT)


def is_ap_poll(name: str) -> bool:
    """True for the AP Top 25 (not Coaches / CFP / playoff)."""
    text = str(name).casefold()
    if "ap" not in text:
        return False
    banned = ("coach", "cfp", "playoff", "play-off")
    return not any(token in text for token in banned)


def default_poll_event_time(
    season: int,
    week: int,
    *,
    tuesday_0600_et: datetime | None,
    season_type: str = "regular",
) -> datetime:
    """Poll release used when CFBD omits ``pollDate``.

    In-season: Sunday 18:00 ET of the same CFB week (Tuesday 06:00 ET minus
    36 hours). Preseason / week <= 1: August 15 18:00 ET of ``season``.
    """
    st = str(season_type).casefold()
    if st == "preseason" or int(week) <= 1 or tuesday_0600_et is None:
        local = datetime(int(season), 8, 15, 18, 0, 0, tzinfo=DECISION_POINT_TZ)
        return to_utc(local)
    return to_utc(tuesday_0600_et) - timedelta(hours=36)


def _parse_poll_date(raw: Any) -> datetime | None:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    text = str(raw).strip()
    if not text:
        return None
    ts = pd.to_datetime(text, utc=True, errors="coerce")
    if ts is None or pd.isna(ts):
        return None
    return to_utc(ts.to_pydatetime())


def school_maps(teams: pd.DataFrame) -> tuple[dict[tuple[int, str], int], dict[str, int]]:
    """``(season, school_casefold) -> team_id`` and a season-agnostic fallback."""
    seasonal: dict[tuple[int, str], int] = {}
    fallback: dict[str, int] = {}
    if teams.empty or "school" not in teams.columns:
        return seasonal, fallback
    for r in teams.itertuples(index=False):
        school = str(r.school).strip()
        if not school:
            continue
        key = school.casefold()
        tid = int(r.team_id)
        season = int(r.season) if hasattr(r, "season") and pd.notna(r.season) else 0
        seasonal[(season, key)] = tid
        fallback[key] = tid
    return seasonal, fallback


def resolve_team_id(
    school: str,
    season: int,
    seasonal: Mapping[tuple[int, str], int],
    fallback: Mapping[str, int],
    team_id: Any = None,
) -> int | None:
    """Prefer an explicit CFBD ``teamId``, else the staged school map."""
    if team_id is not None and pd.notna(team_id):
        try:
            return int(team_id)
        except (TypeError, ValueError):
            pass
    key = str(school).strip().casefold()
    if not key:
        return None
    if (int(season), key) in seasonal:
        return seasonal[(int(season), key)]
    return fallback.get(key)


def parse_rankings_payload(
    payload: Any,
    *,
    season: int,
    seasonal: Mapping[tuple[int, str], int],
    fallback: Mapping[str, int],
    tuesdays: Mapping[tuple[int, int], datetime],
) -> list[dict[str, Any]]:
    """Flatten CFBD ``/rankings`` JSON into AP ballot rows."""
    if payload is None:
        return []
    weeks = payload if isinstance(payload, list) else [payload]
    rows: list[dict[str, Any]] = []
    for block in weeks:
        if not isinstance(block, Mapping):
            continue
        week = int(block.get("week") or 0)
        season_type = str(block.get("seasonType") or block.get("season_type") or "regular")
        block_season = int(block.get("season") or season)
        tue = tuesdays.get((block_season, week))
        polls = block.get("polls") or []
        if not isinstance(polls, list):
            continue
        for poll in polls:
            if not isinstance(poll, Mapping):
                continue
            poll_name = str(poll.get("poll") or "")
            if not is_ap_poll(poll_name):
                continue
            release = _parse_poll_date(
                poll.get("pollDate") or poll.get("poll_date") or block.get("pollDate")
            )
            if release is None:
                release = default_poll_event_time(
                    block_season, week, tuesday_0600_et=tue, season_type=season_type
                )
            ranks = poll.get("ranks") or []
            if not isinstance(ranks, list):
                continue
            for entry in ranks:
                if not isinstance(entry, Mapping):
                    continue
                school = str(entry.get("school") or "")
                tid = resolve_team_id(
                    school,
                    block_season,
                    seasonal,
                    fallback,
                    team_id=entry.get("teamId") or entry.get("team_id"),
                )
                if tid is None:
                    continue
                rank_raw = entry.get("rank")
                if rank_raw is None or pd.isna(rank_raw):
                    continue
                rank = int(rank_raw)
                if rank < 1 or rank > 25:
                    continue
                rows.append(
                    {
                        "season": block_season,
                        "week": week,
                        "season_type": season_type,
                        "poll": poll_name,
                        "team_id": tid,
                        "school": school,
                        "rank": rank,
                        "event_time": release,
                    }
                )
    return rows


def validate_rankings(df: pd.DataFrame) -> pd.DataFrame:
    """Pandera-validate a rankings frame (empty → empty with schema columns)."""
    cols = list(ApRankingSchema.to_schema().columns)
    if df is None or df.empty:
        return pd.DataFrame(columns=cols)
    work = df.copy()
    work["event_time"] = pd.to_datetime(work["event_time"], utc=True)
    return ApRankingSchema.validate(work)


def rankings_partition_path(root: Path | str, season: int) -> Path:
    return Path(root) / f"season={int(season)}" / "part.parquet"


def load_research_rankings(
    seasons: Sequence[int],
    *,
    root: Path | str = DEFAULT_RANKINGS_ROOT,
) -> pd.DataFrame:
    """Read hive partitions under research storage."""
    frames: list[pd.DataFrame] = []
    for season in seasons:
        path = rankings_partition_path(root, season)
        if path.is_file():
            frames.append(pd.read_parquet(path))
    if not frames:
        return pd.DataFrame(columns=list(ApRankingSchema.to_schema().columns))
    out = pd.concat(frames, ignore_index=True)
    return validate_rankings(out)


def write_research_rankings(
    df: pd.DataFrame,
    *,
    root: Path | str = DEFAULT_RANKINGS_ROOT,
) -> list[Path]:
    """Write season partitions. Does not touch ``data/staged``."""
    validated = validate_rankings(df)
    written: list[Path] = []
    if validated.empty:
        return written
    for season, grp in validated.groupby("season"):
        path = rankings_partition_path(root, int(season))
        path.parent.mkdir(parents=True, exist_ok=True)
        grp.sort_values(["week", "rank", "team_id"], kind="mergesort").to_parquet(path, index=False)
        written.append(path)
    return written


def ingest_cfbd_rankings(
    seasons: Sequence[int],
    teams: pd.DataFrame,
    *,
    tuesdays: Mapping[tuple[int, int], datetime] | None = None,
    root: Path | str = DEFAULT_RANKINGS_ROOT,
    client: Any | None = None,
    get_payload: Callable[[int, str], Any] | None = None,
    force: bool = False,
) -> pd.DataFrame:
    """Fetch CFBD ``/rankings`` into research storage for ``seasons``.

    ``get_payload(season, season_type)`` may be injected (tests). Live fetches
    use :class:`ncaa_quant.ingestion.cfbd.CFBDClient.get` and never register
    the endpoint in production ``ENDPOINT_SPECS``.
    """
    existing = load_research_rankings(seasons, root=root)
    have = set(int(s) for s in existing["season"].unique()) if not existing.empty else set()
    missing = [int(s) for s in seasons if int(s) not in have]
    if not missing and not force:
        return existing
    seasonal, fallback = school_maps(teams)
    tue = dict(tuesdays or {})
    fetch = get_payload
    owns_client = False
    if fetch is None and client is None:
        from ncaa_quant.config import load_secrets
        from ncaa_quant.ingestion.cfbd import CFBDClient

        key = load_secrets().cfbd_api_key.get_secret_value()
        if not key:
            msg = "CFBD_API_KEY is empty; cannot ingest research rankings"
            raise RuntimeError(msg)
        client = CFBDClient(key)
        owns_client = True

    def _get(year: int, season_type: str) -> Any:
        if fetch is not None:
            return fetch(year, season_type)
        assert client is not None
        body = client.get("/rankings", params={"year": int(year), "seasonType": season_type})
        return json.loads(body.decode("utf-8"))

    rows: list[dict[str, Any]] = []
    try:
        targets = list(seasons) if force else missing
        for season in targets:
            # CFBD `/rankings` 400s on seasonType=preseason for many years.
            # Week-1 / preseason AP ballots are included in seasonType=regular.
            payload = _get(int(season), "regular")
            rows.extend(
                parse_rankings_payload(
                    payload,
                    season=int(season),
                    seasonal=seasonal,
                    fallback=fallback,
                    tuesdays=tue,
                )
            )
    finally:
        if owns_client and client is not None:
            close = getattr(client, "close", None)
            if callable(close):
                close()
    fetched = pd.DataFrame(rows)
    if existing.empty:
        combined = fetched
    elif fetched.empty:
        combined = existing
    else:
        combined = pd.concat(
            [existing.loc[~existing["season"].isin(targets)], fetched], ignore_index=True
        )
    combined = validate_rankings(combined)
    write_research_rankings(combined, root=root)
    return combined


def attach_ap_ranks_at_kickoff(
    games: pd.DataFrame,
    rankings: pd.DataFrame,
) -> pd.DataFrame:
    """Per-game home/away AP rank from the latest poll with ``event_time < kickoff``.

    A poll released at kickoff is not used. A team missing from that poll is
    unranked (null rank) even if an earlier poll ranked them — we do not carry
    stale ranks forward across polls.
    """
    cols = ["game_id", "home_ap_rank", "away_ap_rank", "ap_poll_event_time"]
    if games.empty:
        return pd.DataFrame(columns=cols)
    kick = games["start_date"] if "start_date" in games.columns else games["event_time"]
    left = pd.DataFrame(
        {
            "game_id": games["game_id"].astype(int).to_numpy(),
            "season": games["season"].astype(int).to_numpy(),
            "home_team_id": games["home_team_id"].astype(int).to_numpy(),
            "away_team_id": games["away_team_id"].astype(int).to_numpy(),
            "kickoff": pd.to_datetime(kick, utc=True).to_numpy(),
        }
    )
    if rankings is None or rankings.empty:
        left["home_ap_rank"] = float("nan")
        left["away_ap_rank"] = float("nan")
        left["ap_poll_event_time"] = pd.NaT
        return left[cols]
    polls = rankings[["season", "event_time"]].drop_duplicates().copy()
    polls["event_time"] = pd.to_datetime(polls["event_time"], utc=True)
    polls = polls.sort_values(["season", "event_time"], kind="mergesort")
    left = left.sort_values(["season", "kickoff"], kind="mergesort")
    merged = pd.merge_asof(
        left,
        polls.rename(columns={"event_time": "poll_time"}),
        left_on="kickoff",
        right_on="poll_time",
        by="season",
        allow_exact_matches=False,
        direction="backward",
    )
    rank_tbl = rankings.copy()
    rank_tbl["event_time"] = pd.to_datetime(rank_tbl["event_time"], utc=True)
    rank_tbl["team_id"] = rank_tbl["team_id"].astype(int)
    rank_tbl["rank"] = pd.to_numeric(rank_tbl["rank"], errors="coerce")
    lookup: dict[tuple[pd.Timestamp, int], float] = {}
    for et, tid, rank in zip(
        rank_tbl["event_time"], rank_tbl["team_id"], rank_tbl["rank"], strict=True
    ):
        if pd.isna(et) or pd.isna(tid) or pd.isna(rank):
            continue
        lookup[(pd.Timestamp(et), int(tid))] = float(rank)

    def _lookup(poll_time: Any, team_id: Any) -> float:
        if pd.isna(poll_time) or pd.isna(team_id):
            return float("nan")
        return lookup.get((pd.Timestamp(poll_time), int(team_id)), float("nan"))

    merged["home_ap_rank"] = [
        _lookup(pt, tid)
        for pt, tid in zip(merged["poll_time"], merged["home_team_id"], strict=True)
    ]
    merged["away_ap_rank"] = [
        _lookup(pt, tid)
        for pt, tid in zip(merged["poll_time"], merged["away_team_id"], strict=True)
    ]
    out = merged[["game_id", "home_ap_rank", "away_ap_rank", "poll_time"]].rename(
        columns={"poll_time": "ap_poll_event_time"}
    )
    out["home_ap_rank"] = pd.to_numeric(out["home_ap_rank"], errors="coerce")
    out["away_ap_rank"] = pd.to_numeric(out["away_ap_rank"], errors="coerce")
    return out.drop_duplicates("game_id")


__all__ = [
    "AP_POLL_SEASONS",
    "DEFAULT_RANKINGS_ROOT",
    "ApRankingSchema",
    "attach_ap_ranks_at_kickoff",
    "default_poll_event_time",
    "ingest_cfbd_rankings",
    "is_ap_poll",
    "load_research_rankings",
    "parse_rankings_payload",
    "school_maps",
    "validate_rankings",
    "write_research_rankings",
]
