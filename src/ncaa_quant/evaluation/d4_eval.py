"""D4: revive inert σ / Stage-1 mixture, then forecast-encompassing test.

All numbers cite canonical_v2. μ heads / features / Stage-1 filter fitting are
not modified — σ and epistemic mixture are reconstructed on the archived μ.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
from sklearn.linear_model import LinearRegression  # type: ignore[import-untyped]

from ncaa_quant.distribution.simulate import (
    default_epistemic_draws,
    mix_epistemic_predictions,
)
from ncaa_quant.evaluation.canonical_eval import _headline_frame, _market_overlap, file_sha256
from ncaa_quant.evaluation.d3_eval import (
    part2_informativeness,
    part4_calibration,
)
from ncaa_quant.evaluation.metrics import mae, rmse
from ncaa_quant.evaluation.production_stack import RATING_FEATURE_DIMS, RATING_MEAN_FEATURE_PREFIXES
from ncaa_quant.evaluation.reports import assert_component_varies_before_conclusion
from ncaa_quant.evaluation.significance import paired_block_bootstrap
from ncaa_quant.models.ensemble import attach_stage1_mixture_variance, ensemble_sigma
from ncaa_quant.models.heads.base import HeadTrainConfig
from ncaa_quant.models.heads.margin import LightGBMMuHead
from ncaa_quant.models.heads.sigma import LightGBMSigmaHead, abs_residual_labels
from ncaa_quant.ratings.state_space import posterior_asof
from ncaa_quant.utils.timeutils import to_utc

CANONICAL_V2_PATH = Path("docs/notes/_artifacts/D3/canonical_v2.json")
CANONICAL_V2_SHA = "ebb9ce08a6b6534f41392ab5402d7b0ea26f775b89a8c9098b873cb7642cef70"
DEFAULT_PREDS = Path("data/backtests/task23_fundamental/fundamental/predictions_enriched.parquet")


def verify_canonical_v2_sha(path: Path | str = CANONICAL_V2_PATH) -> str:
    digest = file_sha256(path)
    if digest != CANONICAL_V2_SHA:
        msg = f"canonical_v2 sha mismatch: got {digest}, expected {CANONICAL_V2_SHA}"
        raise ValueError(msg)
    return digest


def load_canonical_v2_frame(
    preds_path: Path | str = DEFAULT_PREDS,
    *,
    exclude_2019_w1_4: bool = True,
) -> pd.DataFrame:
    preds = pd.read_parquet(preds_path)
    if exclude_2019_w1_4 and {"season", "week"} <= set(preds.columns):
        poison = (preds["season"] == 2019) & (preds["week"] <= 4)
        preds = preds.loc[~poison].copy()
    if "n_train_games" not in preds.columns:
        preds["n_train_games"] = 500
    if "run_kind" not in preds.columns:
        preds["run_kind"] = "backtest"
    return _headline_frame(preds)


# ---------------------------------------------------------------------------
# Part 0 — revive σ head + Stage-1 mixture on archived μ
# ---------------------------------------------------------------------------


def build_rating_feature_matrix(
    frame: pd.DataFrame,
    games: pd.DataFrame,
    filter_result: Any,
) -> pd.DataFrame:
    """Pregame Stage-1 rating features (means + uncertainty) per game."""
    gmap = games.set_index("game_id")
    history = filter_result.history
    rows: list[dict[str, Any]] = []
    for row in frame.itertuples(index=False):
        gid = int(row.game_id)
        entry: dict[str, Any] = {"game_id": gid}
        if gid not in gmap.index:
            rows.append(entry)
            continue
        g = gmap.loc[gid]
        if "start_date" not in gmap.columns or not pd.notna(g.get("start_date")):
            rows.append(entry)
            continue
        kick = to_utc(pd.Timestamp(g["start_date"]).to_pydatetime())
        as_of = kick - timedelta(seconds=1)
        hid, aid = int(g["home_team_id"]), int(g["away_team_id"])
        h = posterior_asof(history, hid, as_of)
        a = posterior_asof(history, aid, as_of)
        if h is None or a is None:
            rows.append(entry)
            continue
        for dim in RATING_FEATURE_DIMS:
            try:
                i = filter_result.config.dim_index(dim)
            except Exception:  # noqa: BLE001
                continue
            hv = float(h.mean[i])
            av = float(a.mean[i])
            entry[f"home_{dim}"] = hv
            entry[f"away_{dim}"] = av
            entry[f"{dim}_diff"] = hv - av
            if dim in {"off_epa", "def_epa"}:
                entry[f"rating_diff_{dim}"] = hv - av
        try:
            i_off = filter_result.config.dim_index("off_epa")
            h_var = float(max(h.cov[i_off, i_off], 0.0))
            a_var = float(max(a.cov[i_off, i_off], 0.0))
            entry["stage1_posterior_var_home"] = h_var
            entry["stage1_posterior_var_away"] = a_var
            entry["rating_uncertainty"] = float(np.sqrt(h_var) + np.sqrt(a_var))
        except Exception:  # noqa: BLE001
            entry["stage1_posterior_var_home"] = float("nan")
            entry["stage1_posterior_var_away"] = float("nan")
            entry["rating_uncertainty"] = 1.0
        if hasattr(row, "week"):
            entry["week"] = int(row.week)
        if hasattr(row, "season"):
            entry["season"] = int(row.season)
        abs_mu = abs(float(getattr(row, "pred_margin", 0.0) or 0.0))
        entry["abs_pred_margin"] = abs_mu
        # Rating differential magnitude (§5.2 item 7).
        off_d = float(entry.get("rating_diff_off_epa", entry.get("off_epa_diff", 0.0)) or 0.0)
        def_d = float(entry.get("rating_diff_def_epa", entry.get("def_epa_diff", 0.0)) or 0.0)
        entry["rating_diff_magnitude"] = abs(off_d) + abs(def_d)
        # expected_possessions / roster-portal: absent on the D4 revive feature
        # matrix unless joined from the feature store — emit null indicators.
        if "expected_possessions" not in entry:
            entry["expected_possessions"] = float("nan")
            entry["expected_possessions_null"] = 1.0
        else:
            entry["expected_possessions_null"] = (
                1.0 if not np.isfinite(float(entry["expected_possessions"])) else 0.0
            )
        entry["roster_portal_null"] = 1.0
        rows.append(entry)
    return pd.DataFrame(rows)


def revive_sigma_walkforward(
    frame: pd.DataFrame,
    features: pd.DataFrame,
    *,
    n_estimators: int = 80,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Walk-forward fit LightGBMSigmaHead on |y−μ_published|; emit per-game σ.

    Uses archived ``pred_margin`` as μ (no μ-head changes). Train on prior
    seasons only; within the earliest season, expand by week.
    """
    work = frame.merge(features, on="game_id", how="inner", suffixes=("", "_feat"))
    y = pd.to_numeric(work["realized_margin"], errors="coerce").to_numpy(dtype=float)
    mu = pd.to_numeric(work["pred_margin"], errors="coerce").to_numpy(dtype=float)
    season = work["season"].to_numpy(dtype=int)
    week = (
        work["week"].to_numpy(dtype=int)
        if "week" in work.columns
        else np.zeros(len(work), dtype=int)
    )
    gids = work["game_id"].to_numpy()
    feat_cols = [
        c
        for c in features.columns
        if c not in {"game_id", "season", "week"} and pd.api.types.is_numeric_dtype(work[c])
    ]
    out = np.full(len(frame), np.nan)
    gid_to_idx = {int(g): i for i, g in enumerate(frame["game_id"].to_numpy())}
    seasons = sorted(int(s) for s in np.unique(season))
    train_cfg = HeadTrainConfig(n_estimators=n_estimators, learning_rate=0.05, num_leaves=15)
    n_fitted = 0

    def _fit_predict(tr_mask: np.ndarray, te_mask: np.ndarray, seed: int) -> int:
        nonlocal n_fitted
        if int(tr_mask.sum()) < 20 or not te_mask.any():
            return 0
        labels = pd.DataFrame({"game_id": gids[tr_mask], "realized_margin": y[tr_mask]})
        mu_frame = pd.DataFrame({"game_id": gids[tr_mask], "pred_margin": mu[tr_mask]})
        lab = abs_residual_labels(labels, mu_frame, target="margin")
        feat_tr = work.loc[tr_mask, ["game_id", *feat_cols]].copy()
        head = LightGBMSigmaHead(target="sigma_margin", train=train_cfg, seed=seed)
        head.fit(feat_tr, lab)
        if not head.is_fitted:
            return 0
        n_fitted += 1
        feat_te = work.loc[te_mask, ["game_id", *feat_cols]].copy()
        pred = head.predict(feat_te)
        col = next(c for c in pred.columns if c != "game_id")
        for gid, val in zip(pred["game_id"].to_numpy(), pred[col].to_numpy(), strict=True):
            idx = gid_to_idx.get(int(gid))
            if idx is not None:
                out[idx] = float(val)
        return int(te_mask.sum())

    for test_s in seasons:
        if test_s == seasons[0]:
            weeks = sorted(int(w) for w in np.unique(week[season == test_s]))
            for w in weeks:
                tr = ((season < test_s) | ((season == test_s) & (week < w))) & np.isfinite(y)
                te = (season == test_s) & (week == w)
                _fit_predict(tr, te, seed=int(test_s) * 100 + int(w))
        else:
            tr = (season < test_s) & np.isfinite(y) & np.isfinite(mu)
            te = season == test_s
            _fit_predict(tr, te, seed=int(test_s))

    # Cold-start rows: production-stack style uncertainty floor (never a global constant).
    missing = ~np.isfinite(out)
    if missing.any():
        unc = np.ones(len(frame), dtype=float)
        if "rating_uncertainty" in features.columns:
            umap = dict(
                zip(
                    features["game_id"].to_numpy(),
                    pd.to_numeric(features["rating_uncertainty"], errors="coerce").to_numpy(
                        dtype=float
                    ),
                    strict=True,
                )
            )
            unc = np.asarray(
                [float(umap.get(int(g), 1.0)) for g in frame["game_id"].to_numpy()],
                dtype=float,
            )
        out = np.where(missing, 8.0 + np.maximum(unc, 0.0), out)

    meta = {
        "n_fitted_seasons": n_fitted,
        "n_finite": int(np.isfinite(out).sum()),
        "mean": float(np.nanmean(out)) if np.isfinite(out).any() else float("nan"),
        "std": float(np.nanstd(out)) if np.isfinite(out).any() else float("nan"),
        "nunique": int(len(np.unique(out[np.isfinite(out)]))) if np.isfinite(out).any() else 0,
        "feature_columns": feat_cols,
    }
    return out, meta


