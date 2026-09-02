"""Bet candidate provider: predictions + staged odds → BetCandidate list (S5).

Resolves lines via the Task 16 as-of ladder (never ``event_time > as_of``),
shops books, de-vigs, and scores model cover probability with the production
MC path (:func:`~ncaa_quant.distribution.simulate.spread_cover_probs` /
:func:`~ncaa_quant.distribution.simulate.total_probs`) at the shopped line.

Totals are implemented and gated by ``BettingConfig.candidate_markets``
(default ``[\"side\"]`` only).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
import structlog

from ncaa_quant.betting.edges import BookPrice, compute_edge
from ncaa_quant.betting.filters import BetCandidate, FilterReason, MarketKind
from ncaa_quant.config import AppConfig, BettingConfig, load_config
from ncaa_quant.distribution.bivariate import assemble_bivariate
from ncaa_quant.distribution.key_numbers import KeyNumberKernel
from ncaa_quant.distribution.simulate import (
    DEFAULT_N_DRAWS,
    JointDraws,
    sample_joint,
    spread_cover_probs,
    total_probs,
    two_way_side_prob,
)
from ncaa_quant.evaluation.walkforward import WalkForwardConfig
from ncaa_quant.features.market_lines import filter_home_side_spreads
from ncaa_quant.utils.timeutils import assert_tz_aware, to_utc

log = structlog.get_logger(__name__)

QbStatusSource = Literal["staged_asof", "unchecked", "check_disabled"]

_ODDS_QUARANTINE_TABLE = "odds_snapshots_quarantine"
_DEFAULT_KERNEL = KeyNumberKernel(offset_weights={}, n=0)


def _float_or_nan(value: Any) -> float:
    if value is None:
        return float("nan")
    try:
        if pd.isna(value):
            return float("nan")
    except (TypeError, ValueError):
        pass
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float("nan")
    if math.isnan(out) or math.isinf(out):
        return float("nan")
    return out


def _sigma_margin_credible(row: Mapping[str, Any]) -> bool:
    """ADR 0014 σ refusal — mirrors webapp.export.sigma_margin_credible."""
    sigma_missing = row.get("sigma_m_is_missing", row.get("sigma_margin_is_missing"))
    null_reason = row.get("null_reason")
    if sigma_missing is True:
        return False
    if null_reason is not None and null_reason != "":
        return False
    sig = _float_or_nan(row.get("sigma_margin", row.get("sigma_m")))
    return bool(np.isfinite(sig) and sig > 0)


def _sigma_total_credible(row: Mapping[str, Any]) -> bool:
    """ADR 0014 σ_total refusal — mirrors webapp.export.sigma_total_credible."""
    sigma_missing = row.get("sigma_t_is_missing", row.get("sigma_total_is_missing"))
    null_reason = row.get("null_reason")
    if sigma_missing is True:
        return False
    if null_reason is not None and null_reason != "":
        return False
    sig = _float_or_nan(row.get("sigma_total", row.get("sigma_t")))
    return bool(np.isfinite(sig) and sig > 0)


class ProviderError(ValueError):
    """Invalid provider inputs."""


def merge_stale_onto_prediction_rows(
    prediction_rows: Sequence[Mapping[str, Any]],
    stamped_predictions: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Index-pair production rows with stamp-only stale fields (export.py ~1394–1398)."""
    from ncaa_quant.webapp.export import merge_prediction_rows

    out: list[dict[str, Any]] = []
    for idx, stamped in enumerate(stamped_predictions):
        prod = prediction_rows[idx] if idx < len(prediction_rows) else None
        out.append(merge_prediction_rows(stamped, prod))
    if len(prediction_rows) > len(stamped_predictions):
        for prod in prediction_rows[len(stamped_predictions) :]:
            out.append(dict(prod))
    return out


def _walkforward_config(app: AppConfig) -> WalkForwardConfig:
    return WalkForwardConfig(
        snapshot_tolerance_minutes_pre_2022_09=int(
            app.data.odds_asof_tolerance_minutes_pre_2022_09
        ),
        snapshot_tolerance_minutes_post_2022_09=int(
            app.data.odds_asof_tolerance_minutes_post_2022_09
        ),
    )


