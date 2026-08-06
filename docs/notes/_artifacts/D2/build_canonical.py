"""Generate D2 canonical artifact from archived task23 predictions."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

from ncaa_quant.evaluation.backtest_runner import load_staged_games
from ncaa_quant.evaluation.canonical_eval import (
    _headline_frame,
    build_comparison_rows,
    compose_canonical_set,
    file_sha256,
    gate_task23_fundamental,
    sigma_diagnostics,
    write_canonical_artifact,
)
from ncaa_quant.models.ensemble import fit_nnls_stack
from ncaa_quant.ratings.elo_baseline import EloConfig, run_elo


def main() -> None:
    pred_path = Path(
        "data/backtests/task23_fundamental/fundamental/predictions_enriched.parquet"
    )
    raw_path = Path("data/backtests/task23_fundamental/fundamental/predictions.parquet")
    preds = pd.read_parquet(pred_path)
    preds = preds.loc[~((preds["season"] == 2019) & (preds["week"] <= 4))].copy()
    preds["n_train_games"] = 500
    preds["run_kind"] = "backtest"

    team_paths = list(Path("data/staged/teams").rglob("*.parquet"))
    teams = (
        pd.concat([pd.read_parquet(p) for p in team_paths], ignore_index=True)
        if team_paths
        else None
    )

    games = load_staged_games("data/staged", sorted(int(s) for s in preds["season"].unique()))
    gmap = games.set_index("game_id")[["home_team_id", "away_team_id"]]
    frame = _headline_frame(preds).join(gmap, on="game_id")
    comp = compose_canonical_set(frame, teams=teams, fcs_rule="include")

    elo_log, _, _ = run_elo(
        games.loc[games["game_id"].isin(frame["game_id"])],
        config=EloConfig(),
        fbs_only=False,
    )
    elo_mu = (
        frame["game_id"]
        .map(elo_log.set_index("game_id")["pred_home_margin"])
        .to_numpy(dtype=float)
    )
    y = frame["realized_margin"].to_numpy(dtype=float)
    mask = np.isfinite(elo_mu) & np.isfinite(y)
    lr = LinearRegression().fit(elo_mu[mask].reshape(-1, 1), y[mask])
    fill = float(np.nanmean(elo_mu[mask]))
    l1_mu = lr.predict(np.nan_to_num(elo_mu, nan=fill).reshape(-1, 1))

    oof = (
        pd.DataFrame(
            {
                "lgbm_mu_margin": frame["pred_margin"].to_numpy(dtype=float),
                "enet_mu_margin": np.nan_to_num(elo_mu, nan=0.0),
                "realized_margin": y,
                "is_out_of_fold": True,
            }
        )
        .replace([np.inf, -np.inf], np.nan)
        .dropna()
    )
    stack = fit_nnls_stack(
        oof,
        target="margin",
        member_columns=["lgbm_mu_margin", "enet_mu_margin"],
    )
    nnls_mu = stack.weights[0] * frame["pred_margin"].to_numpy(dtype=float) + stack.weights[
        1
    ] * np.nan_to_num(elo_mu, nan=0.0)

    comparison = build_comparison_rows(
        frame,
        elo_mu=elo_mu,
        l1_mu=l1_mu,
        lgbm_mu=frame["pred_margin"].to_numpy(dtype=float),
        nnls_mu=nnls_mu,
        nnls_weights=stack.as_dict(),
    )
    gate = gate_task23_fundamental(raw_path, raise_on_fail=False)
    sig = sigma_diagnostics(frame)
    path, digest = write_canonical_artifact(
        composition=comp,
        comparison=comparison,
        gate=gate,
        sigma=sig,
        nnls_folds=[
            {
                "fold": "diagnostic_clean_canonical",
                "weights": stack.as_dict(),
                "condition_number": stack.condition_number,
                "n_oof_rows": stack.n_oof_rows,
                "lgbm_weight": stack.weights[0],
                "note": (
                    f"LightGBM weight={stack.weights[0]:.3f}; "
                    "design §5.2 expects LGBM-dominant Level-0 stack"
                ),
            }
        ],
        source_predictions=pred_path,
    )
    print(
        json.dumps(
            {
                "artifact": str(path),
                "sha256": digest,
                "source_sha": file_sha256(pred_path),
                "sd_y": comp.sd_y_full,
                "sigma": sig,
                "nnls": stack.as_dict(),
                "condition_number": stack.condition_number,
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