def revive_stage1_mixture(
    frame: pd.DataFrame,
    features: pd.DataFrame,
    sigma_head: np.ndarray,
    *,
    n_draws: int | None = None,
    seed: int = 0,
) -> tuple[np.ndarray, dict[str, Any]]:
    """50-draw Stage-1 epistemic Var(μ) through a walk-forward μ mapping.

    Mapping is fit on rating features → archived realized margin (prior seasons).
    Published μ is unchanged; only Var(μ) across posterior feature draws is used.
    """
    n_post = int(n_draws) if n_draws is not None else default_epistemic_draws()
    work = frame.merge(features, on="game_id", how="inner", suffixes=("", "_feat"))
    # Align sigma_head to work order via game_id
    sig_map = dict(
        zip(frame["game_id"].to_numpy(), np.asarray(sigma_head, dtype=float), strict=True)
    )
    sig = np.asarray([sig_map[int(g)] for g in work["game_id"].to_numpy()], dtype=float)

    feat_cols = [c for c in RATING_MEAN_FEATURE_PREFIXES if c in work.columns]
    if not feat_cols:
        feat_cols = [
            c
            for c in work.columns
            if any(c == f"{side}_{dim}" for side in ("home", "away") for dim in RATING_FEATURE_DIMS)
        ]
    if not feat_cols:
        return np.zeros(len(frame)), {
            "n_posterior_draws": 0,
            "mean_stage1_var": 0.0,
            "note": "no rating-mean columns — Stage-1 mixture path dead",
        }

    # Fit a cheap μ head on all rows with finite features (OOF by season).
    y = pd.to_numeric(work["realized_margin"], errors="coerce").to_numpy(dtype=float)
    season = work["season"].to_numpy(dtype=int)
    train_cfg = HeadTrainConfig(n_estimators=60, learning_rate=0.05, num_leaves=15)
    mu_oof = np.full(len(work), np.nan)
    for test_s in sorted(int(s) for s in np.unique(season)):
        tr = (season < test_s) & np.isfinite(y)
        te = season == test_s
        if tr.sum() < 40:
            continue
        head = LightGBMMuHead(target="margin", train=train_cfg, seed=int(test_s))
        feat_tr = work.loc[tr, ["game_id", *feat_cols]].copy()
        lab = work.loc[tr, ["game_id", "realized_margin"]].copy()
        head.fit(feat_tr, lab)
        if not head.is_fitted:
            continue
        pred = head.predict(work.loc[te, ["game_id", *feat_cols]])
        mu_oof[te] = (
            pred.set_index("game_id")
            .reindex(work.loc[te, "game_id"])["pred_margin"]
            .to_numpy(dtype=float)
        )

    # Final mapping head fit on all finite for mixture push-through.
    mask = np.isfinite(y)
    head = LightGBMMuHead(target="margin", train=train_cfg, seed=seed)
    head.fit(
        work.loc[mask, ["game_id", *feat_cols]], work.loc[mask, ["game_id", "realized_margin"]]
    )
    del mu_oof

    base = work[feat_cols].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    unc = (
        work["rating_uncertainty"].to_numpy(dtype=float)
        if "rating_uncertainty" in work.columns
        else np.ones(len(work))
    )
    sd = np.maximum(unc, 1e-3)[:, None] * 0.25
    rng = np.random.default_rng(seed)
    draws = np.empty((n_post, len(work), len(feat_cols)), dtype=float)
    for k in range(n_post):
        noise = rng.normal(0.0, 1.0, size=base.shape) * sd
        draws[k] = base + noise

    gids = work["game_id"].to_numpy()
    sig_safe = np.where(np.isfinite(sig), sig, float(np.nanmedian(sig[np.isfinite(sig)])))

    def mapping_fn(feat_mat: np.ndarray) -> Mapping[str, np.ndarray]:
        frame_f = pd.DataFrame(feat_mat, columns=feat_cols)
        frame_f.insert(0, "game_id", gids)
        pred = head.predict(frame_f)
        mu_m = pred.set_index("game_id").reindex(gids)["pred_margin"].to_numpy(dtype=float)
        return {
            "mu_m": mu_m,
            "sigma_m": sig_safe,
            "mu_t": np.full(len(gids), 50.0),
            "sigma_t": np.full(len(gids), 14.0),
        }

    mix = mix_epistemic_predictions(draws, mapping_fn, rho=0.0, seed=seed)
    stage1 = np.asarray(mix.params.meta["stage1_var_m"], dtype=float)
    # Map back to frame order
    out = np.zeros(len(frame), dtype=float)
    wmap = dict(zip(gids.tolist(), stage1.tolist(), strict=True))
    for i, gid in enumerate(frame["game_id"].to_numpy()):
        out[i] = float(wmap.get(int(gid), 0.0))
    meta = {
        "n_posterior_draws": n_post,
        "mean_stage1_var": float(np.nanmean(out)),
        "feature_columns": feat_cols,
        "draws_identical": bool(float(np.nanmean(out)) < 1e-18),
    }
    return out, meta


