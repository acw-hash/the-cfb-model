"""W13-1: grade_export + W12 call grades + season record."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pandas as pd

from ncaa_quant.config import load_config
from ncaa_quant.data.storage import ParquetStore
from ncaa_quant.webapp.export import load_schedule_frame, load_teams_frame
from ncaa_quant.webapp.grade import grade_export

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[3]

# W10 task-card fallback (no local ledger on this machine; same list as W12-1).
W10_POSTED_CALLS: list[dict] = [
    {
        "game_id": "401856743",
        "matchup": "Texas @ Missouri",
        "side": "Missouri",
        "book": "fanduel",
        "line": 2.5,
        "price": -104.0,
    },
    {
        "game_id": "401856741",
        "matchup": "Georgia @ Ole Miss",
        "side": "Ole Miss",
        "book": "espnbet",
        "line": 3.5,
        "price": -105.0,
    },
    {
        "game_id": "401856740",
        "matchup": "Alabama @ LSU",
        "side": "LSU",
        "book": "draftkings",
        "line": -6.0,
        "price": -108.0,
    },
    {
        "game_id": "401858292",
        "matchup": "North Carolina @ UConn",
        "side": "UConn",
        "book": "fanduel",
        "line": 7.5,
        "price": -102.0,
    },
    {
        "game_id": "401856738",
        "matchup": "Arkansas @ Auburn",
        "side": "Auburn",
        "book": "betmgm",
        "line": -10.0,
        "price": -108.0,
    },
    {
        "game_id": "401860912",
        "matchup": "Texas State @ Oregon State",
        "side": "Texas State",
        "book": "betmgm",
        "line": 5.5,
        "price": -108.0,
    },
    {
        "game_id": "401856745",
        "matchup": "Kentucky @ Tennessee",
        "side": "Kentucky",
        "book": "hardrockbet",
        "line": 6.5,
        "price": -105.0,
    },
]


def _load_partition(table: str, season: int, week: int) -> pd.DataFrame:
    cfg = load_config()
    path = (
        Path(cfg.paths.staged_dir)
        / table
        / f"season={season}"
        / f"week={week}"
        / "part.parquet"
    )
    if not path.is_file():
        return pd.DataFrame()
    return pd.read_parquet(path)


def _classification(teams: pd.DataFrame) -> dict[int, str]:
    id_col = "team_id" if "team_id" in teams.columns else "id"
    out: dict[int, str] = {}
    for _, r in teams.iterrows():
        tid = int(r[id_col])
        cls = str(r.get("classification", "") or "").upper() or "?"
        out[tid] = cls
    return out


def _school(teams: pd.DataFrame) -> dict[int, str]:
    id_col = "team_id" if "team_id" in teams.columns else "id"
    name_col = next(
        c for c in ("school", "team", "name", "display_name") if c in teams.columns
    )
    return {int(r[id_col]): str(r[name_col]) for _, r in teams.iterrows()}


def grade_call(
    c: dict,
    games_by_id: dict[str, dict],
) -> tuple[str, str] | tuple[None, None]:
    g = games_by_id.get(str(c["game_id"]))
    if g is None:
        return None, None
    side = str(c["side"])
    line = float(c["line"])
    home_name, away_name = g["home_name"], g["away_name"]
    home_pts, away_pts = g["home_points"], g["away_points"]
    if side == home_name:
        side_pts, opp_pts = home_pts, away_pts
        side_bucket = "neutral" if g["neutral"] else "home"
    elif side == away_name:
        side_pts, opp_pts = away_pts, home_pts
        side_bucket = "neutral" if g["neutral"] else "away"
    else:
        raise ValueError(f"side {side!r} not in {home_name}/{away_name}")
    margin_vs_line = (side_pts - opp_pts) + line
    if margin_vs_line == 0:
        result = "P"
    elif margin_vs_line > 0:
        result = "W"
    else:
        result = "L"
    print(
        f"{c['matchup']}: {side} {line:+g} @ {c.get('book')} | "
        f"final {away_name} {away_pts:.0f}-{home_pts:.0f} {home_name} | {result}"
    )
    return result, side_bucket


def _games_by_id(week: int, names: dict[int, str], cfg: object) -> dict[str, dict]:
    gf = load_schedule_frame(season=2026, week=week, config=cfg)
    gbi: dict[str, dict] = {}
    for _, r in gf.iterrows():
        hid = int(r["home_team_id"])
        aid = int(r["away_team_id"])
        hp = r.get("home_points")
        ap = r.get("away_points")
        if pd.isna(hp) or pd.isna(ap):
            continue
        gbi[str(r["game_id"])] = {
            "home_points": float(hp),
            "away_points": float(ap),
            "home_name": names.get(hid, str(hid)),
            "away_name": names.get(aid, str(aid)),
            "neutral": bool(r.get("neutral_site", False)),
        }
    return gbi


def _closing_line(
    snaps: pd.DataFrame,
    *,
    game_id: str,
    book: str,
    side: str,
    home_name: str,
    kickoff: pd.Timestamp | None,
) -> tuple[float | None, float | None, str]:
    sub = snaps.loc[snaps["game_id"].astype("Int64") == int(game_id)].copy()
    if sub.empty:
        return None, None, "no_snaps"
    sub = sub.loc[
        (sub["market"].astype(str) == "spread") & (sub["book"].astype(str) == book)
    ]
    if sub.empty:
        return None, None, "book_missing"
    if kickoff is not None and pd.notna(kickoff):
        before = sub.loc[sub["event_time"] <= pd.Timestamp(kickoff)]
        if not before.empty:
            sub = before
    last_et = sub["event_time"].max()
    last = sub.loc[sub["event_time"] == last_et]
    pool = last.loc[last["side"].astype(str).str.casefold() == side.casefold()]
    if pool.empty:
        if side.casefold() == home_name.casefold():
            pool = last.loc[
                last["side"]
                .astype(str)
                .str.casefold()
                .isin({home_name.casefold(), "home"})
            ]
        else:
            pool = last.loc[
                last["side"].astype(str).str.casefold() != home_name.casefold()
            ]
    if pool.empty:
        return None, None, f"side_missing@{last_et.isoformat()}"
    row = pool.iloc[0]
    return float(row["line"]), float(row["price"]), last_et.isoformat()


def main() -> None:
    cfg = load_config()
    teams = load_teams_frame(season=2026, config=cfg)
    cls = _classification(teams)
    names = _school(teams)

    print("=== grade_export(season=2026) ===")
    results = grade_export(season=2026)
    games = results["games"]
    statuses = Counter(g.get("grade_status") for g in games)
    print("GRADE_STATUS", dict(statuses))

    fbs_fbs_ids: set[str] = set()
    for week in range(1, 13):
        gf = _load_partition("games", 2026, week)
        for _, r in gf.iterrows():
            hid, aid = int(r["home_team_id"]), int(r["away_team_id"])
            if (
                cls.get(hid, "?").upper() == "FBS"
                and cls.get(aid, "?").upper() == "FBS"
            ):
                fbs_fbs_ids.add(str(int(r["game_id"])))

    print("\n=== WEEK 5-12 FBS-FBS MAE / coverage ===")
    cov_series = []
    for week in range(5, 13):
        wg = [
            g
            for g in games
            if int(g.get("week") or 0) == week
            and g.get("grade_status") == "graded"
            and str(g["game_id"]) in fbs_fbs_ids
        ]
        abs_err = [
            abs(g["mu_margin"] - g["actual_margin"])
            for g in wg
            if g.get("mu_margin") is not None and g.get("actual_margin") is not None
        ]
        mae = sum(abs_err) / len(abs_err) if abs_err else float("nan")
        hits = [g for g in wg if g.get("margin_interval_hit") is not None]
        n_hit = sum(1 for g in hits if g["margin_interval_hit"])
        cov = n_hit / len(hits) if hits else float("nan")
        cov_series.append(cov)
        print(
            f"w{week} n={len(wg)} MAE={mae:.4f} cov80={cov:.4f} ({n_hit}/{len(hits)})"
        )
    print("cov80_series_w5_w12", " / ".join(f"{c:.3f}" for c in cov_series))

    print("\n=== W12 LEDGER ===")
    ledger_path = ROOT / "data/social/posted_calls_2026_w12.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    print("keys", sorted(ledger.keys()))
    print("n", ledger.get("n"))
    superseded = ledger.get("superseded_calls")
    print("superseded_calls", superseded if superseded is not None else "ABSENT")
    print("posted_at_utc", ledger.get("posted_at_utc"))
    print("plays:")
    for c in ledger["posted_calls"]:
        print(
            f"  {c['matchup']} | {c['side']} {c['line']:+g} @ {c['book']} "
            f"edge={c.get('edge')} posted_at={c.get('posted_at_utc')}"
        )

    print("\n=== WEEK-12 POSTED CALL GRADES ===")
    gbi12 = _games_by_id(12, names, cfg)
    store = ParquetStore(Path(cfg.paths.staged_dir))
    snaps = store.read("odds_snapshots", filters={"season": 2026}).copy()
    snaps["event_time"] = pd.to_datetime(snaps["event_time"], utc=True)
    gf12 = load_schedule_frame(season=2026, week=12, config=cfg)
    kick_by_gid = {
        str(r["game_id"]): pd.to_datetime(
            r.get("start_date"), utc=True, errors="coerce"
        )
        for _, r in gf12.iterrows()
    }
    home_by_gid = {
        str(r["game_id"]): names.get(int(r["home_team_id"]), str(r["home_team_id"]))
        for _, r in gf12.iterrows()
    }

    w = l = p = 0
    for c in ledger["posted_calls"]:
        result, _bucket = grade_call(c, gbi12)
        if result is None:
            print("NO_FINAL", c["matchup"])
            continue
        cl, cp, note = _closing_line(
            snaps,
            game_id=str(c["game_id"]),
            book=str(c["book"]),
            side=str(c["side"]),
            home_name=home_by_gid.get(str(c["game_id"]), ""),
            kickoff=kick_by_gid.get(str(c["game_id"])),
        )
        print(
            f"  close@{c['book']}: line={cl} price={cp} ({note}) | "
            f"posted {c['line']}/{c['price']}"
        )
        if result == "W":
            w += 1
        elif result == "L":
            l += 1
        else:
            p += 1
    print(f"w12 W-L-P = {w}-{l}-{p}")
    print("operator_expected 4-3-0")
    print("disagreement_vs_operator", f"{w}-{l}-{p}" != "4-3-0")

    print("\n=== SEASON RECORD (graded calls) ===")
    season_w = season_l = season_p = 0
    missing = []
    w10_present = (ROOT / "data/social/posted_calls_2026_w10.json").is_file()
    print(f"w10_ledger_present={w10_present}")
    for week in range(1, 13):
        if week == 10:
            path = ROOT / "data/social/posted_calls_2026_w10.json"
            if path.is_file():
                pl = json.loads(path.read_text(encoding="utf-8"))
                calls = pl["posted_calls"]
                source = "ledger"
            else:
                calls = W10_POSTED_CALLS
                source = "task_card_no_ledger"
        else:
            path = ROOT / "data/social" / f"posted_calls_2026_w{week}.json"
            if not path.is_file():
                print(f"w{week} MISSING ledger")
                missing.append(week)
                continue
            pl = json.loads(path.read_text(encoding="utf-8"))
            calls = pl["posted_calls"]
            source = "ledger"
        gbi = _games_by_id(week, names, cfg)
        ww = ll = pp = 0
        for c in calls:
            result, _bucket = grade_call(c, gbi)
            if result is None:
                continue
            if result == "W":
                ww += 1
            elif result == "L":
                ll += 1
            else:
                pp += 1
        print(
            f"w{week} W-L-P = {ww}-{ll}-{pp}  "
            f"(math: {ww}+{ll}+{pp}={ww + ll + pp}; source={source})"
        )
        season_w += ww
        season_l += ll
        season_p += pp
    print(f"SEASON TOTAL W-L-P = {season_w}-{season_l}-{season_p}")
    print("MISSING_WEEKS", missing)
    print("w10_counted", True)


if __name__ == "__main__":
    main()
