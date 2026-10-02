"""S6-W5-CARD — candidate provider read for 2026 CFBD week-5 primary slate.

Gate order mirrors production ``apply_bet_filters`` (QB in the same
``evaluate_filters`` pass as every other §12 filter; exposure accumulates
only on full accept). Probe-only book restriction does not touch global
``odds_books`` config.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

import ncaa_quant.betting.provider as provider_mod
from ncaa_quant.betting.filters import BetCandidate, FilterReason, evaluate_filters
from ncaa_quant.betting.kelly import ExposureState, recommended_stake
from ncaa_quant.betting.provider import build_candidates_from_odds
from ncaa_quant.config import load_config
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.social.render import render_replies
from ncaa_quant.utils.timeutils import DECISION_POINT_TZ, to_utc

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = ROOT / "docs" / "notes" / "_artifacts" / "social-s6-w5-card"
SITE_URL = "https://the-cfb-model.vercel.app"
PUBLIC_MIN_EDGE_DEFAULT = 0.045
PUBLIC_MIN_EDGE_S3 = 0.05
MIN_MODEL_MARKET_AGREEMENT = 7.0
UNIT_FRACTION = 0.005
LIVE_ODDS_CREDITS = 3  # markets (3) × regions (1) per Odds API live endpoint metering
# Probe-only pricing book (do not edit configs/data.yaml odds_books).
PROBE_BOOK = "draftkings"
ET = DECISION_POINT_TZ if isinstance(DECISION_POINT_TZ, ZoneInfo) else ZoneInfo("America/New_York")

S5_DISCRIMINATION = {
    "candidate_p_win_auc": "0.493 [0.43, 0.56]",
    "top_bin": "0.508 vs 0.524 break-even",
    "accept_loop_top_k_overlap": "1.0",
    "probability_clv": "+0.0044 vs +0.0238 required",
}

EXPOSURE_REASONS: frozenset[FilterReason] = frozenset(
    {
        FilterReason.MAX_BETS_PER_WEEK,
        FilterReason.MAX_WEEKLY_EXPOSURE,
        FilterReason.MAX_TEAM_EXPOSURE,
    }
)

ALL_FILTER_REASONS: tuple[str, ...] = tuple(
    fr.value for fr in FilterReason if fr != FilterReason.PASS
)


def _load_slate() -> tuple[dict[str, Any], str]:
    latest = ROOT / "latest" / "week_predictions.json"
    pub_path = ROOT / "data" / "webapp" / "publish_history" / "2026_w5.jsonl"
    if latest.is_file():
        wp = json.loads(latest.read_text(encoding="utf-8"))
        source = "latest/week_predictions.json"
    elif pub_path.is_file():
        lines = [ln.strip() for ln in pub_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        wp = json.loads(lines[-1])
        source = (
            f"data/webapp/publish_history/2026_w5.jsonl "
            f"(record {len(lines)}, as_of={wp.get('as_of')})"
        )
    else:
        raise FileNotFoundError("no week_predictions source")

    as_of = datetime.fromisoformat(str(wp["as_of"]).replace("Z", "+00:00"))
    games = [
        g
        for g in wp.get("games", [])
        if datetime.fromisoformat(str(g["kickoff_utc"]).replace("Z", "+00:00")) > as_of
    ]
    games.sort(key=lambda g: str(g["kickoff_utc"]))
    return {**wp, "games": games}, source


def _per_filter_verdicts(
    candidate: BetCandidate,
    *,
    betting: Any,
    stake_fraction: float,
) -> dict[str, bool]:
    min_edge = (
        float(betting.min_edge_sides)
        if candidate.market == "side"
        else float(betting.min_edge_totals)
    )
    team_exp: dict[str, float] = {}
    return {
        FilterReason.EDGE_TOO_SMALL.value: candidate.edge >= min_edge,
        FilterReason.STALE_INPUTS.value: not (betting.no_bet_on_stale and candidate.is_stale),
        FilterReason.QB_STATUS_UNKNOWN.value: not (
            betting.no_bet_on_qb_unknown and not candidate.qb_status_known
        ),
        FilterReason.MODEL_MARKET_DISAGREE.value: candidate.model_market_residual_points
        <= float(betting.min_model_market_agreement),
        FilterReason.MAX_BETS_PER_WEEK.value: True,
        FilterReason.MAX_WEEKLY_EXPOSURE.value: stake_fraction
        <= float(betting.max_weekly_exposure),
        FilterReason.MAX_TEAM_EXPOSURE.value: all(
            stake_fraction <= float(betting.max_exposure_per_team) for _ in candidate.team_ids
        ),
        FilterReason.NON_POSITIVE_EV.value: candidate.expected_value > 0.0,
        FilterReason.SIGMA_NOT_CREDIBLE.value: FilterReason.SIGMA_NOT_CREDIBLE.value
        not in {r.value for r in candidate.block_reasons},
        FilterReason.KICKOFF_PASSED.value: FilterReason.KICKOFF_PASSED.value
        not in {r.value for r in candidate.block_reasons},
        FilterReason.NO_SNAPSHOT.value: FilterReason.NO_SNAPSHOT.value
        not in {r.value for r in candidate.block_reasons},
        FilterReason.LINE_QUARANTINED.value: FilterReason.LINE_QUARANTINED.value
        not in {r.value for r in candidate.block_reasons},
    }


def _failing_reasons_from_verdicts(per_filter: dict[str, bool]) -> list[str]:
    return [k for k in ALL_FILTER_REASONS if not per_filter.get(k, True)]


def _all_failing_reasons(
    candidate: BetCandidate,
    *,
    betting: Any,
    stake_fraction: float,
    per_filter: dict[str, bool],
) -> list[str]:
    if candidate.block_reasons:
        base = [str(r) for r in candidate.block_reasons]
    else:
        base = []
    fr = evaluate_filters(candidate, betting, proposed_stake_fraction=stake_fraction)
    merged = list(
        dict.fromkeys(
            [
                *base,
                *[str(r) for r in fr.reasons if r != FilterReason.PASS],
                *_failing_reasons_from_verdicts(per_filter),
            ]
        )
    )
    return merged


def _kickoff_et(kickoff_utc: str | datetime) -> str:
    if isinstance(kickoff_utc, datetime):
        dt = kickoff_utc
    else:
        dt = datetime.fromisoformat(str(kickoff_utc).replace("Z", "+00:00"))
    return to_utc(dt).astimezone(ET).strftime("%Y-%m-%d %H:%M %Z")


def _production_accept_loop(
    rows: list[dict[str, Any]],
    betting: Any,
    *,
    public_min_edge: float,
) -> dict[str, Any]:
    """Mirror ``apply_bet_filters`` exactly (predict.py).

    QB is evaluated in the same ``evaluate_filters`` pass as every other
    filter. Exposure accumulates only when ``result.accepted`` (empty
    non-PASS reasons — i.e. empty non-exposure *and* exposure reasons).
    """
    usable = [r for r in rows if "_candidate" in r]
    ordered = sorted(usable, key=lambda r: float(r["edge"]), reverse=True)
    accepted_rows: list[dict[str, Any]] = []
    rejected_rows: list[dict[str, Any]] = []
    exposure = ExposureState()
    bets_this_week = 0
    loop_trace: list[dict[str, Any]] = []

    for row in ordered:
        cand: BetCandidate = row["_candidate"]
        if cand.p_win is not None and cand.american_odds is not None:
            stake = recommended_stake(
                float(cand.p_win),
                float(cand.american_odds),
                bankroll=1.0,
                config=betting,
                weekly_exposure_so_far=0.0,
                team_exposure_so_far=0.0,
            )
            proposed = float(stake.stake_fraction)
        else:
            proposed = 0.0

        result = evaluate_filters(
            cand,
            betting,
            bets_this_week=bets_this_week,
            weekly_exposure_so_far=float(exposure.weekly_total),
            team_exposure_so_far=dict(exposure.per_team or {}),
            proposed_stake_fraction=proposed,
        )
        reasons = tuple(r for r in result.reasons if r != FilterReason.PASS)
        non_exposure = tuple(r for r in reasons if r not in EXPOSURE_REASONS)
        annotated = {
            **{k: v for k, v in row.items() if k != "_candidate"},
            "accept_loop_proposed_stake": proposed,
            "accept_loop_reasons": [str(r) for r in reasons],
            "accept_loop_non_exposure_reasons": [str(r) for r in non_exposure],
            "accept_loop_accepted": bool(result.accepted),
            "_candidate": cand,
        }
        loop_trace.append(
            {
                "game_id": row["game_id"],
                "edge": row.get("edge"),
                "proposed": proposed,
                "accepted": bool(result.accepted),
                "reasons": [str(r) for r in reasons],
                "bets_this_week_before": bets_this_week,
                "weekly_exposure_before": float(exposure.weekly_total),
            }
        )
        if result.accepted:
            accepted_rows.append(annotated)
            bets_this_week += 1
            if proposed > 0.0 and cand.team_ids:
                exposure = exposure.with_bet(cand.team_ids, proposed)
            elif proposed > 0.0:
                exposure = ExposureState(
                    weekly_total=float(exposure.weekly_total + proposed),
                    per_team=dict(exposure.per_team or {}),
                )
        else:
            rejected_rows.append(annotated)

    # PRE-QB: every §12 gate clear except qb_status_unknown, at public edge floor.
    # With production ordering these also cleared exposure at evaluation time
    # (otherwise exposure reasons would appear alongside QB).
    pre_qb: list[dict[str, Any]] = []
    for row in rejected_rows:
        reasons = set(row.get("accept_loop_reasons") or [])
        if reasons != {FilterReason.QB_STATUS_UNKNOWN.value}:
            continue
        if float(row.get("edge") or 0) < float(public_min_edge):
            continue
        if row.get("book") and str(row["book"]) != PROBE_BOOK:
            continue
        pre_qb.append(row)

    qb_known_pass = [
        r
        for r in accepted_rows
        if float(r.get("edge") or 0) >= float(public_min_edge)
        and (not r.get("book") or str(r["book"]) == PROBE_BOOK)
    ]

    qb_worklist = []
    for r in pre_qb:
        away, _, home = str(r["matchup"]).partition(" @ ")
        qb_worklist.append(
            {
                "game_id": r["game_id"],
                "matchup": r["matchup"],
                "home_team": home,
                "away_team": away,
                "side": r.get("side"),
            }
        )

    return {
        "mirror": "apply_bet_filters",
        "probe_book": PROBE_BOOK,
        "public_min_edge": public_min_edge,
        "accepted_count": len(accepted_rows),
        "rejected_count": len(rejected_rows),
        "pre_qb_survivor_count": len(pre_qb),
        "qb_known_pass_count": len(qb_known_pass),
        "final_weekly_exposure": float(exposure.weekly_total),
        "final_bets_this_week": bets_this_week,
        "qb_worklist": qb_worklist,
        "pre_qb_survivors": [_survivor_public_row(r) for r in pre_qb],
        "qb_known_passing": [_survivor_public_row(r) for r in qb_known_pass],
        "accepted_game_ids": [r["game_id"] for r in accepted_rows],
        "loop_trace": loop_trace,
    }


def _survivor_public_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "game_id": row["game_id"],
        "matchup": row.get("matchup"),
        "kickoff_et": _kickoff_et(str(row.get("kickoff_utc", ""))),
        "kickoff_utc": row.get("kickoff_utc"),
        "side": row.get("side"),
        "line": row.get("shopped_line"),
        "price": row.get("price"),
        "book": row.get("book"),
        "snapshot_event_time": row.get("snapshot_event_time"),
        "ladder_rung": row.get("ladder_rung"),
        "model_line": row.get("model_line"),
        "residual": row.get("model_market_residual_points"),
        "edge": row.get("edge"),
        "units": row.get("units"),
        "qb_status_known": row.get("qb_status_known"),
        "accept_loop_reasons": row.get("accept_loop_reasons"),
    }


def _install_probe_book_filter(book: str) -> tuple[Any, Any]:
    """Restrict provider shopping to one book inside this probe only."""
    orig_spread = provider_mod._book_two_way_spread
    orig_total = provider_mod._book_two_way_total

    def _spread(window: pd.DataFrame, home_side: str) -> list[tuple[str, float, float, float]]:
        return [b for b in orig_spread(window, home_side=home_side) if b[0] == book]

    def _total(window: pd.DataFrame) -> list[tuple[str, float, float, float]]:
        return [b for b in orig_total(window) if b[0] == book]

    provider_mod._book_two_way_spread = _spread  # type: ignore[assignment]
    provider_mod._book_two_way_total = _total  # type: ignore[assignment]
    return orig_spread, orig_total


def _game_forecast_row(game: dict[str, Any]) -> dict[str, Any]:
    mu = game.get("mu_margin")
    lo, hi = game.get("margin_interval_lo"), game.get("margin_interval_hi")
    suppressed = game.get("mu_margin") is None or game.get("sigma_margin_credible") is False
    home = game.get("home_team", "?")
    away = game.get("away_team", "?")
    fav = home if mu is not None and float(mu) >= 0 else away
    half_width_sigma = None
    if mu is not None and lo is not None and hi is not None and game.get("sigma_margin"):
        sig = float(game["sigma_margin"])
        if sig > 0:
            half_width_sigma = round((float(hi) - float(lo)) / (2.0 * sig), 3)
    interval_lo_int = round(float(lo)) if lo is not None else None
    interval_hi_int = round(float(hi)) if hi is not None else None
    return {
        "game_id": str(game["game_id"]),
        "matchup": f"{away} @ {home}",
        "kickoff_utc": game["kickoff_utc"],
        "mu_margin": mu,
        "sigma_margin": game.get("sigma_margin"),
        "interval_80_lo": interval_lo_int,
        "interval_80_hi": interval_hi_int,
        "interval_80_lo_raw": lo,
        "interval_80_hi_raw": hi,
        "half_width_over_sigma": half_width_sigma,
        "conviction_tier": game.get("conviction_tier"),
        "p_favored": (game.get("conviction_basis") or {}).get(
            "p_favored", game.get("p_win_home")
        ),
        "coherence_suppressed": suppressed,
        "favored_team": fav,
    }


def _qb_coverage(
    games: list[dict[str, Any]],
    qb_frame: pd.DataFrame,
    as_of: datetime,
) -> dict[str, Any]:
    zero = one = two = 0
    detail: list[dict[str, Any]] = []
    for game in games:
        gid = int(game["game_id"])
        resolved = 0
        teams: list[dict[str, Any]] = []
        for tid, team in (
            (int(game["home_team_id"]), game["home_team"]),
            (int(game["away_team_id"]), game["away_team"]),
        ):
            sub = qb_frame.loc[
                (qb_frame["game_id"].astype("Int64") == gid)
                & (qb_frame["team_id"].astype(int) == tid)
            ]
            status = None
            if not sub.empty:
                sub = sub.copy()
                sub["event_time"] = pd.to_datetime(sub["event_time"], utc=True)
                sub = sub.loc[sub["event_time"] <= pd.Timestamp(as_of)]
                if not sub.empty:
                    latest = sub.sort_values("event_time").iloc[-1]
                    status = str(latest["status"])
                    if status != "unknown":
                        resolved += 1
            teams.append({"team": team, "team_id": tid, "status": status})
        if resolved == 0:
            zero += 1
        elif resolved == 1:
            one += 1
        else:
            two += 1
        detail.append({"game_id": str(gid), "resolved_count": resolved, "teams": teams})
    return {
        "zero_resolved": zero,
        "one_resolved": one,
        "two_resolved": two,
        "rows": detail,
    }


def _crosswalk_summary(store: ParquetStore, season: int, ingest_ingested_at: datetime) -> dict[str, Any]:
    """Summarize the most recent live-ingest crosswalk rows."""
    xw = store.read("odds_cfbd_game_crosswalk", filters={"season": int(season)})
    if xw.empty:
        return {
            "events_in_pull": 0,
            "matched": 0,
            "unmatched": 0,
            "match_rate": None,
            "unmatched_events": [],
        }
    xw = xw.copy()
    xw["ingested_at"] = pd.to_datetime(xw["ingested_at"], utc=True)
    ingest_ts = pd.Timestamp(to_utc(ingest_ingested_at))
    recent = xw.loc[xw["ingested_at"] >= ingest_ts - pd.Timedelta(seconds=30)]
    if recent.empty:
        recent = xw.sort_values("ingested_at").groupby("odds_event_id", sort=False).tail(1)
    matched = int((recent["match_status"] == "matched").sum()) if "match_status" in recent.columns else 0
    unmatched = int((recent["match_status"] != "matched").sum()) if "match_status" in recent.columns else 0
    total = len(recent)
    unmatched_events = []
    if "match_status" in recent.columns:
        bad = recent.loc[recent["match_status"] != "matched"]
        for _, row in bad.iterrows():
            unmatched_events.append(
                {
                    "odds_event_id": str(row.get("odds_event_id", "")),
                    "home_team": str(row.get("home_team", "")),
                    "away_team": str(row.get("away_team", "")),
                    "match_status": str(row.get("match_status", "")),
                }
            )
    return {
        "events_in_pull": total,
        "matched": matched,
        "unmatched": unmatched,
        "match_rate": round(matched / total, 4) if total else None,
        "unmatched_events": unmatched_events,
    }


def _fcs_opponent_ids(store: ParquetStore, season: int) -> set[int]:
    teams = store.read("teams", filters={"season": int(season)})
    if teams.empty or "classification" not in teams.columns:
        return set()
    return set(teams.loc[teams["classification"].astype(str) == "fcs", "team_id"].astype(int))


def _edge_distribution(rows: list[dict[str, Any]], game_lookup: dict[int, dict[str, Any]], fcs_ids: set[int]) -> dict[str, Any]:
    edges = sorted(float(r.get("edge", 0)) for r in rows)
    all_ge_015 = [r["game_id"] for r in rows if float(r.get("edge", 0)) >= 0.15]
    fcs_edges: list[float] = []
    fcs_rows: list[dict[str, Any]] = []
    for r in rows:
        gid = int(r["game_id"])
        game = game_lookup.get(gid, {})
        hi = int(game.get("home_team_id", 0))
        ai = int(game.get("away_team_id", 0))
        if hi in fcs_ids or ai in fcs_ids:
            e = float(r.get("edge", 0))
            fcs_edges.append(e)
            fcs_rows.append({"game_id": r["game_id"], "matchup": r.get("matchup", ""), "edge": e})
    fcs_sorted = sorted(fcs_edges)
    return {
        "min": min(edges) if edges else None,
        "max": max(edges) if edges else None,
        "values": edges,
        "near_bar_0_045": [
            r["game_id"] for r in rows if 0.04 <= float(r.get("edge", 0)) <= 0.05
        ],
        "regime_ge_0_15": all_ge_015,
        "fcs_opponent_games": len(fcs_rows),
        "fcs_edge_min": min(fcs_sorted) if fcs_sorted else None,
        "fcs_edge_max": max(fcs_sorted) if fcs_sorted else None,
        "fcs_regime_ge_0_15": sum(1 for e in fcs_edges if e >= 0.15),
        "fcs_top_edges": sorted(fcs_rows, key=lambda x: float(x["edge"]), reverse=True)[:10],
    }


def _odds_snapshot_summary(
    games: list[dict[str, Any]],
    as_of: datetime,
    betting: Any,
) -> dict[str, Any]:
    parts = sorted((ROOT / "data" / "staged" / "odds_snapshots").glob("season=2026/**/part.parquet"))
    odds_frames = [pd.read_parquet(p) for p in parts]
    odds = pd.concat(odds_frames, ignore_index=True)
    odds["event_time"] = pd.to_datetime(odds["event_time"], utc=True)
    odds["ingested_at"] = pd.to_datetime(odds["ingested_at"], utc=True)
    newest_et = odds["event_time"].max()
    newest_dt = newest_et.to_pydatetime()
    newest_ingested = odds.loc[odds["event_time"] == newest_et, "ingested_at"].max()
    newest_ingested_dt = newest_ingested.to_pydatetime()
    age_h = (as_of - newest_dt).total_seconds() / 3600.0
    max_age = float(betting.odds_max_age_hours)
    stale_wall = newest_dt + timedelta(hours=max_age)

    gids = {int(g["game_id"]) for g in games}
    odds["game_id"] = odds["game_id"].astype("Int64")
    newest_slice = odds.loc[odds["event_time"] == newest_et]
    slate_spread = newest_slice[
        (newest_slice["game_id"].isin(list(gids))) & (newest_slice["market"] == "spread")
    ]
    books_per = (
        slate_spread.groupby("game_id")["book"].nunique().to_dict() if not slate_spread.empty else {}
    )
    coverage_rows: list[dict[str, Any]] = []
    no_snapshot_games: list[dict[str, Any]] = []
    for g in games:
        gid = int(g["game_id"])
        n_books = int(books_per.get(gid, 0))
        matchup = f"{g['away_team']} @ {g['home_team']}"
        coverage_rows.append(
            {
                "game_id": str(gid),
                "matchup": matchup,
                "books_with_spread": n_books,
            }
        )
        if n_books == 0:
            no_snapshot_games.append({"game_id": str(gid), "matchup": matchup})
    book_counts = list(books_per.values())
    return {
        "newest_event_time": newest_dt.isoformat(),
        "newest_ingested_at": newest_ingested_dt.isoformat(),
        "age_hours_at_as_of": round(age_h, 3),
        "odds_max_age_hours": max_age,
        "stale_at_as_of": age_h > max_age,
        "stale_wall_clock_utc": stale_wall.isoformat(),
        "games_with_spread_odds": len(books_per),
        "games_without_spread_odds": len(gids) - len(books_per),
        "books_per_game_min": min(book_counts) if book_counts else 0,
        "books_per_game_median": float(pd.Series(book_counts).median()) if book_counts else 0.0,
        "books_per_game_max": max(book_counts) if book_counts else 0,
        "no_snapshot_games": no_snapshot_games,
        "book_coverage": coverage_rows,
        "live_pull_credit_cost": LIVE_ODDS_CREDITS,
        "live_pull_credit_note": "markets (h2h,spreads,totals) × regions (us) per Odds API live endpoint",
    }


def run_analysis(*, as_of: datetime | None = None) -> dict[str, Any]:
    analysis_as_of = as_of or datetime.now(tz=UTC)
    wp, wp_source = _load_slate()
    games = wp["games"]
    publish_as_of = datetime.fromisoformat(str(wp["as_of"]).replace("Z", "+00:00"))
    cfg = load_config()
    betting = cfg.betting
    public_min_edge = float(cfg.social.public_min_edge_sides)

    orig_spread, orig_total = _install_probe_book_filter(PROBE_BOOK)
    try:
        with ParquetStore(cfg.paths.staged_dir) as store:
            qb_frame = store.read("qb_status", filters={"season": 2026})
            season = int(wp.get("season", 2026))
            fcs_ids = _fcs_opponent_ids(store, season)
            candidates, details = build_candidates_from_odds(
                games,
                season=season,
                week=int(wp.get("week", 4)),
                as_of=analysis_as_of,
                store=store,
                config=cfg,
                n_draws=20_000,
                seed=42,
            )
    finally:
        provider_mod._book_two_way_spread = orig_spread  # type: ignore[assignment]
        provider_mod._book_two_way_total = orig_total  # type: ignore[assignment]

    odds_summary = _odds_snapshot_summary(games, analysis_as_of, betting)
    ingest_ingested = datetime.fromisoformat(
        str(odds_summary["newest_ingested_at"]).replace("Z", "+00:00")
    )
    with ParquetStore(cfg.paths.staged_dir) as store:
        crosswalk_summary = _crosswalk_summary(store, season, ingest_ingested)
    qb_summary = _qb_coverage(games, qb_frame, analysis_as_of)

    rows: list[dict[str, Any]] = []
    rejected_for_replies: list[dict[str, Any]] = []

    for game in games:
        gid = str(game["game_id"])
        key = f"{gid}:side"
        cand = next((c for c in candidates if c.game_id == gid and c.market == "side"), None)
        det = details.get(key, {})
        if cand is None:
            rows.append(
                {
                    "game_id": gid,
                    "matchup": f"{game['away_team']} @ {game['home_team']}",
                    "error": "no candidate constructed",
                }
            )
            continue

        stake = recommended_stake(
            float(cand.p_win or 0),
            float(cand.american_odds or -110),
            1.0,
            betting,
            weekly_exposure_so_far=0.0,
            team_exposure_so_far=0.0,
        )
        units = round(stake.stake_fraction / UNIT_FRACTION, 1) if stake.stake_fraction else 0.0
        per_filter = _per_filter_verdicts(
            cand, betting=betting, stake_fraction=stake.stake_fraction
        )
        all_reasons = _all_failing_reasons(
            cand,
            betting=betting,
            stake_fraction=stake.stake_fraction,
            per_filter=per_filter,
        )
        residual = round(float(cand.model_market_residual_points), 2)
        near_flip = abs(residual - MIN_MODEL_MARKET_AGREEMENT) <= 1.0

        snap_et = det.get("snapshot_event_time")
        snap_age_game_h = None
        if snap_et:
            snap_dt = datetime.fromisoformat(str(snap_et).replace("Z", "+00:00"))
            snap_age_game_h = round((analysis_as_of - snap_dt).total_seconds() / 3600.0, 3)

        side_team = det.get("side_team", "")
        matchup = f"{game['away_team']} @ {game['home_team']}"
        shopped_line = det.get("market_line")
        if det.get("refusal_only"):
            model_line = det.get("model_line")
        else:
            model_line = det.get("model_line")
            if model_line is None and det.get("model_line_home") is not None:
                bet_on = det.get("bet_on", "home")
                ml_home = float(det["model_line_home"])
                model_line = ml_home if bet_on == "home" else -ml_home

        row = {
            "game_id": gid,
            "matchup": matchup,
            "side": side_team,
            "kickoff_utc": game["kickoff_utc"],
            "kickoff_et": _kickoff_et(str(game["kickoff_utc"])),
            "book": det.get("book", ""),
            "shopped_line": shopped_line,
            "price": det.get("american_odds"),
            "snapshot_event_time": snap_et,
            "ladder_rung": det.get("ladder_rung", ""),
            "model_line": model_line,
            "mu_margin": game["mu_margin"],
            "sigma_margin": game["sigma_margin"],
            "sigma_margin_credible": game["sigma_margin_credible"],
            "p_model": det.get("p_win"),
            "p_market": det.get("p_market"),
            "edge": round(float(cand.edge), 4),
            "expected_value": round(float(cand.expected_value), 4),
            "model_market_residual_points": residual,
            "residual_passes_agreement": residual <= MIN_MODEL_MARKET_AGREEMENT,
            "near_agreement_flip": near_flip,
            "is_stale": cand.is_stale,
            "snapshot_age_hours": snap_age_game_h,
            "qb_status_known": cand.qb_status_known,
            "qb_status_source": det.get("qb_status_source"),
            "stake_fraction": stake.stake_fraction,
            "units": units,
            "per_filter_pass": per_filter,
            "filter_reasons_firing": all_reasons,
            "block_reasons": [str(r) for r in cand.block_reasons],
            "refusal_only": bool(det.get("refusal_only")),
            "s5_discrimination_overlay": S5_DISCRIMINATION,
            "_candidate": cand,
        }
        rows.append(row)
        rejected_for_replies.append({"game_id": gid, "reasons": all_reasons})

    gate = _production_accept_loop(rows, betting, public_min_edge=public_min_edge)

    def public_survivors(thr: float) -> list[str]:
        return [
            r["game_id"]
            for r in rows
            if r.get("edge", 0) >= thr and not r.get("filter_reasons_firing")
        ]

    public_045_ids = [
        r["game_id"] for r in rows if float(r.get("edge", 0)) >= PUBLIC_MIN_EDGE_DEFAULT
    ]
    public_050_ids = [
        r["game_id"] for r in rows if float(r.get("edge", 0)) >= PUBLIC_MIN_EDGE_S3
    ]

    game_lookup = {int(g["game_id"]): g for g in games}
    edge_distribution = _edge_distribution(rows, game_lookup, fcs_ids)
    forecast_rows = [_game_forecast_row(g) for g in games]
    replies_md = render_replies(games, [], rejected_for_replies, SITE_URL)

    any_clear_all_filters = any(not r.get("filter_reasons_firing") for r in rows)
    pre_qb_n = int(gate["pre_qb_survivor_count"])
    qb_known_n = int(gate["qb_known_pass_count"])
    if qb_known_n > 0 or any_clear_all_filters:
        verdict = (
            "NOT MEASURED — no ATS discrimination on the 314-ticket population (S5); "
            "every candidate clearing filters is still a draw from a ranking with AUC "
            "0.493 and no information."
        )
    elif pre_qb_n > 0:
        verdict = (
            f"NOT MEASURED — {pre_qb_n} games survive the production accept loop except "
            "qb_status_unknown; S5 overlay (AUC 0.493) applies to any hypothetical "
            "survivor."
        )
    else:
        verdict = (
            "NOT MEASURED — load-bearing gates (stale inputs, missing odds, disagreement, "
            "exposure, QB unknown) block the slate before discrimination matters."
        )

    kickoffs = [
        datetime.fromisoformat(str(g["kickoff_utc"]).replace("Z", "+00:00")) for g in games
    ]

    # Strip non-serializable candidate objects for JSON
    serial_rows = []
    for r in rows:
        out = {k: v for k, v in r.items() if k != "_candidate"}
        serial_rows.append(out)

    set_qb_commands: list[dict[str, Any]] = []
    for item in gate["qb_worklist"]:
        gid = item["game_id"]
        set_qb_commands.append(
            {
                "game_id": gid,
                "matchup": item["matchup"],
                "commands": [
                    (
                        f'uv run ncaa-quant roster set-qb --game {gid} '
                        f'--team "{item["away_team"]}" --status ___'
                    ),
                    (
                        f'uv run ncaa-quant roster set-qb --game {gid} '
                        f'--team "{item["home_team"]}" --status ___'
                    ),
                ],
            }
        )

    return {
        "analysis_as_of": analysis_as_of.isoformat(),
        "publish_as_of": publish_as_of.isoformat(),
        "week_predictions_source": wp_source,
        "season": wp.get("season"),
        "week": wp.get("week"),
        "slate_count": len(games),
        "probe_book": PROBE_BOOK,
        "kickoff_range": {
            "min": min(kickoffs).isoformat() if kickoffs else None,
            "max": max(kickoffs).isoformat() if kickoffs else None,
        },
        "adr_0017_label": "CFBD week 5 primary",
        "stale_inputs_directional_only": odds_summary["stale_at_as_of"],
        "odds_summary": odds_summary,
        "crosswalk_summary": crosswalk_summary,
        "qb_summary": qb_summary,
        "gate_steps_probe_only": False,
        "gate_mirrors_apply_bet_filters": True,
        "gate": gate,
        "set_qb_commands": set_qb_commands,
        "betting_rows": serial_rows,
        "public_min_edge_0_045_edge_only": public_045_ids,
        "public_min_edge_0_05_edge_only": public_050_ids,
        "public_min_edge_0_045_all_filters": public_survivors(PUBLIC_MIN_EDGE_DEFAULT),
        "public_min_edge_0_05_all_filters": public_survivors(PUBLIC_MIN_EDGE_S3),
        "edge_distribution": edge_distribution,
        "forecast_rows": forecast_rows,
        "replies_w5_md": replies_md,
        "site_url_used": SITE_URL,
        "s5_discrimination_overlay": S5_DISCRIMINATION,
        "verdict": verdict,
    }


def _write_phase1_matrix_md(rows: list[dict[str, Any]], path: Path) -> None:
    lines = [
        "# Phase 1 matrix — all games (per-filter verdicts independent)",
        "",
        "| game_id | matchup | side | book | line | px | μ | σ | p_mod | p_mkt | edge | EV | residual | stake | u | reasons |",
        "|--------:|---------|------|------|-----:|---:|--:|--:|------:|------:|-----:|---:|---------:|------:|--:|---------|",
    ]
    for r in rows:
        reasons = ", ".join(r.get("filter_reasons_firing", []))
        lines.append(
            f"| {r['game_id']} | {r.get('matchup','')} | {r.get('side','')} | "
            f"{r.get('book','')} | {r.get('shopped_line','')} | {r.get('price','')} | "
            f"{round(float(r.get('mu_margin',0)),1)} | "
            f"{round(float(r.get('sigma_margin',0)),1)} | "
            f"{r.get('p_model','')} | {r.get('p_market','')} | "
            f"{r.get('edge','')} | {r.get('expected_value','')} | "
            f"{r.get('model_market_residual_points','')} | "
            f"{r.get('stake_fraction','')} | {r.get('units','')} | {reasons} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    payload = run_analysis()
    out_json = ARTIFACT_DIR / "analysis.json"
    out_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    (ARTIFACT_DIR / "replies_w5.md").write_text(payload["replies_w5_md"], encoding="utf-8")
    _write_phase1_matrix_md(payload["betting_rows"], ARTIFACT_DIR / "phase1_matrix.md")
    pre_qb_path = ARTIFACT_DIR / "pre_qb_survivors.md"
    lines = [
        f"# PRE-QB survivors (book={PROBE_BOOK}, public_min_edge={{:.3f}})".format(
            float(payload["gate"]["public_min_edge"])
        ),
        "",
        "| game_id | matchup | kickoff ET | side | line | price | book | snapshot_event_time | ladder_rung | model_line | residual | edge | units |",
        "|--------:|---------|------------|------|-----:|------:|------|--------------------:|------------|-----------:|---------:|-----:|------:|",
    ]
    for r in payload["gate"]["pre_qb_survivors"]:
        lines.append(
            f"| {r['game_id']} | {r['matchup']} | {r['kickoff_et']} | {r['side']} | "
            f"{r['line']} | {r['price']} | {r['book']} | {r['snapshot_event_time']} | "
            f"{r['ladder_rung']} | {r['model_line']} | {r['residual']} | {r['edge']} | "
            f"{r['units']} |"
        )
    lines.extend(["", "## set-qb commands", ""])
    for block in payload["set_qb_commands"]:
        lines.append(f"### {block['game_id']} — {block['matchup']}")
        for cmd in block["commands"]:
            lines.append(f"`{cmd}`")
        lines.append("")
    lines.extend(["## QB-known passing everything", ""])
    if not payload["gate"]["qb_known_passing"]:
        lines.append("(none)")
    else:
        for r in payload["gate"]["qb_known_passing"]:
            lines.append(
                f"- {r['game_id']} {r['matchup']} side={r['side']} "
                f"line={r['line']} edge={r['edge']} u={r['units']}"
            )
    lines.extend(["", "## Verdict", "", payload["verdict"], ""])
    pre_qb_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out_json}")
    print(f"slate={payload['slate_count']} book={PROBE_BOOK} stale={payload['stale_inputs_directional_only']}")
    print(
        "gate:",
        {
            "accepted": payload["gate"]["accepted_count"],
            "pre_qb": payload["gate"]["pre_qb_survivor_count"],
            "qb_known_pass": payload["gate"]["qb_known_pass_count"],
        },
    )
    print("verdict:", payload["verdict"])


if __name__ == "__main__":
    main()