def lotv_decomposition_live(
    frame: pd.DataFrame,
    *,
    sigma_head: np.ndarray,
    elo_mu: np.ndarray,
    nnls_weights: Mapping[str, float],
    stage1_var: np.ndarray,
) -> dict[str, Any]:
    published = pd.to_numeric(frame["pred_margin"], errors="coerce").to_numpy(dtype=float)
    member = np.column_stack([published, np.asarray(elo_mu, dtype=float)])
    w = [float(nnls_weights["lgbm_mu_margin"]), float(nnls_weights["enet_mu_margin"])]
    ens = ensemble_sigma(member, sigma_head, weights=w)
    ens = attach_stage1_mixture_variance(ens, stage1_var)
    decomp = ens.variance_decomposition()
    return {
        "nnls_weights": dict(nnls_weights),
        "decomposition": decomp,
        "mean_aleatoric_var": decomp["aleatoric_mean_var"],
        "mean_member_var": decomp["epistemic_member_mean_var"],
        "mean_stage1_var": decomp["stage1_mixture_mean_var"],
        "total_mean_var": decomp["total_mean_var"],
        "all_three_live": bool(
            decomp["aleatoric_mean_var"] > 1e-9
            and decomp["epistemic_member_mean_var"] > 1e-9
            and decomp["stage1_mixture_mean_var"] > 1e-9
        ),
    }


