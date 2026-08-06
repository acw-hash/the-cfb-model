"""D6: join CFBD closes, Part-0 corrections, powered encompassing test."""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from ncaa_quant.evaluation.backtest_runner import load_staged_games
from ncaa_quant.evaluation.d4_eval import (
    CANONICAL_V2_SHA,
    load_canonical_v2_frame,
    verify_canonical_v2_sha,
)
from ncaa_quant.evaluation.d5_eval import load_encompassing_config
from ncaa_quant.evaluation.d6_eval import (
    D5_STOP_RULE_VERBATIM,
    assert_closes_eval_only,
    assert_derived_market_signs,
    diagnose_expected_possessions,
    join_cfbd_closes_for_evaluation,
    load_cfbd_lines,
    post_join_line_coverage,
    priced_vs_pooled_season_note,
    run_powered_encompassing,
    sigma_bakeoff_paired_cis,
    validate_joined_closes,
)
from ncaa_quant.evaluation.metrics import ats_home_outcomes, log_loss, ou_over_outcomes

ROOT = Path(__file__).resolve().parents[4]
ART = ROOT / "docs" / "notes" / "_artifacts" / "D6"
PRED = (
    ROOT
    / "data"
    / "backtests"
    / "task23_fundamental"
    / "fundamental"
    / "predictions_enriched.parquet"
)
STAGED = ROOT / "data" / "staged"
CFG = ROOT / "configs" / "eval" / "encompassing.yaml"


def _fbs_mask_for_frame(frame: pd.DataFrame, games: pd.DataFrame) -> np.ndarray:
    teams_root = STAGED / "teams"
    paths = list(teams_root.rglob("*.parquet"))
    fbs_ids: set[int] = set()
    if paths:
        teams = pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True)
        if {"team_id", "classification"} <= set(teams.columns):
            mask = teams["classification"].astype(str).str.casefold() == "fbs"
            fbs_ids = {int(t) for t in teams.loc[mask, "team_id"]}
    if not fbs_ids or not {"home_team_id", "away_team_id"} <= set(games.columns):
        return np.ones(len(frame), dtype=bool)
    gmap = games.drop_duplicates("game_id").set_index("game_id")
    hid = frame["game_id"].map(gmap["home_team_id"])
    aid = frame["game_id"].map(gmap["away_team_id"])
    return hid.isin(fbs_ids).to_numpy() & aid.isin(fbs_ids).to_numpy()


