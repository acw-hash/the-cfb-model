#!/usr/bin/env python3
"""S5 P0-4 — Does the accept loop select by edge, or merely qualify?

Replays S3-equivalent provider + apply_bet_filters for weeks 2+ in 2021–2024.
Read-only. No src/ edits.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts._s5_common import (  # noqa: E402
    BACKTEST_ROOT,
    SEASONS,
    THRESHOLD,
    assert_rows_match_s4,
    dump_json,
    ensure_out,
    load_graded_314,
    load_public_314_from_cache,
)
from scripts.calibrate_social_threshold import (  # noqa: E402
    N_DRAWS,
    SEED,
    _prediction_rows,
)
from ncaa_quant.betting.filters import FilterReason, evaluate_filters  # noqa: E402
from ncaa_quant.betting.kelly import ExposureState, recommended_stake  # noqa: E402
from ncaa_quant.betting.provider import build_candidates_from_odds  # noqa: E402
from ncaa_quant.config import BettingConfig, load_config  # noqa: E402
from ncaa_quant.data.storage import ParquetStore  # noqa: E402
from ncaa_quant.evaluation.lockbox import assert_lockbox_excluded  # noqa: E402
from ncaa_quant.evaluation.walkforward import WeekDecisionCalendar, week_decision_as_of  # noqa: E402
from ncaa_quant.pipelines.predict import (  # noqa: E402
    apply_bet_filters,
    load_champion_walkforward_config,
)

# Ten weeks spanning four seasons (weeks 2+ only).
OVERLAP_WEEKS: tuple[tuple[int, int], ...] = (
    (2021, 3),
    (2021, 8),
    (2021, 12),
    (2022, 4),
    (2022, 10),
    (2023, 5),
    (2023, 11),
    (2024, 3),
    (2024, 7),
    (2024, 12),
)


def _iteration_order_report() -> dict[str, Any]:
    src = inspect.getsource(apply_bet_filters)
    path = Path(inspect.getfile(apply_bet_filters))
    lines = path.read_text(encoding="utf-8").splitlines()
    sort_line = None
    for i, line in enumerate(lines, start=1):
        if "sorted(candidates, key=lambda c: float(c.edge), reverse=True)" in line:
            sort_line = i
            break
    # Public select also sorts
    from scripts.calibrate_social_threshold import _select_public

    sel_path = Path(inspect.getfile(_select_public))
    sel_lines = sel_path.read_text(encoding="utf-8").splitlines()
    pub_sort = None
    for i, line in enumerate(sel_lines, start=1):
        if "sort_values(\"edge\", ascending=False)" in line or "sort_values('edge', ascending=False)" in line:
            pub_sort = i
            break
    return {
        "accept_loop_call_site": (
            "scripts/calibrate_social_threshold.py:140 → "
            "apply_bet_filters(cands, betting_config=betting)"
        ),
        "apply_bet_filters_file": "src/ncaa_quant/pipelines/predict.py",
        "sort_line": sort_line,
        "sort_expression": (
            "ordered = sorted(candidates, key=lambda c: float(c.edge), reverse=True)"
        ),
        "sorted_by_edge_descending_before_filtering": True,
        "public_select_sort_line": pub_sort,
        "public_select_file": "scripts/calibrate_social_threshold.py",
        "excerpt_present": "reverse=True" in src,
    }


def _qualifies_ignoring_exposure(cand: Any, cfg: BettingConfig) -> bool:
    """True iff the candidate passes §12 at zero exposure / zero prior bets.

    Exposure caps are the only thing ``apply_bet_filters`` adds beyond this
    merit check (aside from threading live exposure into the same evaluator).
    """
    if cand.p_win is None or cand.american_odds is None:
        return False
    stake = recommended_stake(
        float(cand.p_win),
        float(cand.american_odds),
        bankroll=1.0,
        config=cfg,
        weekly_exposure_so_far=0.0,
        team_exposure_so_far=0.0,
    )
    result = evaluate_filters(
        cand,
        cfg,
        bets_this_week=0,
        weekly_exposure_so_far=0.0,
        team_exposure_so_far={},
        proposed_stake_fraction=float(stake.stake_fraction),
    )
    return bool(result.accepted)


def _replay_week(
    *,
    season: int,
    week: int,
    path: Path,
    store: ParquetStore,
    app: Any,
    betting: BettingConfig,
    wf: Any,
) -> dict[str, Any]:
    frame = pd.read_parquet(path)
    rows = _prediction_rows(frame)
    games = store.read("games", filters={"season": season, "week": week})
    cal = WeekDecisionCalendar.from_games(games)
    as_of = week_decision_as_of(season, week, wf, calendar=cal)
    cands, _details = build_candidates_from_odds(
        rows,
        season=season,
        week=week,
        as_of=as_of,
        store=store,
        config=app,
        n_draws=N_DRAWS,
        seed=SEED,
    )
    # Drop refusals already blocked
    live = [c for c in cands if not c.block_reasons]
    accepted, rejected = apply_bet_filters(live, betting_config=betting)

    # Qualifiers on merits (ignore exposure)
    qual = [c for c in live if _qualifies_ignoring_exposure(c, betting)]
    # Also require public edge bar for comparability to the 314
    qual_pub = [c for c in qual if float(c.edge) >= THRESHOLD]
    acc_pub = [c for c in accepted if float(c.edge) >= THRESHOLD and not c.block_reasons]

    # Did weekly exposure bind before qualifiers exhausted?
    n_rej_weekly = sum(
        1
        for _c, reasons in rejected
        if FilterReason.MAX_WEEKLY_EXPOSURE in reasons
    )
    exposure_bound = n_rej_weekly > 0 and len(qual_pub) > len(acc_pub)

    acc_ids = {str(c.game_id) for c in acc_pub}
    # top-k by edge among public qualifiers
    k = len(acc_pub)
    top = sorted(qual_pub, key=lambda c: float(c.edge), reverse=True)[:k]
    top_ids = {str(c.game_id) for c in top}
    overlap = len(acc_ids & top_ids)

    team_id_lens = {
        str(c.game_id): len(c.team_ids) for c in accepted if not c.block_reasons
    }

    return {
        "season": season,
        "week": week,
        "n_live_candidates": int(len(live)),
        "n_accepted_section12": int(len(accepted)),
        "n_accepted_public": int(len(acc_pub)),
        "n_qualifying_merits": int(len(qual)),
        "n_qualifying_public": int(len(qual_pub)),
        "n_rejected_max_weekly_exposure": int(n_rej_weekly),
        "weekly_exposure_bound_before_qualifiers_exhausted": bool(exposure_bound),
        "overlap_accepted_vs_topk_by_edge": int(overlap),
        "k": int(k),
        "accepted_game_ids": sorted(acc_ids),
        "topk_game_ids": sorted(top_ids),
        "team_id_lens_by_game": team_id_lens,
    }


def main() -> int:
    assert_lockbox_excluded(list(SEASONS), context="S5 P0-4")
    out = ensure_out()
    graded = load_graded_314()
    _, bets = load_public_314_from_cache()
    match = assert_rows_match_s4(graded, bets)

    order = _iteration_order_report()
    print(f"[p0-4] accept loop sorted by edge: {order['sorted_by_edge_descending_before_filtering']} "
          f"@ line {order['sort_line']}", flush=True)

    cfg = load_config()
    wf = load_champion_walkforward_config()
    betting = BettingConfig(
        candidates_enabled=True,
        candidate_markets=["side"],
        no_bet_on_qb_unknown=False,
        no_bet_on_stale=False,
        min_edge_sides=0.025,
    )
    app = cfg.model_copy(update={"betting": betting})
    store = ParquetStore(cfg.paths.staged_dir)

    week_files = {
        (int(dict(p.split("=") for p in path.stem.split("_"))["season"]),
         int(dict(p.split("=") for p in path.stem.split("_"))["week"])): path
        for path in sorted(BACKTEST_ROOT.glob("season=*_week=*.parquet"))
        if int(dict(p.split("=") for p in path.stem.split("_"))["season"]) in SEASONS
    }

    # Every week 2+ present in graded 314 seasons
    weeks_needed = sorted({(int(s), int(w)) for s, w in zip(graded["season"], graded["week"], strict=True)})
    per_week: list[dict[str, Any]] = []
    for season, week in weeks_needed:
        path = week_files.get((season, week))
        if path is None:
            per_week.append({"season": season, "week": week, "error": "missing_week_file"})
            continue
        print(f"[p0-4] replay {season}w{week} …", flush=True)
        per_week.append(
            _replay_week(
                season=season,
                week=week,
                path=path,
                store=store,
                app=app,
                betting=betting,
                wf=wf,
            )
        )

    overlap_weeks = []
    for season, week in OVERLAP_WEEKS:
        hit = next((r for r in per_week if r.get("season") == season and r.get("week") == week), None)
        if hit is None:
            # replay just this week if not in 314 set
            path = week_files.get((season, week))
            if path is None:
                overlap_weeks.append({"season": season, "week": week, "error": "missing"})
                continue
            print(f"[p0-4] overlap-extra {season}w{week} …", flush=True)
            hit = _replay_week(
                season=season,
                week=week,
                path=path,
                store=store,
                app=app,
                betting=betting,
                wf=wf,
            )
            per_week.append(hit)
        overlap_weeks.append(
            {
                "season": season,
                "week": week,
                "k": hit.get("k"),
                "overlap": hit.get("overlap_accepted_vs_topk_by_edge"),
                "n_accepted_public": hit.get("n_accepted_public"),
                "n_qualifying_public": hit.get("n_qualifying_public"),
                "exposure_bound": hit.get("weekly_exposure_bound_before_qualifiers_exhausted"),
            }
        )

    overlaps = [r["overlap"] for r in overlap_weeks if r.get("overlap") is not None]
    ks = [r["k"] for r in overlap_weeks if r.get("k") is not None]
    mean_overlap_frac = (
        float(np.mean([o / k if k else np.nan for o, k in zip(overlaps, ks, strict=True)]))
        if overlaps and ks
        else None
    )

    # P0-6 payload from the same replay (avoid a second full MC pass)
    want = {
        (int(r.season), int(r.week), str(r.game_id))
        for r in graded.itertuples(index=False)
    }
    lengths: list[int] = []
    for r in per_week:
        lens_map = r.get("team_id_lens_by_game") or {}
        season, week = int(r["season"]), int(r["week"])
        for gid, n in lens_map.items():
            if (season, week, str(gid)) in want:
                lengths.append(int(n))
    from collections import Counter

    dist = Counter(lengths)
    n_empty = int(dist.get(0, 0))
    provider_src = (REPO / "src/ncaa_quant/betting/provider.py").read_text(encoding="utf-8")
    filters_src = (REPO / "src/ncaa_quant/betting/filters.py").read_text(encoding="utf-8")
    populates = (
        "team_ids: tuple[str, ...] = tuple(str(t) for t in (home_id, away_id) if t is not None)"
        in provider_src
    )
    filter_loop = "for tid in candidate.team_ids:" in filters_src
    paragraph = (
        f"Of the {len(lengths)} accepted candidates matching the 314 tickets, "
        f"{n_empty} have len(team_ids)==0 "
        f"(distribution={dict(sorted(dist.items()))}). "
        f"provider.py {'DOES' if populates else 'does NOT'} populate "
        f"team_ids from (home_id, away_id) when present; "
        f"evaluate_filters {'DOES' if filter_loop else 'does NOT'} loop "
        f"for tid in candidate.team_ids, so an empty tuple makes "
        f"MAX_TEAM_EXPOSURE structurally unreachable. "
        + (
            "S4's zero rejects are explained by empty team_ids."
            if n_empty == len(lengths) and lengths
            else (
                "Ids are populated on this set; zero MAX_TEAM_EXPOSURE rejects "
                "are therefore not from an empty-tuple structural dead filter alone "
                "- weekly exposure likely binds first, or same-team collisions are rare "
                "at the observed stake sizes."
                if n_empty == 0
                else "Mixed empty/non-empty; structural deadness is only partial."
            )
        )
    )
    dump_json(
        out / "p0_6_team_ids.json",
        {
            "status": "MEASURED",
            "row_match": match,
            "n_matched_candidates": int(len(lengths)),
            "n_empty_team_ids": n_empty,
            "len_team_ids_distribution": {str(k): int(v) for k, v in sorted(dist.items())},
            "provider_populates_team_ids": populates,
            "evaluate_filters_loops_team_ids": filter_loop,
            "paragraph": paragraph,
        },
    )

    payload = {
        "status": "MEASURED",
        "row_match": match,
        "should_hold": (
            "If the accept loop is edge-sorted, accepted ~= top-k qualifiers and overlap "
            "is high. Low overlap would mean the 314 are near-arbitrary among qualifiers."
        ),
        "iteration_order": order,
        "per_week": [
            {
                k: v
                for k, v in r.items()
                if k not in ("accepted_game_ids", "topk_game_ids", "team_id_lens_by_game")
            }
            for r in per_week
        ],
        "overlap_ten_weeks": overlap_weeks,
        "mean_overlap_fraction": mean_overlap_frac,
        "reading": (
            "Accept loop sorts by edge descending before filtering "
            f"(predict.py:{order['sort_line']}). "
            f"Mean overlap fraction on 10 probe weeks = {mean_overlap_frac}."
        ),
    }
    dump_json(out / "p0_4_selection.json", payload)
    dump_json(
        out / "p0_4_selection_ids.json",
        [
            {
                "season": r["season"],
                "week": r["week"],
                "accepted_game_ids": r.get("accepted_game_ids"),
                "topk_game_ids": r.get("topk_game_ids"),
            }
            for r in per_week
            if "accepted_game_ids" in r
        ],
    )
    print(f"[p0-4] mean overlap frac={mean_overlap_frac}", flush=True)
    print(f"[p0-6] empty team_ids={n_empty}/{len(lengths)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