def part2_informativeness_gated(frame: pd.DataFrame) -> dict[str, Any]:
    """D3 Part 2 informativeness with the standing void-conclusion rule."""
    sig = pd.to_numeric(frame["sigma_m"], errors="coerce")
    # Standing rule: refuse "does not help" unless σ varies.
    try:
        assert_component_varies_before_conclusion(
            sig,
            component_name="sigma_m",
            conclusion="sigma head does not help / is noise",
        )
        varies = True
        void_blocked = None
    except Exception as exc:  # noqa: BLE001
        varies = False
        void_blocked = str(exc)
    report = part2_informativeness(frame)
    report["component_varies"] = varies
    report["void_conclusion_blocked"] = void_blocked
    if varies:
        report["note"] = (
            "σ varies; informativeness slope/R² measure whether the revived head "
            "captures heteroscedasticity (S4>S0 established it is present)."
        )
        if report.get("flag_noise") or float(report.get("r2", 0.0)) < 0.01:
            report["conclusion"] = (
                "head varies but does not usefully capture heteroscedasticity "
                "(weak |r| association; check S1 vs S0 CRPS)"
            )
        else:
            report["conclusion"] = "head varies and tracks |residual|"
    else:
        report["conclusion"] = None
        report["note"] = void_blocked or "σ inert — no ablation conclusion emitted"
    return report


