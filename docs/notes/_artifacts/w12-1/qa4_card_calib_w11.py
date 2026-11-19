"""W11-QA-4 — card-range calibration (READ-ONLY).

Reuses W11-QA-3 A4 pricing path; adds edge/gap gates and ledger pooled row.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ncaa_quant.betting.edges import BookPrice, compute_edge
from ncaa_quant.betting.provider import _DEFAULT_KERNEL, _sample_game_draws
from ncaa_quant.config import load_config
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.distribution.simulate import spread_cover_probs, two_way_side_prob
from ncaa_quant.utils.timeutils import to_utc

ROOT = Path(__file__).resolve().parents[4]  # docs/notes/_artifacts/w12-1 → repo root
HIST_DIR = ROOT / "data" / "webapp" / "publish_history"
OUT_DIR = ROOT / "docs" / "notes" / "_artifacts" / "w12-1"
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
N_DRAWS = 20_000
MIN_EDGE = 0.045
MAX_GAP = 7.0


def _wilson_interval(k: int, n: int, z: float = 1.6448536269514722) -> tuple[float, float]:
    """90% Wilson score interval for a binomial proportion."""
    if n <= 0:
        return (float("nan"), float("nan"))
    p = k / n
    denom = 1.0 + z * z / n
    centre = p + z * z / (2.0 * n)
    half = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
    return ((centre - half) / denom, (centre + half) / denom)


def _load_publish_week(week: int) -> dict[str, Any]:
    path = HIST_DIR / f"2026_w{week}.jsonl"
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    tue = [r for r in rows if r.get("refresh_kind") == "tuesday_primary"]
    if tue:
        return max(tue, key=lambda r: str(r.get("as_of") or ""))
    return max(rows, key=lambda r: str(r.get("as_of") or ""))


def _fbs_ids(teams: pd.DataFrame) -> set[int]:
    fbs = teams.loc[teams["classification"].astype(str).str.casefold() == "fbs"]
    return {int(x) for x in fbs["team_id"].tolist()}


def _prekick_two_way(
    snaps: pd.DataFrame,
    *,
    game_id: int,
    home_team: str,
    kickoff: Any,
) -> tuple[float, float, float, str, datetime] | None:
    """Latest regulated two-way: home_line, home_px, away_px, book, et."""
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
    latest_et = sub["event_time"].max()
    window = sub.loc[sub["event_time"] == latest_et]
    home_cf = str(home_team).casefold()
    for book, bdf in window.groupby("book", sort=False):
        home = bdf.loc[bdf["side"].astype(str).str.casefold() == home_cf]
        away = bdf.loc[bdf["side"].astype(str).str.casefold() != home_cf]
        if home.empty or away.empty:
            continue
        line = home.iloc[0]["line"]
        hp = home.iloc[0]["price"]
        ap = away.iloc[0]["price"]
        if not all(math.isfinite(float(x)) for x in (line, hp, ap)):
            continue
        return float(line), float(hp), float(ap), str(book), latest_et.to_pydatetime()
    return None


def _bucket(p: float) -> str | None:
    if p < 0.50:
        return None
    if p < 0.55:
        return "50-55"
    if p < 0.60:
        return "55-60"
    if p < 0.65:
        return "60-65"
    return "65+"


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    buckets = {k: {"n": 0, "pred_sum": 0.0, "cover_sum": 0.0} for k in ("50-55", "55-60", "60-65", "65+")}
    for r in rows:
        lab = _bucket(float(r["p_model"]))
        if lab is None:
            continue
        buckets[lab]["n"] += 1
        buckets[lab]["pred_sum"] += float(r["p_model"])
        buckets[lab]["cover_sum"] += float(r["cover"])
    out_b = {}
    for lab, b in buckets.items():
        n = int(b["n"])
        out_b[lab] = {
            "n": n,
            "mean_predicted": (b["pred_sum"] / n) if n else None,
            "realized_cover_rate": (b["cover_sum"] / n) if n else None,
        }
    n = len(rows)
    k = int(sum(float(r["cover"]) for r in rows))
    mean_p = float(np.mean([r["p_model"] for r in rows])) if n else None
    realized = (k / n) if n else None
    lo, hi = _wilson_interval(k, n) if n else (float("nan"), float("nan"))
    return {
        "buckets": out_b,
        "pooled": {
            "n": n,
            "mean_predicted": mean_p,
            "realized_cover_rate": realized,
            "n_covers": k,
            "ci90_wilson": [lo, hi],
        },
    }


def _price_one(
    *,
    gid: int,
    mu: float,
    sig: float,
    actual: float,
    home_line: float,
    home_px: float,
    away_px: float,
    book: str,
) -> dict[str, Any] | None:
    if not (math.isfinite(sig) and sig > 0 and math.isfinite(mu)):
        return None
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
    model_line_home = -float(mu)
    gap = abs(model_line_home - float(home_line))
    # Prefer higher two-way cover side (same as A4 model side).
    if p_home >= p_away:
        model_side = "home"
        p_model = p_home
        side_px, other_px = home_px, away_px
        market_line = home_line
        cover_raw = actual + home_line
    else:
        model_side = "away"
        p_model = p_away
        side_px, other_px = away_px, home_px
        market_line = -home_line
        cover_raw = -(actual + home_line)
    if abs(cover_raw) < 1e-12:
        return None  # push
    cover = 1.0 if cover_raw > 0 else 0.0
    edge_res = compute_edge(p_model, [BookPrice(book, side_px, other_px)])
    return {
        "game_id": gid,
        "book": book,
        "home_line": home_line,
        "market_line": market_line,
        "model_side": model_side,
        "p_model": float(p_model),
        "edge": float(edge_res.edge),
        "gap": float(gap),
        "cover": cover,
        "passes_gates": bool(float(edge_res.edge) >= MIN_EDGE and gap <= MAX_GAP),
    }


def section_slate(store: ParquetStore) -> dict[str, Any]:
    teams = store.read("teams", filters={"season": 2026})
    fbs = _fbs_ids(teams)
    id_to_school = {int(r.team_id): str(r.school) for r in teams.itertuples(index=False)}
    games = store.read("games", filters={"season": 2026})
    games = games.copy()
    games["game_id"] = pd.to_numeric(games["game_id"], errors="coerce").astype("Int64")
    snaps = store.read("odds_snapshots", filters={"season": 2026})
    snaps = snaps.copy()
    snaps["game_id"] = pd.to_numeric(snaps["game_id"], errors="coerce").astype("Int64")
    snaps = snaps.loc[snaps["week"].between(2, 11)]

    all_rows: list[dict[str, Any]] = []
    gated_rows: list[dict[str, Any]] = []
    for week in range(2, 12):
        pub = _load_publish_week(week)
        for g in pub.get("games") or []:
            try:
                gid = int(g["game_id"])
                hid = int(g["home_team_id"])
                aid = int(g["away_team_id"])
                mu = float(g["mu_margin"])
                sig = float(g["sigma_margin"])
            except (TypeError, ValueError, KeyError):
                continue
            if hid not in fbs or aid not in fbs:
                continue
            hit = games.loc[games["game_id"] == gid]
            if hit.empty:
                continue
            gr = hit.iloc[0]
            if pd.isna(gr.get("home_points")) or pd.isna(gr.get("away_points")):
                continue
            actual = float(gr["home_points"]) - float(gr["away_points"])
            home = id_to_school.get(hid, "")
            resolved = _prekick_two_way(
                snaps, game_id=gid, home_team=home, kickoff=gr.get("start_date")
            )
            if resolved is None:
                continue
            home_line, home_px, away_px, book, _et = resolved
            priced = _price_one(
                gid=gid,
                mu=mu,
                sig=sig,
                actual=actual,
                home_line=home_line,
                home_px=home_px,
                away_px=away_px,
                book=book,
            )
            if priced is None:
                continue
            priced["week"] = week
            all_rows.append(priced)
            if priced["passes_gates"]:
                gated_rows.append(priced)

    # Reproduce A4-style (all model-side, no edge gate) for table 1
    a4_like = _summarize(all_rows)
    gated = _summarize(gated_rows)
    return {
        "n_all": len(all_rows),
        "n_gated": len(gated_rows),
        "a4_all_model_side": a4_like,
        "gated_edge_gap": gated,
        "gates": {"min_edge": MIN_EDGE, "max_gap": MAX_GAP},
    }


def _ledger_calls() -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for wk in (2, 4, 5, 6, 7, 8, 9, 11):
        path = ROOT / "data" / "social" / f"posted_calls_2026_w{wk}.json"
        if not path.is_file():
            continue
        d = json.loads(path.read_text(encoding="utf-8"))
        for c in d.get("posted_calls") or []:
            calls.append({**c, "week": wk})
    # w10 task-card plays (no ledger on this machine) from W11-QA addendum
    w10 = [
        {"game_id": 401856743, "side": "Missouri", "line": 2.5, "price": -104, "book": "fanduel", "matchup": "Texas @ Missouri"},
        {"game_id": 401856741, "side": "Ole Miss", "line": 3.5, "price": -105, "book": "espnbet", "matchup": "Georgia @ Ole Miss"},
        {"game_id": 401856740, "side": "LSU", "line": -6.0, "price": -108, "book": "draftkings", "matchup": "Alabama @ LSU"},
        {"game_id": 401858292, "side": "UConn", "line": 7.5, "price": -102, "book": "fanduel", "matchup": "North Carolina @ UConn"},
        {"game_id": 401856738, "side": "Auburn", "line": -10.0, "price": -108, "book": "betmgm", "matchup": "Arkansas @ Auburn"},
        {"game_id": 401860912, "side": "Texas State", "line": 5.5, "price": -108, "book": "betmgm", "matchup": "Texas State @ Oregon State"},
        {"game_id": 401856745, "side": "Kentucky", "line": 6.5, "price": -105, "book": "hardrockbet", "matchup": "Kentucky @ Tennessee"},
    ]
    for c in w10:
        calls.append({**c, "week": 10, "source": "w11_qa_task_card"})
    return calls


def section_ledgers(store: ParquetStore) -> dict[str, Any]:
    games = store.read("games", filters={"season": 2026})
    games = games.copy()
    games["game_id"] = pd.to_numeric(games["game_id"], errors="coerce").astype("Int64")
    teams = store.read("teams", filters={"season": 2026})
    id_to_school = {int(r.team_id): str(r.school) for r in teams.itertuples(index=False)}
    school_to_id = {str(r.school): int(r.team_id) for r in teams.itertuples(index=False)}

    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for c in _ledger_calls():
        try:
            gid = int(c["game_id"])
            side = str(c["side"])
            line = float(c["line"])
            price = float(c["price"])
            book = str(c.get("book") or "unknown")
            week = int(c["week"])
        except (TypeError, ValueError, KeyError) as exc:
            skipped.append({"call": c, "reason": str(exc)})
            continue
        pub = _load_publish_week(week)
        gpub = next((g for g in (pub.get("games") or []) if int(g["game_id"]) == gid), None)
        if gpub is None:
            skipped.append({"game_id": gid, "week": week, "reason": "not_in_publish"})
            continue
        mu = float(gpub["mu_margin"])
        sig = float(gpub["sigma_margin"])
        hit = games.loc[games["game_id"] == gid]
        if hit.empty:
            skipped.append({"game_id": gid, "reason": "no_game"})
            continue
        gr = hit.iloc[0]
        if pd.isna(gr.get("home_points")) or pd.isna(gr.get("away_points")):
            skipped.append({"game_id": gid, "reason": "no_final"})
            continue
        actual = float(gr["home_points"]) - float(gr["away_points"])
        home_id = int(gr["home_team_id"])
        away_id = int(gr["away_team_id"])
        home = id_to_school.get(home_id, "")
        away = id_to_school.get(away_id, "")
        # home-centric line for the posted side
        if side.casefold() == home.casefold():
            home_line = line
            bet_on = "home"
        elif side.casefold() == away.casefold():
            home_line = -line
            bet_on = "away"
        else:
            skipped.append({"game_id": gid, "side": side, "reason": "side_not_home_or_away", "home": home, "away": away})
            continue
        # Other side price unknown in ledger → approximate other at -110 for de-vig edge
        # but for P(cover) we only need mu/sigma/line; edge not required for ledger pooled.
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
        p_model = two_way_side_prob(
            spread_cover_probs(draws, home_line, game_index=0, side=bet_on)  # type: ignore[arg-type]
        )
        cover_raw = (actual + home_line) if bet_on == "home" else -(actual + home_line)
        if abs(cover_raw) < 1e-12:
            skipped.append({"game_id": gid, "reason": "push"})
            continue
        cover = 1.0 if cover_raw > 0 else 0.0
        # optional edge vs -110 other
        other = -110.0
        edge_res = compute_edge(p_model, [BookPrice(book, price, other)])
        model_line_home = -mu
        gap = abs(model_line_home - home_line)
        rows.append(
            {
                "week": week,
                "game_id": gid,
                "side": side,
                "book": book,
                "line": line,
                "price": price,
                "p_model": float(p_model),
                "edge": float(edge_res.edge),
                "gap": float(gap),
                "cover": cover,
                "matchup": c.get("matchup"),
            }
        )
    return {
        "n_calls_loaded": len(_ledger_calls()),
        "n_used": len(rows),
        "n_skipped": len(skipped),
        "skipped_sample": skipped[:20],
        "pooled": _summarize(rows)["pooled"],
        "rows": rows,
    }


def section_fcs() -> dict[str, Any]:
    cfg = load_config()
    with ParquetStore(cfg.paths.staged_dir) as store:
        teams = store.read("teams", filters={"season": 2026})
    by_school = {
        str(r.school): {
            "team_id": int(r.team_id),
            "classification": str(r.classification),
            "conference": str(r.conference) if pd.notna(r.conference) else None,
        }
        for r in teams.itertuples(index=False)
    }
    top = json.loads(
        (ROOT / "docs/notes/_artifacts/social-s6-w11-card/b3_top25.json").read_text(
            encoding="utf-8"
        )
    )
    hits = []
    for r in top:
        a, h = str(r["matchup"]).split(" @ ", 1)
        for name, role in ((a, "away"), (h, "home")):
            meta = by_school.get(name)
            if meta is None:
                hits.append({**r, "role": role, "school": name, "classification": "MISSING"})
            elif str(meta["classification"]).casefold() != "fbs":
                hits.append({**r, "role": role, "school": name, **meta})
    # Also flag NDSU explicitly (colloquial FCS; staged 2026 class may differ)
    ndsu_note = by_school.get("North Dakota State")
    return {
        "non_fbs_in_top25": hits,
        "north_dakota_state_staged": ndsu_note,
        "code_path": (
            "src/ncaa_quant/ratings/league_state.py:350-363 — when home_is_fcs/away_is_fcs, "
            "key becomes FCS_TEAM_KEY; state loaded via fcs_pinned_state(cfg). "
            "Definition: src/ncaa_quant/ratings/state_space.py:529-541 "
            "(single shared GaussianState; mean=fcs_prior_mean on off/def). "
            "Keying: state_space.py:743-746 _team_key(..., is_fcs=True) -> FCS_TEAM_KEY; "
            "run_filter wiring state_space.py:1210-1241."
        ),
    }


def main() -> None:
    cfg = load_config()
    assert cfg.webapp.export_enabled is False
    print("export", cfg.webapp.export_enabled, flush=True)
    with ParquetStore(cfg.paths.staged_dir) as store:
        print("slate…", flush=True)
        slate = section_slate(store)
        print("ledgers…", flush=True)
        ledgers = section_ledgers(store)
    fcs = section_fcs()
    # Original A4 buckets from qa3 for paste fidelity
    qa3 = json.loads((OUT_DIR / "qa3_part_a.json").read_text(encoding="utf-8"))
    report = {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "export_enabled": False,
        "A4_from_qa3": qa3["A4"]["buckets"],
        "A4_qa3_n_used": qa3["A4"]["n_used"],
        "slate_recompute": slate,
        "ledgers": {
            "n_calls_loaded": ledgers["n_calls_loaded"],
            "n_used": ledgers["n_used"],
            "n_skipped": ledgers["n_skipped"],
            "skipped_sample": ledgers["skipped_sample"],
            "pooled": ledgers["pooled"],
        },
        "fcs": fcs,
    }
    out = OUT_DIR / "qa4_card_calib.json"
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    # also keep ledger row detail separately (smaller main report)
    (OUT_DIR / "qa4_ledger_rows.json").write_text(
        json.dumps(ledgers["rows"], indent=2), encoding="utf-8"
    )
    print("WROTE", out)
    print("gated pooled", slate["gated_edge_gap"]["pooled"])
    print("ledger pooled", ledgers["pooled"])
    print("fcs hits", fcs["non_fbs_in_top25"])
    print("NDSU staged", fcs["north_dakota_state_staged"])


if __name__ == "__main__":
    main()
