"""W11-QA C1/C2: settle def_epa direction and audit consumers (read-only)."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "notes" / "_artifacts" / "w11-qa"
OUT.mkdir(parents=True, exist_ok=True)

FILTER_HIST = ROOT / "data" / "artifacts" / "state_space" / "filter_history.parquet"
TEAMS = ROOT / "data" / "staged" / "teams" / "season=2026" / "part.parquet"
GAMES_ROOT = ROOT / "data" / "staged" / "games"
PLAYS_ROOT = ROOT / "data" / "staged" / "plays"
RATINGS = OUT / "team_ratings_w10_publish.json"
if not RATINGS.is_file():
    RATINGS = ROOT / "data" / "social" / "artifacts_cache" / "latest" / "team_ratings_2026.json"


def _load_games(weeks: range) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for w in weeks:
        p = GAMES_ROOT / "season=2026" / f"week={w}" / "part.parquet"
        if p.is_file():
            frames.append(pd.read_parquet(p))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _load_plays(weeks: range) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for w in weeks:
        p = PLAYS_ROOT / "season=2026" / f"week={w}" / "part.parquet"
        if p.is_file():
            frames.append(pd.read_parquet(p))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def main() -> None:
    report: dict = {"generated_from": str(FILTER_HIST), "ratings_path": str(RATINGS)}

    # --- quote measurement ---
    measurement_quote = {
        "file": "src/ncaa_quant/ratings/league_state.py",
        "lines": "384-399",
        "home_epa_row": (
            "h[home_off]=1.0; h[away_def]=-1.0  ⇒  y_home_epa ≈ off_home − def_away (+ HFA + env)"
        ),
        "away_epa_row": (
            "h[away_off]=1.0; h[home_def]=-1.0  ⇒  y_away_epa ≈ off_away − def_home (+ env)"
        ),
        "implication": (
            "Holding offense fixed, a higher def_epa lowers predicted opponent EPA/play "
            "(more suppression) ⇒ higher def_epa = BETTER defense."
        ),
    }
    report["measurement"] = measurement_quote

    teams = pd.read_parquet(TEAMS) if TEAMS.is_file() else pd.DataFrame()
    fbs_ids: set[int] = set()
    school: dict[int, str] = {}
    conf: dict[int, str] = {}
    if not teams.empty:
        for r in teams.itertuples(index=False):
            tid = int(r.team_id)
            school[tid] = str(getattr(r, "school", tid))
            conf[tid] = str(getattr(r, "conference", "") or "")
            if str(getattr(r, "classification", "")).lower() == "fbs":
                fbs_ids.add(tid)

    # Local filter_history.parquet max season is 2025 (stale Aug 2026 file).
    # Use Nov 4 week-10 publish team_ratings for 2026 rating state.
    ratings_art = json.loads(RATINGS.read_text(encoding="utf-8"))
    report["filter_history_max_season"] = int(
        pd.read_parquet(FILTER_HIST, columns=["season"])["season"].max()
    )
    report["ratings_published_at"] = ratings_art.get("published_at")
    latest_rows: list[dict] = []
    for tid_s, blob in (ratings_art.get("teams") or {}).items():
        tid = int(tid_s)
        if fbs_ids and tid not in fbs_ids:
            continue
        weeks = sorted(blob.get("weeks") or [], key=lambda w: int(w["week"]))
        if not weeks:
            continue
        # Prefer last week<=10 point (week-10 synthetic shares EPA with post-w9).
        w10 = [w for w in weeks if int(w["week"]) <= 10]
        last = w10[-1] if w10 else weeks[-1]
        latest_rows.append(
            {
                "team_id": tid,
                "school": blob.get("school") or school.get(tid, tid_s),
                "off_epa": float(last.get("off_epa")),
                "def_epa": float(last.get("def_epa")),
                "week": int(last["week"]),
                "as_of_utc": last.get("as_of_utc"),
            }
        )
    latest = pd.DataFrame(latest_rows)
    report["n_teams_latest"] = int(len(latest))
    report["ratings_source"] = str(RATINGS)

    # Opponent raw offensive EPA/play against each team + points allowed/scored
    games = _load_games(range(1, 11))
    plays = _load_plays(range(1, 11))
    completed = games[games["completed"].astype(bool)].copy() if not games.empty else games

    # points allowed / scored per game
    pts_for: dict[int, list[float]] = defaultdict(list)
    pts_against: dict[int, list[float]] = defaultdict(list)
    if not completed.empty:
        for r in completed.itertuples(index=False):
            hid, aid = int(r.home_team_id), int(r.away_team_id)
            hp = getattr(r, "home_points", None)
            ap = getattr(r, "away_points", None)
            if hp is None or ap is None or (isinstance(hp, float) and math.isnan(hp)):
                continue
            hp, ap = float(hp), float(ap)
            pts_for[hid].append(hp)
            pts_against[hid].append(ap)
            pts_for[aid].append(ap)
            pts_against[aid].append(hp)

    # raw EPA/play against each defense / by each offense (play-level means)
    opp_raw_off: dict[int, float] = {}
    own_raw_off: dict[int, float] = {}
    if not plays.empty and "epa" in plays.columns:
        pl = plays.dropna(subset=["epa", "offense_id"]).copy()
        if "defense_id" in pl.columns:
            pl = pl.dropna(subset=["defense_id"])
            opp_raw_off = {
                int(k): float(v)
                for k, v in pl.groupby("defense_id")["epa"].mean().items()
            }
        own_raw_off = {
            int(k): float(v) for k, v in pl.groupby("offense_id")["epa"].mean().items()
        }

    def _lookup(d: dict[int, float], tid: int) -> float:
        return float(d[tid]) if tid in d else float("nan")

    rows = []
    for r in latest.itertuples(index=False):
        tid = int(r.team_id)
        rows.append(
            {
                "team_id": tid,
                "school": str(r.school),
                "conference": conf.get(tid, ""),
                "off_epa": float(r.off_epa),
                "def_epa": float(r.def_epa),
                "opp_raw_off_epa": _lookup(opp_raw_off, tid),
                "own_raw_off_epa": _lookup(own_raw_off, tid),
                "pts_allowed_pg": (
                    float(np.mean(pts_against[tid])) if pts_against.get(tid) else float("nan")
                ),
                "pts_scored_pg": (
                    float(np.mean(pts_for[tid])) if pts_for.get(tid) else float("nan")
                ),
            }
        )
    df = pd.DataFrame(rows)
    report["df_columns"] = list(df.columns)
    report["df_n"] = int(len(df))

    def spearman(a: str, b: str) -> dict:
        sub = df[[a, b]].dropna()
        if len(sub) < 10:
            return {"n": int(len(sub)), "rho": None, "pvalue": None}
        rho, p = stats.spearmanr(sub[a], sub[b])
        return {"n": int(len(sub)), "rho": float(rho), "pvalue": float(p)}

    spears = {
        "def_epa_vs_opp_raw_off_epa": spearman("def_epa", "opp_raw_off_epa"),
        "def_epa_vs_pts_allowed": spearman("def_epa", "pts_allowed_pg"),
        "off_epa_vs_own_raw_off_epa": spearman("off_epa", "own_raw_off_epa"),
        "off_epa_vs_pts_scored": spearman("off_epa", "pts_scored_pg"),
    }
    report["spearman"] = spears

    # Verdict from measurement + Spearman on defense
    # If higher def = better: expect NEGATIVE corr with opp EPA and pts allowed
    def_opp = spears["def_epa_vs_opp_raw_off_epa"].get("rho")
    def_pts = spears["def_epa_vs_pts_allowed"].get("rho")
    if def_opp is not None and def_pts is not None:
        if def_opp < 0 and def_pts < 0:
            verdict = "BETTER"
        elif def_opp > 0 and def_pts > 0:
            verdict = "WORSE"
        else:
            verdict = "MIXED"
    else:
        verdict = "UNVERIFIED"
    report["c1_verdict"] = (
        f"higher def_epa = {verdict} defense"
        if verdict in {"BETTER", "WORSE"}
        else verdict
    )
    report["c1_verdict_short"] = verdict

    # --- C2 consumers ---
    from ncaa_quant.ratings.state_space import StateSpaceConfig, fcs_pinned_state

    cfg = StateSpaceConfig()
    fcs = fcs_pinned_state(cfg)
    i_def = cfg.dim_index("def_epa")
    i_off = cfg.dim_index("off_epa")
    fcs_def = float(fcs.mean[i_def])
    fcs_off = float(fcs.mean[i_off])
    # FBS average from latest
    fbs_def_mean = float(df["def_epa"].mean())
    fbs_off_mean = float(df["off_epa"].mean())

    fcs_block = {
        "fcs_prior_mean_config": cfg.fcs_prior_mean,
        "fcs_off_epa": fcs_off,
        "fcs_def_epa": fcs_def,
        "fbs_mean_off_epa": fbs_off_mean,
        "fbs_mean_def_epa": fbs_def_mean,
        "comment_in_fcs_pinned_state": (
            "Higher def_epa means worse defense under off − def measurement."
        ),
        "code": "mean[def] = -config.fcs_prior_mean  # with prior=-0.05 → def=+0.05",
        "file_line": "src/ncaa_quant/ratings/state_space.py:529-538",
    }
    if verdict == "BETTER":
        # higher def better ⇒ FCS def +0.05 is BETTER than mean≈0 ⇒ FAIL
        fcs_defense_vs_fbs = "BETTER" if fcs_def > fbs_def_mean else "WORSE_OR_EQUAL"
        fcs_block["pinned_fcs_defense_vs_avg_fbs"] = fcs_defense_vs_fbs
        fcs_block["fail"] = fcs_defense_vs_fbs == "BETTER"
    elif verdict == "WORSE":
        fcs_defense_vs_fbs = "WORSE" if fcs_def > fbs_def_mean else "BETTER_OR_EQUAL"
        fcs_block["pinned_fcs_defense_vs_avg_fbs"] = fcs_defense_vs_fbs
        fcs_block["fail"] = fcs_defense_vs_fbs != "WORSE"
    else:
        fcs_block["fail"] = None
        fcs_block["pinned_fcs_defense_vs_avg_fbs"] = "UNVERIFIED"
    report["c2_fcs_prior"] = fcs_block

    predictor = {
        "file_line": "src/ncaa_quant/evaluation/production_stack.py:438-445",
        "quote": (
            "for dim in RATING_FEATURE_DIMS: "
            "row[f'home_{dim}']=h; row[f'away_{dim}']=a; "
            "row[f'{dim}_diff']=h-a; "
            "if dim in {off_epa,def_epa}: row[f'rating_diff_{dim}']=h-a"
        ),
        "linear_margin_identity_if_higher_def_better": (
            "home_margin ∝ (off_h − def_a) − (off_a − def_h) "
            "= (off_h − off_a) + (def_h − def_a) "
            "⇒ rating_diff_off and rating_diff_def as (home − away) are correct"
        ),
        "fail": False if verdict == "BETTER" else (True if verdict == "WORSE" else None),
        "note": (
            "No explicit sign flip on def_epa in feature builder; LightGBM consumes raw diffs. "
            "Under C1=BETTER, h−a for def is the right linear direction."
        ),
    }
    if verdict == "WORSE":
        predictor["fail"] = True
        predictor["note"] = (
            "Under C1=WORSE, rating_diff_def=h−a would enter the linear identity with "
            "the wrong sign relative to off−def measurement; flag FAIL."
        )
    report["c2_predictor"] = predictor

    site = {
        "RatingTrajectoryChart.tsx:186-188": (
            "Higher is better on defense. The rating measures EPA suppressed..."
        ),
        "fail_if_c1_better": False,
        "fail_if_c1_worse": True,
        "aligned_with_c1": verdict == "BETTER",
    }
    report["c2_site_labels"] = site

    acceptance = {
        "file_line": "scripts/_state_space_acceptance_14.py:252-253",
        "quote": (
            "Higher def_epa = more EPA suppressed = better defense; "
            "top_def = eos.sort_values('def_epa', ascending=False)"
        ),
        "aligned_with_c1": verdict == "BETTER",
    }
    report["c2_acceptance_script"] = acceptance

    # build_team_ratings — passthrough, no sort
    report["c2_build_team_ratings"] = {
        "file_line": "src/ncaa_quant/webapp/export.py:1295-1304",
        "quote": "emits weeks[].def_epa as stored; no rank/sort/label of defense quality",
        "fail": False,
    }

    # STOP gate
    stop = bool(fcs_block.get("fail")) or bool(predictor.get("fail"))
    report["c2_stop_gate"] = {
        "stop": stop,
        "reason": (
            "FCS prior pins defense BETTER than FBS average under C1=BETTER"
            if fcs_block.get("fail")
            else (
                "predictor feature sign wrong under C1"
                if predictor.get("fail")
                else "no stop"
            )
        ),
    }

    # also dump sample extremes for sanity
    report["def_epa_extremes"] = {
        "highest_10": df.nlargest(10, "def_epa")[
            ["school", "def_epa", "pts_allowed_pg", "opp_raw_off_epa"]
        ].to_dict(orient="records"),
        "lowest_10": df.nsmallest(10, "def_epa")[
            ["school", "def_epa", "pts_allowed_pg", "opp_raw_off_epa"]
        ].to_dict(orient="records"),
    }

    out_path = OUT / "c1_c2_def_epa.json"
    out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    md = OUT / "c1_c2_def_epa.md"
    lines = [
        "# W11-QA C1/C2 def_epa",
        "",
        f"## C1 verdict: **higher def_epa = {verdict} defense**",
        "",
        "### Measurement (league_state.update_game)",
        f"- `{measurement_quote['home_epa_row']}`",
        f"- `{measurement_quote['away_epa_row']}`",
        f"- {measurement_quote['implication']}",
        "",
        "### Spearman (FBS through week 10)",
    ]
    for k, v in spears.items():
        lines.append(f"- `{k}`: n={v['n']} rho={v['rho']} p={v['pvalue']}")
    lines += [
        "",
        "## C2 FCS prior",
        f"- config fcs_prior_mean={cfg.fcs_prior_mean}",
        f"- pinned fcs def_epa={fcs_def} off_epa={fcs_off}",
        f"- FBS mean def_epa={fbs_def_mean:.6f}",
        f"- pinned FCS defense vs avg FBS: **{fcs_block['pinned_fcs_defense_vs_avg_fbs']}**",
        f"- FAIL: **{fcs_block['fail']}**",
        "",
        "## C2 predictor",
        f"- {predictor['file_line']}",
        f"- fail: **{predictor['fail']}**",
        f"- {predictor['note']}",
        "",
        f"## STOP GATE: **{stop}** — {report['c2_stop_gate']['reason']}",
        "",
    ]
    md.write_text("\n".join(lines), encoding="utf-8")
    print(md.read_text(encoding="utf-8"))
    print("WROTE", out_path)


if __name__ == "__main__":
    main()