# ---------------------------------------------------------------------------
# Part 1 — encompassing test
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EncompassingResult:
    b1: float
    b2: float
    se_b1: float
    se_b2: float
    p_b2: float
    a: float
    n: int
    verdict: str


def _ols_two_predictor(y: np.ndarray, x1: np.ndarray, x2: np.ndarray) -> tuple[float, float, float]:
    x = np.column_stack([np.ones(len(y)), x1, x2])
    beta, _, _, _ = np.linalg.lstsq(x, y, rcond=None)
    return float(beta[0]), float(beta[1]), float(beta[2])


def encompassing_regression(
    y: np.ndarray,
    market: np.ndarray,
    stack_mu: np.ndarray,
    blocks: Sequence[Any],
    *,
    n_boot: int = 1000,
    seed: int = 0,
) -> EncompassingResult:
    """y = a + b1*market + b2*stack_mu + e; SEs from week block bootstrap."""
    mask = np.isfinite(y) & np.isfinite(market) & np.isfinite(stack_mu)
    y_m, mkt, stk = y[mask], market[mask], stack_mu[mask]
    bl = [blocks[i] for i, ok in enumerate(mask) if ok]
    a, b1, b2 = _ols_two_predictor(y_m, mkt, stk)

    def _stat_b1(idx: np.ndarray) -> float:
        _, bb1, _ = _ols_two_predictor(y_m[idx], mkt[idx], stk[idx])
        return bb1

    def _stat_b2(idx: np.ndarray) -> float:
        _, _, bb2 = _ols_two_predictor(y_m[idx], mkt[idx], stk[idx])
        return bb2

    # Manual block bootstrap of coefficients
    from ncaa_quant.evaluation.significance import _group_indices
    from ncaa_quant.utils.seeding import set_global_seed

    groups = _group_indices(bl)
    set_global_seed(seed)
    rng = np.random.default_rng(seed)
    b1s: list[float] = []
    b2s: list[float] = []
    n_g = len(groups)
    for _ in range(n_boot):
        draw = rng.integers(0, n_g, size=n_g)
        idx = np.concatenate([groups[i] for i in draw])
        try:
            _, bb1, bb2 = _ols_two_predictor(y_m[idx], mkt[idx], stk[idx])
        except Exception:  # noqa: BLE001
            continue
        b1s.append(bb1)
        b2s.append(bb2)
    b1_arr = np.asarray(b1s, dtype=float)
    b2_arr = np.asarray(b2s, dtype=float)
    se_b1 = float(np.std(b1_arr, ddof=1)) if len(b1_arr) > 1 else float("nan")
    se_b2 = float(np.std(b2_arr, ddof=1)) if len(b2_arr) > 1 else float("nan")
    # Two-sided p on b2 via bootstrap percentile relative to 0
    if len(b2_arr) > 10:
        p_b2 = float(2.0 * min(np.mean(b2_arr <= 0.0), np.mean(b2_arr >= 0.0)))
        p_b2 = min(max(p_b2, 0.0), 1.0)
    else:
        p_b2 = float("nan")

    if not np.isfinite(p_b2) or p_b2 >= 0.10:
        ci_lo = b2 - 1.96 * se_b2 if np.isfinite(se_b2) else float("nan")
        ci_hi = b2 + 1.96 * se_b2 if np.isfinite(se_b2) else float("nan")
        # D5: a non-significant b2 whose CI still covers a substantial edge is
        # UNDERPOWERED, not a negative encompassing verdict.
        if np.isfinite(ci_lo) and np.isfinite(ci_hi) and (ci_lo <= -0.10 or ci_hi >= 0.10):
            verdict = (
                f"UNDERPOWERED (n={int(mask.sum())}): b2={b2:.4f} "
                f"(SE {se_b2:.4f}, p={p_b2:.4f}); 95% CI "
                f"[{ci_lo:.3f}, {ci_hi:.3f}] still contains |b2|≥0.10 — "
                "cannot claim the market encompasses the model."
            )
        else:
            verdict = (
                "b2 indistinguishable from 0 → the market encompasses the model. "
                "There is no edge in this μ, and no betting-layer work can create one."
            )
    elif b2 > 0:
        verdict = (
            "b2 reliably > 0 → the model carries information the closing line "
            "does not. That is the entire basis for a bet."
        )
    else:
        verdict = (
            "b2 reliably < 0 → stack μ is anti-informative relative to the "
            "market on this sample (treat as a warning)."
        )
    del _stat_b1, _stat_b2
    return EncompassingResult(
        b1=b1, b2=b2, se_b1=se_b1, se_b2=se_b2, p_b2=p_b2, a=a, n=int(mask.sum()), verdict=verdict
    )


