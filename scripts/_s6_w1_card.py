"""S6-W1-CARD — candidate provider read for 2026 CFBD week-1 second primary slate."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ncaa_quant.betting.filters import BetCandidate, FilterReason, evaluate_filters
from ncaa_quant.betting.kelly import recommended_stake
from ncaa_quant.betting.provider import build_candidates_from_odds
from ncaa_quant.config import load_config
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.social.render import render_replies

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = ROOT / "docs" / "notes" / "_artifacts" / "social-s6-w1-card"
SITE_URL = "https://the-cfb-model.vercel.app"
PUBLIC_MIN_EDGE_DEFAULT = 0.045
PUBLIC_MIN_EDGE_S3 = 0.05
MIN_MODEL_MARKET_AGREEMENT = 7.0
UNIT_FRACTION = 0.005
LIVE_ODDS_CREDITS = 3  # markets (3) × regions (1) per Odds API live endpoint metering

S5_DISCRIMINATION = {
    "candidate_p_win_auc": "0.493 [0.43, 0.56]",
    "top_bin": "0.508 vs 0.524 break-even",
    "accept_loop_top_k_overlap": "1.0",
    "probability_clv": "+0.0044 vs +0.0238 required",
}

# Ordered gate steps (probe-only view — production filter order unchanged).
GATE_STEPS: tuple[tuple[str, ...], ...] = (
    (
        FilterReason.NO_SNAPSHOT.value,
        FilterReason.STALE_INPUTS.value,
        FilterReason.KICKOFF_PASSED.value,
        FilterReason.LINE_QUARANTINED.value,
    ),
    (
        FilterReason.EDGE_TOO_SMALL.value,
        FilterReason.NON_POSITIVE_EV.value,
        FilterReason.SIGMA_NOT_CREDIBLE.value,
    ),
    (FilterReason.MODEL_MARKET_DISAGREE.value,),
    (
        FilterReason.MAX_BETS_PER_WEEK.value,
        FilterReason.MAX_WEEKLY_EXPOSURE.value,
        FilterReason.MAX_TEAM_EXPOSURE.value,
    ),
    (FilterReason.QB_STATUS_UNKNOWN.value,),
)

ALL_FILTER_REASONS: tuple[str, ...] = tuple(
    fr.value for fr in FilterReason if fr != FilterReason.PASS
)


def _load_slate() -> tuple[dict[str, Any], str]:
    latest = ROOT / "latest" / "week_predictions.json"
    pub_path = ROOT / "data" / "webapp" / "publish_history" / "2026_w1.jsonl"
    if latest.is_file():
        wp = json.loads(latest.read_text(encoding="utf-8"))
        source = "latest/week_predictions.json"
    elif pub_path.is_file():
        lines = [ln.strip() for ln in pub_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        wp = json.loads(lines[-1])
        source = (
            f"data/webapp/publish_history/2026_w1.jsonl "
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


def _exposure_fails(
    candidate: BetCandidate,
    betting: Any,
    stake_fraction: float,
    *,
    bets_this_week: int,
    weekly_exposure: float,
    team_exposure: dict[str, float],
) -> list[str]:
    fr = evaluate_filters(
        candidate,
        betting,
        bets_this_week=bets_this_week,
        weekly_exposure_so_far=weekly_exposure,
        team_exposure_so_far=team_exposure,
        proposed_stake_fraction=stake_fraction,
    )
    return [
        str(r)
        for r in fr.reasons
        if r in {
            FilterReason.MAX_BETS_PER_WEEK,
            FilterReason.MAX_WEEKLY_EXPOSURE,
            FilterReason.MAX_TEAM_EXPOSURE,
        }
    ]


def _passes_step(reasons: set[str], step_reasons: tuple[str, ...]) -> bool:
    return not any(r in reasons for r in step_reasons)


def _ordered_gate_survivors(
    rows: list[dict[str, Any]],
    betting: Any,
) -> dict[str, Any]:
    """Apply gate steps in probe order; exposure step simulates edge-sorted accept loop."""
    survivors = list(rows)
    step_counts: list[int] = []
    step_labels = [
        "step1_snapshot_stale_kickoff_quarantine",
        "step2_edge_ev_sigma",
        "step3_model_market_disagree",
        "step4_exposure_caps",
        "step5_qb_status_unknown",
    ]

    for idx, step_reasons in enumerate(GATE_STEPS):
        if idx == 3:
            # Exposure: edge-sorted cumulative accept simulation.
            ordered = sorted(survivors, key=lambda r: float(r["edge"]), reverse=True)
            kept: list[dict[str, Any]] = []
            bets = 0
            weekly = 0.0
            team_exp: dict[str, float] = {}
            for row in ordered:
                cand = row["_candidate"]
                stake = float(row["stake_fraction"])
                fails = _exposure_fails(
                    cand,
                    betting,
                    stake,
                    bets_this_week=bets,
                    weekly_exposure=weekly,
                    team_exposure=team_exp,
                )
                if fails:
                    row = dict(row)
                    row["exposure_gate_fails"] = fails
                    continue
                kept.append(row)
                bets += 1
                weekly += stake
                for tid in cand.team_ids:
                    team_exp[tid] = team_exp.get(tid, 0.0) + stake
            survivors = kept
        else:
            survivors = [
                r
                for r in survivors
                if _passes_step(set(r["filter_reasons_firing"]), step_reasons)
            ]
        step_counts.append(len(survivors))

    qb_worklist = [
        {
            "game_id": r["game_id"],
            "matchup": r["matchup"],
            "home_team": r["matchup"].split(" @ ")[1],
            "away_team": r["matchup"].split(" @ ")[0],
        }
        for r in survivors
    ]

    return {
        "step_counts": dict(zip(step_labels, step_counts, strict=True)),
        "qb_worklist": qb_worklist,
        "final_survivors": [r["game_id"] for r in survivors],
    }


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


def _odds_snapshot_summary(
    games: list[dict[str, Any]],
    as_of: datetime,
    betting: Any,
) -> dict[str, Any]:
    parts = sorted((ROOT / "data" / "staged" / "odds_snapshots").glob("season=2026/**/part.parquet"))
    newest_et = max(pd.read_parquet(p, columns=["event_time"])["event_time"].max() for p in parts)
    newest_dt = pd.Timestamp(newest_et).to_pydatetime()
    if newest_dt.tzinfo is None:
        newest_dt = newest_dt.replace(tzinfo=UTC)
    age_h = (as_of - newest_dt).total_seconds() / 3600.0

    gids = {int(g["game_id"]) for g in games}
    odds_frames = [pd.read_parquet(p) for p in parts]
    odds = pd.concat(odds_frames, ignore_index=True)
    odds["game_id"] = odds["game_id"].astype("Int64")
    slate_spread = odds[(odds["game_id"].isin(list(gids))) & (odds["market"] == "spread")]
    books_per = (
        slate_spread.groupby("game_id")["book"].nunique().to_dict() if not slate_spread.empty else {}
    )
    coverage_rows: list[dict[str, Any]] = []
    for g in games:
        gid = int(g["game_id"])
        n_books = int(books_per.get(gid, 0))
        coverage_rows.append(
            {
                "game_id": str(gid),
                "matchup": f"{g['away_team']} @ {g['home_team']}",
                "books_with_spread": n_books,
            }
        )
    return {
        "newest_event_time": newest_dt.isoformat(),
        "age_hours_at_as_of": round(age_h, 3),
        "odds_max_age_hours": float(betting.odds_max_age_hours),
        "stale_at_as_of": age_h > float(betting.odds_max_age_hours),
        "games_with_spread_odds": len(books_per),
        "games_without_spread_odds": len(gids) - len(books_per),
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

    with ParquetStore(cfg.paths.staged_dir) as store:
        qb_frame = store.read("qb_status", filters={"season": 2026})
        candidates, details = build_candidates_from_odds(
            games,
            season=int(wp.get("season", 2026)),
            week=int(wp.get("week", 1)),
            as_of=analysis_as_of,
            store=store,
            config=cfg,
            n_draws=20_000,
            seed=42,
        )

    odds_summary = _odds_snapshot_summary(games, analysis_as_of, betting)
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
            100.0,
            betting,
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

    gate = _ordered_gate_survivors(rows, betting)
    gate["step_counts"]["start"] = len(rows)

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

    edges = sorted(float(r.get("edge", 0)) for r in rows)
    forecast_rows = [_game_forecast_row(g) for g in games]
    replies_md = render_replies(games, [], rejected_for_replies, SITE_URL)

    any_clear_all_filters = any(not r.get("filter_reasons_firing") for r in rows)
    verdict = (
        "NOT MEASURED — no ATS discrimination on the 314-ticket population (S5); "
        "every candidate clearing filters is still a draw from a ranking with AUC "
        "0.493 and no information."
        if any_clear_all_filters
        else "NOT MEASURED — load-bearing gates (stale inputs, missing odds, QB unknown) "
        "block the slate before discrimination matters."
    )

    kickoffs = [
        datetime.fromisoformat(str(g["kickoff_utc"]).replace("Z", "+00:00")) for g in games
    ]

    # Strip non-serializable candidate objects for JSON
    serial_rows = []
    for r in rows:
        out = {k: v for k, v in r.items() if k != "_candidate"}
        serial_rows.append(out)

    return {
        "analysis_as_of": analysis_as_of.isoformat(),
        "publish_as_of": publish_as_of.isoformat(),
        "week_predictions_source": wp_source,
        "season": wp.get("season"),
        "week": wp.get("week"),
        "slate_count": len(games),
        "kickoff_range": {
            "min": min(kickoffs).isoformat(),
            "max": max(kickoffs).isoformat(),
        },
        "adr_0017_label": "CFBD week 1 second weekend (Sept 1 operator primary)",
        "stale_inputs_directional_only": odds_summary["stale_at_as_of"],
        "odds_summary": odds_summary,
        "qb_summary": qb_summary,
        "gate_steps_probe_only": True,
        "gate": gate,
        "betting_rows": serial_rows,
        "public_min_edge_0_045_edge_only": public_045_ids,
        "public_min_edge_0_05_edge_only": public_050_ids,
        "public_min_edge_0_045_all_filters": public_survivors(PUBLIC_MIN_EDGE_DEFAULT),
        "public_min_edge_0_05_all_filters": public_survivors(PUBLIC_MIN_EDGE_S3),
        "edge_distribution": {
            "min": min(edges) if edges else None,
            "max": max(edges) if edges else None,
            "values": edges,
            "near_bar_0_045": [
                r["game_id"] for r in rows if 0.04 <= float(r.get("edge", 0)) <= 0.05
            ],
            "regime_ge_0_15": [
                r["game_id"] for r in rows if float(r.get("edge", 0)) >= 0.15
            ],
        },
        "forecast_rows": forecast_rows,
        "replies_w1_md": replies_md,
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
    (ARTIFACT_DIR / "replies_w1.md").write_text(payload["replies_w1_md"], encoding="utf-8")
    _write_phase1_matrix_md(payload["betting_rows"], ARTIFACT_DIR / "phase1_matrix.md")
    print(f"wrote {out_json}")
    print(f"slate={payload['slate_count']} stale={payload['stale_inputs_directional_only']}")
    print("gate steps:", payload["gate"]["step_counts"])
    print("verdict:", payload["verdict"])


if __name__ == "__main__":
    main()
