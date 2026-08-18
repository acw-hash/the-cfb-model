"""W9-D Phase 0 diagnostics. Report only. No R2 write, no fit, no latest/."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
ART = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

ET = ZoneInfo("America/New_York")


def _log(msg: str) -> None:
    now = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"W9-D {now} {msg}", flush=True)


def _iso(ts: Any) -> str | None:
    if ts is None or (isinstance(ts, float) and np.isnan(ts)):
        return None
    if isinstance(ts, datetime):
        return ts.astimezone(UTC).isoformat()
    parsed = pd.to_datetime(ts, utc=True)
    if pd.isna(parsed):
        return None
    return parsed.isoformat()


def _summarize_start_dates(rows: list[dict[str, Any]]) -> dict[str, Any]:
    starts = []
    for item in rows:
        raw = item.get("startDate") or item.get("start_date")
        ts = pd.to_datetime(raw, utc=True, errors="coerce")
        if pd.notna(ts):
            starts.append(ts)
    if not starts:
        return {"n": 0, "min": None, "max": None, "span_days": None}
    smin, smax = min(starts), max(starts)
    return {
        "n": len(starts),
        "min": smin.isoformat(),
        "max": smax.isoformat(),
        "span_days": float((smax - smin) / pd.Timedelta(days=1)),
    }


def _game_id(item: dict[str, Any]) -> str | None:
    gid = item.get("id") or item.get("game_id")
    if gid is None:
        return None
    return str(int(gid)) if str(gid).isdigit() else str(gid)


def diagnostic_02() -> dict[str, Any]:
    """Slate completeness: CFBD query vs staged lake vs prior years."""
    from ncaa_quant.config import load_config, load_secrets
    from ncaa_quant.evaluation.backtest_runner import load_staged_games
    from ncaa_quant.evaluation.walkforward import et_monday_of
    from ncaa_quant.ingestion.cfbd import CFBDClient
    from ncaa_quant.utils.timeutils import week_of

    cfg = load_config()
    key = load_secrets().cfbd_api_key.get_secret_value()
    client = CFBDClient(key)
    out: dict[str, Any] = {"inspected_at": datetime.now(tz=UTC).isoformat()}

    def _fetch(**params: Any) -> list[dict[str, Any]]:
        body = client.get("/games", params)
        parsed = json.loads(body.decode("utf-8"))
        if not isinstance(parsed, list):
            return []
        return [x for x in parsed if isinstance(x, dict)]

    fbs_w1 = _fetch(year=2026, week=1, seasonType="regular", classification="fbs")
    any_w1 = _fetch(year=2026, week=1, seasonType="regular")
    fbs_all = _fetch(year=2026, seasonType="regular", classification="fbs")
    any_all = _fetch(year=2026, seasonType="regular")
    fbs_w1_post = _fetch(year=2026, week=1, seasonType="postseason", classification="fbs")

    out["api"] = {
        "fbs_week1_regular_n": len(fbs_w1),
        "no_classification_week1_regular_n": len(any_w1),
        "fbs_all_regular_n": len(fbs_all),
        "no_classification_all_regular_n": len(any_all),
        "fbs_week1_postseason_n": len(fbs_w1_post),
        "fbs_week1_start": _summarize_start_dates(fbs_w1),
        "no_class_week1_start": _summarize_start_dates(any_w1),
    }

    def _class_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in rows:
            home_c = str(item.get("homeClassification") or item.get("home_classification") or "?")
            away_c = str(item.get("awayClassification") or item.get("away_classification") or "?")
            key_c = f"{home_c.casefold()} vs {away_c.casefold()}"
            counts[key_c] = counts.get(key_c, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

    out["api"]["fbs_week1_home_away_class"] = _class_counts(fbs_w1)
    out["api"]["no_class_week1_home_away_class"] = _class_counts(any_w1)

    fbs_ids = {_game_id(x) for x in fbs_w1}
    any_ids = {_game_id(x) for x in any_w1}
    out["api"]["ids_in_no_class_not_in_fbs"] = sorted(x for x in (any_ids - fbs_ids) if x)
    out["api"]["ids_in_fbs_not_in_no_class"] = sorted(x for x in (fbs_ids - any_ids) if x)

    # Week histogram for full-season FBS fetch.
    week_hist: dict[str, int] = {}
    for item in fbs_all:
        week_hist[str(item.get("week"))] = week_hist.get(str(item.get("week")), 0) + 1
    out["api"]["fbs_all_n_by_week"] = dict(sorted(week_hist.items(), key=lambda kv: int(kv[0] or -1)))

    staged = Path(cfg.paths.staged_dir)
    games_26 = load_staged_games(staged, (2026,))
    games_hist = load_staged_games(staged, (2024, 2025))
    out["staged"] = {}
    for season in (2024, 2025, 2026):
        src = games_26 if season == 2026 else games_hist
        sub = src.loc[src["season"].astype(int) == season].copy()
        w1 = sub.loc[sub["week"].astype(int) == 1]
        kick = pd.to_datetime(w1["start_date"], utc=True) if not w1.empty else pd.Series(dtype="datetime64[ns, UTC]")
        by_week = {int(w): int(n) for w, n in sub.groupby(sub["week"].astype(int)).size().items()}
        out["staged"][str(season)] = {
            "n_games": int(len(sub)),
            "n_week1": int(len(w1)),
            "week1_kick_min": _iso(kick.min()) if len(kick) else None,
            "week1_kick_max": _iso(kick.max()) if len(kick) else None,
            "n_by_week": by_week,
        }

    # Partition-boundary: FBS games whose kickoff sits in week-1's date span
    # but CFBD labeled a different week; and week-1 games whose ET Monday is
    # not the modal week-1 Monday.
    w1_starts = [
        pd.to_datetime(item.get("startDate") or item.get("start_date"), utc=True)
        for item in fbs_w1
    ]
    w1_starts = [ts.to_pydatetime() for ts in w1_starts if pd.notna(ts)]
    outside: list[dict[str, Any]] = []
    if w1_starts:
        lo, hi = min(w1_starts), max(w1_starts)
        for item in fbs_all:
            week = item.get("week")
            ts = pd.to_datetime(item.get("startDate") or item.get("start_date"), utc=True, errors="coerce")
            if pd.isna(ts):
                continue
            kick = ts.to_pydatetime()
            in_span = lo <= kick <= hi
            if in_span and int(week or -1) != 1:
                outside.append(
                    {
                        "game_id": _game_id(item),
                        "week": week,
                        "start": kick.isoformat(),
                        "home": item.get("homeTeam") or item.get("home_team"),
                        "away": item.get("awayTeam") or item.get("away_team"),
                    }
                )
        mondays = [et_monday_of(k) for k in w1_starts]
        counts: dict[datetime, int] = {}
        for m in mondays:
            counts[m] = counts.get(m, 0) + 1
        modal = min(d for d, n in counts.items() if n == max(counts.values()))
        non_modal = []
        for item, kick, monday in zip(fbs_w1, w1_starts, mondays, strict=False):
            if monday != modal:
                non_modal.append(
                    {
                        "game_id": _game_id(item),
                        "start": kick.isoformat(),
                        "et_monday": monday.isoformat(),
                    }
                )
        out["partition"] = {
            "week1_span": {"min": lo.isoformat(), "max": hi.isoformat()},
            "modal_et_monday": modal.isoformat(),
            "n_week1_on_modal_monday": int(counts.get(modal, 0)),
            "n_week1_off_modal_monday": len(non_modal),
            "week1_off_modal": non_modal[:20],
            "n_fbs_in_week1_span_other_week": len(outside),
            "in_span_other_week": outside[:30],
        }

        # Staged week-2 overlap with week-1 span.
        w2 = games_26.loc[games_26["week"].astype(int) == 2]
        if not w2.empty:
            k2 = pd.to_datetime(w2["start_date"], utc=True)
            overlap = w2.loc[(k2 >= lo) & (k2 <= hi)]
            out["partition"]["staged_week2_in_week1_span"] = int(len(overlap))
            out["partition"]["staged_week2_kick_min"] = _iso(k2.min())
        # Labor-Day week_of vs CFBD week for week-1 rows.
        if not games_26.empty:
            w1s = games_26.loc[games_26["week"].astype(int) == 1].copy()
            w1s["start_date"] = pd.to_datetime(w1s["start_date"], utc=True)
            labor = [week_of(ts.to_pydatetime(), 2026) for ts in w1s["start_date"]]
            out["partition"]["staged_week1_labor_day_week_of_counts"] = {
                str(k): int(labor.count(k)) for k in sorted(set(labor))
            }

    # Prior-year week-1 date spans for comparison.
    prior_spans = {}
    for season in (2024, 2025):
        src = games_hist.loc[
            (games_hist["season"].astype(int) == season) & (games_hist["week"].astype(int) == 1)
        ]
        kick = pd.to_datetime(src["start_date"], utc=True)
        prior_spans[str(season)] = {
            "n": int(len(src)),
            "min": _iso(kick.min()) if len(kick) else None,
            "max": _iso(kick.max()) if len(kick) else None,
            "span_days": float((kick.max() - kick.min()) / pd.Timedelta(days=1)) if len(kick) else None,
        }
    out["prior_week1_spans"] = prior_spans

    n_api = len(fbs_w1)
    n_staged = int(out["staged"]["2026"]["n_week1"])
    n_no_class = len(any_w1)
    extra_no_class = n_no_class - n_api
    verdict = "cfbd_incompleteness"
    if extra_no_class > 0 and n_api < 130:
        # Extra rows without classification are typically FCS; not the shortfall.
        pass
    if n_api != n_staged:
        verdict = "staged_lag_vs_api"
    # Query artifact: omitting classification yields a *larger FBS* week-1 slate
    # than classification=fbs, meaning the ingest filter dropped FBS games.
    fbs_like_no_class = 0
    for item in any_w1:
        home_c = str(item.get("homeClassification") or "").casefold()
        if home_c == "fbs":
            fbs_like_no_class += 1
    if fbs_like_no_class > n_api + 5:
        verdict = "query_artifact_classification_filter"
    out["verdict"] = {
        "label": verdict,
        "n_api_fbs_week1": n_api,
        "n_staged_week1": n_staged,
        "n_no_classification_week1": n_no_class,
        "n_no_class_home_fbs": fbs_like_no_class,
        "historical_week1": {"2024": 146, "2025": 142},
        "expected_to_grow_before_kickoff": n_api < 130,
        "reingest_indicated": n_api > n_staged,
        "note": (
            "classification=fbs is the ingest query. A shortfall vs 2024/2025 "
            "with matching API and staged counts is CFBD schedule timing, not "
            "a partition or classification bug."
        ),
    }
    client.close()
    return out


def diagnostic_03() -> dict[str, Any]:
    """First-publish date from the production calendar, no override."""
    from ncaa_quant.config import load_config, load_secrets
    from ncaa_quant.evaluation.backtest_runner import load_staged_games
    from ncaa_quant.evaluation.walkforward import WeekDecisionCalendar, week_decision_as_of
    from ncaa_quant.pipelines.predict import load_champion_walkforward_config
    from ncaa_quant.webapp.export import next_expected_publish_utc

    cfg = load_config()
    wf = load_champion_walkforward_config()
    games = load_staged_games(Path(cfg.paths.staged_dir), (2026,))
    w1 = games.loc[games["week"].astype(int) == 1].copy()
    calendar = WeekDecisionCalendar.from_games(games)
    as_of = week_decision_as_of(2026, 1, wf, calendar=calendar)
    pts = calendar.get(2026, 1)
    kick = pd.to_datetime(w1["start_date"], utc=True)
    before = w1.loc[kick < pd.Timestamp(as_of)].copy()
    before_ids = []
    for _, row in before.iterrows():
        before_ids.append(
            {
                "game_id": str(int(row["game_id"])),
                "kickoff_utc": _iso(row["start_date"]),
            }
        )

    now = datetime.now(tz=UTC)
    # Intended publish = calendar Tuesday (operator attended run at as_of).
    # Cron in config is Tuesday 06:00 UTC, which is four hours earlier than
    # 06:00 ET. Report both; calendar (no override) is the feature clock.
    cron = cfg.pipeline.predict_publish_cron_tuesday
    intended = as_of
    cron_utc = datetime(2026, 9, 1, 6, 0, tzinfo=UTC)
    next_exp = next_expected_publish_utc(intended, "tuesday_primary")

    production_live: dict[str, Any] = {}
    try:
        import boto3

        secrets = load_secrets()
        s3 = boto3.client(
            "s3",
            endpoint_url=cfg.webapp.r2_endpoint_url or None,
            aws_access_key_id=secrets.r2_access_key_id.get_secret_value(),
            aws_secret_access_key=secrets.r2_secret_access_key.get_secret_value(),
            region_name="auto",
        )
        body = s3.get_object(Bucket=cfg.webapp.r2_bucket, Key="latest/meta.json")["Body"].read()
        week_body = s3.get_object(Bucket=cfg.webapp.r2_bucket, Key="latest/week_predictions.json")[
            "Body"
        ].read()
        meta = json.loads(body.decode("utf-8"))
        week = json.loads(week_body.decode("utf-8"))
        production_live = {
            "meta_schema_version": meta.get("schema_version"),
            "meta_fixture": meta.get("fixture"),
            "meta_season": meta.get("season"),
            "meta_week": meta.get("week"),
            "meta_published_at": meta.get("published_at"),
            "week_n_games": len(week.get("games") or []),
            "week_fixture": week.get("fixture"),
            "read_only": True,
        }
    except Exception as exc:  # noqa: BLE001 — diagnostic; never fail Phase 0 on GET
        production_live = {"error": type(exc).__name__, "read_only": True}

    site_between_now_and_publish = (
        "Production latest/ currently serves 2024 fixture week-5 data "
        f"(fixture={production_live.get('meta_fixture')}, "
        f"season={production_live.get('meta_season')}, "
        f"week={production_live.get('meta_week')}, "
        f"n={production_live.get('week_n_games')}). "
        "No 2026 artifact is on latest/. Between now and the Tuesday week-1 "
        "publish the public site therefore continues to show 2024 fixture "
        "data, including through the opening weekend (games kick off "
        "2026-08-29, before as_of)."
    )

    return {
        "now_utc": now.isoformat(),
        "calendar_as_of": as_of.isoformat(),
        "calendar_as_of_et": as_of.astimezone(ET).isoformat(),
        "tuesday_0600_et": pts.tuesday_0600_et.isoformat() if pts else None,
        "saturday_0600_et": pts.saturday_0600_et.isoformat() if pts else None,
        "intended_publish_datetime_utc": intended.isoformat(),
        "intended_publish_equals_as_of": True,
        "config_cron_tuesday": cron,
        "cron_interpreted_2026_09_01_0600Z": cron_utc.isoformat(),
        "cron_vs_calendar_hours": (as_of - cron_utc).total_seconds() / 3600.0,
        "next_expected_publish_if_published_at_as_of": next_exp,
        "n_week1_games": int(len(w1)),
        "n_kickoff_before_as_of": int(len(before)),
        "early_games": before_ids,
        "kickoff_min": _iso(kick.min()) if len(kick) else None,
        "kickoff_max": _iso(kick.max()) if len(kick) else None,
        "opening_weekend_before_publish": True,
        "site_between_now_and_publish": site_between_now_and_publish,
        "production_latest_readonly": production_live,
        "override_used": False,
        "wf_clock": {
            "as_of_weekday": wf.as_of_weekday,
            "as_of_hour": wf.as_of_hour,
            "as_of_minute": wf.as_of_minute,
            "as_of_tz": wf.as_of_tz,
        },
    }


def _interval_impact(raw: np.ndarray, ordered: np.ndarray, rows: list[dict[str, Any]]) -> dict[str, Any]:
    from ncaa_quant.models.conformal import NOMINAL_TO_QUANTILES
    from ncaa_quant.models.heads.quantile import QUANTILES

    q = list(QUANTILES)
    q_lo, q_hi = NOMINAL_TO_QUANTILES[0.8]
    i_lo, i_hi = q.index(q_lo), q.index(q_hi)
    n = int(raw.shape[0])
    row_cross = np.any(raw != ordered, axis=1)
    adjacent_desc = np.any(np.diff(raw, axis=1) < -1e-12, axis=1)
    q10_changed = np.abs(raw[:, i_lo] - ordered[:, i_lo]) > 1e-9
    q90_changed = np.abs(raw[:, i_hi] - ordered[:, i_hi]) > 1e-9
    inverted_raw = raw[:, i_lo] > raw[:, i_hi] + 1e-12

    # CQR threshold from published rows (constant per batch in production).
    thrs: list[float] = []
    for rec, ord_row in zip(rows, ordered, strict=False):
        cqr_lo = rec.get("cqr_lo")
        if cqr_lo is None:
            continue
        try:
            thrs.append(float(ord_row[i_lo]) - float(cqr_lo))
        except (TypeError, ValueError):
            continue
    thr = float(np.median(thrs)) if thrs else 0.0

    pub_lo = ordered[:, i_lo] - thr
    pub_hi = ordered[:, i_hi] + thr
    raw_lo = raw[:, i_lo] - thr
    raw_hi = raw[:, i_hi] + thr
    d_lo = pub_lo - raw_lo  # == ordered_q10 - raw_q10
    d_hi = pub_hi - raw_hi
    d_width = (pub_hi - pub_lo) - (raw_hi - raw_lo)

    def _mag(arr: np.ndarray) -> dict[str, float]:
        absv = np.abs(arr)
        return {
            "min": float(np.min(absv)),
            "median": float(np.median(absv)),
            "p90": float(np.quantile(absv, 0.9)),
            "max": float(np.max(absv)),
            "mean": float(np.mean(absv)),
        }

    # Also q05/q95 in case a reader looks at uncalibrated tails.
    i05, i95 = q.index(0.05), q.index(0.95)
    d05 = ordered[:, i05] - raw[:, i05]
    d95 = ordered[:, i95] - raw[:, i95]

    return {
        "n_rows": n,
        "n_rows_raw_unordered": int(row_cross.sum()),
        "fraction_raw_unordered": float(row_cross.mean()) if n else None,
        "n_adjacent_decrease": int(adjacent_desc.sum()),
        "n_q10_changed_by_sort": int(q10_changed.sum()),
        "n_q90_changed_by_sort": int(q90_changed.sum()),
        "n_raw_q10_gt_q90": int(inverted_raw.sum()),
        "cqr_nominal": 0.8,
        "cqr_quantile_pair": [q_lo, q_hi],
        "cqr_threshold": thr,
        "published_lo_shift_vs_unsorted": _mag(d_lo),
        "published_hi_shift_vs_unsorted": _mag(d_hi),
        "published_width_shift_vs_unsorted": _mag(d_width),
        "n_published_lo_changed": int((np.abs(d_lo) > 1e-9).sum()),
        "n_published_hi_changed": int((np.abs(d_hi) > 1e-9).sum()),
        "q05_shift": _mag(d05),
        "q95_shift": _mag(d95),
        "universal": bool(n > 0 and int(row_cross.sum()) == n),
        "total_interval_note": (
            "total_interval_* are null on the published GamePrediction; "
            "there is no total quantile head, so sort does not change totals."
        ),
    }


def diagnostic_01() -> dict[str, Any]:
    """Quantile crossing fraction and published-interval magnitude. Report only."""
    import ncaa_quant.models.heads.quantile as qmod
    from ncaa_quant.pipelines.predict import live_predict_rows

    orig = qmod.enforce_quantile_order
    captures: list[dict[str, Any]] = []

    def wrapped(
        quantile_matrix: np.ndarray,
        *,
        quantiles: Any = qmod.QUANTILES,
    ) -> tuple[np.ndarray, bool]:
        ordered, crossed = orig(quantile_matrix, quantiles=quantiles)
        raw = np.asarray(quantile_matrix, dtype=float)
        captures.append(
            {
                "n": int(raw.shape[0]),
                "batch_crossed_flag": bool(crossed),
                "raw": raw.copy(),
                "ordered": np.asarray(ordered, dtype=float).copy(),
            }
        )
        return ordered, crossed

    qmod.enforce_quantile_order = wrapped  # type: ignore[assignment]
    out: dict[str, Any] = {}
    try:
        for label, season, week in (("2026_w1", 2026, 1), ("2024_w5", 2024, 5)):
            captures.clear()
            _log(f"quantile intercept live_predict_rows({season}, {week})")
            started = datetime.now(tz=UTC)
            rows = live_predict_rows(season, week)
            elapsed = (datetime.now(tz=UTC) - started).total_seconds()
            # Last capture is the margin quantile head on this batch.
            if not captures:
                out[label] = {"error": "no quantile capture", "elapsed_sec": elapsed}
                continue
            cap = captures[-1]
            impact = _interval_impact(cap["raw"], cap["ordered"], rows)
            impact["elapsed_sec"] = elapsed
            impact["n_predict_rows"] = len(rows)
            impact["n_captures_this_call"] = len(captures)
            impact["batch_warning_flag"] = cap["batch_crossed_flag"]
            out[label] = impact
            _log(
                f"{label} unordered={impact['n_rows_raw_unordered']}/{impact['n_rows']} "
                f"universal={impact['universal']} lo_changed={impact['n_published_lo_changed']}"
            )
    finally:
        qmod.enforce_quantile_order = orig  # type: ignore[assignment]
    return out


def main() -> None:
    ART.mkdir(parents=True, exist_ok=True)
    _log("phase0 start")
    skip_q = "--skip-quantile" in sys.argv

    _log("0.2 slate completeness")
    d02 = diagnostic_02()
    (ART / "phase0_02_slate.json").write_text(json.dumps(d02, indent=2, default=str) + "\n", encoding="utf-8")
    _log("0.2 verdict=" + json.dumps(d02.get("verdict"), default=str))

    _log("0.3 first-publish date")
    d03 = diagnostic_03()
    (ART / "phase0_03_calendar.json").write_text(json.dumps(d03, indent=2, default=str) + "\n", encoding="utf-8")
    _log("0.3 as_of=" + str(d03.get("calendar_as_of")) + " n_early=" + str(d03.get("n_kickoff_before_as_of")))

    d01: dict[str, Any] | None = None
    if skip_q:
        _log("0.1 skipped (--skip-quantile)")
    else:
        _log("0.1 quantile crossing (live predict, two weeks)")
        d01 = diagnostic_01()
        (ART / "phase0_01_quantile.json").write_text(
            json.dumps(d01, indent=2, default=str) + "\n", encoding="utf-8"
        )

    summary = {"0.1": d01, "0.2": d02.get("verdict"), "0.3": {
        "as_of": d03.get("calendar_as_of"),
        "intended_publish": d03.get("intended_publish_datetime_utc"),
        "n_kickoff_before_as_of": d03.get("n_kickoff_before_as_of"),
        "site": d03.get("site_between_now_and_publish"),
    }}
    (ART / "phase0_summary.json").write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")
    _log("phase0 done")


if __name__ == "__main__":
    main()