def _snapshot_tolerance_minutes(as_of: datetime, config: WalkForwardConfig) -> int:
    cutoff = datetime(2022, 9, 1, tzinfo=UTC)
    if to_utc(as_of) < cutoff:
        return int(config.snapshot_tolerance_minutes_pre_2022_09)
    return int(config.snapshot_tolerance_minutes_post_2022_09)


def resolve_asof_snapshot_window(
    snapshots: pd.DataFrame,
    *,
    game_id: int,
    bound: datetime,
    kickoff: datetime | None,
    config: WalkForwardConfig,
) -> tuple[pd.DataFrame, str]:
    """Latest snapshot rows at or before ``bound`` (and before kickoff).

    Ladder (ADR 0002 Part C / Task 16): decision-point window → nearest earlier
    within tolerance (``odds_api_snapshot_fallback``) → empty + ``null``.
    Never returns rows with ``event_time > bound``.
    """
    assert_tz_aware(bound)
    bound_utc = to_utc(bound)
    if snapshots.empty or "event_time" not in snapshots.columns:
        return snapshots.iloc[0:0].copy(), "null"
    if "game_id" not in snapshots.columns:
        return snapshots.iloc[0:0].copy(), "null"

    id_mask = snapshots["game_id"].notna() & (snapshots["game_id"].astype("Int64") == int(game_id))
    base = snapshots.loc[id_mask]
    if base.empty:
        return base.copy(), "null"

    work = base.copy()
    work["event_time"] = pd.to_datetime(work["event_time"], utc=True)
    bound_ts = pd.Timestamp(bound_utc)
    mask = work["event_time"] <= bound_ts
    if kickoff is not None:
        mask = mask & (work["event_time"] < pd.Timestamp(to_utc(kickoff)))
    eligible = work.loc[mask]
    if eligible.empty:
        return eligible.copy(), "null"

    tol = timedelta(minutes=_snapshot_tolerance_minutes(bound_utc, config))
    latest_ts = eligible["event_time"].max()
    source = "odds_api_snapshot_fallback" if latest_ts < bound_ts - tol else "odds_api_snapshot"
    window = eligible.loc[eligible["event_time"] == latest_ts].copy()
    return window, source


def _parse_kickoff(
    row: Mapping[str, Any], games_by_id: Mapping[int, Mapping[str, Any]]
) -> datetime | None:
    gid_raw = row.get("game_id")
    if gid_raw is None:
        return None
    try:
        gid = int(gid_raw)
    except (TypeError, ValueError):
        return None
    game = games_by_id.get(gid)
    raw = None
    if game is not None:
        raw = game.get("start_date") or game.get("event_time")
    if raw is None:
        raw = row.get("kickoff_utc") or row.get("start_date")
    if raw is None:
        return None
    ts = pd.to_datetime(raw, utc=True)
    return to_utc(ts.to_pydatetime())


def _game_teams(
    row: Mapping[str, Any],
    games_by_id: Mapping[int, Mapping[str, Any]],
    teams_by_id: Mapping[int, str],
) -> tuple[str, str, int | None, int | None]:
    gid = int(row["game_id"])
    game = games_by_id.get(gid, {})
    home_id = game.get("home_team_id")
    away_id = game.get("away_team_id")
    if home_id is None:
        home_id = row.get("home_team_id")
    if away_id is None:
        away_id = row.get("away_team_id")
    home_name = str(
        row.get("home_team")
        or (teams_by_id.get(int(home_id)) if home_id is not None else "")
        or game.get("home_team")
        or ""
    )
    away_name = str(
        row.get("away_team")
        or (teams_by_id.get(int(away_id)) if away_id is not None else "")
        or game.get("away_team")
        or ""
    )
    return (
        home_name,
        away_name,
        int(home_id) if home_id is not None else None,
        int(away_id) if away_id is not None else None,
    )


