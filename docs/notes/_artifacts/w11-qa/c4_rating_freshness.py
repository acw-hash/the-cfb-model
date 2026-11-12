"""W11-QA C4: Nov 4 week-10 forecast rating freshness vs gap-check labels."""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime
from pathlib import Path

import boto3
import pandas as pd

from ncaa_quant.config import load_config, load_secrets
from ncaa_quant.webapp.export import load_teams_frame

OUT = Path(__file__).resolve().parent
PUBLISH_AT = datetime.fromisoformat("2026-11-04T14:07:21+00:00")
PUBLISH_AS_OF = datetime.fromisoformat("2026-11-04T14:03:14.629521+00:00")
SEED = 20261112

CARD_SCHOOLS = [
    "Texas",
    "Missouri",
    "Georgia",
    "Ole Miss",
    "Alabama",
    "LSU",
    "North Carolina",
    "UConn",
    "Arkansas",
    "Auburn",
    "Texas State",
    "Oregon State",
    "Kentucky",
    "Tennessee",
]


def _s3():
    cfg = load_config()
    secrets = load_secrets()
    return boto3.client(
        "s3",
        endpoint_url=cfg.webapp.r2_endpoint_url or None,
        aws_access_key_id=secrets.r2_access_key_id.get_secret_value(),
        aws_secret_access_key=secrets.r2_secret_access_key.get_secret_value(),
        region_name="auto",
    ), cfg.webapp.r2_bucket


def _get_json(s3, bucket: str, key: str) -> dict:
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    (OUT / Path(key).name.replace("/", "_")).write_bytes(body)
    return json.loads(body)


def _load_games(week: int) -> pd.DataFrame:
    cfg = load_config()
    path = (
        Path(cfg.paths.staged_dir)
        / "games"
        / "season=2026"
        / f"week={week}"
        / "part.parquet"
    )
    if not path.is_file():
        return pd.DataFrame()
    return pd.read_parquet(path)


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(UTC)


def _team_game_map(games: pd.DataFrame) -> dict[int, dict]:
    """Map team_id -> {event_time, opponent_id, game_id, week, start_date}."""
    out: dict[int, dict] = {}
    if games.empty:
        return out
    for r in games.itertuples(index=False):
        if not bool(getattr(r, "completed", False)):
            continue
        et = getattr(r, "event_time", None)
        if et is None and hasattr(r, "start_date"):
            et = getattr(r, "start_date")
        et_dt = pd.Timestamp(et).to_pydatetime()
        if et_dt.tzinfo is None:
            et_dt = et_dt.replace(tzinfo=UTC)
        else:
            et_dt = et_dt.astimezone(UTC)
        hid = int(r.home_team_id)
        aid = int(r.away_team_id)
        gid = str(int(r.game_id))
        week = int(r.week)
        out[hid] = {
            "event_time": et_dt,
            "opponent_id": aid,
            "game_id": gid,
            "week": week,
            "home": True,
        }
        out[aid] = {
            "event_time": et_dt,
            "opponent_id": hid,
            "game_id": gid,
            "week": week,
            "home": False,
        }
    return out


def _weeks_by_num(entry: dict) -> dict[int, dict]:
    return {int(w["week"]): w for w in (entry.get("weeks") or [])}


def _last_folded_from_ratings(
    entry: dict,
    *,
    publish_week: int,
    publish_as_of: datetime,
    names: dict[int, str],
    games_by_week: dict[int, dict[int, dict]],
) -> dict:
    """Infer last game folded into rating state used at publish.

    Publish-week points are synthetic weekly snapshots at as_of
    (``_append_current_week_rating_points``). Prefer the highest game-week
    point whose as_of_utc is not the publish as_of, else match schedule.
    """
    by_week = _weeks_by_num(entry)
    school = entry.get("school")
    # Candidate: max week < publish_week with a point
    game_weeks = sorted(w for w in by_week if w < publish_week)
    if not game_weeks:
        # only synthetic?
        syn = by_week.get(publish_week)
        return {
            "school": school,
            "last_rating_week_label": publish_week if syn else None,
            "last_as_of_utc": syn.get("as_of_utc") if syn else None,
            "inferred_source": "synthetic_only",
            "week9_present": False,
            "week9_bye": True,
            "last_game_week": None,
            "last_game_event_time": None,
            "opponent": None,
            "game_id": None,
        }

    # Walk down weeks; match as_of to schedule event_time when possible
    for w in reversed(game_weeks):
        pt = by_week[w]
        as_of = _parse_ts(pt.get("as_of_utc"))
        # Skip if this looks like a prior publish synthetic (Tue afternoon ~14:xx)
        # Prefer schedule match.
        gmap = games_by_week.get(w, {})
        # We need team_id — resolve via school from outer caller; here use as_of match
        # across all teams in that week is done by caller with tid.
        return {
            "school": school,
            "last_rating_week_label": w,
            "last_as_of_utc": pt.get("as_of_utc"),
            "as_of_dt": as_of,
            "has_publish_week_label": publish_week in by_week,
            "publish_week_as_of": (by_week.get(publish_week) or {}).get("as_of_utc"),
            "week9_point": by_week.get(9),
        }
    return {"school": school}


