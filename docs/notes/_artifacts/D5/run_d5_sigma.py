"""D5 Part 2: refit sigma head with §5.2 feature set; informativeness + S0–S4."""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from ncaa_quant.evaluation.backtest_runner import load_staged_games
from ncaa_quant.evaluation.d3_eval import part2_bakeoff
from ncaa_quant.evaluation.d4_eval import (
    CANONICAL_V2_SHA,
    build_rating_feature_matrix,
    load_canonical_v2_frame,
    part2_informativeness_gated,
    revive_sigma_walkforward,
    verify_canonical_v2_sha,
)
from ncaa_quant.evaluation.d5_eval import audit_sigma_feature_set
from ncaa_quant.evaluation.production_stack import build_observations_from_staged
from ncaa_quant.ratings.state_space import run_filter

ROOT = Path(__file__).resolve().parents[4]
ART = ROOT / "docs" / "notes" / "_artifacts" / "D5"
PRED = ROOT / "data" / "backtests" / "task23_fundamental" / "fundamental" / "predictions_enriched.parquet"


def _load_advanced(seasons: list[int]) -> pd.DataFrame:
    root = ROOT / "data" / "staged" / "advanced_box"
    paths = list(root.rglob("*.parquet"))
    if not paths:
        return pd.DataFrame()
    adv = pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True)
    if "season" in adv.columns:
        adv = adv.loc[adv["season"].isin(seasons)]
    return adv


def main() -> None:
    ART.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    sha = verify_canonical_v2_sha(ROOT / "docs" / "notes" / "_artifacts" / "D3" / "canonical_v2.json")
    assert sha == CANONICAL_V2_SHA

    frame = load_canonical_v2_frame(PRED, exclude_2019_w1_4=True)
    seasons = sorted(int(s) for s in frame["season"].unique())
    games = load_staged_games(str(ROOT / "data" / "staged"), list(range(2014, max(seasons) + 1)))
    gmap = games.set_index("game_id")[["home_team_id", "away_team_id"]]
    frame = frame.join(gmap, on="game_id", how="left")

    print("Stage-1 filter…", flush=True)
    adv = _load_advanced(list(range(2014, max(seasons) + 1)))
    obs, _, _ = build_observations_from_staged(advanced=adv, games=games)
    filt = run_filter(obs, record_weekly=False)
    features = build_rating_feature_matrix(frame, games, filt)
    audit = audit_sigma_feature_set(list(features.columns))
    # Distinguish all-null columns.
    null_rates = {
        c: float(pd.to_numeric(features[c], errors="coerce").isna().mean())
        for c in features.columns
        if c != "game_id"
    }
    audit["null_rates"] = null_rates
    audit["effectively_absent"] = [
        c
        for c in ("expected_possessions",)
        if c in features.columns and null_rates.get(c, 1.0) > 0.99
    ]

    print("revive sigma…", flush=True)
    sigma_m, sigma_meta = revive_sigma_walkforward(frame, features)
    frame = frame.copy()
    frame["sigma_m"] = sigma_m

    info = part2_informativeness_gated(frame)
    bake = part2_bakeoff(frame)
    by = {r["scheme"]: r for r in bake["table"]}
    recommend_simplify = bool(
        by["S4"]["crps"] < by["S1"]["crps"] and by["S1"]["crps"] >= by["S0"]["crps"]
    )

    out = {
        "canonical_v2_sha": sha,
        "sigma_feature_audit": audit,
        "sigma_meta": sigma_meta,
        "informativeness": info,
        "bakeoff": bake,
        "recommend_week_bucket_sigma": recommend_simplify,
        "wall_clock_s": time.time() - t0,
    }
    path = ART / "d5_sigma.json"
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("recommend_week_bucket_sigma", "informativeness")}, indent=2, default=str))
    print("bakeoff", bake["table"])
    print("wrote", path, "in", out["wall_clock_s"], "s", flush=True)


if __name__ == "__main__":
    main()