def _load_games_index(store: Any, season: int, week: int) -> dict[int, dict[str, Any]]:
    games = store.read("games", filters={"season": int(season), "week": int(week)})
    if games.empty:
        games = store.read("games", filters={"season": int(season)})
        if not games.empty and "week" in games.columns:
            games = games.loc[games["week"].astype(int) == int(week)]
    out: dict[int, dict[str, Any]] = {}
    for rec in games.to_dict(orient="records"):
        out[int(rec["game_id"])] = rec
    return out


def _load_teams_index(store: Any, season: int) -> dict[int, str]:
    try:
        teams = store.read("teams", filters={"season": int(season)})
    except Exception:  # noqa: BLE001 — table may be missing in thin fixtures
        return {}
    if teams.empty:
        return {}
    name_col = "school" if "school" in teams.columns else "team"
    if name_col not in teams.columns or "team_id" not in teams.columns:
        return {}
    return {int(r.team_id): str(getattr(r, name_col)) for r in teams.itertuples(index=False)}


def _load_snapshots(store: Any, season: int, week: int) -> pd.DataFrame:
    """Load season odds snapshots (week hive tags can lag CFBD week by ±1)."""
    del week  # filter by game_id later; do not trust hive week alone
    try:
        frame = store.read("odds_snapshots", filters={"season": int(season)})
    except Exception:  # noqa: BLE001
        return pd.DataFrame()
    return frame if frame is not None else pd.DataFrame()


def _load_quarantine(store: Any, season: int, week: int) -> pd.DataFrame:
    """Load quarantine sidecars for ``week-1..week+1`` (same hive skew as snapshots)."""
    frames: list[pd.DataFrame] = []
    for w in range(max(0, int(week) - 1), int(week) + 2):
        root = (
            Path(store.root) / _ODDS_QUARANTINE_TABLE / f"season={int(season)}" / f"week={int(w)}"
        )
        path = root / "part.parquet"
        if path.is_file():
            frames.append(pd.read_parquet(path))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _market_quarantined(
    quarantine: pd.DataFrame,
    *,
    game_id: int,
    market: Literal["spread", "total"],
) -> bool:
    if quarantine.empty:
        return False
    if "game_id" not in quarantine.columns or "market" not in quarantine.columns:
        return False
    mask = (quarantine["game_id"].astype("Int64") == int(game_id)) & (
        quarantine["market"].astype(str) == market
    )
    return bool(mask.any())


def qb_status_known_for_game(
    qb_frame: pd.DataFrame,
    *,
    game_id: int,
    home_team_id: int | None,
    away_team_id: int | None,
    as_of: datetime,
) -> tuple[bool, QbStatusSource]:
    """Both teams must have a non-unknown status row with ``event_time <= as_of``."""
    assert_tz_aware(as_of)
    as_of_utc = to_utc(as_of)
    if home_team_id is None or away_team_id is None:
        return False, "unchecked"
    if qb_frame.empty:
        return False, "unchecked"
    work = qb_frame.loc[qb_frame["game_id"].astype("Int64") == int(game_id)].copy()
    if work.empty:
        return False, "unchecked"
    work["event_time"] = pd.to_datetime(work["event_time"], utc=True)
    work = work.loc[work["event_time"] <= pd.Timestamp(as_of_utc)]
    if work.empty:
        return False, "unchecked"

    def _team_ok(tid: int) -> bool:
        sub = work.loc[work["team_id"].astype(int) == int(tid)]
        if sub.empty:
            return False
        latest = sub.sort_values("event_time").iloc[-1]
        return str(latest["status"]).casefold() != "unknown"

    if _team_ok(home_team_id) and _team_ok(away_team_id):
        return True, "staged_asof"
    return False, "unchecked"


def _sample_game_draws(
    *,
    mu_m: float,
    sigma_m: float,
    mu_t: float,
    sigma_t: float,
    rho: float,
    kernel: KeyNumberKernel,
    n_draws: int,
    seed: int,
) -> JointDraws:
    params = assemble_bivariate([mu_m], [sigma_m], [mu_t], [sigma_t], rho=rho)
    return sample_joint(params, kernel=kernel, n_draws=n_draws, seed=seed)


