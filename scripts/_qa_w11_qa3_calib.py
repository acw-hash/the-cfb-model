"""W11-QA-3 Part A — pricing sigma vs realized error (READ-ONLY).

Decides the week-11 card: FAIL if R = median(sigma)/SD(error) < 0.90.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ncaa_quant.betting.provider import _DEFAULT_KERNEL, _sample_game_draws
from ncaa_quant.config import load_config
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.distribution.simulate import (
    spread_cover_probs,
    two_way_side_prob,
)
from ncaa_quant.utils.timeutils import to_utc

ROOT = Path(__file__).resolve().parents[1]
HIST_DIR = ROOT / "data" / "webapp" / "publish_history"
OUT_DIR = ROOT / "docs" / "notes" / "_artifacts" / "w11-qa"
OUT_DIR.mkdir(parents=True, exist_ok=True)

REGULATED = {
    "draftkings",
    "fanduel",
    "betmgm",
    "williamhill_us",
    "espnbet",
    "fanatics",
    "hardrockbet",
    "ballybet",
    "betrivers",
}
N_DRAWS = 20_000  # matches scripts/_s6_w10_card.py production card path


def _load_publish_week(week: int) -> dict[str, Any]:
    path = HIST_DIR / f"2026_w{week}.jsonl"
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    # Prefer tuesday_primary; else latest as_of.
    tue = [r for r in rows if r.get("refresh_kind") == "tuesday_primary"]
    if tue:
        return max(tue, key=lambda r: str(r.get("as_of") or ""))
    return max(rows, key=lambda r: str(r.get("as_of") or ""))


def _fbs_ids(teams: pd.DataFrame) -> set[int]:
    fbs = teams.loc[teams["classification"].astype(str).str.casefold() == "fbs"]
    return set(int(x) for x in fbs["team_id"].tolist())


def _games_frame(store: ParquetStore) -> pd.DataFrame:
    g = store.read("games", filters={"season": 2026})
    g = g.copy()
    g["game_id"] = pd.to_numeric(g["game_id"], errors="coerce").astype("Int64")
    return g


def section_a1(store: ParquetStore, fbs: set[int]) -> dict[str, Any]:
    """Sigma the card path uses for P(cover)."""
    # Source: prediction row sigma_margin → provider._sample_game_draws → spread_cover_probs
    pub = _load_publish_week(11)
    games = pub.get("games") or []
    sigs: list[float] = []
    rows_out: list[dict[str, Any]] = []
    for g in games:
        try:
            hid = int(g["home_team_id"])
            aid = int(g["away_team_id"])
        except (TypeError, ValueError, KeyError):
            continue
        if hid not in fbs or aid not in fbs:
            continue
        sig = g.get("sigma_margin")
        if sig is None or not math.isfinite(float(sig)) or float(sig) <= 0:
            continue
        sigs.append(float(sig))
        rows_out.append(
            {
                "game_id": int(g["game_id"]),
                "sigma_margin": float(sig),
                "mu_margin": float(g["mu_margin"]) if g.get("mu_margin") is not None else None,
            }
        )
    arr = np.asarray(sigs, dtype=float)
    return {
        "verdict": "PASS" if len(arr) else "FAIL",
        "source": (
            "Per-game sigma_margin on the prediction/publish row "
            "(LightGBMSigmaHead via production_ensemble). "
            "Card path: build_candidates_from_odds reads row['sigma_margin'] "
            "(src/ncaa_quant/betting/provider.py:575-576, 665-677) and samples "
            "joint draws; P(cover)=two_way_side_prob(spread_cover_probs(...)) "
            "(provider.py:692-698). Card script uses n_draws=20_000 "
            "(scripts/_s6_w10_card.py:778). Default key kernel is empty "
            "(provider._DEFAULT_KERNEL)."
        ),
        "file_line": "src/ncaa_quant/betting/provider.py:575-576,665-677,692-698",
        "fixed_or_per_game": "per-game",
        "n_w11_fbs_fbs": int(len(arr)),
        "median": float(np.median(arr)) if len(arr) else None,
        "iqr": (
            float(np.percentile(arr, 75) - np.percentile(arr, 25)) if len(arr) else None
        ),
        "p25": float(np.percentile(arr, 25)) if len(arr) else None,
        "p75": float(np.percentile(arr, 75)) if len(arr) else None,
        "min": float(arr.min()) if len(arr) else None,
        "max": float(arr.max()) if len(arr) else None,
        "mean": float(arr.mean()) if len(arr) else None,
    }


def _graded_errors(store: ParquetStore, fbs: set[int]) -> pd.DataFrame:
    games = _games_frame(store)
    rows: list[dict[str, Any]] = []
    for week in range(2, 11):
        pub = _load_publish_week(week)
        as_of = pub.get("as_of")
        for g in pub.get("games") or []:
            try:
                gid = int(g["game_id"])
                hid = int(g["home_team_id"])
                aid = int(g["away_team_id"])
                mu = float(g["mu_margin"])
                sig = float(g["sigma_margin"]) if g.get("sigma_margin") is not None else float("nan")
            except (TypeError, ValueError, KeyError):
                continue
            if hid not in fbs or aid not in fbs:
                continue
            if not math.isfinite(mu):
                continue
            hit = games.loc[games["game_id"] == gid]
            if hit.empty:
                continue
            r = hit.iloc[0]
            hp, ap = r.get("home_points"), r.get("away_points")
            if pd.isna(hp) or pd.isna(ap):
                continue
            actual = float(hp) - float(ap)
            kick = r.get("start_date") or r.get("kickoff") or g.get("kickoff_utc")
            rows.append(
                {
                    "week": week,
                    "game_id": gid,
                    "mu": mu,
                    "sigma": sig,
                    "actual": actual,
                    "error": actual - mu,
                    "abs_mu": abs(mu),
                    "as_of": as_of,
                    "kickoff": str(kick) if kick is not None else None,
                }
            )
    return pd.DataFrame(rows)


def _sd(x: np.ndarray) -> float:
    return float(np.std(x, ddof=1)) if len(x) >= 2 else float("nan")


def _robust_sd(x: np.ndarray) -> float:
    if len(x) < 1:
        return float("nan")
    med = float(np.median(x))
    mad = float(np.median(np.abs(x - med)))
    return 1.4826 * mad


def section_a2(err: pd.DataFrame) -> dict[str, Any]:
    e = err["error"].to_numpy(dtype=float)
    by_week = []
    for week, sub in err.groupby("week"):
        ee = sub["error"].to_numpy(dtype=float)
        by_week.append(
            {
                "week": int(week),
                "n": int(len(ee)),
                "sd": _sd(ee),
                "robust_sd": _robust_sd(ee),
            }
        )
    buckets = [(0, 7, "0-7"), (7, 14, "7-14"), (14, 1e9, "14+")]
    by_mu = []
    for lo, hi, lab in buckets:
        sub = err.loc[(err["abs_mu"] >= lo) & (err["abs_mu"] < hi)]
        ee = sub["error"].to_numpy(dtype=float)
        by_mu.append(
            {
                "bucket": lab,
                "n": int(len(ee)),
                "sd": _sd(ee) if len(ee) >= 2 else None,
                "robust_sd": _robust_sd(ee) if len(ee) else None,
            }
        )
    return {
        "verdict": "PASS" if len(e) >= 20 else "FAIL",
        "n": int(len(e)),
        "sd": _sd(e),
        "robust_sd": _robust_sd(e),
        "mean_error": float(np.mean(e)) if len(e) else None,
        "by_week": by_week,
        "by_abs_mu": by_mu,
    }


def section_a3(a1: dict[str, Any], a2: dict[str, Any], err: pd.DataFrame) -> dict[str, Any]:
    # sigma_price = median of per-game sigma on the same graded sample (primary),
    # also report week-11 median from A1.
    sigs = err["sigma"].to_numpy(dtype=float)
    sigs = sigs[np.isfinite(sigs) & (sigs > 0)]
    med_sample = float(np.median(sigs)) if len(sigs) else float("nan")
    med_w11 = a1.get("median")
    sd = a2["sd"]
    rsd = a2["robust_sd"]
    r_plain = med_sample / sd if sd and sd > 0 else float("nan")
    r_robust = med_sample / rsd if rsd and rsd > 0 else float("nan")
    r_plain_w11 = (float(med_w11) / sd) if med_w11 and sd and sd > 0 else float("nan")
    decision = "PASS" if (math.isfinite(r_plain) and r_plain >= 0.90) else "FAIL"
    return {
        "verdict": decision,
        "sigma_price_median_graded_w2_w10": med_sample,
        "sigma_price_median_w11": med_w11,
        "realized_sd": sd,
        "realized_robust_sd": rsd,
        "R_plain": r_plain,
        "R_robust": r_robust,
        "R_plain_using_w11_median": r_plain_w11,
        "decision_rule": "FAIL if R_plain = median(sigma_graded)/SD(error) < 0.90",
        "decision": decision,
        "n_sigma": int(len(sigs)),
    }


def _prekick_home_line(
    snaps: pd.DataFrame,
    *,
    game_id: int,
    home_team: str,
    kickoff: Any,
) -> tuple[float, str, datetime] | None:
    """Latest regulated two-way spread home line with event_time < kickoff."""
    if kickoff is None or (isinstance(kickoff, float) and math.isnan(kickoff)):
        return None
    try:
        ko = to_utc(pd.Timestamp(kickoff).to_pydatetime())
    except Exception:  # noqa: BLE001
        return None
    sub = snaps.loc[
        (snaps["game_id"] == game_id)
        & (snaps["market"].astype(str) == "spread")
        & (snaps["book"].astype(str).isin(REGULATED))
    ].copy()
    if sub.empty:
        return None
    sub["event_time"] = pd.to_datetime(sub["event_time"], utc=True)
    sub = sub.loc[sub["event_time"] < ko]
    if sub.empty:
        return None
    # Prefer the latest snapshot time, then any book with both sides.
    latest_et = sub["event_time"].max()
    window = sub.loc[sub["event_time"] == latest_et]
    home_cf = str(home_team).casefold()
    for book, bdf in window.groupby("book", sort=False):
        home = bdf.loc[bdf["side"].astype(str).str.casefold() == home_cf]
        away = bdf.loc[bdf["side"].astype(str).str.casefold() != home_cf]
        if home.empty or away.empty:
            continue
        line = home.iloc[0]["line"]
        if line is None or not math.isfinite(float(line)):
            continue
        return float(line), str(book), latest_et.to_pydatetime()
    return None


def section_a4(store: ParquetStore, err: pd.DataFrame, fbs: set[int]) -> dict[str, Any]:
    games = _games_frame(store)
    teams = store.read("teams", filters={"season": 2026})
    id_to_school = {
        int(r.team_id): str(r.school) for r in teams.itertuples(index=False)
    }
    # Load odds once for weeks 2-10
    snaps = store.read("odds_snapshots", filters={"season": 2026})
    snaps = snaps.copy()
    snaps["game_id"] = pd.to_numeric(snaps["game_id"], errors="coerce").astype("Int64")
    snaps = snaps.loc[snaps["week"].between(2, 10)]

    buckets = {
        "50-55": {"n": 0, "pred_sum": 0.0, "cover_sum": 0.0},
        "55-60": {"n": 0, "pred_sum": 0.0, "cover_sum": 0.0},
        "60-65": {"n": 0, "pred_sum": 0.0, "cover_sum": 0.0},
        "65+": {"n": 0, "pred_sum": 0.0, "cover_sum": 0.0},
    }
    n_used = 0
    n_skip_no_line = 0
    n_skip_push = 0
    details: list[dict[str, Any]] = []

    for row in err.itertuples(index=False):
        gid = int(row.game_id)
        hit = games.loc[games["game_id"] == gid]
        if hit.empty:
            continue
        g = hit.iloc[0]
        home = id_to_school.get(int(g["home_team_id"]), str(g.get("home_team") or ""))
        kick = g.get("start_date")
        resolved = _prekick_home_line(snaps, game_id=gid, home_team=home, kickoff=kick)
        if resolved is None:
            n_skip_no_line += 1
            continue
        home_line, book, et = resolved
        mu = float(row.mu)
        sig = float(row.sigma)
        if not (math.isfinite(sig) and sig > 0):
            continue
        draws = _sample_game_draws(
            mu_m=mu,
            sigma_m=sig,
            mu_t=50.0,
            sigma_t=16.0,
            rho=0.0,
            kernel=_DEFAULT_KERNEL,
            n_draws=N_DRAWS,
            seed=42 + gid,
        )
        p_home = two_way_side_prob(
            spread_cover_probs(draws, home_line, game_index=0, side="home")
        )
        p_away = two_way_side_prob(
            spread_cover_probs(draws, home_line, game_index=0, side="away")
        )
        if p_home >= p_away:
            model_side = "home"
            p_model = p_home
            # home covers if actual + home_line > 0
            margin = float(row.actual)
            cover = 1.0 if (margin + home_line) > 0 else (0.0 if (margin + home_line) < 0 else None)
        else:
            model_side = "away"
            p_model = p_away
            margin = float(row.actual)
            cover = 1.0 if (margin + home_line) < 0 else (0.0 if (margin + home_line) > 0 else None)
        if cover is None:
            n_skip_push += 1
            continue
        if p_model < 0.50:
            # should be rare given argmax; skip
            continue
        if p_model < 0.55:
            lab = "50-55"
        elif p_model < 0.60:
            lab = "55-60"
        elif p_model < 0.65:
            lab = "60-65"
        else:
            lab = "65+"
        buckets[lab]["n"] += 1
        buckets[lab]["pred_sum"] += p_model
        buckets[lab]["cover_sum"] += cover
        n_used += 1
        if len(details) < 30:
            details.append(
                {
                    "game_id": gid,
                    "week": int(row.week),
                    "book": book,
                    "home_line": home_line,
                    "model_side": model_side,
                    "p_model": round(p_model, 4),
                    "cover": cover,
                    "snap_et": et.isoformat(),
                }
            )

    out_buckets = {}
    for lab, b in buckets.items():
        n = int(b["n"])
        out_buckets[lab] = {
            "n": n,
            "mean_predicted": (b["pred_sum"] / n) if n else None,
            "realized_cover_rate": (b["cover_sum"] / n) if n else None,
        }
    return {
        "verdict": "PASS" if n_used >= 50 else "UNVERIFIED",
        "n_used": n_used,
        "n_skip_no_line": n_skip_no_line,
        "n_skip_push": n_skip_push,
        "n_draws": N_DRAWS,
        "buckets": out_buckets,
        "sample_details": details,
        "note": (
            "Model side = argmax two_way cover prob at latest pre-kickoff "
            "regulated-book home line (staged odds_snapshots)."
        ),
    }


def section_a5() -> dict[str, Any]:
    return {
        "verdict": "UNVERIFIED",
        "skipped": True,
        "reason": (
            "CFBD /lines is season_week grain; weeks 5–10 need 6 calls (>5 credit cap). "
            "lines_historical has no 2026 rows on this machine. Skip A5."
        ),
    }


def main() -> None:
    cfg = load_config()
    assert cfg.webapp.export_enabled is False, "export must stay FALSE"
    with ParquetStore(cfg.paths.staged_dir) as store:
        teams = store.read("teams", filters={"season": 2026})
        fbs = _fbs_ids(teams)
        a1 = section_a1(store, fbs)
        err = _graded_errors(store, fbs)
        a2 = section_a2(err)
        a3 = section_a3(a1, a2, err)
        print("A1-A3 done; running A4 (MC)…", flush=True)
        a4 = section_a4(store, err, fbs)
        a5 = section_a5()

    decision = a3["decision"]
    report = {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "export_enabled": False,
        "head": "b277a8b",
        "A1": a1,
        "A2": a2,
        "A3": a3,
        "A4": a4,
        "A5": a5,
        "card_decision": decision,
        "card_decision_rule": "PASS card iff R_plain >= 0.90",
        "summary_table": [
            {"check": "A1", "verdict": a1["verdict"]},
            {"check": "A2", "verdict": a2["verdict"]},
            {"check": "A3", "verdict": a3["verdict"]},
            {"check": "A4", "verdict": a4["verdict"]},
            {"check": "A5", "verdict": a5["verdict"]},
            {"check": "CARD_GATE", "verdict": decision},
        ],
    }
    out = OUT_DIR / "qa3_part_a.json"
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    md = OUT_DIR / "qa3_part_a.md"
    lines = [
        "## W11-QA-3 Part A — pricing σ vs realized error",
        "",
        f"Generated: `{report['generated_at']}`",
        "",
        f"**CARD GATE: {decision}** (R_plain={a3['R_plain']:.4f}; need ≥ 0.90)",
        "",
        f"- σ_price median (graded w2–w10): **{a3['sigma_price_median_graded_w2_w10']:.4f}**",
        f"- σ_price median (w11 FBS–FBS): **{a1['median']}** (IQR {a1['iqr']})",
        f"- realized SD: **{a2['sd']:.4f}** (n={a2['n']})",
        f"- robust SD: **{a2['robust_sd']:.4f}**",
        f"- R_plain: **{a3['R_plain']:.4f}** | R_robust: **{a3['R_robust']:.4f}**",
        "",
        "Full JSON: `docs/notes/_artifacts/w11-qa/qa3_part_a.json`.",
        "",
    ]
    md.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(report["summary_table"], indent=2))
    print("CARD_GATE", decision, "R_plain", a3["R_plain"])
    print("WROTE", out)


if __name__ == "__main__":
    main()
