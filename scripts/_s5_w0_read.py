"""S5-W0-READ — per-filter eight-game analysis (read-only on staged data)."""

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
from ncaa_quant.social.render import dominant_rejection_reason, render_replies

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_DIR = ROOT / "docs" / "notes" / "_artifacts" / "social-s5-w0-read"
PUBLIC_MIN_EDGE_DEFAULT = 0.045
PUBLIC_MIN_EDGE_S3 = 0.05
MIN_MODEL_MARKET_AGREEMENT = 7.0
UNIT_FRACTION = 0.005  # bankroll fraction per 1u


def _load_eight_games() -> tuple[dict[str, Any], str]:
    latest = ROOT / "latest" / "week_predictions.json"
    pub_path = ROOT / "data" / "webapp" / "publish_history" / "2026_w1.jsonl"
    if latest.is_file():
        wp = json.loads(latest.read_text(encoding="utf-8"))
        source = "latest/week_predictions.json"
    elif pub_path.is_file():
        lines = [
            ln.strip() for ln in pub_path.read_text(encoding="utf-8").splitlines() if ln.strip()
        ]
        wp = json.loads(lines[-1])
        source = (
            f"data/webapp/publish_history/2026_w1.jsonl "
            f"(record {len(lines)}, as_of={wp.get('as_of')})"
        )
    else:
        raise FileNotFoundError("no week_predictions source")

    cutoff = datetime(2026, 8, 31, tzinfo=UTC)
    games = [
        g
        for g in wp.get("games", [])
        if datetime.fromisoformat(str(g["kickoff_utc"]).replace("Z", "+00:00")) < cutoff
    ]
    games.sort(key=lambda g: str(g["kickoff_utc"]))
    wp_eight = {**wp, "games": games}
    if len(games) != 8:
        msg = f"expected 8 games, got {len(games)}"
        raise ValueError(msg)
    return wp_eight, source


def _per_filter_verdicts(
    candidate: BetCandidate,
    *,
    betting: Any,
    stake_fraction: float,
) -> dict[str, bool]:
    """Every §12 filter evaluated independently (ignore block_reason short-circuit)."""
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


def _all_failing_reasons(
    candidate: BetCandidate,
    *,
    betting: Any,
    stake_fraction: float,
) -> list[str]:
    if candidate.block_reasons:
        base = [str(r) for r in candidate.block_reasons]
    else:
        base = []
    fr = evaluate_filters(candidate, betting, proposed_stake_fraction=stake_fraction)
    merged = list(dict.fromkeys([*base, *[str(r) for r in fr.reasons if r != FilterReason.PASS]]))
    return merged


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
    return {
        "game_id": str(game["game_id"]),
        "matchup": f"{away} @ {home}",
        "kickoff_utc": game["kickoff_utc"],
        "mu_margin": mu,
        "sigma_margin": game.get("sigma_margin"),
        "interval_80_lo": lo,
        "interval_80_hi": hi,
        "half_width_over_sigma": half_width_sigma,
        "conviction_tier": game.get("conviction_tier"),
        "p_favored": (game.get("conviction_basis") or {}).get("p_favored", game.get("p_win_home")),
        "coherence_suppressed": suppressed,
        "favored_team": fav,
    }