def analyze_team(
    tid: int,
    entry: dict,
    *,
    publish_week: int,
    names: dict[int, str],
    games_by_week: dict[int, dict[int, dict]],
    pre_entry: dict | None,
) -> dict:
    by_week = _weeks_by_num(entry)
    school = entry.get("school") or names.get(tid, str(tid))
    w9_sched = games_by_week.get(9, {}).get(tid)
    week9_bye = w9_sched is None

    # Last folded = highest week < publish_week present in ratings
    prior_weeks = sorted(w for w in by_week if w < publish_week)
    last_label = prior_weeks[-1] if prior_weeks else None
    last_pt = by_week.get(last_label) if last_label is not None else None
    last_as_of = _parse_ts((last_pt or {}).get("as_of_utc"))

    # Match to schedule game for that week if this team played
    sched = None
    if last_label is not None:
        sched = games_by_week.get(last_label, {}).get(tid)

    # Week-9 presence: rating has week=9 point whose as_of matches w9 game event
    # (postgame), OR week=9 exists and differs from pre-publish week-9 / week-8.
    w9_pt = by_week.get(9)
    week9_present = False
    week9_detail = "absent"
    if week9_bye:
        week9_detail = "bye"
        week9_present = True  # bye is OK
    elif w9_pt and w9_sched:
        as_of = _parse_ts(w9_pt.get("as_of_utc"))
        game_et = w9_sched["event_time"]
        # postgame uses game event_time; synthetic uses publish as_of
        if as_of is not None and abs((as_of - game_et).total_seconds()) < 3600:
            week9_present = True
            week9_detail = "week9_postgame_as_of_matches_game"
        elif as_of is not None and as_of.date() == game_et.date():
            week9_present = True
            week9_detail = "week9_as_of_same_calendar_day_as_game"
        elif as_of is not None and as_of >= game_et and as_of < PUBLISH_AS_OF:
            # as_of after game but before this publish — plausible fold
            week9_present = True
            week9_detail = "week9_as_of_after_game_before_publish"
        else:
            # compare EPA change vs pre-publish backup
            pre_by = _weeks_by_num(pre_entry or {})
            pre9 = pre_by.get(9) or pre_by.get(max(pre_by) if pre_by else -1)
            if pre9 and w9_pt:
                d_off = abs(float(w9_pt["off_epa"]) - float(pre9["off_epa"]))
                d_def = abs(float(w9_pt["def_epa"]) - float(pre9["def_epa"]))
                if d_off + d_def > 1e-9:
                    week9_present = True
                    week9_detail = "week9_epa_changed_vs_pre_publish"
                else:
                    week9_detail = (
                        f"week9_point_as_of={w9_pt.get('as_of_utc')} "
                        f"game_et={game_et.isoformat()} epa_unchanged_vs_pre"
                    )
            else:
                week9_detail = (
                    f"week9_point_as_of={w9_pt.get('as_of_utc')} "
                    f"game_et={game_et.isoformat()} no_pre_compare"
                )
    elif w9_sched and not w9_pt:
        week9_detail = "played_week9_but_no_week9_rating_point"
        week9_present = False

    opp_name = None
    opp_id = None
    game_id = None
    game_et_out = None
    game_week = last_label
    if sched:
        opp_id = sched["opponent_id"]
        opp_name = names.get(opp_id, str(opp_id))
        game_id = sched["game_id"]
        game_et_out = sched["event_time"].isoformat()
        game_week = sched["week"]
    elif w9_sched and week9_present:
        opp_id = w9_sched["opponent_id"]
        opp_name = names.get(opp_id, str(opp_id))
        game_id = w9_sched["game_id"]
        game_et_out = w9_sched["event_time"].isoformat()
        game_week = 9

    # If last_label week's schedule doesn't have this team (bye that week in
    # ratings carry?), fall back to scanning all schedule weeks for as_of match
    if sched is None and last_as_of is not None:
        for w, gmap in games_by_week.items():
            g = gmap.get(tid)
            if not g:
                continue
            if abs((g["event_time"] - last_as_of).total_seconds()) < 3600:
                opp_id = g["opponent_id"]
                opp_name = names.get(opp_id, str(opp_id))
                game_id = g["game_id"]
                game_et_out = g["event_time"].isoformat()
                game_week = w
                break

    syn10 = by_week.get(publish_week)
    return {
        "team_id": tid,
        "school": school,
        "publish_week_label_present": publish_week in by_week,
        "publish_week_as_of": (syn10 or {}).get("as_of_utc"),
        "last_rating_week_lt_publish": last_label,
        "last_rating_as_of_utc": (last_pt or {}).get("as_of_utc"),
        "last_folded_game_week": game_week,
        "last_folded_game_event_time": game_et_out,
        "opponent": opp_name,
        "game_id": game_id,
        "week9_bye": week9_bye,
        "week9_present_or_bye": week9_present,
        "week9_detail": week9_detail,
        "week9_rating_as_of": (w9_pt or {}).get("as_of_utc"),
        "week9_off_epa": (w9_pt or {}).get("off_epa"),
        "week10_off_epa": (syn10 or {}).get("off_epa"),
        "epa_week9_eq_week10": (
            w9_pt is not None
            and syn10 is not None
            and float(w9_pt["off_epa"]) == float(syn10["off_epa"])
            and float(w9_pt["def_epa"]) == float(syn10["def_epa"])
        ),
    }