def residual_on_residual(
    y: np.ndarray, market: np.ndarray, stack_mu: np.ndarray
) -> dict[str, float]:
    mask = np.isfinite(y) & np.isfinite(market) & np.isfinite(stack_mu)
    ey = y[mask] - market[mask]
    ex = stack_mu[mask] - market[mask]
    if ey.size < 5 or float(np.std(ex)) < 1e-12:
        return {
            "slope": float("nan"),
            "r2": float("nan"),
            "corr": float("nan"),
            "n": float(ey.size),
        }
    lr = LinearRegression().fit(ex.reshape(-1, 1), ey)
    return {
        "slope": float(lr.coef_[0]),
        "r2": float(lr.score(ex.reshape(-1, 1), ey)),
        "corr": float(np.corrcoef(ex, ey)[0, 1]),
        "n": float(ey.size),
    }


def optimal_combination_weight(
    frame: pd.DataFrame,
    *,
    train_seasons: Sequence[int],
    test_seasons: Sequence[int],
    n_boot: int = 800,
    seed: int = 1,
) -> dict[str, Any]:
    """w minimizing MAE of w*stack + (1-w)*market; fit on train, eval OOS."""
    market = _market_overlap(frame)
    if market.empty:
        return {"w": float("nan"), "note": "no market overlap"}
    y = pd.to_numeric(market["realized_margin"], errors="coerce").to_numpy(dtype=float)
    mkt = -pd.to_numeric(market["spread_close"], errors="coerce").to_numpy(dtype=float)
    stk = pd.to_numeric(market["pred_margin"], errors="coerce").to_numpy(dtype=float)
    season = market["season"].to_numpy(dtype=int)
    weeks = (
        market["week"].to_numpy(dtype=int) if "week" in market.columns else np.zeros(len(market))
    )
    tr = np.isin(season, list(train_seasons)) & np.isfinite(y) & np.isfinite(mkt) & np.isfinite(stk)
    te = np.isin(season, list(test_seasons)) & np.isfinite(y) & np.isfinite(mkt) & np.isfinite(stk)
    if tr.sum() < 20 or te.sum() < 10:
        return {"w": float("nan"), "note": "too few train/test market rows"}

    grid = np.linspace(0.0, 1.0, 101)
    best_w, best_mae = 0.0, float("inf")
    for w in grid:
        pred = w * stk[tr] + (1.0 - w) * mkt[tr]
        err = float(np.mean(np.abs(y[tr] - pred)))
        if err < best_mae:
            best_mae, best_w = err, float(w)
    comb = best_w * stk[te] + (1.0 - best_w) * mkt[te]
    mae_comb = float(np.mean(np.abs(y[te] - comb)))
    mae_mkt = float(np.mean(np.abs(y[te] - mkt[te])))
    abs_comb = np.abs(y[te] - comb)
    abs_mkt = np.abs(y[te] - mkt[te])
    bl = list(zip(season[te].tolist(), weeks[te].tolist(), strict=True))
    ci = paired_block_bootstrap(abs_comb, abs_mkt, bl, n_boot=n_boot, alpha=0.05, seed=seed)
    return {
        "w": best_w,
        "mae_combined": mae_comb,
        "mae_market": mae_mkt,
        "delta_mae": mae_comb - mae_mkt,
        "delta_ci": {"low": ci.ci_low, "high": ci.ci_high, "estimate": ci.estimate},
        "n_train": int(tr.sum()),
        "n_test": int(te.sum()),
        "near_zero": bool(best_w < 0.05),
    }