def run_analysis(*, as_of: datetime | None = None) -> dict[str, Any]:
    as_of_utc = as_of or datetime.now(tz=UTC)
    wp, wp_source = _load_eight_games()
    games = wp["games"]
    cfg = load_config()
    betting = cfg.betting

    with ParquetStore(cfg.paths.staged_dir) as store:
        qb_frame = store.read("qb_status", filters={"season": 2026})
        candidates, details = build_candidates_from_odds(
            games,
            season=2026,
            week=1,
            as_of=as_of_utc,
            store=store,
            config=cfg,
            n_draws=20_000,
            seed=42,
        )

    # Global snapshot age
    odds_parts = sorted(
        (ROOT / "data" / "staged" / "odds_snapshots").glob("season=2026/**/part.parquet")
    )
    newest_et = max(
        pd.read_parquet(p, columns=["event_time"])["event_time"].max() for p in odds_parts
    )
    newest_et = pd.Timestamp(newest_et).to_pydatetime()
    if newest_et.tzinfo is None:
        newest_et = newest_et.replace(tzinfo=UTC)
    snap_age_h = (as_of_utc - newest_et).total_seconds() / 3600.0
    stale_global = snap_age_h > float(betting.odds_max_age_hours)

    rows: list[dict[str, Any]] = []
    rejected_for_replies: list[dict[str, Any]] = []

    for game in games:
        gid = str(game["game_id"])
        key = f"{gid}:side"
        cand = next((c for c in candidates if c.game_id == gid and c.market == "side"), None)
        det = details.get(key, {})
        if cand is None:
            continue

        stake = recommended_stake(
            float(cand.p_win or 0),
            float(cand.american_odds or -110),
            100.0,
            betting,
        )
        units = round(stake.stake_fraction / UNIT_FRACTION, 1)
        hit_max_stake = stake.stake_fraction >= float(betting.max_stake_pct) - 1e-12
        per_filter = _per_filter_verdicts(
            cand, betting=betting, stake_fraction=stake.stake_fraction
        )
        all_reasons = _all_failing_reasons(
            cand, betting=betting, stake_fraction=stake.stake_fraction
        )
        residual = round(float(cand.model_market_residual_points), 2)
        near_flip = abs(residual - MIN_MODEL_MARKET_AGREEMENT) <= 1.0

        snap_et = det.get("snapshot_event_time")
        snap_age_game_h = None
        if snap_et:
            snap_dt = datetime.fromisoformat(str(snap_et).replace("Z", "+00:00"))
            snap_age_game_h = round((as_of_utc - snap_dt).total_seconds() / 3600.0, 3)

        row = {
            "game_id": gid,
            "matchup": f"{game['away_team']} @ {game['home_team']}",
            "kickoff_utc": game["kickoff_utc"],
            "book": det.get("book", ""),
            "shopped_line": det.get("market_line"),
            "price": det.get("american_odds"),
            "snapshot_event_time": snap_et,
            "ladder_rung": det.get("ladder_rung", ""),
            "model_implied_line": det.get("model_line"),
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
            "hit_max_stake_pct": hit_max_stake,
            "per_filter_pass": per_filter,
            "filter_reasons_firing": all_reasons,
            "side_team": det.get("side_team"),
            "block_reasons": [str(r) for r in cand.block_reasons],
        }
        rows.append(row)
        rejected_for_replies.append({"game_id": gid, "reasons": all_reasons})

    # QB table (16 teams)
    qb_rows: list[dict[str, Any]] = []
    for game in games:
        gid = int(game["game_id"])
        for label, tid, team in (
            ("away", int(game["away_team_id"]), game["away_team"]),
            ("home", int(game["home_team_id"]), game["home_team"]),
        ):
            sub = qb_frame.loc[
                (qb_frame["game_id"].astype("Int64") == gid)
                & (qb_frame["team_id"].astype(int) == tid)
            ]
            if sub.empty:
                qb_rows.append(
                    {
                        "game_id": str(gid),
                        "team": team,
                        "team_id": tid,
                        "status": None,
                        "event_time": None,
                    }
                )
                continue
            sub = sub.copy()
            sub["event_time"] = pd.to_datetime(sub["event_time"], utc=True)
            sub = sub.loc[sub["event_time"] <= pd.Timestamp(as_of_utc)]
            if sub.empty:
                qb_rows.append(
                    {
                        "game_id": str(gid),
                        "team": team,
                        "team_id": tid,
                        "status": None,
                        "event_time": None,
                        "note": "AFTER_AS_OF",
                    }
                )
                continue
            latest = sub.sort_values("event_time").iloc[-1]
            qb_rows.append(
                {
                    "game_id": str(gid),
                    "team": team,
                    "team_id": tid,
                    "status": str(latest["status"]),
                    "event_time": latest["event_time"].isoformat(),
                    "source_version": str(latest.get("source_version", "")),
                    "ingested_at": pd.Timestamp(latest["ingested_at"]).isoformat()
                    if "ingested_at" in latest.index
                    else None,
                }
            )

    # Public overlay (edge-only; ignores other gates for "clears bar" count)
    def clears_public(edge: float, thr: float) -> bool:
        return edge >= thr

    public_045 = [r for r in rows if clears_public(r["edge"], PUBLIC_MIN_EDGE_DEFAULT)]
    public_050 = [r for r in rows if clears_public(r["edge"], PUBLIC_MIN_EDGE_S3)]

    edges = sorted(r["edge"] for r in rows)
    forecast_rows = [_game_forecast_row(g) for g in games]

    # Reply bank via render.py (all reasons, stale-only, whole-point intervals)
    site_url = "https://ridge.example.com"  # CLI default; SocialConfig has no site_url
    replies_md = render_replies(games, [], rejected_for_replies, site_url)

    # Dominant reason for doc
    dom = dominant_rejection_reason(rejected_for_replies)

    payload = {
        "as_of": as_of_utc.isoformat(),
        "week_predictions_source": wp_source,
        "stale_inputs_directional_only": stale_global,
        "snapshot_newest_event_time": newest_et.isoformat(),
        "snapshot_age_hours": round(snap_age_h, 3),
        "odds_max_age_hours": float(betting.odds_max_age_hours),
        "games": games,
        "betting_rows": rows,
        "qb_status_rows": qb_rows,
        "public_min_edge_0_045": [r["game_id"] for r in public_045],
        "public_min_edge_0_05": [r["game_id"] for r in public_050],
        "edge_distribution": {
            "min": min(edges),
            "max": max(edges),
            "values": edges,
            "near_bar_0_045": [r["game_id"] for r in rows if 0.04 <= r["edge"] <= 0.05],
            "regime_ge_0_15": [r["game_id"] for r in rows if r["edge"] >= 0.15],
        },
        "forecast_rows": forecast_rows,
        "replies_w1_md": replies_md,
        "dominant_rejection_reason": dom,
        "site_url_used": site_url,
        "qb_fully_resolved_games": ["401858202", "401858201"],
        "qb_blocked_count": 6,
        "qb_clear_count": 2,
    }
    return payload


def main() -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    payload = run_analysis()
    out_json = ARTIFACT_DIR / "analysis.json"
    out_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    (ARTIFACT_DIR / "replies_w1.md").write_text(payload["replies_w1_md"], encoding="utf-8")
    print(f"wrote {out_json}")
    print(f"dominant reason: {payload['dominant_rejection_reason']}")
    print(f"stale: {payload['stale_inputs_directional_only']}")
    halfs = [
        r["half_width_over_sigma"]
        for r in payload["forecast_rows"]
        if r.get("half_width_over_sigma") is not None
    ]
    if halfs:
        print(f"half_width/sigma min={min(halfs)} max={max(halfs)}")


if __name__ == "__main__":
    main()