def _book_two_way_spread(
    window: pd.DataFrame,
    *,
    home_side: str,
) -> list[tuple[str, float, float, float]]:
    """Per book: ``(book, home_line, home_american, away_american)``."""
    if window.empty or "market" not in window.columns:
        return []
    spreads = window.loc[window["market"] == "spread"]
    if spreads.empty:
        return []
    home_rows = filter_home_side_spreads(spreads, home_side)
    out: list[tuple[str, float, float, float]] = []
    if "book" not in spreads.columns:
        return out
    for book, book_df in spreads.groupby("book", sort=False):
        home = filter_home_side_spreads(book_df, home_side)
        away = book_df.loc[book_df["side"].astype(str).str.casefold() != str(home_side).casefold()]
        if home.empty or away.empty:
            continue
        h = home.iloc[0]
        a = away.iloc[0]
        line = _float_or_nan(h.get("line"))
        hp = _float_or_nan(h.get("price"))
        ap = _float_or_nan(a.get("price"))
        if not (np.isfinite(line) and np.isfinite(hp) and np.isfinite(ap)):
            continue
        out.append((str(book), float(line), float(hp), float(ap)))
    if not out and not home_rows.empty:
        # Single-book degenerate frames still usable when both sides present.
        pass
    return out


def _book_two_way_total(window: pd.DataFrame) -> list[tuple[str, float, float, float]]:
    """Per book: ``(book, line, over_american, under_american)``."""
    if window.empty or "market" not in window.columns:
        return []
    totals = window.loc[window["market"] == "total"]
    if totals.empty or "book" not in totals.columns:
        return []
    out: list[tuple[str, float, float, float]] = []
    for book, book_df in totals.groupby("book", sort=False):
        over = book_df.loc[book_df["side"].astype(str).str.casefold() == "over"]
        under = book_df.loc[book_df["side"].astype(str).str.casefold() == "under"]
        if over.empty or under.empty:
            continue
        o = over.iloc[0]
        u = under.iloc[0]
        line = _float_or_nan(o.get("line"))
        if not np.isfinite(line):
            line = _float_or_nan(u.get("line"))
        op = _float_or_nan(o.get("price"))
        up = _float_or_nan(u.get("price"))
        if not (np.isfinite(line) and np.isfinite(op) and np.isfinite(up)):
            continue
        out.append((str(book), float(line), float(op), float(up)))
    return out


def _blocked_candidate(
    *,
    game_id: str,
    market: MarketKind,
    reason: FilterReason,
    is_stale: bool,
    qb_known: bool,
    team_ids: tuple[str, ...],
) -> BetCandidate:
    return BetCandidate(
        game_id=str(game_id),
        market=market,
        edge=0.0,
        expected_value=0.0,
        is_stale=is_stale,
        qb_status_known=qb_known,
        is_bowl=False,
        model_market_residual_points=0.0,
        team_ids=team_ids,
        block_reasons=(reason,),
        p_win=None,
        american_odds=None,
    )


def _refusal_details(
    *,
    home_team: str,
    away_team: str,
    qb_status_source: QbStatusSource,
    ladder_rung: str,
) -> dict[str, Any]:
    return {
        "refusal_only": True,
        "home_team": home_team,
        "away_team": away_team,
        "qb_status_source": qb_status_source,
        "ladder_rung": ladder_rung,
        "side_team": home_team or "unknown",
        "market_line": 0.0,
        "model_line": 0.0,
        "american_odds": -110.0,
        "book": "",
        "p_win": 0.0,
        "n_books_available": 0,
        "stake_fraction": 0.0,
    }


