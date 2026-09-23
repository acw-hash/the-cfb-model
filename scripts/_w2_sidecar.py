"""W2-2b — operator sidecar for 2026 week 2 (no publish / no R2).

Approved workaround: call library functions directly. Does NOT call
``execute_predict_publish``. Does NOT set ``candidates_enabled`` /
``export_enabled``. Enables ``social.enabled`` only for the in-process
``export_social_candidates`` write to ``data/social/...``.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ncaa_quant.betting.baseline_convention import compute_baseline_convention_eligibility
from ncaa_quant.betting.filters import BetCandidate, FilterReason, evaluate_filters
from ncaa_quant.betting.kelly import ExposureState, recommended_stake
from ncaa_quant.betting.provider import build_candidates_from_odds
from ncaa_quant.config import load_config
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.pipelines.predict import apply_bet_filters
from ncaa_quant.social.candidates import (
    export_social_candidates,
    records_from_filter_result,
)

ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "data" / "webapp" / "publish_history" / "2026_w2.jsonl"
PUBLISH_AT = "2026-09-08T15:34:11Z"  # same live W2 publish W2-Q used
SEASON = 2026
WEEK = 2
REFRESH_KIND = "tuesday_primary"
N_DRAWS = 20_000
SEED = 42


def _load_slate() -> tuple[list[dict[str, Any]], str]:
    """Load live week-2 games from publish_history (same source as W2-Q)."""
    rows = [json.loads(l) for l in HISTORY.read_text(encoding="utf-8").splitlines() if l.strip()]
    art = next(r for r in rows if r.get("published_at") == PUBLISH_AT)
    games = list(art["games"])
    assert len(games) == 86, len(games)
    source = (
        f"publish_history={HISTORY.as_posix()} published_at={PUBLISH_AT} "
        f"refresh_kind={art.get('refresh_kind')} n_games={len(games)}"
    )
    return games, source


def _min_edge(c: BetCandidate, cfg: Any) -> float:
    base = float(cfg.min_edge_sides) if c.market == "side" else float(cfg.min_edge_totals)
    return base * float(cfg.bowl_edge_multiplier) if c.is_bowl else base


def _proposed_stake(cand: BetCandidate, cfg: Any) -> tuple[float, float]:
    """Return (stake_fraction used by apply_bet_filters, fractional_kelly_before_cap)."""
    if cand.p_win is None or cand.american_odds is None:
        return 0.0, 0.0
    # Match apply_bet_filters: exposure args forced to 0 so before_cap is uncapped
    # by running exposure; caps are enforced inside evaluate_filters.
    stake = recommended_stake(
        float(cand.p_win),
        float(cand.american_odds),
        bankroll=1.0,
        config=cfg,
        weekly_exposure_so_far=0.0,
        team_exposure_so_far=0.0,
    )
    return float(stake.stake_fraction), float(stake.fractional_kelly_before_cap)


def _funnel(candidates: list[BetCandidate], cfg: Any) -> list[tuple[str, int]]:
    ordered = sorted(candidates, key=lambda c: float(c.edge), reverse=True)
    steps: list[tuple[str, int]] = [("start", len(ordered))]

    pool = [c for c in ordered if not c.block_reasons]
    steps.append(("block_reasons_cleared", len(pool)))

    pool = [c for c in pool if float(c.edge) >= _min_edge(c, cfg)]
    steps.append(("edge_too_small", len(pool)))

    if cfg.no_bet_on_stale:
        pool = [c for c in pool if not c.is_stale]
    steps.append(("stale_inputs", len(pool)))

    if cfg.no_bet_on_qb_unknown:
        pool = [c for c in pool if c.qb_status_known]
    steps.append(("qb_status_unknown", len(pool)))

    pool = [
        c
        for c in pool
        if float(c.model_market_residual_points) <= float(cfg.min_model_market_agreement)
    ]
    steps.append(("model_market_disagree", len(pool)))

    def _run_through(stop_after: str) -> int:
        exp = ExposureState()
        b = 0
        kept = 0
        for cand in pool:
            proposed, _ = _proposed_stake(cand, cfg)
            fail_bets = b >= int(cfg.max_bets_per_week)
            fail_weekly = (
                float(exp.weekly_total) + proposed > float(cfg.max_weekly_exposure) + 1e-15
            )
            fail_team = any(
                float(exp.per_team.get(tid, 0.0)) + proposed
                > float(cfg.max_exposure_per_team) + 1e-15
                for tid in cand.team_ids
            )
            fail_ev = cand.expected_value <= 0.0
            if stop_after == "max_bets_per_week" and fail_bets:
                continue
            if stop_after == "max_weekly_exposure" and (fail_bets or fail_weekly):
                continue
            if stop_after == "max_team_exposure" and (fail_bets or fail_weekly or fail_team):
                continue
            if stop_after == "non_positive_ev" and (
                fail_bets or fail_weekly or fail_team or fail_ev
            ):
                continue
            kept += 1
            b += 1
            if proposed > 0.0 and cand.team_ids:
                exp = exp.with_bet(cand.team_ids, proposed)
            elif proposed > 0.0:
                exp = ExposureState(
                    weekly_total=float(exp.weekly_total + proposed),
                    per_team=dict(exp.per_team or {}),
                )
        return kept

    steps.append(("max_bets_per_week", _run_through("max_bets_per_week")))
    steps.append(("max_weekly_exposure", _run_through("max_weekly_exposure")))
    steps.append(("max_team_exposure", _run_through("max_team_exposure")))
    steps.append(("non_positive_ev", _run_through("non_positive_ev")))
    return steps


def _instrumented_accept_loop(
    candidates: list[BetCandidate], cfg: Any
) -> tuple[
    list[BetCandidate],
    list[tuple[BetCandidate, tuple[FilterReason, ...]]],
    list[dict[str, Any]],
    str,
]:
    """Mirror ``apply_bet_filters`` exactly; dump stake / before_cap / exposure."""
    ordered = sorted(candidates, key=lambda c: float(c.edge), reverse=True)
    accepted: list[BetCandidate] = []
    rejected: list[tuple[BetCandidate, tuple[FilterReason, ...]]] = []
    exposure = ExposureState()
    bets_this_week = 0
    rows: list[dict[str, Any]] = []
    saw_continue_past_cap = False

    for cand in ordered:
        proposed, before_cap = _proposed_stake(cand, cfg)
        weekly_before = float(exposure.weekly_total)
        weekly_room = float(cfg.max_weekly_exposure) - weekly_before
        exceeds_room = proposed > weekly_room + 1e-15 and weekly_room >= 0

        result = evaluate_filters(
            cand,
            cfg,
            bets_this_week=bets_this_week,
            weekly_exposure_so_far=weekly_before,
            team_exposure_so_far=dict(exposure.per_team or {}),
            proposed_stake_fraction=proposed,
        )
        action = "ACCEPT" if result.accepted else "REJECT"
        if exceeds_room and not result.accepted:
            # Production loop does not break — it continues to the next candidate.
            saw_continue_past_cap = True
            action = "REJECT_CONTINUE"  # would still iterate

        rows.append(
            {
                "game_id": cand.game_id,
                "edge": float(cand.edge),
                "stake": proposed,
                "before_cap": before_cap,
                "weekly_exposure_before": weekly_before,
                "weekly_room": weekly_room,
                "bets_before": bets_this_week,
                "action": action,
                "reasons": [str(r) for r in result.reasons],
            }
        )

        if result.accepted:
            accepted.append(cand)
            bets_this_week += 1
            if proposed > 0.0 and cand.team_ids:
                exposure = exposure.with_bet(cand.team_ids, proposed)
            elif proposed > 0.0:
                exposure = ExposureState(
                    weekly_total=float(exposure.weekly_total + proposed),
                    per_team=dict(exposure.per_team or {}),
                )
        else:
            rejected.append((cand, result.reasons))
        # NO break — production apply_bet_filters always continues.

    loop_policy = "CONTINUES" if True else "BREAKS"  # production always continues
    if saw_continue_past_cap:
        loop_policy = "CONTINUES (saw reject-after-cap and kept iterating)"
    return accepted, rejected, rows, loop_policy


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    as_of = datetime.now(tz=UTC)
    games, slate_source = _load_slate()
    games_by_id = {str(g["game_id"]): g for g in games}

    cfg = load_config()
    assert cfg.webapp.export_enabled is False, "export gate must stay False"
    assert cfg.betting.candidates_enabled is False, "do not enable candidates_enabled"

    print(f"as_of={as_of.isoformat()}")
    print(f"slate_source={slate_source}")
    print(f"export_enabled={cfg.webapp.export_enabled}")
    print(f"candidates_enabled={cfg.betting.candidates_enabled}")
    print(f"social.enabled(config)={cfg.social.enabled}")

    with ParquetStore(cfg.paths.staged_dir) as store:
        # build_candidates_from_odds uses _load_snapshots(season) — season-wide;
        # hive week is deliberately unused (Labor Day skew).
        candidates, details = build_candidates_from_odds(
            games,
            season=SEASON,
            week=WEEK,
            as_of=as_of,
            store=store,
            config=cfg,
            n_draws=N_DRAWS,
            seed=SEED,
        )

    odds_covered = [c for c in candidates if not c.block_reasons]
    print(f"\nn_slate={len(games)} n_candidates={len(candidates)} "
          f"n_two_sided_quote={len(odds_covered)}")

    # Compare fingerprint to W2-Q-ish: top edges among odds-covered
    top_edges = sorted((float(c.edge), c.game_id) for c in odds_covered)[-5:]
    print(f"top5_edges_odds_covered={list(reversed(top_edges))}")

    steps = _funnel(candidates, cfg.betting)
    print("\n=== FUNNEL ===")
    for label, n in steps:
        print(f"  {label}: {n}")

    accepted, rejected, loop_rows, loop_policy = _instrumented_accept_loop(
        candidates, cfg.betting
    )
    # Sanity: match production apply_bet_filters
    acc2, rej2 = apply_bet_filters(candidates, betting_config=cfg.betting)
    assert {c.game_id for c in accepted} == {c.game_id for c in acc2}
    assert len(rejected) == len(rej2)

    for label, n in [("accepted", len(accepted))]:
        print(f"  {label}: {n}")

    reason_counts: Counter[str] = Counter()
    for _c, reasons in rejected:
        for r in reasons:
            if r != FilterReason.PASS:
                reason_counts[str(r)] += 1
    print("\n=== FilterReason counts ===")
    for reason, n in reason_counts.most_common():
        print(f"  {reason}: {n}")

    print("\n=== ACCEPTED detail ===")
    any_baseline_false = False
    for i, cand in enumerate(sorted(accepted, key=lambda c: float(c.edge), reverse=True), 1):
        g = games_by_id[cand.game_id]
        det = details.get(f"{cand.game_id}:{cand.market}", {})
        stake, before_cap = _proposed_stake(cand, cfg.betting)
        eligible, axes = compute_baseline_convention_eligibility(
            week=WEEK, edge=float(cand.edge), market=cand.market
        )
        if not eligible:
            any_baseline_false = True
        print(
            f"  #{i:02d} gid={cand.game_id} {g['away_team']} @ {g['home_team']} "
            f"mkt={cand.market} side={det.get('side_team')} "
            f"mkt_line={det.get('market_line')} model={det.get('model_line')} "
            f"edge={float(cand.edge):.4f} EV={float(cand.expected_value):.4f} "
            f"odds={cand.american_odds} book={det.get('book')} p_win={cand.p_win} "
            f"stake={stake:.4f} resid={cand.model_market_residual_points:.2f} "
            f"baseline_eligible={eligible} axes={list(axes)}"
        )

    print("\n=== TOP 20 by edge (accepted+rejected) ===")
    all_ranked: list[tuple[BetCandidate, str, list[str]]] = []
    acc_ids = {id(c) for c in accepted}
    rej_map = {id(c): [str(r) for r in reasons] for c, reasons in rejected}
    for c in sorted(candidates, key=lambda x: float(x.edge), reverse=True):
        if id(c) in acc_ids:
            all_ranked.append((c, "accepted", ["pass"]))
        else:
            all_ranked.append((c, "rejected", rej_map.get(id(c), ["?"])))
    accepted_edges = [float(c.edge) for c in accepted]
    min_acc = min(accepted_edges) if accepted_edges else None
    outrank_flags: list[str] = []
    for i, (cand, status, reasons) in enumerate(all_ranked[:20], 1):
        g = games_by_id[cand.game_id]
        flag = ""
        if (
            status == "rejected"
            and min_acc is not None
            and float(cand.edge) > min_acc
            and not any(r != "pass" for r in reasons)  # no explicit reason — anomalous
        ):
            flag = " *** ANOMALOUS_OUTRANK_NO_FILTERREASON ***"
            outrank_flags.append(cand.game_id)
        elif status == "rejected" and min_acc is not None and float(cand.edge) > min_acc:
            flag = " (outranks some accepted; has FilterReason)"
        print(
            f"  #{i:02d} edge={float(cand.edge):.4f} {status} gid={cand.game_id} "
            f"{g['away_team']} @ {g['home_team']} reasons={reasons}{flag}"
        )

    print("\n=== ACCEPT LOOP (stake / before_cap / exposure) ===")
    print(f"loop_policy={loop_policy}")
    for row in loop_rows:
        if row["action"] in {"ACCEPT", "REJECT_CONTINUE"} or row["bets_before"] > 0:
            print(
                f"  gid={row['game_id']} edge={row['edge']:.4f} "
                f"stake={row['stake']:.4f} before_cap={row['before_cap']:.4f} "
                f"weekly_before={row['weekly_exposure_before']:.4f} "
                f"weekly_room={row['weekly_room']:.4f} bets_before={row['bets_before']} "
                f"action={row['action']} reasons={row['reasons']}"
            )

    # Write sidecar — enable social only in-process for export_social_candidates
    cfg_write = cfg.model_copy(
        update={"social": cfg.social.model_copy(update={"enabled": True})}
    )
    acc_dicts, rej_dicts = records_from_filter_result(
        accepted, rejected, details=details, betting=cfg.betting
    )
    publish_result = {
        "season": SEASON,
        "week": WEEK,
        "refresh_kind": REFRESH_KIND,
        "published_at": as_of.isoformat().replace("+00:00", "Z"),
        "fixture": False,
        "accepted": acc_dicts,
        "rejected": rej_dicts,
    }
    path = export_social_candidates(publish_result, cfg_write)
    print(f"\nsidecar_path={path}")

    # STOP checks
    print("\n=== STOP CHECKS ===")
    if outrank_flags:
        print(f"STOP: anomalous outrank without FilterReason: {outrank_flags}")
    if "CONTINUES" in loop_policy:
        print("STOP: accept loop CONTINUES past the cap (production semantics)")
    if any_baseline_false:
        print("STOP: accepted candidate with baseline_convention_eligible=False")


if __name__ == "__main__":
    main()