def _uncalibrated_ll(frame: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    out: dict = {}
    scored = frame.copy()
    mu = pd.to_numeric(scored["pred_margin"], errors="coerce").to_numpy(dtype=float)
    sig = pd.to_numeric(scored["sigma_m"], errors="coerce").to_numpy(dtype=float)
    y = pd.to_numeric(scored["realized_margin"], errors="coerce").to_numpy(dtype=float)
    p_ml = stats.norm.cdf(mu / np.maximum(sig, 1e-8))
    y_ml = (y > 0).astype(float)
    m = np.isfinite(p_ml) & np.isfinite(y_ml) & np.isfinite(y)
    out["ml"] = {"n": int(m.sum()), "log_loss": float(log_loss(p_ml[m], y_ml[m]))}

    sp = pd.to_numeric(scored["spread_close"], errors="coerce").to_numpy(dtype=float)
    p_ats = stats.norm.cdf((mu + sp) / np.maximum(sig, 1e-8))
    y_ats = ats_home_outcomes(y, sp)
    m = np.isfinite(p_ats) & np.isfinite(y_ats)
    out["ats_close"] = {
        "n": int(m.sum()),
        "log_loss": float(log_loss(p_ats[m], y_ats[m])) if m.any() else float("nan"),
    }

    mt = pd.to_numeric(scored["pred_total"], errors="coerce").to_numpy(dtype=float)
    st = pd.to_numeric(scored["sigma_t"], errors="coerce").to_numpy(dtype=float)
    tot = pd.to_numeric(scored["total_close"], errors="coerce").to_numpy(dtype=float)
    yt = pd.to_numeric(scored["realized_total"], errors="coerce").to_numpy(dtype=float)
    p_ou = stats.norm.cdf((mt - tot) / np.maximum(st, 1e-8))
    y_ou = ou_over_outcomes(yt, tot)
    m = np.isfinite(p_ou) & np.isfinite(y_ou)
    out["ou_close"] = {
        "n": int(m.sum()),
        "log_loss": float(log_loss(p_ou[m], y_ou[m])) if m.any() else float("nan"),
    }
    scored["p_ats_home"] = p_ats
    scored["p_ou_over"] = p_ou
    return out, scored


def main() -> None:
    ART.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    sha = verify_canonical_v2_sha(
        ROOT / "docs" / "notes" / "_artifacts" / "D3" / "canonical_v2.json"
    )
    assert sha == CANONICAL_V2_SHA

    assert_closes_eval_only(
        ["home_off_epa", "away_def_epa", "rating_uncertainty", "week", "abs_pred_margin"]
    )

    frame = load_canonical_v2_frame(PRED, exclude_2019_w1_4=True)
    seasons = sorted(int(s) for s in frame["season"].unique())
    lines = load_cfbd_lines(STAGED, seasons=list(range(2014, max(seasons) + 1)))
    games = load_staged_games(str(STAGED), list(range(2014, max(seasons) + 1)))

    joined, join_meta = join_cfbd_closes_for_evaluation(frame, lines, only_fill_null=True)
    coverage = post_join_line_coverage(joined, canonical_seasons=seasons)
    validation = validate_joined_closes(joined, lines, n_spot=25, seed=0)
    if not validation["passed"]:
        raise SystemExit(f"joined close validation failed: {validation}")

    priced_note = priced_vs_pooled_season_note(joined)
    exp_pos = diagnose_expected_possessions(
        feature_store_root=ROOT / "data" / "features",
        registry_has_name=True,
    )

    unc, scored = _uncalibrated_ll(joined)
    sign_report = assert_derived_market_signs(scored)

    print("sigma bake-off paired CIs…", flush=True)
    sigma_cis = sigma_bakeoff_paired_cis(joined, n_boot=800, seed=0)

    cfg = load_encompassing_config(CFG)
    fbs_mask = _fbs_mask_for_frame(joined, games)

    print("powered encompassing…", flush=True)
    enc = run_powered_encompassing(joined, cfg, fbs_mask=fbs_mask)

    out = {
        "canonical_v2_sha": sha,
        "stop_rule_verbatim": D5_STOP_RULE_VERBATIM,
        "join": join_meta,
        "coverage": coverage,
        "validation": {
            **{k: v for k, v in validation.items() if k != "spot_check"},
            "spot_check": {
                "n": validation["spot_check"]["n"],
                "n_match": validation["spot_check"]["n_match"],
                "match_rate": validation["spot_check"]["match_rate"],
                "rows": validation["spot_check"]["rows"],
            },
        },
        "priced_vs_pooled": priced_note,
        "expected_possessions": exp_pos,
        "uncalibrated": unc,
        "sign_gate": sign_report,
        "sigma_bakeoff_cis": sigma_cis,
        "encompassing": enc,
        "wall_clock_s": time.time() - t0,
    }
    path = ART / "d6_results.json"
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    joint = enc["joint"]
    stop = enc["stop_rule"]
    print(
        json.dumps(
            {
                "n": joint["n"],
                "b2": joint["b2"],
                "ci95": joint["ci95"],
                "verdict_status": stop["status"],
                "verdict": stop["verdict_sentence"],
                "coverage_total_spread": coverage["total_n_with_spread_close"],
                "sigma_prefer": sigma_cis["prefer"],
            },
            indent=2,
            default=str,
        ),
        flush=True,
    )
    print("wrote", path, "in", out["wall_clock_s"], "s", flush=True)


if __name__ == "__main__":
    main()
