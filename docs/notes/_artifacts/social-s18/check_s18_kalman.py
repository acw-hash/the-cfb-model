"""S18 part 2: Kalman as-of + kickoff splits. Read-only."""

from __future__ import annotations

from datetime import UTC, datetime
from importlib.machinery import SourceFileLoader
from statistics import median

import duckdb
import pandas as pd

from ncaa_quant.cli import resolve_filter_history_path
from ncaa_quant.config import load_config

_v = SourceFileLoader("verify_pub", "scripts/verify_published_artifacts.py").load_module()
_get_json = _v._get_json
_s3_client = _v._s3_client

AS_OF = datetime(2026, 8, 27, 11, 33, 22, tzinfo=UTC)
TEAMS = {
    164: "Rutgers",
    113: "Massachusetts",
    328: "Utah State",
    304: "Idaho State",
}
DIMS = ["off_epa", "def_epa", "st_value", "pace"]


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def main() -> None:
    hist = pd.read_parquet(resolve_filter_history_path())
    bound = pd.Timestamp(AS_OF)
    print("===5 KALMAN filter_history posterior_asof===")
    print("hist_seasons", sorted(hist["season"].unique().tolist()))
    for tid, name in TEAMS.items():
        sub = hist[
            (hist["team_id"] == tid)
            & (hist["kind"] == "postgame")
            & (pd.to_datetime(hist["event_time"], utc=True) < bound)
        ].sort_values("event_time")
        if sub.empty:
            print({"team": name, "team_id": tid, "posterior": None})
            continue
        r = sub.iloc[-1]
        print(
            {
                "team": name,
                "team_id": tid,
                "season": int(r["season"]),
                "week": int(r["week"]),
                "event_time": str(r["event_time"]),
                "kind": r["kind"],
                **{d: float(r[d]) for d in DIMS},
                **{f"sd_{d}": float(r[f"sd_{d}"]) for d in DIMS},
            }
        )

    # prior 2026 observations: completed games with event_time < as_of
    con = duckdb.connect()
    games = con.execute(
        """
        SELECT game_id, home_team_id, away_team_id, event_time, completed, start_date
        FROM read_parquet('data/staged/games/season=2026/week=*/part.parquet', hive_partitioning=true)
        """
    ).fetchdf()
    games["event_time"] = pd.to_datetime(games["event_time"], utc=True)
    before = games[(games["event_time"] < bound) & (games["completed"] == True)]  # noqa: E712
    print("===5 PRIOR 2026 GAMES event_time < as_of===")
    print("n_completed_before_asof", len(before))
    for tid, name in TEAMS.items():
        n = int(((before["home_team_id"] == tid) | (before["away_team_id"] == tid)).sum())
        print({"team": name, "team_id": tid, "n_prior_2026_games": n})

    # also start_date < as_of
    games["start_date"] = pd.to_datetime(games["start_date"], utc=True)
    before_kick = games[(games["start_date"] < bound) & (games["completed"] == True)]  # noqa: E712
    print("n_completed_start_date_before_asof", len(before_kick))

    # earliest 2026 event_times
    et = games["event_time"].min()
    print("earliest_2026_event_time", et)

    # kickoff splits more carefully
    cfg = load_config()
    results = _get_json(_s3_client(), bucket=cfg.webapp.r2_bucket, key="latest/results_2026.json")
    graded = [g for g in results["games"] if g.get("grade_status") == "graded"]

    def ae(g):
        return abs(float(g["mu_margin"]) - float(g["actual_margin"]))

    def utc_date(g):
        return datetime.fromisoformat(str(g["kickoff_utc"]).replace("Z", "+00:00")).astimezone(UTC).date()

    early = [g for g in graded if utc_date(g).isoformat() in ("2026-08-29", "2026-08-30")]
    sept5 = [g for g in graded if utc_date(g).isoformat() == "2026-09-05"]
    not_early = [g for g in graded if utc_date(g).isoformat() not in ("2026-08-29", "2026-08-30")]

    print("===6 SPLITS===")
    for label, subset in (
        ("aug_29_30_utc", early),
        ("sept_5_utc_only", sept5),
        ("not_aug_29_30_complement_91", not_early),
    ):
        aes = [ae(g) for g in subset]
        print(
            {
                "bucket": label,
                "n": len(subset),
                "MAE": mean(aes),
                "median_AE": median(aes),
            }
        )

    # ET calendar date (America/New_York) in case user meant local slate
    import zoneinfo

    et_tz = zoneinfo.ZoneInfo("America/New_York")

    def et_date(g):
        return (
            datetime.fromisoformat(str(g["kickoff_utc"]).replace("Z", "+00:00"))
            .astimezone(et_tz)
            .date()
        )

    from collections import Counter

    print("kickoff_ET_date_counts", dict(sorted(Counter(et_date(g).isoformat() for g in graded).items())))
    early_et = [g for g in graded if et_date(g).isoformat() in ("2026-08-29", "2026-08-30")]
    sept5_et = [g for g in graded if et_date(g).isoformat() == "2026-09-05"]
    for label, subset in (("aug_29_30_ET", early_et), ("sept_5_ET", sept5_et)):
        aes = [ae(g) for g in subset]
        print({"bucket": label, "n": len(subset), "MAE": mean(aes), "median_AE": median(aes)})


if __name__ == "__main__":
    main()