def main() -> None:
    s3, bucket = _s3()
    print("bucket", bucket)

    # Nov 4 week-10 publish ratings (versioned) + pre-publish backup
    keys = {
        "w10_publish": "v1/2026/w10/tuesday_primary/team_ratings_2026.json",
        "w10_publish_latest_backup": "backup_latest_20261112/team_ratings_2026.json",
        "pre_publish": "backup_latest_20261104/team_ratings_2026.json",
        "w10_meta": "v1/2026/w10/tuesday_primary/meta.json",
        "w10_preds": "v1/2026/w10/tuesday_primary/week_predictions.json",
    }
    loaded: dict[str, dict] = {}
    for name, key in keys.items():
        try:
            loaded[name] = _get_json(s3, bucket, key)
            print("loaded", key, "published_at", loaded[name].get("published_at"), "bytes_ok")
        except Exception as exc:  # noqa: BLE001
            print("FAIL", key, type(exc).__name__, exc)

    ratings = loaded.get("w10_publish") or loaded.get("w10_publish_latest_backup")
    pre = loaded.get("pre_publish")
    if ratings is None:
        raise SystemExit("no week-10 publish ratings available")
    if pre is None:
        # local social cache is week-9 Tuesday (published_at 2026-10-28)
        local = Path("data/social/artifacts_cache/latest/team_ratings_2026.json")
        if local.is_file():
            pre = json.loads(local.read_text(encoding="utf-8"))
            print("pre_publish fallback local cache", pre.get("published_at"))
        else:
            raise SystemExit("no pre-publish ratings")

    (OUT / "team_ratings_w10_publish.json").write_text(
        json.dumps(ratings, indent=2), encoding="utf-8"
    )
    (OUT / "team_ratings_pre_publish.json").write_text(
        json.dumps(pre, indent=2), encoding="utf-8"
    )

    meta = loaded.get("w10_meta") or {}
    preds = loaded.get("w10_preds") or {}
    print(
        "meta",
        meta.get("published_at"),
        meta.get("week"),
        meta.get("refresh_kind"),
    )
    print(
        "preds",
        preds.get("published_at"),
        preds.get("week"),
        preds.get("as_of"),
        "n_games",
        len(preds.get("games") or []),
    )

    teams_frame = load_teams_frame(season=2026)
    names = {int(r.team_id): str(r.school) for r in teams_frame.itertuples()}
    school_to_id = {v: k for k, v in names.items()}
    # aliases
    for alias, canon in [("UConn", "Connecticut"), ("Ole Miss", "Ole Miss")]:
        if alias not in school_to_id and canon in school_to_id:
            school_to_id[alias] = school_to_id[canon]
    # Connecticut may be listed as UConn in ratings
    for tid, school in names.items():
        if school in ("Connecticut", "UConn"):
            school_to_id["UConn"] = tid
            school_to_id["Connecticut"] = tid

    games_by_week: dict[int, dict[int, dict]] = {}
    for w in range(1, 11):
        gdf = _load_games(w)
        games_by_week[w] = _team_game_map(gdf)
        print(f"week {w}: completed team-sides {len(games_by_week[w])}")

    teams = ratings.get("teams") or {}
    pre_teams = pre.get("teams") or {}

    # --- 1b card teams ---
    card_rows = []
    missing_schools = []
    for school in CARD_SCHOOLS:
        tid = school_to_id.get(school)
        if tid is None:
            # search case-insensitive / contains
            hits = [i for i, s in names.items() if school.lower() in s.lower()]
            if len(hits) == 1:
                tid = hits[0]
            else:
                missing_schools.append(school)
                continue
        entry = teams.get(str(tid)) or {}
        pre_entry = pre_teams.get(str(tid)) or {}
        row = analyze_team(
            tid,
            entry,
            publish_week=10,
            names=names,
            games_by_week=games_by_week,
            pre_entry=pre_entry,
        )
        card_rows.append(row)
        print(
            "CARD",
            row["school"],
            "last_et",
            row["last_folded_game_event_time"],
            "opp",
            row["opponent"],
            "w9",
            row["week9_present_or_bye"],
            row["week9_detail"],
        )

    if missing_schools:
        print("MISSING_SCHOOLS", missing_schools)

    card_fail = [r for r in card_rows if not r["week9_present_or_bye"]]
    print("CARD_FAIL_COUNT", len(card_fail))

    # --- 1c random 20 FBS ---
    # FBS pool = teams in ratings artifact
    fbs_ids = sorted(int(t) for t in teams.keys())
    rng = random.Random(SEED)
    sample_ids = sorted(rng.sample(fbs_ids, 20))
    rand_rows = []
    for tid in sample_ids:
        entry = teams.get(str(tid)) or {}
        pre_entry = pre_teams.get(str(tid)) or {}
        row = analyze_team(
            tid,
            entry,
            publish_week=10,
            names=names,
            games_by_week=games_by_week,
            pre_entry=pre_entry,
        )
        rand_rows.append(row)
        print(
            "RAND",
            row["school"],
            "last_et",
            row["last_folded_game_event_time"],
            "opp",
            row["opponent"],
            "w9",
            row["week9_present_or_bye"],
            row["week9_detail"],
        )

    # Aggregate EPA change vs pre for week-9 players
    played9 = set(games_by_week[9].keys())
    changed = 0
    unchanged = 0
    for tid in sorted(played9):
        e = teams.get(str(tid)) or {}
        p = pre_teams.get(str(tid)) or {}
        by = _weeks_by_num(e)
        pby = _weeks_by_num(p)
        # compare current max prior-to-10 vs pre max
        cur = by.get(9) or by.get(10)
        prev = pby.get(9) or (pby.get(max(pby)) if pby else None)
        if not cur or not prev:
            continue
        delta = abs(float(cur["off_epa"]) - float(prev["off_epa"])) + abs(
            float(cur["def_epa"]) - float(prev["def_epa"])
        )
        if delta > 1e-9:
            changed += 1
        else:
            unchanged += 1
    print("week9_players_epa_vs_pre changed", changed, "unchanged", unchanged)

    # Label semantics samples
    samples = []
    for school in ("Texas", "Auburn", "Georgia"):
        tid = school_to_id.get(school)
        if tid is None:
            continue
        by = _weeks_by_num(teams.get(str(tid)) or {})
        samples.append(
            {
                "school": school,
                "weeks": [
                    {
                        "week": w,
                        "as_of_utc": by[w].get("as_of_utc"),
                        "off_epa": by[w].get("off_epa"),
                    }
                    for w in sorted(by)
                    if w >= 7
                ],
            }
        )

    summary = {
        "publish_at": PUBLISH_AT.isoformat(),
        "publish_as_of": PUBLISH_AS_OF.isoformat(),
        "ratings_published_at": ratings.get("published_at"),
        "pre_published_at": pre.get("published_at"),
        "card_rows": card_rows,
        "card_fail_count": len(card_fail),
        "card_fails": card_fail,
        "random_seed": SEED,
        "random_rows": rand_rows,
        "random_fail_count": sum(1 for r in rand_rows if not r["week9_present_or_bye"]),
        "week9_players_epa_vs_pre": {"changed": changed, "unchanged": unchanged},
        "label_samples": samples,
        "missing_schools": missing_schools,
        "verdict_hint": (
            "gap check broken (labels only)"
            if not card_fail and changed > 0
            else (
                "week-10 forecasts built on stale ratings"
                if card_fail
                else "inconclusive"
            )
        ),
    }
    (OUT / "c4_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print("wrote", OUT / "c4_summary.json")
    print("VERDICT_HINT", summary["verdict_hint"])


if __name__ == "__main__":
    main()
