"""Next plays after the max-7 card (both STARTER, edge order)."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pandas as pd

from ncaa_quant.config import load_config
from ncaa_quant.data.storage import ParquetStore

ANALYSIS = Path("docs/notes/_artifacts/social-s6-w9-card/analysis.json")
RATINGS = Path("data/social/artifacts_cache/latest/team_ratings_2026.json")


def main() -> None:
    d = json.loads(ANALYSIS.read_text(encoding="utf-8"))
    ratings = json.loads(RATINGS.read_text(encoding="utf-8"))
    id_to_school = {int(k): v["school"] for k, v in ratings["teams"].items()}

    cfg = load_config()
    with ParquetStore(cfg.paths.staged_dir) as store:
        qb = store.read("qb_status", filters={"season": 2026}).copy()
    qb["event_time"] = pd.to_datetime(qb["event_time"], utc=True)
    qb = qb.sort_values("event_time").groupby(["game_id", "team_id"], as_index=False).tail(1)

    st: dict[str, dict[str, str]] = defaultdict(dict)
    for _, r in qb.iterrows():
        school = id_to_school.get(int(r["team_id"]), str(int(r["team_id"])))
        st[str(int(r["game_id"]))][school] = str(r["status"])

    cands: list[dict] = []
    for r in d["betting_rows"]:
        edge = float(r.get("edge") or 0)
        gap = float(r.get("model_market_residual_points") or 0)
        if edge + 1e-12 < 0.045 or gap > 7.0 + 1e-9:
            continue
        gid = str(r["game_id"])
        away, _, home = str(r["matchup"]).partition(" @ ")
        qs = st.get(gid, {})
        if qs.get(away) != "starter" or qs.get(home) != "starter":
            continue
        cands.append(r)

    seen: set[str] = set()
    uniq: list[dict] = []
    for r in sorted(cands, key=lambda x: -float(x["edge"])):
        gid = str(r["game_id"])
        if gid in seen:
            continue
        seen.add(gid)
        uniq.append(r)

    print(f"both_starter_passers={len(uniq)}")
    for i, r in enumerate(uniq, 1):
        mark = "CARD" if i <= 7 else ("NEXT" if i <= 9 else "")
        line = r.get("shopped_line") if r.get("shopped_line") is not None else r.get("line")
        book = r.get("book_display") or r.get("book")
        print(
            f"{i:2d} {mark:4s} | {r['game_id']} | {r['matchup']} | {r['side']} | "
            f"{book} {line}/{r['price']} | edge={float(r['edge']):.4f} "
            f"gap={float(r['model_market_residual_points']):.2f}"
        )


if __name__ == "__main__":
    main()