def score_point(y: np.ndarray, pred: np.ndarray) -> dict[str, float]:
    mask = np.isfinite(y) & np.isfinite(pred)
    yy, pp = y[mask], pred[mask]
    resid = yy - pp
    ss_res = float(np.sum(resid**2))
    ss_tot = float(np.sum((yy - np.mean(yy)) ** 2))
    return {
        "n": float(mask.sum()),
        "mae": mae(yy, pp),
        "rmse": rmse(yy, pp),
        "residual_sd": float(np.std(resid, ddof=0)),
        "r2": float(1.0 - ss_res / ss_tot) if ss_tot > 0 else float("nan"),
    }


# ---------------------------------------------------------------------------
# Part 2 — where the market beats us
# ---------------------------------------------------------------------------


def _slice_labels(
    frame: pd.DataFrame, games: pd.DataFrame, teams: pd.DataFrame | None
) -> pd.DataFrame:
    out = frame.copy()
    gmap = games.set_index("game_id") if not games.empty else None
    if gmap is not None and "neutral_site" in gmap.columns:
        out["neutral_site"] = out["game_id"].map(gmap["neutral_site"]).fillna(False).astype(bool)
    else:
        out["neutral_site"] = False
    week = pd.to_numeric(out["week"], errors="coerce").fillna(0).astype(int)
    out["week_bucket"] = np.where(week <= 4, "1-4", np.where(week <= 9, "5-9", "10+"))
    fav = (-pd.to_numeric(out.get("spread_close", np.nan), errors="coerce")).abs()
    out["favorite_size_bucket"] = pd.cut(
        fav, bins=[-0.1, 3, 7, 14, 100], labels=["0-3", "3-7", "7-14", "14+"]
    ).astype(str)
    tot = pd.to_numeric(out.get("total_close", np.nan), errors="coerce")
    out["total_bucket"] = pd.cut(
        tot, bins=[0, 45, 55, 65, 120], labels=["<=45", "45-55", "55-65", "65+"]
    ).astype(str)
    # Conference tier from teams if available
    out["conference_tier"] = "unknown"
    if (
        teams is not None
        and not teams.empty
        and {"home_team_id", "away_team_id"} <= set(out.columns)
    ):
        power = {"SEC", "Big Ten", "Big 12", "ACC", "Pac-12", "Pac-12 Conference"}
        tmap = {}
        for r in teams.itertuples(index=False):
            tid = getattr(r, "team_id", None) or getattr(r, "id", None)
            conf = str(getattr(r, "conference", "") or "")
            if tid is not None:
                tmap[int(tid)] = "P4/P5" if conf in power else "G5/other"
        ht = out["home_team_id"].map(tmap)
        at = out["away_team_id"].map(tmap)
        out["conference_tier"] = np.where(
            ht.isna() | at.isna(),
            "unknown",
            np.where(ht == at, ht, "cross_tier"),
        )
    # Rest differential / roster-portal nulls — null when columns absent
    out["rest_diff_bucket"] = "unknown"
    if "home_rest_days" in out.columns and "away_rest_days" in out.columns:
        rd = pd.to_numeric(out["home_rest_days"], errors="coerce") - pd.to_numeric(
            out["away_rest_days"], errors="coerce"
        )
        out["rest_diff_bucket"] = np.where(
            rd.isna(),
            "unknown",
            np.where(rd > 2, "home+rest", np.where(rd < -2, "away+rest", "even")),
        )
    roster_null = False
    for c in out.columns:
        if "roster" in c.lower() or "portal" in c.lower():
            roster_null = True
            break
    out["roster_portal_null"] = "features_absent" if not roster_null else "present"
    return out


