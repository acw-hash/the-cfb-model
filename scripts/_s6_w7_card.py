"""S6-W7-CARD — candidate provider read for 2026 CFBD week-7 primary slate.

Gate order mirrors production ``apply_bet_filters`` (QB in the same
``evaluate_filters`` pass as every other §12 filter; exposure accumulates
only on full accept). Probe-only book restriction does not touch global
``odds_books`` config.

W7-5 DECISIONS (2026-10-13T14:32:17-04:00):
- HFA: NONE. Reason: W7-BT2 §4. 4.56 is the model's full home-field
  term (Enet intercept × stack weight), not an excess; measured excess
  over market is +0.89 pts (95% CI 0.63–1.15). The ROBUST rule
  (W7-2) is withdrawn.
- Card rule = W6 logic: edge ≥ 0.045, raw |resid| ≤ 7.0, snapshot ≤ 6h,
  QB starter on both sides. Max 6 plays by edge, stake 0.015.
- Best line: all regulated books, as in W7-3.
- Radar: up to 2 plays that fail only on QB, same ranking.
- Thread wording: no edge %, no "calibrated" (see W7-BT2).

W7-2 DECISIONS (WITHDRAWN by W7-5): HFA=ROBUST (eligible only if raw AND
home-μ−4.56 both clear every gate), stake=0.015 / max 6 / 10% weekly,
POST_BY=Wed Oct 14 12:00 PM ET (kickoff must be >= POST_BY+2h).

Probe-only (not production): reject backup/unknown/missing QB on either
side; price across betmgm/draftkings/fanduel/williamhill_us via production
``build_candidates_from_odds`` only (seed 42+game_id). Flip ``HFA_MODE``
to ``NONE`` only after this decision is committed (W7-5 rerun).
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
ARTIFACT_DIR = ROOT / "docs" / "notes" / "_artifacts" / "social-s6-w7-card"
SITE_URL = "https://the-cfb-model.vercel.app"
PUBLIC_MIN_EDGE_DEFAULT = 0.045
PUBLIC_MIN_EDGE_S3 = 0.05
MIN_MODEL_MARKET_AGREEMENT = 7.0
UNIT_FRACTION = 0.005
FIXED_STAKE = 0.015
MAX_BETS = 6
HFA_ROBUST_PTS = 4.56
# W7-5: ROBUST withdrawn — W6 single gate (raw pricing only).
HFA_MODE: str = "NONE"
LIVE_ODDS_CREDITS = 3  # markets (3) × regions (1) per Odds API live endpoint metering
# Probe-only pricing books (do not edit configs/data.yaml odds_books).
PROBE_BOOKS: tuple[str, ...] = ("betmgm", "draftkings", "fanduel", "williamhill_us")
ET = DECISION_POINT_TZ if isinstance(DECISION_POINT_TZ, ZoneInfo) else ZoneInfo("America/New_York")
POST_BY = datetime(2026, 10, 14, 12, 0, 0, tzinfo=ET)
POST_BY_PLUS_2H = POST_BY + timedelta(hours=2)

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
    pub_path = ROOT / "data" / "webapp" / "publish_history" / "2026_w7.jsonl"
    if latest.is_file():
        wp = json.loads(latest.read_text(encoding="utf-8"))
        source = "latest/week_predictions.json"
    elif pub_path.is_file():
        lines = [ln.strip() for ln in pub_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        wp = json.loads(lines[-1])
        source = (
            f"data/webapp/publish_history/2026_w7.jsonl "
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


def _side_is(game: dict[str, Any], side: str) -> str:
    if bool(game.get("neutral_site")):
        return "neutral"
    if side == game.get("home_team"):
        return "home"
    if side == game.get("away_team"):
        return "away"
    return "?"


def _kickoff_ok(kickoff_utc: str | datetime) -> bool:
    if isinstance(kickoff_utc, datetime):
        dt = to_utc(kickoff_utc)
    else:
        dt = to_utc(datetime.fromisoformat(str(kickoff_utc).replace("Z", "+00:00")))
    return dt >= POST_BY_PLUS_2H.astimezone(UTC)


def _hfa_adjust_games(games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reduce listed home team's μ by HFA_ROBUST_PTS (all games, neutrals included)."""
    out: list[dict[str, Any]] = []
    for g in games:
        gg = dict(g)
        if gg.get("mu_margin") is not None:
            gg["mu_margin"] = float(gg["mu_margin"]) - HFA_ROBUST_PTS
        out.append(gg)
    return out


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


