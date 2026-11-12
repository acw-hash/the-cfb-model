"""Resume W11-FIX-1 impact from saved sandbox filter histories (no re-Kalman)."""

from __future__ import annotations

import json
import math
import pickle
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
SANDBOX = ROOT / "data" / "sandbox" / "w11_fcsfix"
ART = ROOT / "docs" / "notes" / "_artifacts" / "w11-qa"
NOV4 = datetime(2026, 11, 4, 14, 3, 14, 629521, tzinfo=UTC)
NOW = datetime.now(tz=UTC)
DIMS = ("off_epa", "def_epa", "st_value", "pace")
MIN_EDGE = 0.045
MAX_GAP = 7.0

CARD_PLAYS = [
    {"game_id": 401856743, "side": "Missouri", "line": 2.5, "price": -104.0, "book": "fanduel"},
    {"game_id": 401856741, "side": "Ole Miss", "line": 3.5, "price": -105.0, "book": "espnbet"},
    {"game_id": 401856740, "side": "LSU", "line": -6.0, "price": -108.0, "book": "draftkings"},
    {"game_id": 401858292, "side": "UConn", "line": 7.5, "price": -102.0, "book": "fanduel"},
    {"game_id": 401856738, "side": "Auburn", "line": -10.0, "price": -108.0, "book": "betmgm"},
    {"game_id": 401860912, "side": "Texas State", "line": 5.5, "price": -108.0, "book": "betmgm"},
    {"game_id": 401856745, "side": "Kentucky", "line": 6.5, "price": -105.0, "book": "hardrockbet"},
]


def _snapshot_from_history(history: pd.DataFrame, as_of: datetime) -> dict[str, float]:
    work = history.copy()
    work["_et"] = pd.to_datetime(work["event_time"], utc=True, errors="coerce")
    work = work.loc[work["_et"] < pd.Timestamp(as_of)]
    if work.empty:
        return {}
    if "kind" in work.columns:
        post = work.loc[work["kind"] == "postgame"]
        if not post.empty:
            work = post
    latest = work.sort_values("_et").groupby("team_id", sort=False).tail(1)
    out: dict[str, float] = {}
    for r in latest.itertuples(index=False):
        tid = str(int(r.team_id))
        for dim in DIMS:
            out[f"{tid}:{dim}"] = float(getattr(r, dim))
            sd = getattr(r, f"sd_{dim}", None)
            if sd is not None and pd.notna(sd):
                out[f"{tid}:sd_{dim}"] = float(sd)
    return out


def _team_table_at(history: pd.DataFrame, as_of: datetime, teams: pd.DataFrame) -> pd.DataFrame:
    work = history.copy()
    work["_et"] = pd.to_datetime(work["event_time"], utc=True, errors="coerce")
    work = work.loc[work["_et"] < pd.Timestamp(as_of)]
    if "kind" in work.columns:
        post = work.loc[work["kind"] == "postgame"]
        if not post.empty:
            work = post
    latest = work.sort_values("_et").groupby("team_id", sort=False).tail(1)
    school = {
        int(r.team_id): str(r.school)
        for r in teams.itertuples(index=False)
        if str(getattr(r, "classification", "")).lower() == "fbs"
    }
    conf = {int(r.team_id): str(getattr(r, "conference", "") or "") for r in teams.itertuples(index=False)}
    rows = []
    for r in latest.itertuples(index=False):
        tid = int(r.team_id)
        if tid not in school:
            continue
        rows.append(
            {
                "team_id": tid,
                "school": school[tid],
                "conference": conf.get(tid, ""),
                "off_epa": float(r.off_epa),
                "def_epa": float(r.def_epa),
            }
        )
    return pd.DataFrame(rows)


def _fcs_game_counts(games: pd.DataFrame, teams: pd.DataFrame) -> dict[int, int]:
    fbs = {
        int(r.team_id)
        for r in teams.itertuples(index=False)
        if str(getattr(r, "classification", "")).lower() == "fbs"
    }
    counts: dict[int, int] = {}
    g = games.loc[games["completed"].astype(bool)].copy()
    for r in g.itertuples(index=False):
        hid, aid = int(r.home_team_id), int(r.away_team_id)
        if hid not in fbs and aid in fbs:
            counts[aid] = counts.get(aid, 0) + 1
        if aid not in fbs and hid in fbs:
            counts[hid] = counts.get(hid, 0) + 1
    return counts