def _append_refusal(
    candidates: list[BetCandidate],
    details: dict[str, dict[str, Any]],
    *,
    key: str,
    game_id: str,
    market: MarketKind,
    reason: FilterReason,
    is_stale: bool,
    qb_known: bool,
    team_ids: tuple[str, ...],
    home_team: str,
    away_team: str,
    qb_source: QbStatusSource,
    ladder_rung: str = "null",
) -> None:
    candidates.append(
        _blocked_candidate(
            game_id=game_id,
            market=market,
            reason=reason,
            is_stale=is_stale,
            qb_known=qb_known,
            team_ids=team_ids,
        )
    )
    details[key] = _refusal_details(
        home_team=home_team,
        away_team=away_team,
        qb_status_source=qb_source,
        ladder_rung=ladder_rung,
    )


def build_candidates_from_odds(
    prediction_rows: Sequence[Mapping[str, Any]],
    *,
    season: int,
    week: int,
    as_of: datetime,
    store: Any,
    config: AppConfig | BettingConfig | None = None,
    stamped_predictions: Sequence[Mapping[str, Any]] | None = None,
    key_kernel: KeyNumberKernel | None = None,
    n_draws: int = DEFAULT_N_DRAWS,
    seed: int = 42,
    rho: float = 0.0,
    draws: JointDraws | None = None,
) -> tuple[list[BetCandidate], dict[str, dict[str, Any]]]:
    """Build bet candidates and social orientation details from staged odds.

    Parameters
    ----------
    prediction_rows:
        Full production predict rows (not stamp-only). When
        ``stamped_predictions`` is provided, stale fields are merged by index.
    as_of:
        Decision instant (UTC). Snapshot ``event_time`` must be ``<= as_of``.
    draws:
        Optional pre-built joint draws (tests). When set, one game is assumed
        at ``game_index=0`` for every row (fixture use only).
    """
    assert_tz_aware(as_of)
    as_of_utc = to_utc(as_of)

    if isinstance(config, BettingConfig):
        app = load_config()
        betting = config
    elif config is None:
        app = load_config()
        betting = app.betting
    else:
        app = config
        betting = app.betting

    markets = list(betting.candidate_markets)
    for m in markets:
        if m not in ("side", "total"):
            raise ProviderError(f"invalid candidate market: {m!r}")

    rows = (
        merge_stale_onto_prediction_rows(prediction_rows, stamped_predictions)
        if stamped_predictions is not None
        else [dict(r) for r in prediction_rows]
    )

    wf = _walkforward_config(app)
    games_by_id = _load_games_index(store, season, week)
    teams_by_id = _load_teams_index(store, season)
    snapshots = _load_snapshots(store, season, week)
    quarantine = _load_quarantine(store, season, week)
    try:
        qb_frame = store.read("qb_status", filters={"season": int(season)})
    except Exception:  # noqa: BLE001
        qb_frame = pd.DataFrame()

    kernel = key_kernel if key_kernel is not None else _DEFAULT_KERNEL
    candidates: list[BetCandidate] = []
    details: dict[str, dict[str, Any]] = {}

    for row in rows:
        gid = str(row["game_id"])
        try:
            gid_int = int(row["game_id"])
        except (TypeError, ValueError):
            continue

        home_team, away_team, home_id, away_id = _game_teams(row, games_by_id, teams_by_id)
        team_ids: tuple[str, ...] = tuple(str(t) for t in (home_id, away_id) if t is not None)
        kickoff = _parse_kickoff(row, games_by_id)

        # A2: always resolve staged as-of truth. Missing / unknown rows → not known.
        # ``no_bet_on_qb_unknown`` only gates the §12 filter — never hardcodes known.
        qb_known, qb_source = qb_status_known_for_game(
            qb_frame,
            game_id=gid_int,
            home_team_id=home_id,
            away_team_id=away_id,
            as_of=as_of_utc,
        )
        if not betting.no_bet_on_qb_unknown:
            qb_source = "check_disabled"

        mu_m = _float_or_nan(row.get("mu_margin", row.get("pred_margin")))
        sig_m = _float_or_nan(row.get("sigma_margin", row.get("sigma_m")))
        mu_t = _float_or_nan(row.get("mu_total", row.get("pred_total")))
        sig_t = _float_or_nan(row.get("sigma_total", row.get("sigma_t")))
        if not np.isfinite(mu_t):
            mu_t = 50.0
        if not np.isfinite(sig_t) or sig_t <= 0:
            sig_t = 16.0

        row_rho = _float_or_nan(row.get("rho"))
        game_rho = float(row_rho) if np.isfinite(row_rho) else float(rho)

        max_age = timedelta(hours=float(betting.odds_max_age_hours))

        for market in markets:
            key = f"{gid}:{market}"
            odds_market: Literal["spread", "total"] = "spread" if market == "side" else "total"
            # Snapshot age is computed after ladder resolve; early refusals stay non-stale.
            is_stale = False
            snap_event_time: datetime | None = None

            def _refuse(
                reason: FilterReason,
                rung: str = "null",
                *,
                _gid: str = gid,
                _market: str = market,
                _key: str = key,
                _is_stale: bool = False,
                _qb_known: bool = qb_known,
                _team_ids: tuple[str, ...] = team_ids,
                _home: str = home_team,
                _away: str = away_team,
                _qb_src: QbStatusSource = qb_source,
            ) -> None:
                mkt: MarketKind = "side" if _market == "side" else "total"
                _append_refusal(
                    candidates,
                    details,
                    key=_key,
                    game_id=_gid,
                    market=mkt,
                    reason=reason,
                    is_stale=_is_stale,
                    qb_known=_qb_known,
                    team_ids=_team_ids,
                    home_team=_home,
                    away_team=_away,
                    qb_source=_qb_src,
                    ladder_rung=rung,
                )

            if market == "side" and not _sigma_margin_credible(row):
                _refuse(FilterReason.SIGMA_NOT_CREDIBLE)
                continue
            if market == "total" and not _sigma_total_credible(row):
                _refuse(FilterReason.SIGMA_NOT_CREDIBLE)
                continue
            if kickoff is not None and kickoff < as_of_utc:
                _refuse(FilterReason.KICKOFF_PASSED)
                continue
            if _market_quarantined(quarantine, game_id=gid_int, market=odds_market):
                _refuse(FilterReason.LINE_QUARANTINED)
                continue

            window, rung = resolve_asof_snapshot_window(
                snapshots,
                game_id=gid_int,
                bound=as_of_utc,
                kickoff=kickoff,
                config=wf,
            )
            log.info(
                "candidate_asof_ladder",
                game_id=gid,
                market=market,
                ladder_rung=rung,
                n_window=int(len(window)),
            )
            if window.empty or rung == "null":
                _refuse(FilterReason.NO_SNAPSHOT, rung=rung)
                continue

            latest_et = pd.Timestamp(window["event_time"].max()).to_pydatetime()
            snap_event_time = to_utc(latest_et)
            is_stale = (as_of_utc - snap_event_time) > max_age

            if draws is not None:
                game_draws = draws
            else:
                if not (np.isfinite(mu_m) and np.isfinite(sig_m) and sig_m > 0):
                    _refuse(FilterReason.SIGMA_NOT_CREDIBLE, rung=rung, _is_stale=is_stale)
                    continue
                game_draws = _sample_game_draws(
                    mu_m=float(mu_m),
                    sigma_m=float(sig_m),
                    mu_t=float(mu_t),
                    sigma_t=float(sig_t),
                    rho=game_rho,
                    kernel=kernel,
                    n_draws=int(n_draws),
                    seed=int(seed) + gid_int,
                )

            best: dict[str, Any] | None = None

            if market == "side":
                if not home_team:
                    _refuse(FilterReason.NO_SNAPSHOT, rung=rung, _is_stale=is_stale)
                    continue
                books = _book_two_way_spread(window, home_side=home_team)
                n_books = len({b for b, *_ in books})
                for book, home_line, home_px, away_px in books:
                    for bet_on, side_px, other_px in (
                        ("home", home_px, away_px),
                        ("away", away_px, home_px),
                    ):
                        p_model = two_way_side_prob(
                            spread_cover_probs(
                                game_draws,
                                float(home_line),
                                game_index=0,
                                side=bet_on,  # type: ignore[arg-type]
                            )
                        )
                        edge_res = compute_edge(
                            p_model,
                            [BookPrice(book, side_px, other_px)],
                        )
                        model_line_home = -float(mu_m)
                        residual = abs(model_line_home - float(home_line))
                        payload = {
                            "bet_on": bet_on,
                            "book": book,
                            "home_line": float(home_line),
                            "model_line_home": model_line_home,
                            "edge": edge_res.edge,
                            "expected_value": edge_res.expected_value,
                            "american_odds": edge_res.side_american,
                            "p_win": edge_res.p_model,
                            "p_market": edge_res.p_market,
                            "residual": residual,
                            "n_books": n_books,
                        }
                        if best is None or float(payload["edge"]) > float(best["edge"]):  # type: ignore[arg-type]
                            best = payload
            else:
                books = _book_two_way_total(window)
                n_books = len({b for b, *_ in books})
                for book, total_line, over_px, under_px in books:
                    for bet_on, side_px, other_px in (
                        ("over", over_px, under_px),
                        ("under", under_px, over_px),
                    ):
                        p_model = two_way_side_prob(
                            total_probs(
                                game_draws,
                                float(total_line),
                                game_index=0,
                                side=bet_on,  # type: ignore[arg-type]
                            )
                        )
                        edge_res = compute_edge(
                            p_model,
                            [BookPrice(book, side_px, other_px)],
                        )
                        residual = abs(float(mu_t) - float(total_line))
                        payload = {
                            "bet_on": bet_on,
                            "book": book,
                            "home_line": float(total_line),
                            "model_line_home": float(mu_t),
                            "edge": edge_res.edge,
                            "expected_value": edge_res.expected_value,
                            "american_odds": edge_res.side_american,
                            "p_win": edge_res.p_model,
                            "p_market": edge_res.p_market,
                            "residual": residual,
                            "n_books": n_books,
                        }
                        if best is None or float(payload["edge"]) > float(best["edge"]):  # type: ignore[arg-type]
                            best = payload

            if best is None:
                _refuse(FilterReason.NO_SNAPSHOT, rung=rung, _is_stale=is_stale)
                continue

            bet_on = str(best["bet_on"])
            market_line_home = float(best["home_line"])
            model_line_home = float(best["model_line_home"])
            if market == "side":
                if bet_on == "home":
                    side_team = home_team
                    market_line = market_line_home
                    model_line = model_line_home
                else:
                    side_team = away_team
                    market_line = -market_line_home
                    model_line = -model_line_home
            else:
                side_team = "Over" if bet_on == "over" else "Under"
                market_line = market_line_home
                model_line = model_line_home

            cand = BetCandidate(
                game_id=gid,
                market=market,  # type: ignore[arg-type]
                edge=float(best["edge"]),
                expected_value=float(best["expected_value"]),
                is_stale=is_stale,
                qb_status_known=qb_known,
                is_bowl=False,
                model_market_residual_points=float(best["residual"]),
                team_ids=team_ids,
                block_reasons=(),
                p_win=float(best["p_win"]),
                american_odds=float(best["american_odds"]),
            )
            candidates.append(cand)
            details[key] = {
                "side_team": side_team,
                "market_line": market_line,
                "model_line": model_line,
                "american_odds": float(best["american_odds"]),
                "book": str(best["book"]),
                "p_win": float(best["p_win"]),
                "p_market": float(best["p_market"]),
                "n_books_available": int(best["n_books"]),
                "qb_status_source": qb_source,
                "ladder_rung": rung,
                "home_team": home_team,
                "away_team": away_team,
                "bet_on": bet_on,
                "market_line_home": market_line_home,
                "model_line_home": model_line_home,
                "snapshot_event_time": snap_event_time.isoformat() if snap_event_time else None,
                "is_stale": is_stale,
            }

    return candidates, details


__all__ = [
    "ProviderError",
    "build_candidates_from_odds",
    "merge_stale_onto_prediction_rows",
    "qb_status_known_for_game",
    "resolve_asof_snapshot_window",
]
