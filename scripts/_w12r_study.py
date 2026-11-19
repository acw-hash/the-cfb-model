"""W12-R — market-blend β study (research only; nothing ships).

Pricing (fixed):
  P(side covers) = Φ(β · (μ_ridge − line_margin) / σ)
  line_margin := −median_CFBD_close_home_spread  (so μ − line = μ + spread)
  σ = production per-game sigma_m / sigma_margin
  β = 1 → today's card; β = 0 → Ridge adds nothing to the line

Fit β by Bernoulli MLE on home-cover outcomes (pushes dropped).
Train: 2019, 2021–2023. Decide: 2024. Report-only: 2026 w2–w11.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from scipy.optimize import minimize_scalar
from scipy.stats import norm

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "notes" / "_artifacts" / "w12-r"
PRED_V3 = (
    ROOT
    / "data"
    / "backtests"
    / "task23_fundamental_reduced_v3"
    / "full"
    / "predictions.parquet"
)
HIST = ROOT / "data" / "webapp" / "publish_history"
TRAIN_SEASONS = (2019, 2021, 2022, 2023)
DECIDE_SEASON = 2024
Z90 = float(norm.ppf(0.95))  # ~1.64485 for two-sided 90% / one-sided 95%


def wilson_interval(k: int, n: int, z: float = Z90) -> tuple[float, float]:
    if n <= 0:
        return (float("nan"), float("nan"))
    p = k / n
    denom = 1.0 + z * z / n
    centre = p + z * z / (2.0 * n)
    half = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
    return ((centre - half) / denom, (centre + half) / denom)


def median_close_home_spread(game_obj: dict[str, Any]) -> float | None:
    """Median across providers of CFBD closing home `spread` (not spreadOpen)."""
    lines = game_obj.get("lines") or []
    vals: list[float] = []
    for line in lines:
        if not isinstance(line, dict):
            continue
        sp = line.get("spread")
        if sp is None:
            continue
        try:
            v = float(sp)
        except (TypeError, ValueError):
            continue
        if math.isfinite(v) and -70.0 < v < 70.0:
            vals.append(v)
    if not vals:
        return None
    return float(np.median(vals))


def load_cfbd_line_margins(seasons: list[int]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for season in seasons:
        path = OUT / f"cfbd_lines_{season}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        for g in payload:
            if not isinstance(g, dict):
                continue
            gid = g.get("id") or g.get("gameId")
            if gid is None:
                continue
            spread = median_close_home_spread(g)
            if spread is None:
                continue
            rows.append(
                {
                    "game_id": int(gid),
                    "season": int(g.get("season") or season),
                    "week": int(g.get("week") or 0),
                    "home_spread_close": spread,
                    "line_margin": -spread,  # market implied home margin
                }
            )
    return pd.DataFrame(rows)


def load_v3_predictions() -> pd.DataFrame:
    df = pq.read_table(PRED_V3).to_pandas()
    df["game_id"] = df["game_id"].astype(int)
    df["mu"] = pd.to_numeric(df["pred_margin"], errors="coerce")
    df["sigma"] = pd.to_numeric(df["sigma_m"], errors="coerce")
    df["home_points"] = pd.to_numeric(df["home_points"], errors="coerce")
    df["away_points"] = pd.to_numeric(df["away_points"], errors="coerce")
    return df


def load_2026_publish_games(weeks: range) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for week in weeks:
        path = HIST / f"2026_w{week}.jsonl"
        if not path.exists():
            continue
        records = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        tue = [r for r in records if r.get("refresh_kind") == "tuesday_primary"]
        chosen = max(tue or records, key=lambda r: str(r.get("published_at") or r.get("as_of") or ""))
        for g in chosen.get("games") or []:
            rows.append(
                {
                    "game_id": int(g["game_id"]),
                    "season": int(chosen.get("season") or 2026),
                    "week": int(g.get("week") or week),
                    "mu": float(g["mu_margin"]) if g.get("mu_margin") is not None else float("nan"),
                    "sigma": (
                        float(g["sigma_margin"])
                        if g.get("sigma_margin") is not None
                        else float("nan")
                    ),
                    "home_team": g.get("home_team"),
                    "away_team": g.get("away_team"),
                    "null_reason": g.get("null_reason"),
                }
            )
    return pd.DataFrame(rows)


def load_2026_scores() -> pd.DataFrame:
    """Final scores from staged games (season=2026)."""
    from ncaa_quant.config import load_config
    from ncaa_quant.data.storage import ParquetStore

    cfg = load_config()
    store = ParquetStore(Path(cfg.paths.staged_dir))
    games = store.read("games", filters={"season": 2026}).copy()
    games["game_id"] = games["game_id"].astype(int)
    games["home_points"] = pd.to_numeric(games["home_points"], errors="coerce")
    games["away_points"] = pd.to_numeric(games["away_points"], errors="coerce")
    games["completed"] = games["completed"].fillna(False).astype(bool)
    return games.loc[
        games["completed"],
        ["game_id", "home_points", "away_points", "week"],
    ]


@dataclass
class FitResult:
    beta: float
    nll: float
    ci_lo: float
    ci_hi: float
    n: int
    n_covers: int


def build_panel(pred: pd.DataFrame, lines: pd.DataFrame) -> pd.DataFrame:
    m = pred.merge(lines[["game_id", "home_spread_close", "line_margin"]], on="game_id", how="inner")
    m["realized_margin"] = m["home_points"] - m["away_points"]
    m["cover_raw"] = m["realized_margin"] + m["home_spread_close"]
    # drop pushes
    m = m.loc[m["cover_raw"].notna() & (m["cover_raw"] != 0)].copy()
    m["y_home_cover"] = (m["cover_raw"] > 0).astype(int)
    m["edge"] = m["mu"] - m["line_margin"]  # == mu + home_spread_close
    m = m.loc[
        m["mu"].notna()
        & m["sigma"].notna()
        & (m["sigma"] > 0)
        & m["edge"].notna()
        & np.isfinite(m["edge"])
        & np.isfinite(m["sigma"])
    ].copy()
    return m


def nll_beta(beta: float, edge: np.ndarray, sigma: np.ndarray, y: np.ndarray) -> float:
    z = float(beta) * edge / sigma
    # clip for numerical stability
    p = norm.cdf(z)
    p = np.clip(p, 1e-12, 1.0 - 1e-12)
    return float(-np.sum(y * np.log(p) + (1 - y) * np.log(1 - p)))


def fit_beta(df: pd.DataFrame) -> FitResult:
    edge = df["edge"].to_numpy(dtype=float)
    sigma = df["sigma"].to_numpy(dtype=float)
    y = df["y_home_cover"].to_numpy(dtype=float)
    n = int(len(df))

    def obj(b: float) -> float:
        return nll_beta(b, edge, sigma, y)

    res = minimize_scalar(obj, bounds=(-3.0, 3.0), method="bounded", options={"xatol": 1e-6})
    beta_hat = float(res.x)
    nll_hat = float(res.fun)

    # 90% profile-likelihood CI: {β : 2(nll(β)-nll_hat) <= chi2_90,1 = 2.70554}
    thresh = nll_hat + 0.5 * 2.705543454095404
    grid = np.linspace(-3.0, 3.0, 2401)
    nlls = np.array([obj(float(b)) for b in grid])
    ok = nlls <= thresh
    if not ok.any():
        ci_lo, ci_hi = float("nan"), float("nan")
    else:
        ci_lo = float(grid[ok][0])
        ci_hi = float(grid[ok][-1])

    return FitResult(
        beta=beta_hat,
        nll=nll_hat,
        ci_lo=ci_lo,
        ci_hi=ci_hi,
        n=n,
        n_covers=int(y.sum()),
    )


def p_cover_home(beta: float, edge: np.ndarray, sigma: np.ndarray) -> np.ndarray:
    return norm.cdf(beta * edge / sigma)


def high_conf_table(df: pd.DataFrame, beta: float, threshold: float = 0.55) -> dict[str, Any]:
    """Sides with blended P(cover) >= threshold (home or away)."""
    edge = df["edge"].to_numpy(dtype=float)
    sigma = df["sigma"].to_numpy(dtype=float)
    y_home = df["y_home_cover"].to_numpy(dtype=int)
    p_h = p_cover_home(beta, edge, sigma)
    p_a = 1.0 - p_h

    pick_home = p_h >= p_a
    p_side = np.where(pick_home, p_h, p_a)
    covered = np.where(pick_home, y_home == 1, y_home == 0)

    mask = p_side >= threshold
    n = int(mask.sum())
    if n == 0:
        return {
            "n": 0,
            "mean_predicted": None,
            "n_covers": 0,
            "realized": None,
            "wilson90": [None, None],
            "pass_n": False,
            "pass_realized": False,
            "verdict": "INCONCLUSIVE",
        }
    k = int(covered[mask].sum())
    realized = k / n
    lo, hi = wilson_interval(k, n)
    pass_n = n >= 100
    pass_realized = realized > 0.524
    if not pass_n:
        verdict = "INCONCLUSIVE"
    elif pass_realized:
        verdict = "PASS"
    else:
        verdict = "FAIL"
    return {
        "n": n,
        "mean_predicted": float(p_side[mask].mean()),
        "n_covers": k,
        "realized": float(realized),
        "wilson90": [float(lo), float(hi)],
        "pass_n": pass_n,
        "pass_realized": pass_realized,
        "verdict": verdict,
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    seasons_hist = [2019, 2021, 2022, 2023, 2024]
    lines = load_cfbd_line_margins(seasons_hist + [2026])
    pred = load_v3_predictions()

    # Historical panel from production walk-forward μ/σ
    hist = build_panel(
        pred.loc[pred["season"].isin(seasons_hist)].copy(),
        lines,
    )
    train = hist.loc[hist["season"].isin(TRAIN_SEASONS)].copy()
    decide = hist.loc[hist["season"] == DECIDE_SEASON].copy()

    fit_train = fit_beta(train)
    per_season: dict[str, Any] = {}
    for s in list(TRAIN_SEASONS) + [DECIDE_SEASON]:
        sub = hist.loc[hist["season"] == s]
        if sub.empty:
            per_season[str(s)] = {"n": 0}
            continue
        fr = fit_beta(sub)
        per_season[str(s)] = {
            "beta": fr.beta,
            "ci90": [fr.ci_lo, fr.ci_hi],
            "n": fr.n,
            "n_covers": fr.n_covers,
            "excludes_zero": bool(fr.ci_lo > 0 or fr.ci_hi < 0),
        }

    table_2024 = high_conf_table(decide, fit_train.beta)
    beta_pos = fit_train.beta > 0 and fit_train.ci_lo > 0
    pass_all = (
        beta_pos
        and table_2024["verdict"] == "PASS"
        and bool(table_2024["pass_n"])
        and bool(table_2024["pass_realized"])
    )
    if pass_all:
        card = "ELIGIBLE (all 2024 gates pass)"
    elif not beta_pos:
        card = "OFF (β gate failed — β≤0 or 90% CI includes 0)"
    elif table_2024["verdict"] == "INCONCLUSIVE":
        card = "OFF (INCONCLUSIVE — n<100 at P≥0.55)"
    else:
        card = "OFF (2024 P≥0.55 realized cover gate failed)"

    # 2026 report-only
    pub = load_2026_publish_games(range(2, 12))
    scores = load_2026_scores()
    lines_26 = lines.loc[lines["season"] == 2026]
    m26 = pub.merge(lines_26[["game_id", "home_spread_close", "line_margin"]], on="game_id", how="inner")
    m26 = m26.merge(scores[["game_id", "home_points", "away_points"]], on="game_id", how="inner")
    m26 = m26.loc[m26["null_reason"].isna() | (m26["null_reason"].astype(str) == "None")]
    m26["realized_margin"] = m26["home_points"] - m26["away_points"]
    m26["cover_raw"] = m26["realized_margin"] + m26["home_spread_close"]
    m26 = m26.loc[m26["cover_raw"].notna() & (m26["cover_raw"] != 0)].copy()
    m26["y_home_cover"] = (m26["cover_raw"] > 0).astype(int)
    m26["edge"] = m26["mu"] - m26["line_margin"]
    m26 = m26.loc[
        m26["mu"].notna()
        & m26["sigma"].notna()
        & (m26["sigma"] > 0)
        & np.isfinite(m26["edge"])
        & np.isfinite(m26["sigma"])
    ].copy()
    # de-dupe game_id if multiple weeks republished same game
    m26 = m26.sort_values(["game_id", "week"]).drop_duplicates("game_id", keep="last")
    table_2026 = high_conf_table(m26, fit_train.beta)

    report = {
        "method": {
            "formula": "P(cover)=Φ(β·(μ_ridge−line_margin)/σ)",
            "line_field": "CFBD /lines provider `spread` (close); line_margin=-median(spread)",
            "sigma_field": "walk-forward sigma_m (hist) / publish sigma_margin (2026)",
            "mu_source_hist": str(PRED_V3.relative_to(ROOT)),
            "mu_source_2026": "data/webapp/publish_history/2026_w{2..11}.jsonl tuesday_primary",
            "train_seasons": list(TRAIN_SEASONS),
            "decide_season": DECIDE_SEASON,
            "report_only": "2026 w2–w11",
        },
        "data_counts": {
            "train_n": int(len(train)),
            "decide_n": int(len(decide)),
            "report_2026_n": int(len(m26)),
            "train_by_season": {
                str(s): int((train["season"] == s).sum()) for s in TRAIN_SEASONS
            },
        },
        "beta_train": {
            "beta": fit_train.beta,
            "ci90": [fit_train.ci_lo, fit_train.ci_hi],
            "n": fit_train.n,
            "n_covers": fit_train.n_covers,
            "excludes_zero": bool(fit_train.ci_lo > 0),
            "beta_gt_0": bool(fit_train.beta > 0),
        },
        "beta_per_season": per_season,
        "pass_2024": {
            "beta_gate": beta_pos,
            "table_p_ge_0_55": table_2024,
            "all_pass": pass_all,
            "card": card,
        },
        "report_2026_w2_w11": {
            "table_p_ge_0_55": table_2026,
            "note": "Already inspected historically — cannot decide; report only.",
        },
    }
    (OUT / "study_report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    train.to_parquet(OUT / "panel_train.parquet", index=False)
    decide.to_parquet(OUT / "panel_2024.parquet", index=False)
    m26.to_parquet(OUT / "panel_2026.parquet", index=False)

    print(json.dumps(report["beta_train"], indent=2))
    print("2024 table", json.dumps(table_2024, indent=2))
    print("2026 table", json.dumps(table_2026, indent=2))
    print("CARD", card)


if __name__ == "__main__":
    main()