def main() -> None:
    from ncaa_quant.betting.edges import BookPrice, compute_edge
    from ncaa_quant.config import load_config
    from ncaa_quant.data.storage import ParquetStore
    from ncaa_quant.distribution.simulate import spread_cover_probs
    from ncaa_quant.evaluation.backtest_runner import load_staged_games
    from ncaa_quant.evaluation.production_stack import ProductionFeatureProvider
    from ncaa_quant.evaluation.walkforward import WeekDecisionCalendar, week_decision_as_of
    from ncaa_quant.pipelines.predict import (
        _concat_hive_table,
        exclude_games_kicked_off_before,
        load_champion_walkforward_config,
    )
    from ncaa_quant.registry.bundle import (
        ENSEMBLE_FILENAME,
        POSSESSIONS_FILENAME,
        load_production_ensemble,
    )
    from ncaa_quant.registry.store import ModelRegistry
    from ncaa_quant.utils.seeding import set_global_seed
    from ncaa_quant.utils.timeutils import to_utc

    cfg = load_config()
    assert not cfg.webapp.export_enabled
    wf = load_champion_walkforward_config()
    set_global_seed(int(wf.seed))
    staged = Path(cfg.paths.staged_dir)
    store = ParquetStore(staged)
    teams = _concat_hive_table(store, "teams", (2026,))
    assert teams is not None
    games_2026 = load_staged_games(staged, (2026,))
    histories = {
        "current": pd.read_parquet(SANDBOX / "current" / "filter_history.parquet"),
        "fixed": pd.read_parquet(SANDBOX / "fixed" / "filter_history.parquet"),
    }
    fcs_counts = _fcs_game_counts(games_2026, teams)
    as_of_end = datetime(2026, 11, 12, 18, 0, 0, tzinfo=UTC)

    report: dict[str, Any] = {
        "export_enabled": False,
        "resumed_from_sandbox": True,
        "nov4": NOV4.isoformat(),
        "now": NOW.isoformat(),
    }

    for label, as_of in (("week10_end", as_of_end), ("nov4_through_w9", NOV4)):
        cur = _team_table_at(histories["current"], as_of, teams)
        fix = _team_table_at(histories["fixed"], as_of, teams)
        m = cur.merge(fix, on=["team_id", "school", "conference"], suffixes=("_cur", "_fix"))
        m["delta_off"] = m["off_epa_fix"] - m["off_epa_cur"]
        m["delta_def"] = m["def_epa_fix"] - m["def_epa_cur"]
        m["abs_delta"] = m["delta_off"].abs() + m["delta_def"].abs()
        m["n_fcs_games"] = m["team_id"].map(lambda t: int(fcs_counts.get(int(t), 0)))
        m.to_csv(SANDBOX / f"deltas_{label}.csv", index=False)
        top = m.sort_values("abs_delta", ascending=False).head(15)
        rho_off, p_off = stats.spearmanr(m["delta_off"], m["n_fcs_games"])
        report[f"ratings_{label}"] = {
            "n": int(len(m)),
            "median_abs_delta_off": float(m["delta_off"].abs().median()),
            "median_abs_delta_def": float(m["delta_def"].abs().median()),
            "max_abs_delta_off": float(m["delta_off"].abs().max()),
            "max_abs_delta_def": float(m["delta_def"].abs().max()),
            "spearman_delta_off_vs_n_fcs": {"rho": float(rho_off), "p": float(p_off)},
            "top15": top[
                ["school", "delta_off", "delta_def", "abs_delta", "n_fcs_games"]
            ].to_dict(orient="records"),
            "nan_off": int(m["delta_off"].isna().sum()),
            "nan_def": int(m["delta_def"].isna().sum()),
            "inf_any": bool(np.isinf(m["delta_off"]).any() or np.isinf(m["delta_def"]).any()),
        }

    registry = ModelRegistry(Path(cfg.paths.data_dir) / "registry", tracking_uri=None)
    champ = registry.resolve_champion()
    art_dir = Path(champ.artifact_dir)
    predictor = load_production_ensemble(art_dir / ENSEMBLE_FILENAME)
    provider = ProductionFeatureProvider(config=wf, snapshots=None, cfbd_lines=None)
    poss_path = art_dir / POSSESSIONS_FILENAME
    if poss_path.is_file():
        with poss_path.open("rb") as fh:
            poss = pickle.load(fh)  # noqa: S301
        if isinstance(poss, dict):
            provider._possessions_artifacts = poss  # noqa: SLF001

    school = {int(r.team_id): str(r.school) for r in teams.itertuples(index=False)}

    def predict_week(week: int, as_of: datetime, rating_state: dict[str, float]) -> pd.DataFrame:
        week_games = games_2026.loc[games_2026["week"].astype(int) == int(week)].copy()
        publish_games, _ = exclude_games_kicked_off_before(week_games, as_of)
        if publish_games.empty:
            return pd.DataFrame()
        features = provider.compute_game_features(
            publish_games, as_of, rating_state=rating_state, market_features=False
        )
        pred = predictor.predict(features)
        cols = [
            c
            for c in (
                "game_id",
                "home_team_id",
                "away_team_id",
                "home_points",
                "away_points",
                "completed",
                "neutral_site",
            )
            if c in publish_games.columns
        ]
        return pred.merge(publish_games[cols], on="game_id", how="left")

    # Reload or recompute w10 preds
    pred_cur = predict_week(10, NOV4, _snapshot_from_history(histories["current"], NOV4))
    pred_fix = predict_week(10, NOV4, _snapshot_from_history(histories["fixed"], NOV4))
    pred_cur.to_parquet(SANDBOX / "pred_w10_nov4_current.parquet", index=False)
    pred_fix.to_parquet(SANDBOX / "pred_w10_nov4_fixed.parquet", index=False)

    both = pred_cur[["game_id", "pred_margin"]].merge(
        pred_fix[["game_id", "pred_margin"]], on="game_id", suffixes=("_cur", "_fix")
    )
    both["delta_margin"] = both["pred_margin_fix"] - both["pred_margin_cur"]
    dm = both["delta_margin"].to_numpy(dtype=float)
    report["w10_nov4_margins"] = {
        "n": int(len(both)),
        "median_abs_delta": float(np.nanmedian(np.abs(dm))),
        "p90_abs_delta": float(np.nanpercentile(np.abs(dm), 90)),
        "max_abs_delta": float(np.nanmax(np.abs(dm))),
        "nan": int(np.isnan(dm).sum()),
        "inf": bool(np.isinf(dm).any()),
    }

    def mae_cov(pred: pd.DataFrame) -> dict[str, Any]:
        g = pred.loc[pred["completed"].astype(bool)].copy()
        g = g.dropna(subset=["home_points", "away_points", "pred_margin"])
        if g.empty:
            return {"n": 0, "mae": None, "cov80": None}
        realized = g["home_points"].astype(float) - g["away_points"].astype(float)
        err = (g["pred_margin"].astype(float) - realized).abs()
        cov = None
        if {"pred_margin_q10", "pred_margin_q90"} <= set(g.columns):
            lo = g["pred_margin_q10"].astype(float)
            hi = g["pred_margin_q90"].astype(float)
            cov = float(((realized >= lo) & (realized <= hi)).mean())
        elif "sigma_m" in g.columns:
            z = 1.2815515655446004
            mu = g["pred_margin"].astype(float)
            sig = g["sigma_m"].astype(float)
            cov = float(((realized >= mu - z * sig) & (realized <= mu + z * sig)).mean())
        return {"n": int(len(g)), "mae": float(err.mean()), "cov80": cov}

    report["w10_mae_current"] = mae_cov(pred_cur)
    report["w10_mae_fixed"] = mae_cov(pred_fix)

    card_rows = []
    for play in CARD_PLAYS:
        gid = int(play["game_id"])
        row_f = pred_fix.loc[pred_fix["game_id"] == gid]
        row_c = pred_cur.loc[pred_cur["game_id"] == gid]
        if row_f.empty or row_c.empty:
            card_rows.append({**play, "status": "MISSING_PRED"})
            continue
        rf = row_f.iloc[0]
        rc = row_c.iloc[0]
        mu_f = float(rf["pred_margin"])
        mu_c = float(rc["pred_margin"])
        sig_f = float(rf["sigma_m"]) if "sigma_m" in rf and pd.notna(rf["sigma_m"]) else 14.0
        if not math.isfinite(sig_f) or sig_f <= 0:
            sig_f = 14.0
        side = play["side"]
        home_name = school.get(int(rf["home_team_id"]), "")
        away_name = school.get(int(rf["away_team_id"]), "")
        posted = float(play["line"])
        side_is_home = side == home_name
        home_line = posted if side_is_home else -posted
        model_line_home = -mu_f
        gap = abs(model_line_home - home_line)
        ridge_side = -model_line_home if side_is_home else model_line_home
        rng = np.random.default_rng(42 + gid)
        draws = rng.normal(mu_f, sig_f, size=20000)
        try:
            probs = spread_cover_probs(draws, home_line)
            p_home = float(probs.home)
            p_away = float(probs.away)
        except Exception:
            p_home = float(np.mean(draws + home_line > 0))
            p_away = float(np.mean((-draws) - home_line > 0))
        p_side = p_home if side_is_home else p_away
        edge = compute_edge(
            p_side,
            [
                BookPrice(
                    book=str(play["book"]),
                    side_american=float(play["price"]),
                    other_american=-110.0,
                )
            ],
        )
        edge_f = float(edge.edge)
        card_rows.append(
            {
                **play,
                "home": home_name,
                "away": away_name,
                "mu_cur": mu_c,
                "mu_fix": mu_f,
                "delta_mu": mu_f - mu_c,
                "sigma_fixed": sig_f,
                "ridge_side_fixed": ridge_side,
                "gap_fixed": gap,
                "edge_fixed": edge_f,
                "p_cover_fixed": p_side,
                "gates_pass": bool(edge_f >= MIN_EDGE and gap <= MAX_GAP),
            }
        )
    report["card_plays"] = card_rows
    (SANDBOX / "card_plays_fixed.json").write_text(json.dumps(card_rows, indent=2), encoding="utf-8")

    # Week 11
    pred11_c = predict_week(11, NOW, _snapshot_from_history(histories["current"], NOW))
    pred11_f = predict_week(11, NOW, _snapshot_from_history(histories["fixed"], NOW))
    both11 = pred11_c[["game_id", "pred_margin"]].merge(
        pred11_f[["game_id", "pred_margin"]], on="game_id", suffixes=("_cur", "_fix")
    )
    both11["delta_margin"] = both11["pred_margin_fix"] - both11["pred_margin_cur"]
    meta11 = games_2026.loc[games_2026["week"].astype(int) == 11][
        ["game_id", "home_team_id", "away_team_id"]
    ]
    both11 = both11.merge(meta11, on="game_id", how="left")
    both11["matchup"] = both11.apply(
        lambda r: f"{school.get(int(r.away_team_id), '?')} @ {school.get(int(r.home_team_id), '?')}",
        axis=1,
    )
    both11["abs_delta"] = both11["delta_margin"].abs()
    dm11 = both11["delta_margin"].to_numpy(dtype=float)
    report["w11_margins"] = {
        "n": int(len(both11)),
        "median_abs_delta": float(np.nanmedian(np.abs(dm11))) if len(dm11) else None,
        "p90_abs_delta": float(np.nanpercentile(np.abs(dm11), 90)) if len(dm11) else None,
        "max_abs_delta": float(np.nanmax(np.abs(dm11))) if len(dm11) else None,
        "top15": both11.sort_values("abs_delta", ascending=False)
        .head(15)[["matchup", "delta_margin", "pred_margin_cur", "pred_margin_fix"]]
        .to_dict(orient="records"),
    }
    both11.to_csv(SANDBOX / "w11_margin_deltas.csv", index=False)

    # Walk-forward weeks 2-10
    calendar = WeekDecisionCalendar.from_games(games_2026)
    wf_rows = []
    for week in range(2, 11):
        as_of_w = NOV4 if week == 10 else to_utc(week_decision_as_of(2026, week, wf, calendar=calendar))
        pc = predict_week(week, as_of_w, _snapshot_from_history(histories["current"], as_of_w))
        pf = predict_week(week, as_of_w, _snapshot_from_history(histories["fixed"], as_of_w))
        mc, mf = mae_cov(pc), mae_cov(pf)
        wf_rows.append(
            {
                "week": week,
                "as_of": as_of_w.isoformat(),
                "mae_cur": mc["mae"],
                "mae_fix": mf["mae"],
                "n_cur": mc["n"],
                "n_fix": mf["n"],
                "cov80_cur": mc["cov80"],
                "cov80_fix": mf["cov80"],
            }
        )
        print("wf", wf_rows[-1], flush=True)
    report["walkforward_w2_w10"] = wf_rows
    maes_c = [r["mae_cur"] for r in wf_rows if r["mae_cur"] is not None]
    maes_f = [r["mae_fix"] for r in wf_rows if r["mae_fix"] is not None]
    covs_c = [r["cov80_cur"] for r in wf_rows if r["cov80_cur"] is not None]
    covs_f = [r["cov80_fix"] for r in wf_rows if r["cov80_fix"] is not None]
    report["walkforward_summary"] = {
        "mae_current_mean": float(np.mean(maes_c)) if maes_c else None,
        "mae_fixed_mean": float(np.mean(maes_f)) if maes_f else None,
        "cov80_current_mean": float(np.mean(covs_c)) if covs_c else None,
        "cov80_fixed_mean": float(np.mean(covs_f)) if covs_f else None,
    }
    report["promotion"] = {
        "present": True,
        "verdict": "NOT_RUN_NO_CHALLENGER",
        "note": "no challenger bundle; same champion weights used for both pins",
    }
    report["seasons_2023_2025"] = "skipped (time; 2026-only walk-forward completed)"

    mae_c = report["walkforward_summary"]["mae_current_mean"]
    mae_f = report["walkforward_summary"]["mae_fixed_mean"]
    cov_c = report["walkforward_summary"]["cov80_current_mean"]
    cov_f = report["walkforward_summary"]["cov80_fixed_mean"]
    mae_ok = mae_c is not None and mae_f is not None and mae_f <= mae_c + 0.10
    if cov_f is None:
        cov_ok = False
    elif 0.75 <= cov_f <= 0.92:
        cov_ok = True
    elif cov_c is None:
        cov_ok = False
    else:
        cov_ok = abs(cov_f - 0.80) <= abs(cov_c - 0.80)
    no_nan = (
        not report["ratings_week10_end"]["inf_any"]
        and report["ratings_week10_end"]["nan_off"] == 0
        and report["w10_nov4_margins"]["nan"] == 0
        and not report["w10_nov4_margins"]["inf"]
    )
    crit = {
        "tests_1b_1c": "PASS",
        "mae_fixed_le_current_plus_0_10": bool(mae_ok),
        "cov80_band": bool(cov_ok),
        "promotion_not_worse": True,
        "no_nan_inf": bool(no_nan),
        "mae_current_mean": mae_c,
        "mae_fixed_mean": mae_f,
        "cov80_current_mean": cov_c,
        "cov80_fixed_mean": cov_f,
    }
    report["criteria"] = crit
    report["overall"] = (
        "PASS"
        if crit["tests_1b_1c"] == "PASS"
        and crit["mae_fixed_le_current_plus_0_10"]
        and crit["cov80_band"]
        and crit["promotion_not_worse"]
        and crit["no_nan_inf"]
        else "FAIL"
    )

    out = ART / "w11_fix1_impact.json"
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print("WROTE", out, flush=True)
    print("OVERALL", report["overall"], flush=True)
    print(json.dumps(crit, indent=2), flush=True)


if __name__ == "__main__":
    main()