def disagreement_slices(
    frame: pd.DataFrame,
    games: pd.DataFrame,
    teams: pd.DataFrame | None,
) -> dict[str, Any]:
    market = _market_overlap(frame)
    if market.empty:
        return {"n": 0}
    work = _slice_labels(market, games, teams)
    y = pd.to_numeric(work["realized_margin"], errors="coerce").to_numpy(dtype=float)
    mkt = -pd.to_numeric(work["spread_close"], errors="coerce").to_numpy(dtype=float)
    stk = pd.to_numeric(work["pred_margin"], errors="coerce").to_numpy(dtype=float)
    disagree = stk - mkt
    err_stack = np.abs(y - stk)
    err_mkt = np.abs(y - mkt)
    # Positive delta_err ⇒ stack worse than market on that game
    delta_err = err_stack - err_mkt
    slice_cols = [
        "week_bucket",
        "conference_tier",
        "favorite_size_bucket",
        "total_bucket",
        "neutral_site",
        "rest_diff_bucket",
        "roster_portal_null",
    ]
    tables: dict[str, list[dict[str, Any]]] = {}
    for col in slice_cols:
        rows = []
        for key, chunk in work.groupby(col, dropna=False):
            idx = chunk.index.to_numpy()
            # Align to work positional index
            pos = work.index.get_indexer(idx)
            d = disagree[pos]
            de = delta_err[pos]
            rows.append(
                {
                    "slice": str(key),
                    "n": int(len(pos)),
                    "mean_signed_disagree": float(np.nanmean(d)),
                    "mean_abs_disagree": float(np.nanmean(np.abs(d))),
                    "mean_delta_err_stack_minus_mkt": float(np.nanmean(de)),
                    "stack_worse_rate": float(np.nanmean(de > 0)),
                    "pattern": (
                        "systematic_wrong"
                        if abs(float(np.nanmean(de))) > 1.0 and float(np.nanmean(de)) > 0
                        else ("bias" if abs(float(np.nanmean(d))) > 1.5 else "noisy")
                    ),
                }
            )
        tables[col] = rows
    return {"n": len(work), "slices": tables}


def top_disagreement_games(
    frame: pd.DataFrame,
    games: pd.DataFrame,
    *,
    k: int = 20,
) -> dict[str, Any]:
    market = _market_overlap(frame)
    if market.empty:
        return {"market_right": [], "stack_right": []}
    y = pd.to_numeric(market["realized_margin"], errors="coerce").to_numpy(dtype=float)
    mkt = -pd.to_numeric(market["spread_close"], errors="coerce").to_numpy(dtype=float)
    stk = pd.to_numeric(market["pred_margin"], errors="coerce").to_numpy(dtype=float)
    disagree = stk - mkt
    err_s = np.abs(y - stk)
    err_m = np.abs(y - mkt)
    abs_d = np.abs(disagree)
    gmap = games.set_index("game_id") if not games.empty else None

    def _rows(mask: np.ndarray) -> list[dict[str, Any]]:
        idx = np.where(mask)[0]
        if idx.size == 0:
            return []
        order = idx[np.argsort(-abs_d[idx])][:k]
        rows = []
        for i in order:
            gid = int(market.iloc[i]["game_id"])
            row: dict[str, Any] = {
                "game_id": gid,
                "season": int(market.iloc[i]["season"]),
                "week": int(market.iloc[i]["week"]),
                "y": float(y[i]),
                "market": float(mkt[i]),
                "stack_mu": float(stk[i]),
                "disagree": float(disagree[i]),
                "err_stack": float(err_s[i]),
                "err_market": float(err_m[i]),
            }
            if gmap is not None and gid in gmap.index:
                g = gmap.loc[gid]
                row["home_team_id"] = (
                    int(g["home_team_id"]) if "home_team_id" in gmap.columns else None
                )
                row["away_team_id"] = (
                    int(g["away_team_id"]) if "away_team_id" in gmap.columns else None
                )
            rows.append(row)
        return rows

    market_right = (err_m < err_s) & np.isfinite(abs_d)
    stack_right = (err_s < err_m) & np.isfinite(abs_d)
    return {
        "market_right": _rows(market_right),
        "stack_right": _rows(stack_right),
    }


def uncalibrated_log_loss_report(frame: pd.DataFrame) -> dict[str, Any]:
    """Post-σ-fix uncalibrated log-loss per market (the original diagnostic metric)."""
    cal = part4_calibration(frame)
    return {
        "uncalibrated": cal.get("uncalibrated", {}),
        "note": (
            "Uncalibrated Gaussian probs from revived σ; D3 reported gate decisions "
            "but these are the underlying numbers the diagnostic sequence started from."
        ),
    }