def _game_has_backup_qb(
    qb_frame: pd.DataFrame,
    *,
    game_id: str,
    team_ids: tuple[str, ...],
    as_of: datetime,
) -> bool:
    """True if either side has a non-unknown ``backup`` row at ``as_of`` (probe-only)."""
    if qb_frame.empty or not team_ids:
        return False
    as_of_utc = to_utc(as_of)
    work = qb_frame.loc[qb_frame["game_id"].astype(str) == str(game_id)].copy()
    if work.empty:
        return False
    work["event_time"] = pd.to_datetime(work["event_time"], utc=True)
    work = work.loc[work["event_time"] <= pd.Timestamp(as_of_utc)]
    if work.empty:
        return False
    for tid in team_ids:
        sub = work.loc[work["team_id"].astype(int) == int(tid)]
        if sub.empty:
            continue
        latest = sub.sort_values("event_time").iloc[-1]
        if str(latest["status"]).casefold() == "backup":
            return True
    return False


def _production_accept_loop(
    rows: list[dict[str, Any]],
    betting: Any,
    *,
    public_min_edge: float,
    qb_frame: pd.DataFrame | None = None,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Mirror ``apply_bet_filters`` exactly (predict.py).

    QB is evaluated in the same ``evaluate_filters`` pass as every other
    filter. Exposure accumulates only when ``result.accepted`` (empty
    non-PASS reasons — i.e. empty non-exposure *and* exposure reasons).

    Probe-only: also reject ``qb_backup`` in this same pass (before exposure).
    """
    usable = [r for r in rows if "_candidate" in r]
    ordered = sorted(usable, key=lambda r: float(r["edge"]), reverse=True)
    accepted_rows: list[dict[str, Any]] = []
    rejected_rows: list[dict[str, Any]] = []
    exposure = ExposureState()
    bets_this_week = 0
    loop_trace: list[dict[str, Any]] = []
    qb = qb_frame if qb_frame is not None else pd.DataFrame()
    as_of_gate = as_of or datetime.now(tz=UTC)

    # W7-2: fixed stake 0.015, max 6 plays (probe override; production config untouched).
    betting_ov = betting.model_copy(
        update={"max_bets_per_week": MAX_BETS, "max_weekly_exposure": 0.10}
    )

    for row in ordered:
        cand: BetCandidate = row["_candidate"]
        proposed = float(FIXED_STAKE)

        result = evaluate_filters(
            cand,
            betting_ov,
            bets_this_week=bets_this_week,
            weekly_exposure_so_far=float(exposure.weekly_total),
            team_exposure_so_far=dict(exposure.per_team or {}),
            proposed_stake_fraction=proposed,
        )
        reasons = [str(r) for r in result.reasons if r != FilterReason.PASS]
        # Probe-only: backup QB on either side (same pass, before exposure).
        if _game_has_backup_qb(
            qb,
            game_id=str(row["game_id"]),
            team_ids=tuple(cand.team_ids or ()),
            as_of=as_of_gate,
        ):
            if "qb_backup" not in reasons:
                reasons.append("qb_backup")
        # W7-2 timing: kickoff must be >= POST_BY + 2h.
        if not _kickoff_ok(str(row.get("kickoff_utc", ""))):
            if "excluded_timing" not in reasons:
                reasons.append("excluded_timing")
        # W7-2 HFA ROBUST (skipped when HFA_MODE=NONE / W7-5).
        if HFA_MODE == "ROBUST" and not row.get("passes_hfa_gates", False):
            if "hfa_robust_fail" not in reasons:
                reasons.append("hfa_robust_fail")
        # Public edge floor (probe).
        if float(row.get("edge") or 0) < float(public_min_edge):
            if FilterReason.EDGE_TOO_SMALL.value not in reasons:
                reasons.append(FilterReason.EDGE_TOO_SMALL.value)
        accepted = len(reasons) == 0
        non_exposure = tuple(r for r in reasons if r not in {fr.value for fr in EXPOSURE_REASONS})
        annotated = {
            **{k: v for k, v in row.items() if k != "_candidate"},
            "accept_loop_proposed_stake": proposed,
            "accept_loop_reasons": list(reasons),
            "accept_loop_non_exposure_reasons": list(non_exposure),
            "accept_loop_accepted": accepted,
            "_candidate": cand,
        }
        loop_trace.append(
            {
                "game_id": row["game_id"],
                "edge": row.get("edge"),
                "proposed": proposed,
                "accepted": accepted,
                "reasons": list(reasons),
                "bets_this_week_before": bets_this_week,
                "weekly_exposure_before": float(exposure.weekly_total),
            }
        )
        if accepted:
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

    # PRE-QB: every gate clear except qb_status_unknown (HFA + timing already
    # enforced above so they cannot appear here). Public edge floor required.
    pre_qb: list[dict[str, Any]] = []
    for row in rejected_rows:
        reasons = set(row.get("accept_loop_reasons") or [])
        if reasons != {FilterReason.QB_STATUS_UNKNOWN.value}:
            continue
        if float(row.get("edge") or 0) < float(public_min_edge):
            continue
        if row.get("book") and str(row["book"]) not in PROBE_BOOKS:
            continue
        if HFA_MODE == "ROBUST" and not row.get("passes_hfa_gates", False):
            continue
        if not _kickoff_ok(str(row.get("kickoff_utc", ""))):
            continue
        pre_qb.append(row)

    qb_known_pass = [
        r
        for r in accepted_rows
        if float(r.get("edge") or 0) >= float(public_min_edge)
        and (not r.get("book") or str(r["book"]) in PROBE_BOOKS)
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
        "probe_books": list(PROBE_BOOKS),
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
        "side_is": row.get("side_is"),
        "line": row.get("shopped_line"),
        "price": row.get("price"),
        "book": row.get("book"),
        "snapshot_event_time": row.get("snapshot_event_time"),
        "ladder_rung": row.get("ladder_rung"),
        "model_line": row.get("model_line"),
        "residual": row.get("model_market_residual_points"),
        "edge": row.get("edge"),
        "edge_hfa_adj": row.get("edge_hfa_adj"),
        "residual_hfa_adj": row.get("residual_hfa_adj"),
        "units": row.get("units"),
        "qb_status_known": row.get("qb_status_known"),
        "accept_loop_reasons": row.get("accept_loop_reasons"),
        "other_books": row.get("other_books"),
    }


def _install_probe_book_filter(books: tuple[str, ...] | str) -> tuple[Any, Any]:
    """Restrict provider shopping to probe books inside this probe only."""
    allowed = {books} if isinstance(books, str) else set(books)
    orig_spread = provider_mod._book_two_way_spread
    orig_total = provider_mod._book_two_way_total

    def _spread(window: pd.DataFrame, home_side: str) -> list[tuple[str, float, float, float]]:
        return [b for b in orig_spread(window, home_side=home_side) if b[0] in allowed]

    def _total(window: pd.DataFrame) -> list[tuple[str, float, float, float]]:
        return [b for b in orig_total(window) if b[0] in allowed]

    provider_mod._book_two_way_spread = _spread  # type: ignore[assignment]
    provider_mod._book_two_way_total = _total  # type: ignore[assignment]
    return orig_spread, orig_total


def _passes_pricing_gates(row: dict[str, Any]) -> bool:
    """True when every gate that depends on this book's line/price clears.

    QB and exposure are deferred to the accept loop (same as PRE-QB logic).
    W7-2 probe floor: edge ≥ 0.045 and residual ≤ 7.0 (stricter than
    production ``min_edge_sides`` 0.025).
    """
    if row.get("error"):
        return False
    if float(row.get("edge") or -1) < PUBLIC_MIN_EDGE_DEFAULT:
        return False
    if float(row.get("model_market_residual_points") or 999) > MIN_MODEL_MARKET_AGREEMENT:
        return False
    if row.get("is_stale"):
        return False
    reasons = set(row.get("filter_reasons_firing") or [])
    deferred = {
        FilterReason.QB_STATUS_UNKNOWN.value,
        FilterReason.MAX_BETS_PER_WEEK.value,
        FilterReason.MAX_WEEKLY_EXPOSURE.value,
        FilterReason.MAX_TEAM_EXPOSURE.value,
    }
    return not (reasons - deferred)


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


def _build_rows_for_book(
    *,
    book: str,
    games: list[dict[str, Any]],
    analysis_as_of: datetime,
    cfg: Any,
    betting: Any,
    qb_frame: pd.DataFrame,
) -> list[dict[str, Any]]:
    """Build one betting row per game priced exclusively at ``book``."""
    orig_spread, orig_total = _install_probe_book_filter(book)
    try:
        with ParquetStore(cfg.paths.staged_dir) as store:
            season = int(games[0].get("season", 2026)) if games else 2026
            week = int(games[0].get("week", 7)) if games else 7
            candidates, details = build_candidates_from_odds(
                games,
                season=season,
                week=week,
                as_of=analysis_as_of,
                store=store,
                config=cfg,
                n_draws=20_000,
                seed=42,
            )
    finally:
        provider_mod._book_two_way_spread = orig_spread  # type: ignore[assignment]
        provider_mod._book_two_way_total = orig_total  # type: ignore[assignment]

    rows: list[dict[str, Any]] = []
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
                    "kickoff_utc": game["kickoff_utc"],
                    "kickoff_et": _kickoff_et(str(game["kickoff_utc"])),
                    "book": book,
                    "error": "no candidate constructed",
                    "edge": -1.0,
                    "filter_reasons_firing": ["no_candidate"],
                    "passes_pricing_gates": False,
                    "side_is": "?",
                }
            )
            continue

        stake_fraction = float(FIXED_STAKE)
        units = round(stake_fraction / UNIT_FRACTION, 1)
        per_filter = _per_filter_verdicts(
            cand, betting=betting, stake_fraction=stake_fraction
        )
        all_reasons = _all_failing_reasons(
            cand,
            betting=betting,
            stake_fraction=stake_fraction,
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
        model_line = det.get("model_line")
        if model_line is None and det.get("model_line_home") is not None:
            bet_on = det.get("bet_on", "home")
            ml_home = float(det["model_line_home"])
            model_line = ml_home if bet_on == "home" else -ml_home

        row = {
            "game_id": gid,
            "matchup": matchup,
            "side": side_team,
            "side_is": _side_is(game, str(side_team)),
            "kickoff_utc": game["kickoff_utc"],
            "kickoff_et": _kickoff_et(str(game["kickoff_utc"])),
            "book": det.get("book", book),
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
            "stake_fraction": stake_fraction,
            "units": units,
            "per_filter_pass": per_filter,
            "filter_reasons_firing": all_reasons,
            "block_reasons": [str(r) for r in cand.block_reasons],
            "refusal_only": bool(det.get("refusal_only")),
            "s5_discrimination_overlay": S5_DISCRIMINATION,
            "_candidate": cand,
        }
        row["passes_pricing_gates"] = _passes_pricing_gates(row)
        rows.append(row)
    return rows


def _book_quote_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "book": row.get("book"),
        "line": row.get("shopped_line"),
        "price": row.get("price"),
        "edge": row.get("edge"),
        "residual": row.get("model_market_residual_points"),
        "side": row.get("side"),
        "edge_hfa_adj": row.get("edge_hfa_adj"),
        "residual_hfa_adj": row.get("residual_hfa_adj"),
        "passes_pricing_gates": row.get("passes_pricing_gates"),
        "passes_hfa_gates": row.get("passes_hfa_gates"),
        "passes_dual_gates": row.get("passes_dual_gates"),
        "filter_reasons_firing": row.get("filter_reasons_firing"),
        "error": row.get("error"),
    }


def _choose_book_rows(
    by_book: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Per game: keep the highest-edge dual-gate row.

    Tie-break: prefer draftkings. Attach every other book's quote for reporting.
    """
    by_gid: dict[str, list[dict[str, Any]]] = {}
    for book, rows in by_book.items():
        for row in rows:
            by_gid.setdefault(str(row["game_id"]), []).append(row)

    chosen: list[dict[str, Any]] = []
    other_by_gid: dict[str, list[dict[str, Any]]] = {}
    for gid, options in by_gid.items():
        gated = [r for r in options if r.get("passes_dual_gates")]
        pool = gated if gated else options
        best_edge = max(float(r.get("edge") or -999) for r in pool)
        top = [r for r in pool if abs(float(r.get("edge") or -999) - best_edge) < 1e-12]
        book_rank = {b: i for i, b in enumerate(PROBE_BOOKS)}
        top.sort(key=lambda r: book_rank.get(str(r.get("book")), 99))
        pick = top[0]
        others = [_book_quote_summary(r) for r in options if r.get("book") != pick.get("book")]
        pick = {**pick, "other_books": others}
        if others:
            # keep legacy single-other field for older report helpers
            pick["other_book"] = others[0]
        other_by_gid[gid] = others
        chosen.append(pick)
    chosen.sort(key=lambda r: str(r.get("kickoff_utc", "")))
    return chosen, other_by_gid


def _merge_hfa_metrics(
    raw_by_book: dict[str, list[dict[str, Any]]],
    hfa_by_book: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    """Attach HFA-adjusted edge/residual; dual-gate = raw AND hfa pricing pass."""
    merged: dict[str, list[dict[str, Any]]] = {}
    for book in PROBE_BOOKS:
        hfa_by_gid = {str(r["game_id"]): r for r in hfa_by_book.get(book, [])}
        out: list[dict[str, Any]] = []
        for row in raw_by_book.get(book, []):
            hfa = hfa_by_gid.get(str(row["game_id"]))
            annotated = dict(row)
            if hfa is None:
                annotated["edge_hfa_adj"] = None
                annotated["residual_hfa_adj"] = None
                annotated["hfa_side"] = None
                annotated["passes_hfa_gates"] = False
                annotated["hfa_side_match"] = False
            else:
                annotated["edge_hfa_adj"] = hfa.get("edge")
                annotated["residual_hfa_adj"] = hfa.get("model_market_residual_points")
                annotated["hfa_side"] = hfa.get("side")
                side_match = str(hfa.get("side") or "") == str(row.get("side") or "")
                annotated["hfa_side_match"] = side_match
                # Same market row must clear under adjusted μ (same side required).
                annotated["passes_hfa_gates"] = bool(
                    side_match and hfa.get("passes_pricing_gates")
                )
            if HFA_MODE == "ROBUST":
                annotated["passes_dual_gates"] = bool(
                    annotated.get("passes_pricing_gates") and annotated.get("passes_hfa_gates")
                )
            else:
                # W7-5 HFA=NONE: W6 single gate (raw pricing only).
                annotated["passes_dual_gates"] = bool(annotated.get("passes_pricing_gates"))
            out.append(annotated)
        merged[book] = out
    return merged


def run_analysis(*, as_of: datetime | None = None) -> dict[str, Any]:
    analysis_as_of = as_of or datetime.now(tz=UTC)
    wp, wp_source = _load_slate()
    games = wp["games"]
    # stamp season/week onto games for per-book builder
    for g in games:
        g.setdefault("season", wp.get("season", 2026))
        g.setdefault("week", wp.get("week", 7))
    publish_as_of = datetime.fromisoformat(str(wp["as_of"]).replace("Z", "+00:00"))
    cfg = load_config()
    betting = cfg.betting
    # W7-2 DECISIONS: public floor 0.045 (do not loosen if social config is lower).
    public_min_edge = max(float(cfg.social.public_min_edge_sides), PUBLIC_MIN_EDGE_DEFAULT)

    with ParquetStore(cfg.paths.staged_dir) as store:
        qb_frame = store.read("qb_status", filters={"season": 2026})
        season = int(wp.get("season", 2026))
        fcs_ids = _fcs_opponent_ids(store, season)

    raw_by_book: dict[str, list[dict[str, Any]]] = {}
    hfa_by_book: dict[str, list[dict[str, Any]]] = {}
    for book in PROBE_BOOKS:
        raw_by_book[book] = _build_rows_for_book(
            book=book,
            games=games,
            analysis_as_of=analysis_as_of,
            cfg=cfg,
            betting=betting,
            qb_frame=qb_frame,
        )
    if HFA_MODE == "ROBUST":
        hfa_games = _hfa_adjust_games(games)
        for book in PROBE_BOOKS:
            hfa_by_book[book] = _build_rows_for_book(
                book=book,
                games=hfa_games,
                analysis_as_of=analysis_as_of,
                cfg=cfg,
                betting=betting,
                qb_frame=qb_frame,
            )

    by_book = _merge_hfa_metrics(raw_by_book, hfa_by_book)
    rows, _other = _choose_book_rows(by_book)

    odds_summary = _odds_snapshot_summary(games, analysis_as_of, betting)
    ingest_ingested = datetime.fromisoformat(
        str(odds_summary["newest_ingested_at"]).replace("Z", "+00:00")
    )
    with ParquetStore(cfg.paths.staged_dir) as store:
        crosswalk_summary = _crosswalk_summary(store, season, ingest_ingested)
    qb_summary = _qb_coverage(games, qb_frame, analysis_as_of)

    rejected_for_replies: list[dict[str, Any]] = []
    for row in rows:
        rejected_for_replies.append(
            {"game_id": row["game_id"], "reasons": row.get("filter_reasons_firing") or []}
        )

    gate = _production_accept_loop(
        rows,
        betting,
        public_min_edge=public_min_edge,
        qb_frame=qb_frame,
        as_of=analysis_as_of,
    )

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

    # Per-book matrix for audit
    serial_by_book = {
        book: [{k: v for k, v in r.items() if k != "_candidate"} for r in brows]
        for book, brows in by_book.items()
    }

    # Rank dual-gate timing-ok rows by edge for PRE-QB / card marking.
    dual_ok = [
        r
        for r in rows
        if r.get("passes_dual_gates")
        and _kickoff_ok(str(r.get("kickoff_utc", "")))
        and float(r.get("edge") or 0) >= public_min_edge
    ]
    dual_ok_sorted = sorted(dual_ok, key=lambda r: float(r.get("edge") or 0), reverse=True)

    hfa_fail_raw_pass = [
        {
            **_survivor_public_row(r),
            "hfa_side": r.get("hfa_side"),
            "hfa_side_match": r.get("hfa_side_match"),
            "passes_pricing_gates": r.get("passes_pricing_gates"),
            "passes_hfa_gates": r.get("passes_hfa_gates"),
        }
        for r in rows
        if r.get("passes_pricing_gates")
        and not r.get("passes_hfa_gates")
        and _kickoff_ok(str(r.get("kickoff_utc", "")))
        and float(r.get("edge") or 0) >= public_min_edge
    ]
    hfa_fail_raw_pass.sort(key=lambda r: float(r.get("edge") or 0), reverse=True)

    excluded_timing = [
        {
            **_survivor_public_row(r),
            "passes_dual_gates": r.get("passes_dual_gates"),
            "passes_pricing_gates": r.get("passes_pricing_gates"),
            "passes_hfa_gates": r.get("passes_hfa_gates"),
        }
        for r in rows
        if not _kickoff_ok(str(r.get("kickoff_utc", "")))
        and r.get("passes_dual_gates")
        and float(r.get("edge") or 0) >= public_min_edge
    ]
    excluded_timing.sort(key=lambda r: str(r.get("kickoff_utc") or ""))

    # Worklist: marked top-6 PRE-QB survivors + next 4 for set-qb (by edge).
    pre_qb_sorted = sorted(
        gate["pre_qb_survivors"],
        key=lambda r: float(r.get("edge") or 0),
        reverse=True,
    )
    # Prefer accept-loop PRE-QB list; fall back to dual_ok (QB unknown deferred).
    card_pool = pre_qb_sorted if pre_qb_sorted else [
        _survivor_public_row(r) for r in dual_ok_sorted
    ]
    marked = card_pool[:MAX_BETS]
    next4 = card_pool[MAX_BETS : MAX_BETS + 4]
    set_qb_pool = marked + next4

    set_qb_commands: list[dict[str, Any]] = []
    for item in set_qb_pool:
        gid = item["game_id"]
        matchup = str(item.get("matchup") or "")
        away, _, home = matchup.partition(" @ ")
        set_qb_commands.append(
            {
                "game_id": gid,
                "matchup": matchup,
                "commands": [
                    (
                        f'uv run ncaa-quant roster set-qb --game {gid} '
                        f'--team "{away}" --status ___'
                    ),
                    (
                        f'uv run ncaa-quant roster set-qb --game {gid} '
                        f'--team "{home}" --status ___'
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
        "probe_books": list(PROBE_BOOKS),
        "decisions": {
            "hfa": HFA_MODE,
            "hfa_pts": HFA_ROBUST_PTS if HFA_MODE == "ROBUST" else 0.0,
            "stake": FIXED_STAKE,
            "max_bets": MAX_BETS,
            "weekly_cap": 0.10,
            "post_by_et": POST_BY.isoformat(),
            "post_by_plus_2h_et": POST_BY_PLUS_2H.isoformat(),
            "public_min_edge": public_min_edge,
            "decided_at": "2026-10-13T14:32:17-04:00",
            "decision_id": "W7-5",
        },
        "book_selection_rule": (
            "per game: among betmgm+draftkings+fanduel+williamhill_us rows, keep "
            "highest edge that passes pricing gates"
            + (
                " under dual HFA (raw AND μ−4.56)"
                if HFA_MODE == "ROBUST"
                else " (HFA=NONE / W6 single gate)"
            )
            + " (QB/exposure deferred); tie → book order in PROBE_BOOKS"
        ),
        "kickoff_range": {
            "min": min(kickoffs).isoformat() if kickoffs else None,
            "max": max(kickoffs).isoformat() if kickoffs else None,
        },
        "adr_0017_label": "CFBD week 7 primary",
        "stale_inputs_directional_only": odds_summary["stale_at_as_of"],
        "odds_summary": odds_summary,
        "crosswalk_summary": crosswalk_summary,
        "qb_summary": qb_summary,
        "gate_steps_probe_only": False,
        "gate_mirrors_apply_bet_filters": True,
        "gate": gate,
        "marked_card_rows": marked,
        "next4_after_marked": next4,
        "hfa_fail_raw_pass": hfa_fail_raw_pass,
        "excluded_timing": excluded_timing,
        "set_qb_commands": set_qb_commands,
        "betting_rows": serial_rows,
        "betting_rows_by_book": serial_by_book,
        "public_min_edge_0_045_edge_only": public_045_ids,
        "public_min_edge_0_05_edge_only": public_050_ids,
        "public_min_edge_0_045_all_filters": public_survivors(PUBLIC_MIN_EDGE_DEFAULT),
        "public_min_edge_0_05_all_filters": public_survivors(PUBLIC_MIN_EDGE_S3),
        "edge_distribution": edge_distribution,
        "forecast_rows": forecast_rows,
        "replies_w7_md": replies_md,
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
    (ARTIFACT_DIR / "replies_w7.md").write_text(payload["replies_w7_md"], encoding="utf-8")
    _write_phase1_matrix_md(payload["betting_rows"], ARTIFACT_DIR / "phase1_matrix.md")
    pre_qb_path = ARTIFACT_DIR / "pre_qb_survivors.md"
    marked_ids = {str(r["game_id"]) for r in payload.get("marked_card_rows") or []}
    lines = [
        (
            f"# PRE-QB survivors (books={'+'.join(PROBE_BOOKS)}, "
            f"HFA={HFA_MODE}"
            + (f" −{HFA_ROBUST_PTS}" if HFA_MODE == "ROBUST" else "")
            + f", stake={FIXED_STAKE}, max={MAX_BETS}, "
            f"public_min_edge={float(payload['gate']['public_min_edge']):.3f})"
        ),
        "",
        "| mark | game_id | matchup | kickoff ET | side | side_is | book | line | price | snapshot_event_time | model_line | residual | edge | edge_hfa_adj | residual_hfa_adj | units | other_books |",
        "|:----:|--------:|---------|------------|------|---------|------|-----:|------:|--------------------:|-----------:|---------:|-----:|-------------:|-----------------:|------:|-------------|",
    ]
    survivors_sorted = sorted(
        payload["gate"]["pre_qb_survivors"],
        key=lambda r: float(r.get("edge") or 0),
        reverse=True,
    )
    other_lookup = {
        str(r["game_id"]): r.get("other_books") or []
        for r in payload["betting_rows"]
    }
    for r in survivors_sorted:
        mark = "CARD" if str(r["game_id"]) in marked_ids else ""
        others = other_lookup.get(str(r["game_id"])) or r.get("other_books") or []
        other_txt = "; ".join(
            f"{o.get('book')}:{o.get('line')}/{o.get('price')} e={o.get('edge')} "
            f"hfa={o.get('edge_hfa_adj')} "
            f"{'PASS' if o.get('passes_dual_gates') else 'FAIL'}"
            for o in others
        )
        lines.append(
            f"| {mark} | {r['game_id']} | {r['matchup']} | {r['kickoff_et']} | "
            f"{r['side']} | {r.get('side_is','')} | {r['book']} | {r['line']} | "
            f"{r['price']} | {r['snapshot_event_time']} | {r['model_line']} | "
            f"{r['residual']} | {r['edge']} | {r.get('edge_hfa_adj')} | "
            f"{r.get('residual_hfa_adj')} | {r['units']} | {other_txt} |"
        )

    lines.extend(["", "## HFA fail (raw pass, HFA fail)", ""])
    if not payload.get("hfa_fail_raw_pass"):
        lines.append("(none)")
    else:
        for r in payload["hfa_fail_raw_pass"]:
            lines.append(
                f"- {r['game_id']} {r['matchup']} side={r['side']} "
                f"edge={r['edge']} edge_hfa={r.get('edge_hfa_adj')} "
                f"resid={r['residual']} resid_hfa={r.get('residual_hfa_adj')} "
                f"side_match={r.get('hfa_side_match')}"
            )

    lines.extend(["", "## EXCLUDED_TIMING (dual-gate pass, kickoff < POST_BY+2h)", ""])
    if not payload.get("excluded_timing"):
        lines.append("(none)")
    else:
        for r in payload["excluded_timing"]:
            lines.append(
                f"- {r['game_id']} {r['matchup']} {r['kickoff_et']} "
                f"side={r['side']} edge={r['edge']} book={r['book']}"
            )

    lines.extend(["", "## set-qb commands (marked + next 4)", ""])
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
    print(
        f"slate={payload['slate_count']} books={'+'.join(PROBE_BOOKS)} "
        f"stale={payload['stale_inputs_directional_only']}"
    )
    print(
        "gate:",
        {
            "accepted": payload["gate"]["accepted_count"],
            "pre_qb": payload["gate"]["pre_qb_survivor_count"],
            "qb_known_pass": payload["gate"]["qb_known_pass_count"],
            "marked": len(payload.get("marked_card_rows") or []),
            "hfa_fail_raw_pass": len(payload.get("hfa_fail_raw_pass") or []),
            "excluded_timing": len(payload.get("excluded_timing") or []),
        },
    )
    print("verdict:", payload["verdict"])


if __name__ == "__main__":
    main()
