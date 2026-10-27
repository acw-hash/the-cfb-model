"""Situational-angle research harness (pre-registered definitions, PIT, ledger)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
import yaml

from ncaa_quant.evaluation.walkforward import WalkForwardConfig, week_decision_as_of
from ncaa_quant.research.angles import (
    CONDITION_KEYS,
    OUTCOME_COLUMNS,
    STATUS_NOT_RUN_DATA_MISSING,
    STATUS_RETIRED_UNOPENED,
    AnglesConfigError,
    ConditionSet,
    NotRunDataMissingError,
    RetiredUnopenedError,
    as_of_minus,
    attach_efficiency_l3d,
    attach_last_game_luck,
    attach_outcomes,
    attach_trailing_luck_quantiles,
    build_schedule_conditions,
    build_team_game_frame,
    compute_game_luck,
    condition_columns,
    definition_needs_luck,
    evaluate_conditions,
    fbs_ids_by_season,
    flag_definition,
    hypothesis_needs_luck,
    load_angles_config,
    spread_bucket_label,
    truncate_games_after,
    week_bucket_label,
)
from ncaa_quant.research.angles_analysis import analyze_definition
from ncaa_quant.research.angles_ledger import HoldoutLockedError, open_holdout, read_ledger
from ncaa_quant.research.angles_run import (
    AnglesData,
    cfbd_close_frame,
    parse_ids,
    run_angles,
)
from ncaa_quant.research.angles_stats import benjamini_hochberg, cluster_mean, ols_cluster


def _kick(season: int, week: int, *, hours: float = 13.0) -> datetime:
    tue = week_decision_as_of(season, week, WalkForwardConfig())
    return tue + timedelta(days=4, hours=hours)


def _teams(*ids: int, season: int = 2019) -> pd.DataFrame:
    rows = []
    for tid in ids:
        rows.append(
            {
                "team_id": tid,
                "season": season,
                "school": f"School{tid}",
                "classification": "fbs",
                "event_time": datetime(season, 1, 1, tzinfo=UTC),
                "ingested_at": datetime(season, 1, 1, tzinfo=UTC),
            }
        )
    return pd.DataFrame(rows)


def _game(
    gid: int,
    season: int,
    week: int,
    home: int,
    away: int,
    *,
    home_pts: int | None = 31,
    away_pts: int | None = 17,
    conference: bool = False,
    fcs_away: bool = False,
    season_type: str = "regular",
) -> dict[str, Any]:
    start = _kick(season, week)
    return {
        "game_id": gid,
        "season": season,
        "week": week,
        "season_type": season_type,
        "start_date": start,
        "event_time": start + timedelta(hours=3.5),
        "home_team_id": home,
        "away_team_id": away,
        "home_points": home_pts,
        "away_points": away_pts,
        "neutral_site": False,
        "conference_game": conference,
        "venue_id": 1,
        "completed": home_pts is not None and away_pts is not None,
    }


def _h1_schedule(season: int = 2019, gid0: int = 100) -> pd.DataFrame:
    """A rested (14d) vs a 7-day opponent with no prior bye; plus an FCS rest-week."""
    # Team 1: W1 vs 3, W3 vs 2 (bye W2) — rest ~14d.
    # Team 2: W1 vs 4, W2 vs 5, W3 vs 1 — rest ~7d, no prior long rest.
    # Team 1 also plays FCS team 99 in W0 so the opener isn't the bye game.
    rows = [
        _game(gid0, season, 1, 1, 99, home_pts=40, away_pts=10),  # 1 vs FCS
        _game(gid0 + 1, season, 1, 2, 4, home_pts=21, away_pts=14),
        _game(gid0 + 2, season, 2, 2, 5, home_pts=24, away_pts=20),
        _game(gid0 + 3, season, 3, 1, 2, home_pts=31, away_pts=17, conference=True),
    ]
    return pd.DataFrame(rows)


def _teams_for(games: pd.DataFrame) -> pd.DataFrame:
    seasons = sorted({int(s) for s in games["season"].unique()})
    ids = sorted({int(x) for x in games["home_team_id"]} | {int(x) for x in games["away_team_id"]})
    rows = []
    for season in seasons:
        for tid in ids:
            rows.append(
                {
                    "team_id": tid,
                    "season": season,
                    "school": f"School{tid}",
                    "classification": "fcs" if tid == 99 else "fbs",
                    "event_time": datetime(season, 1, 1, tzinfo=UTC),
                    "ingested_at": datetime(season, 1, 1, tzinfo=UTC),
                }
            )
    return pd.DataFrame(rows)


def _primary(**conditions: Any) -> ConditionSet:
    return ConditionSet(
        id="t_primary",
        description="test",
        conditions=conditions,
        statistic="mean_ats_residual",
        direction="positive",
    )


def test_load_pre_registered_config() -> None:
    cfg = load_angles_config("configs/angles.yaml")
    assert set(cfg.hypotheses) >= {
        "H1",
        "H3",
        "H7",
        "H9",
        "H14",
        "H15",
        "H22",
        "H22r",
        "H23",
        "H24",
        "H25",
    }
    assert cfg.hypotheses["H25"].include_in_bh is False
    assert cfg.hypotheses["H22r"].primary.conditions["venue_away"] is True
    assert cfg.hypotheses["H22r"].status == STATUS_RETIRED_UNOPENED
    assert cfg.hypotheses["H25"].status == STATUS_RETIRED_UNOPENED
    assert "general home effect" in cfg.hypotheses["H22r"].retired_reason
    assert "slope p=0.27" in cfg.hypotheses["H25"].retired_reason
    h1 = cfg.hypotheses["H1"]
    assert h1.market == "spread"
    assert h1.primary.conditions["team_rest_days_min"] == 13.0
    assert h1.primary.conditions["opp_rest_days_min"] == 6.0
    assert h1.primary.conditions["opp_rest_days_max"] == 8.0
    assert set(h1.primary.conditions) <= CONDITION_KEYS
    assert cfg.split_seasons("discovery") == (2014, 2015, 2016, 2017, 2018, 2019, 2021, 2022)
    assert cfg.split_sensitivity_seasons("discovery") == (2020,)
    assert cfg.split_seasons("holdout") == (2023, 2024, 2025)
    h14 = cfg.hypotheses["H14"]
    assert h14.primary.conditions["opp_luck_last_q_min"] == 0.90
    digest = cfg.hypothesis_hash("H1")
    assert len(digest) == 16
    raw = yaml.safe_load(Path("configs/angles.yaml").read_text(encoding="utf-8"))
    variant_ids = [v["id"] for v in raw["hypotheses"][0]["variants"]]
    assert "H1v_rest10" in variant_ids
    assert "H1v_both_rested" not in variant_ids
    # Variants are not part of the lock hash: changing them would not be
    # visible here, but primary thresholds are.
    assert cfg.hypotheses["H1"].primary.conditions["opp_prior_long_rest_count_max"] == 0


def test_unknown_condition_key_refuses() -> None:
    with pytest.raises(AnglesConfigError, match="unknown condition"):
        evaluate_conditions(pd.DataFrame({"team_rest_days": [14.0]}), {"not_a_key": 1})


def test_sign_convention_home_favorite_covers() -> None:
    """Home favorite that wins by more than the spread has positive ats_residual from its side."""
    games = pd.DataFrame(
        [
            _game(1, 2019, 3, 10, 20, home_pts=31, away_pts=17),  # margin +14
        ]
    )
    teams = _teams(10, 20, season=2019)
    frame = build_team_game_frame(games, teams)
    close = pd.DataFrame(
        [{"game_id": 1, "spread": -7.0, "line_source": "cfbd_close", "n_books": 1}]
    )
    out = attach_outcomes(
        frame, games, close_cfbd=close, close_odds=None, tuesday_odds=None, ridge_predictions=None
    )
    home = out.rows.loc[out.rows["is_home"]].iloc[0]
    away = out.rows.loc[~out.rows["is_home"]].iloc[0]
    assert home["spread_close_cfbd"] == pytest.approx(-7.0)
    assert home["margin"] == pytest.approx(14.0)
    assert home["ats_residual_cfbd"] == pytest.approx(7.0)  # 14 − 7
    assert home["cover_cfbd"] == pytest.approx(1.0)
    assert away["spread_close_cfbd"] == pytest.approx(7.0)
    assert away["ats_residual_cfbd"] == pytest.approx(-7.0)
    assert away["cover_cfbd"] == pytest.approx(0.0)


def test_push_is_null_cover() -> None:
    games = pd.DataFrame([_game(1, 2019, 3, 10, 20, home_pts=24, away_pts=17)])  # +7
    teams = _teams(10, 20)
    frame = build_team_game_frame(games, teams)
    close = pd.DataFrame(
        [{"game_id": 1, "spread": -7.0, "line_source": "cfbd_close", "n_books": 1}]
    )
    out = attach_outcomes(
        frame, games, close_cfbd=close, close_odds=None, tuesday_odds=None, ridge_predictions=None
    )
    home = out.rows.loc[out.rows["is_home"]].iloc[0]
    assert home["ats_residual_cfbd"] == pytest.approx(0.0)
    assert pd.isna(home["cover_cfbd"])


def test_both_qualify_games_are_dropped_and_counted() -> None:
    games = pd.DataFrame(
        [
            _game(1, 2019, 2, 10, 20, conference=True),
            _game(2, 2019, 3, 10, 30, conference=False),
        ]
    )
    teams = _teams(10, 20, 30)
    frame = build_team_game_frame(games, teams)
    # Empty conditions hold on every side → both teams qualify → every game dropped.
    flagged = flag_definition(frame.rows, _primary())
    assert flagged.n_both_qualify_games == 2
    assert set(flagged.both_qualify_game_ids) == {1, 2}
    assert flagged.rows.empty or int(flagged.rows["qualifies"].sum()) == 0
    # A one-sided condition keeps a single row and does not drop the game.
    one = flag_definition(frame.rows, _primary(conference_game=True))
    assert one.n_both_qualify_games == 1
    assert 1 not in set(one.rows["game_id"])
    assert int(one.rows["qualifies"].sum()) == 0


def test_h1_rest_qualifies_and_counts_fcs_as_played() -> None:
    games = _h1_schedule()
    teams = _teams_for(games)
    frame = build_team_game_frame(games, teams)
    # FCS game is not in the FBS-vs-FBS universe.
    assert 99 not in set(frame.rows["team_id"]) and 99 not in set(frame.rows["opp_id"])
    # But rest for team 1 in week 3 still counts the FCS game as played.
    w3 = frame.rows.loc[(frame.rows["game_id"] == 103) & frame.rows["is_home"]].iloc[0]
    assert w3["team_rest_days"] == pytest.approx(14.0, abs=0.6)
    assert w3["opp_rest_days"] == pytest.approx(7.0, abs=0.6)
    assert int(w3["opp_prior_long_rest_count"]) == 0
    assert bool(w3["team_opener"]) is False
    cfg = load_angles_config()
    flagged = flag_definition(
        frame.rows, cfg.hypotheses["H1"].primary, cfg.hypotheses["H1"].baseline
    )
    q = flagged.rows.loc[flagged.rows["qualifies"]]
    assert len(q) == 1
    assert int(q.iloc[0]["team_id"]) == 1


def test_point_in_time_conditions_stable_when_later_games_deleted() -> None:
    later = _h1_schedule(2019, 200)
    later["week"] = later["week"] + 5
    later["start_date"] = [_kick(2019, int(w)) for w in later["week"]]
    later["event_time"] = later["start_date"] + pd.Timedelta(hours=3.5)
    games = pd.concat([_h1_schedule(2019, 100), later], ignore_index=True)
    teams = _teams_for(games)
    full = build_team_game_frame(games, teams)
    target_gid = 103
    target_kick = pd.Timestamp(games.loc[games["game_id"] == target_gid, "start_date"].iloc[0])
    truncated = truncate_games_after(games, target_kick.to_pydatetime())
    pit = build_team_game_frame(truncated, teams)
    cols = [
        c
        for c in condition_columns(full.rows)
        if c in pit.rows.columns and c not in {"kickoff", "game_end", "tuesday_as_of", "as_of"}
    ]
    a = full.rows.loc[full.rows["game_id"] == target_gid, cols].sort_values("is_home")
    b = pit.rows.loc[pit.rows["game_id"] == target_gid, cols].sort_values("is_home")
    pd.testing.assert_frame_equal(
        a.reset_index(drop=True), b.reset_index(drop=True), check_dtype=False
    )
    assert not any(c in full.rows.columns for c in OUTCOME_COLUMNS)


def test_holdout_ledger_refuses_second_open_without_force(tmp_path: Path) -> None:
    path = tmp_path / "holdout_ledger.jsonl"
    first = open_holdout("H1", "abc123", path=path, reason="first look")
    assert first.forced is False
    with pytest.raises(HoldoutLockedError, match="already opened"):
        open_holdout("H1", "abc123", path=path)
    with pytest.raises(HoldoutLockedError, match="definition has since changed"):
        open_holdout("H1", "fff999", path=path)
    forced = open_holdout("H1", "fff999", path=path, force=True, reason="override")
    assert forced.forced is True
    assert forced.override_kind == "definition_changed"
    assert forced.prior_config_hash == "abc123"
    entries = read_ledger(path)
    assert len(entries) == 2


def _flagged_planted(
    *,
    effect: float,
    n_seasons: int = 8,
    weeks: int = 10,
    seed: int = 1,
) -> tuple[Any, Any, Any]:
    """Synthetic flagged frame with a constant planted ATS residual on qualifies."""
    rng = np.random.default_rng(seed)
    rows: list[dict[str, Any]] = []
    gid = 1
    seasons = [2014 + i for i in range(n_seasons) if 2014 + i != 2020]
    for season in seasons:
        for week in range(1, weeks + 1):
            resid = float(effect)
            spread = float(rng.choice([-10.0, -3.0, 3.0, 10.0]))
            rows.append(
                {
                    "game_id": gid,
                    "season": season,
                    "week": week,
                    "season_week": f"{season}-{week:02d}",
                    "qualifies": True,
                    "baseline": False,
                    "venue": "home",
                    "conference_game": False,
                    "spread_close_cfbd": spread,
                    "ats_residual_cfbd": resid,
                    "cover_cfbd": 1.0 if resid > 0 else 0.0 if resid < 0 else np.nan,
                    "spread_close_odds": np.nan,
                    "ats_residual_odds": np.nan,
                    "cover_odds": np.nan,
                    "ridge_residual": resid - 0.2,
                    "spread_tue_odds": np.nan,
                    "line_move_toward_angle": np.nan,
                }
            )
            gid += 1
            rows.append(
                {
                    "game_id": gid,
                    "season": season,
                    "week": week,
                    "season_week": f"{season}-{week:02d}",
                    "qualifies": False,
                    "baseline": True,
                    "venue": "away",
                    "conference_game": True,
                    "spread_close_cfbd": -spread,
                    "ats_residual_cfbd": 0.0,
                    "cover_cfbd": np.nan,
                    "spread_close_odds": np.nan,
                    "ats_residual_odds": np.nan,
                    "cover_odds": np.nan,
                    "ridge_residual": 0.0,
                    "spread_tue_odds": np.nan,
                    "line_move_toward_angle": np.nan,
                }
            )
            gid += 1
    frame = pd.DataFrame(rows)
    from ncaa_quant.research.angles import FlaggedFrame
    from ncaa_quant.research.angles import Hypothesis as Hyp

    flagged = FlaggedFrame(
        rows=frame, n_both_qualify_games=0, n_both_baseline_games=0, both_qualify_game_ids=()
    )
    hyp = Hyp(
        id="Hplant",
        name="planted",
        side="test",
        market="spread",
        primary=_primary(),
        baseline=_primary(),
    )
    cfg = load_angles_config()
    return flagged, hyp, cfg


def test_planted_plus_three_is_recovered_no_effect_is_not_flagged() -> None:
    from ncaa_quant.research.angles import FlaggedFrame

    planted, hyp, cfg = _flagged_planted(effect=3.0)
    got = analyze_definition(
        planted,
        hyp,
        hyp.primary,
        cfg,
        kind="primary",
        seasons=tuple(sorted(planted.rows["season"].unique())),
        sensitivity_seasons=(),
    )
    cfbd = got.by_instrument["cfbd"]
    assert cfbd["mean_ats_residual"]["mean"] == pytest.approx(3.0, abs=0.05)
    assert cfbd["mean_ats_residual"]["p_two_sided"] < 0.01
    assert got.primary_p < 0.01

    zero, hyp0, cfg0 = _flagged_planted(effect=0.0)
    none = analyze_definition(
        zero,
        hyp0,
        hyp0.primary,
        cfg0,
        kind="primary",
        seasons=tuple(sorted(zero.rows["season"].unique())),
        sensitivity_seasons=(),
    )
    assert none.by_instrument["cfbd"]["mean_ats_residual"]["mean"] == pytest.approx(0.0, abs=0.05)
    assert none.primary_p > 0.10 or not np.isfinite(none.primary_p)
    bh = benjamini_hochberg({"plant": got.primary_p, "none": none.primary_p}, q=0.10)
    assert bh["plant"].rejected is True
    assert bh["none"].rejected is False
    assert isinstance(planted, FlaggedFrame)


def test_bh_step_up_arithmetic() -> None:
    # Classic BH: p = 0.01, 0.04, 0.15 with m=3, q=0.10 → thresholds 0.033, 0.067, 0.10
    out = benjamini_hochberg({"a": 0.01, "b": 0.04, "c": 0.15}, q=0.10)
    assert out["a"].rejected is True
    assert out["b"].rejected is True
    assert out["c"].rejected is False
    nan_out = benjamini_hochberg({"a": float("nan"), "b": 0.001}, q=0.10)
    assert nan_out["a"].rejected is False
    assert nan_out["b"].rejected is True


def test_run_angles_prints_counts_before_outcomes_and_skips_holdout(tmp_path: Path) -> None:
    games = _h1_schedule(2019)
    teams = _teams_for(games)
    lines = pd.DataFrame(
        [
            {
                "game_id": 103,
                "spread": -7.0,
                "line_type": "close",
                "book": "consensus",
                "season": 2019,
                "week": 3,
            }
        ]
    )
    cfg = load_angles_config()
    printed: list[str] = []
    data = AnglesData(games=games, teams=teams, cfbd_lines=lines)
    result = run_angles(
        data=data,
        ids=("H1",),
        split="discovery",
        config=cfg,
        notes_dir=tmp_path,
        ledger_path=tmp_path / "ledger.jsonl",
        family_path=tmp_path / "family.json",
        echo=printed.append,
        write_reports=True,
    )
    joined = "\n".join(printed)
    assert joined.index("Step 1") < joined.index("Step 2")
    assert "qualifying counts" in joined
    assert "2019" in joined
    assert result.holdout_entries == {}
    assert (tmp_path / "H1.md").is_file()
    assert (tmp_path / "summary.md").is_file()
    assert "Holdout was **not** opened" in (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert not (tmp_path / "ledger.jsonl").exists()


def test_run_angles_holdout_refuses_second_open(tmp_path: Path) -> None:
    games = _h1_schedule(2023)
    teams = _teams_for(games)
    cfg = load_angles_config()
    data = AnglesData(games=games, teams=teams, cfbd_lines=pd.DataFrame())
    ledger = tmp_path / "ledger.jsonl"
    run_angles(
        data=data,
        ids=("H1",),
        split="holdout",
        config=cfg,
        notes_dir=tmp_path / "r1",
        ledger_path=ledger,
        echo=lambda _s: None,
        write_reports=False,
    )
    with pytest.raises(HoldoutLockedError):
        run_angles(
            data=data,
            ids=("H1",),
            split="holdout",
            config=cfg,
            notes_dir=tmp_path / "r2",
            ledger_path=ledger,
            echo=lambda _s: None,
            write_reports=False,
        )


def test_cli_help_and_parse_ids() -> None:
    from typer.testing import CliRunner

    from ncaa_quant.cli import app

    result = CliRunner().invoke(app, ["research", "angles", "--help"])
    assert result.exit_code == 0
    assert "--split" in result.stdout
    assert "holdout" in result.stdout
    cfg = load_angles_config()
    assert parse_ids("H1,H3,H14", cfg) == ("H1", "H3", "H14")
    with pytest.raises(ValueError, match="unknown"):
        parse_ids("H99", cfg)
    all_ids = parse_ids("all", cfg)
    assert "H22r" not in all_ids
    assert "H25" not in all_ids
    assert "H2" not in all_ids
    assert "H13" not in all_ids
    assert "H22" in all_ids
    assert "H4" in all_ids
    assert "H20" in all_ids
    assert parse_ids("H22r", cfg) == ("H22r",)
    assert cfg.hypotheses["H2"].status == STATUS_NOT_RUN_DATA_MISSING
    assert cfg.hypotheses["H13"].status == STATUS_NOT_RUN_DATA_MISSING
    with pytest.raises(NotRunDataMissingError, match="H2"):
        parse_ids("H2", cfg)


def test_production_modules_do_not_import_research() -> None:
    root = Path("src/ncaa_quant")
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        if "research" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "ncaa_quant.research" in text and path.name != "cli.py":
            offenders.append(str(path))
    assert offenders == []
    # CLI may wire the research verb; it must not import research at module load.
    cli_text = Path("src/ncaa_quant/cli.py").read_text(encoding="utf-8")
    assert "from ncaa_quant.research" not in cli_text.split("def research_angles")[0]


def test_cfbd_close_frame_median() -> None:
    lines = pd.DataFrame(
        [
            {"game_id": 1, "line_type": "close", "spread": -6.5, "book": "a"},
            {"game_id": 1, "line_type": "close", "spread": -7.5, "book": "b"},
            {"game_id": 1, "line_type": "open", "spread": -3.0, "book": "a"},
        ]
    )
    out = cfbd_close_frame(lines)
    assert out.iloc[0]["spread"] == pytest.approx(-7.0)
    assert out.iloc[0]["line_source"] == "cfbd_close"
    assert int(out.iloc[0]["n_books"]) == 2


def test_cluster_mean_and_ols_shapes() -> None:
    y = np.array([1.0, 1.0, 2.0, 2.0])
    c = np.array(["a", "a", "b", "b"])
    cm = cluster_mean(y, c)
    assert cm.n == 4
    assert cm.n_clusters == 2
    assert cm.mean == pytest.approx(1.5)
    x = np.column_stack([np.ones(4), np.array([0.0, 0.0, 1.0, 1.0])])
    ols = ols_cluster(y, x, ["intercept", "g"], c)
    assert ols.n == 4
    assert "g" in ols.names


def test_schedule_conditions_opener_and_streak() -> None:
    games = pd.DataFrame(
        [
            _game(1, 2019, 1, 1, 2),
            _game(2, 2019, 2, 1, 3),
            _game(3, 2019, 4, 1, 4),  # ~14d rest after W2 → streak resets
        ]
    )
    cond = build_schedule_conditions(games)
    t1 = cond.loc[cond["team_id"] == 1].sort_values("game_id")
    assert bool(t1.iloc[0]["opener"]) is True
    assert int(t1.iloc[1]["consecutive_weeks"]) == 2
    assert int(t1.iloc[2]["consecutive_weeks"]) == 1
    assert int(t1.iloc[2]["prior_long_rest_count"]) == 0
    # After recording the long rest of game 3, the next game would see count=1.
    # Game 3 itself still has count 0 (prior only).
    assert t1.iloc[2]["rest_days"] >= 13.0


def test_week_and_spread_bucket_labels() -> None:
    buckets = [[1, 4], [5, 8], [9, 12], [13, 20]]
    assert week_bucket_label(3, buckets) == "w01-04"
    assert week_bucket_label(10, buckets) == "w09-12"
    assert week_bucket_label(99, buckets) == "w_other"
    spread_b = {
        "fav_7plus": {"max": -7.0},
        "fav_lt7": {"min": -7.0, "max": 0.0},
        "dog_lt7": {"min": 0.0, "max": 7.0},
        "dog_7plus": {"min": 7.0},
    }
    assert spread_bucket_label(-10.0, spread_b) == "fav_7plus"
    assert spread_bucket_label(-3.0, spread_b) == "fav_lt7"
    assert spread_bucket_label(3.0, spread_b) == "dog_lt7"
    assert spread_bucket_label(10.0, spread_b) == "dog_7plus"
    assert spread_bucket_label(float("nan"), spread_b) is None


def test_h3_trend_and_h14_regression_on_synthetic_frame() -> None:
    cfg = load_angles_config()
    h3 = cfg.hypotheses["H3"]
    h14 = cfg.hypotheses["H14"]
    assert hypothesis_needs_luck(h14) is True
    assert definition_needs_luck(h3.primary) is False
    rows: list[dict[str, Any]] = []
    gid = 1
    for season in (2016, 2017, 2018, 2019, 2021, 2022):
        for week in range(1, 9):
            streak = 2 + (week % 6)
            luck = float(week - 4)
            resid = 0.4 * min(streak, 7) + 0.1 * luck
            rows.append(
                {
                    "game_id": gid,
                    "season": season,
                    "week": week,
                    "season_week": f"{season}-{week:02d}",
                    "qualifies": True,
                    "baseline": False,
                    "venue": "home" if week % 2 else "away",
                    "conference_game": bool(week % 3 == 0),
                    "opp_consecutive_weeks": streak,
                    "opp_luck_last": luck,
                    "spread_close_cfbd": -3.0,
                    "ats_residual_cfbd": resid,
                    "cover_cfbd": 1.0 if resid > 0 else 0.0,
                    "spread_close_odds": -3.0 if season >= 2021 else float("nan"),
                    "ats_residual_odds": resid if season >= 2021 else float("nan"),
                    "cover_odds": (1.0 if resid > 0 else 0.0) if season >= 2021 else float("nan"),
                    "ridge_residual": resid - 0.1,
                    "spread_tue_odds": -2.0 if season >= 2021 else float("nan"),
                    "line_move_toward_angle": 1.0 if season >= 2021 else float("nan"),
                }
            )
            gid += 1
    from ncaa_quant.research.angles import FlaggedFrame

    flagged = FlaggedFrame(
        rows=pd.DataFrame(rows),
        n_both_qualify_games=0,
        n_both_baseline_games=0,
        both_qualify_game_ids=(),
    )
    seasons = (2016, 2017, 2018, 2019, 2021, 2022)
    t = analyze_definition(
        flagged, h3, h3.primary, cfg, kind="primary", seasons=seasons, sensitivity_seasons=()
    )
    assert t.by_instrument["cfbd"]["trend"] is not None
    assert t.by_instrument["cfbd"]["trend"]["slope"]["coef"] > 0
    r = analyze_definition(
        flagged, h14, h14.primary, cfg, kind="primary", seasons=seasons, sensitivity_seasons=()
    )
    cregr = r.by_instrument["cfbd"]["continuous_regression"]
    assert cregr is not None and cregr["n"] > 0
    move = r.by_instrument["odds"].get("line_movement")
    assert move is not None and move["n"] > 0


def test_luck_sign_blowout_favorite_epa_matches_expectation() -> None:
    """Won by 30 as a 7-point favorite, EPA margin = expectation → luck = +23."""
    home_fav = pd.DataFrame(
        [_game(1, 2019, 3, 10, 20, home_pts=37, away_pts=7)]  # margin +30
    )
    epa = pd.DataFrame(
        [
            {
                "game_id": 1,
                "home_epa": 0.0,
                "away_epa": 0.0,
                "home_plays": 70.0,
                "away_plays": 70.0,
            }
        ]
    )
    prior = pd.DataFrame(
        [
            {
                "team_id": tid,
                "season": 2019,
                "event_time": pd.Timestamp("2019-01-01T00:00:00Z"),
                "off_epa": 0.0,
                "def_epa": 0.0,
                "kind": "postgame",
            }
            for tid in (10, 20)
        ]
    )
    luck = compute_game_luck(home_fav, epa, {1: -7.0}, prior, hfa_epa_per_play=0.0)
    row = luck.loc[luck["game_id"] == 1].iloc[0]
    assert row["luck_ats_home"] == pytest.approx(23.0)
    assert row["epa_margin_home"] == pytest.approx(0.0)
    assert row["expected_epa_margin_home"] == pytest.approx(0.0)
    assert row["luck_home"] == pytest.approx(23.0)

    # Same team, same blowout, but they were the away favorite (home spread +7).
    away_fav = pd.DataFrame([_game(2, 2019, 3, 20, 10, home_pts=7, away_pts=37)])
    epa2 = pd.DataFrame(
        [
            {
                "game_id": 2,
                "home_epa": 0.0,
                "away_epa": 0.0,
                "home_plays": 70.0,
                "away_plays": 70.0,
            }
        ]
    )
    luck2 = compute_game_luck(away_fav, epa2, {2: 7.0}, prior, hfa_epa_per_play=0.0)
    home_row = luck2.loc[luck2["game_id"] == 2].iloc[0]
    assert home_row["luck_home"] == pytest.approx(-23.0)
    nxt = pd.DataFrame(
        [
            _game(2, 2019, 3, 20, 10, home_pts=7, away_pts=37),
            _game(3, 2019, 4, 30, 10, home_pts=14, away_pts=21),
        ]
    )
    teams = _teams(10, 20, 30)
    frame = build_team_game_frame(nxt, teams)
    attached = attach_last_game_luck(frame.rows, luck2, nxt)
    carried = attached.loc[(attached["game_id"] == 3) & (attached["opp_id"] == 10)].iloc[0]
    assert carried["opp_luck_last"] == pytest.approx(23.0)


def test_luck_from_last_game_is_knowable_before_tuesday() -> None:
    g1 = _game(1, 2019, 1, 10, 20, home_pts=31, away_pts=17)
    g2 = _game(2, 2019, 2, 30, 10, home_pts=14, away_pts=21)
    games = pd.DataFrame([g1, g2])
    teams = _teams(10, 20, 30)
    epa = pd.DataFrame(
        [
            {
                "game_id": 1,
                "home_epa": 0.1,
                "away_epa": 0.0,
                "home_plays": 70.0,
                "away_plays": 70.0,
            }
        ]
    )
    hist = pd.DataFrame(
        [
            {
                "team_id": 10,
                "season": 2019,
                "event_time": pd.Timestamp("2019-01-01T00:00:00Z"),
                "off_epa": 0.1,
                "def_epa": 0.0,
                "kind": "postgame",
            },
            {
                "team_id": 20,
                "season": 2019,
                "event_time": pd.Timestamp("2019-01-01T00:00:00Z"),
                "off_epa": 0.0,
                "def_epa": 0.0,
                "kind": "postgame",
            },
        ]
    )
    luck = compute_game_luck(games, epa, {1: -7.0, 2: 3.0}, hist, hfa_epa_per_play=0.025)
    assert luck.loc[luck["game_id"] == 1, "luck_ats_home"].iloc[0] == pytest.approx(7.0)
    frame = build_team_game_frame(games, teams)
    rows = attach_last_game_luck(frame.rows, luck, games)
    rows = attach_trailing_luck_quantiles(rows, luck, games)
    nxt = rows.loc[(rows["game_id"] == 2) & (rows["opp_id"] == 10)].iloc[0]
    assert nxt["opp_last_game_completed_before_as_of"] in (True, False) or pd.notna(
        nxt["opp_luck_last"]
    )
    # Team 10 was home in game 1; game 2's opponent 10 carries that luck.
    assert pd.notna(nxt["opp_luck_last"])


def test_attach_efficiency_l3d_reuses_store_and_does_not_recompute() -> None:
    games = pd.DataFrame([_game(1, 2019, 3, 10, 20)])
    teams = _teams(10, 20)
    frame = build_team_game_frame(games, teams)
    feat = pd.DataFrame(
        [
            {"entity_id": 10, "season": 2019, "week": 3, "value": 0.12},
            {"entity_id": 20, "season": 2019, "week": 3, "value": -0.04},
        ]
    )
    out = attach_efficiency_l3d(frame.rows, {"adj_off_epa_l3d": feat})
    home = out.loc[out["is_home"]].iloc[0]
    assert home["team_adj_off_epa_l3d"] == pytest.approx(0.12)
    assert home["opp_adj_off_epa_l3d"] == pytest.approx(-0.04)


def test_odds_tuesday_uses_named_decision_point() -> None:
    from ncaa_quant.research.angles_run import odds_tuesday_frame

    games = pd.DataFrame([_game(1, 2021, 3, 10, 20)])
    teams = _teams(10, 20, season=2021)
    tue = week_decision_as_of(2021, 3, WalkForwardConfig())
    snaps = pd.DataFrame(
        [
            {
                "game_id": 1,
                "market": "spread",
                "side": "School10",
                "line": -6.5,
                "book": "dk",
                "event_time": tue,
                "decision_point": "tuesday_0600_et",
                "n_books_available": 4,
            }
        ]
    )
    out = odds_tuesday_frame(snaps, games, teams)
    assert len(out) == 1
    assert out.iloc[0]["spread"] == pytest.approx(-6.5)
    assert out.iloc[0]["line_source"] == "odds_api_snapshot"


def test_odds_close_drops_null_game_id() -> None:
    from ncaa_quant.research.angles_run import odds_close_frame

    games = pd.DataFrame([_game(1, 2021, 3, 10, 20)])
    teams = _teams(10, 20, season=2021)
    kick = pd.Timestamp(games.iloc[0]["start_date"])
    snaps = pd.DataFrame(
        [
            {
                "game_id": pd.NA,
                "market": "spread",
                "side": "School10",
                "line": -1.0,
                "book": "dk",
                "event_time": kick - pd.Timedelta(minutes=5),
                "decision_point": "slot_close",
                "n_books_available": 1,
            },
            {
                "game_id": 1,
                "market": "spread",
                "side": "School10",
                "line": -4.5,
                "book": "dk",
                "event_time": kick - pd.Timedelta(minutes=5),
                "decision_point": "slot_close",
                "n_books_available": 3,
            },
        ]
    )
    out = odds_close_frame(snaps, games, teams)
    assert len(out) == 1
    assert int(out.iloc[0]["game_id"]) == 1
    assert out.iloc[0]["spread"] == pytest.approx(-4.5)


def test_odds_close_prefers_slot_close() -> None:
    from ncaa_quant.research.angles_run import odds_close_frame

    games = pd.DataFrame([_game(1, 2021, 3, 10, 20)])
    teams = _teams(10, 20, season=2021)
    kick = pd.Timestamp(games.iloc[0]["start_date"])
    snaps = pd.DataFrame(
        [
            {
                "game_id": 1,
                "market": "spread",
                "side": "School10",
                "line": -3.5,
                "book": "dk",
                "event_time": kick - pd.Timedelta(minutes=5),
                "decision_point": "slot_close",
                "n_books_available": 3,
            }
        ]
    )
    out = odds_close_frame(snaps, games, teams)
    assert out.iloc[0]["spread"] == pytest.approx(-3.5)


def test_load_angles_data_reads_hive(tmp_path: Path) -> None:
    from ncaa_quant.research.angles_run import load_angles_data

    games = pd.DataFrame([_game(1, 2019, 3, 10, 20)])
    teams = _teams(10, 20, season=2019)
    gdir = tmp_path / "games" / "season=2019" / "week=3"
    tdir = tmp_path / "teams" / "season=2019"
    gdir.mkdir(parents=True)
    tdir.mkdir(parents=True)
    games.to_parquet(gdir / "part.parquet", index=False)
    teams.to_parquet(tdir / "part.parquet", index=False)
    cfg = load_angles_config()
    data = load_angles_data(cfg, seasons=(2019,), need_luck=False, staged_root=tmp_path)
    assert not data.games.empty
    assert int(data.games.iloc[0]["game_id"]) == 1


def test_config_errors_empty_paths_and_remaining_conditions(tmp_path: Path) -> None:
    cfg = load_angles_config()
    with pytest.raises(AnglesConfigError, match="unknown split"):
        cfg.split_seasons("not-a-split")
    assert cfg.split_lockbox_seasons("holdout") == (2025,)
    assert fbs_ids_by_season(pd.DataFrame()) == {}
    empty_sched = build_schedule_conditions(pd.DataFrame())
    assert empty_sched.empty
    empty_frame = build_team_game_frame(pd.DataFrame(), pd.DataFrame())
    assert empty_frame.n_games_universe == 0
    missing = tmp_path / "nope.yaml"
    with pytest.raises(AnglesConfigError, match="not found"):
        load_angles_config(missing)
    (tmp_path / "list.yaml").write_text("- not a mapping\n", encoding="utf-8")
    with pytest.raises(AnglesConfigError, match="mapping"):
        load_angles_config(tmp_path / "list.yaml")

    def _write(name: str, body: str) -> Path:
        path = tmp_path / name
        path.write_text(body, encoding="utf-8")
        return path

    with pytest.raises(AnglesConfigError, match="at least one"):
        load_angles_config(_write("empty_hyps.yaml", "hypotheses: []\n"))
    with pytest.raises(AnglesConfigError, match="each hypothesis"):
        load_angles_config(_write("hyps_scalar.yaml", "hypotheses: [1]\n"))
    with pytest.raises(AnglesConfigError, match="missing id"):
        load_angles_config(
            _write("no_id.yaml", "hypotheses:\n  - {market: spread, primary: {}, baseline: {}}\n")
        )
    with pytest.raises(AnglesConfigError, match="duplicate"):
        load_angles_config(
            _write(
                "dup.yaml",
                "hypotheses:\n"
                "  - id: H1\n    market: spread\n"
                "    primary: {conditions: {}}\n    baseline: {conditions: {}}\n"
                "  - id: H1\n    market: spread\n"
                "    primary: {conditions: {}}\n    baseline: {conditions: {}}\n",
            )
        )
    with pytest.raises(AnglesConfigError, match="market"):
        load_angles_config(
            _write(
                "bad_mkt.yaml",
                "hypotheses:\n"
                "  - id: Hx\n    market: ml\n"
                "    primary: {conditions: {}}\n    baseline: {conditions: {}}\n",
            )
        )
    with pytest.raises(AnglesConfigError, match="primary and baseline"):
        load_angles_config(_write("no_pri.yaml", "hypotheses:\n  - {id: Hx, market: spread}\n"))
    with pytest.raises(AnglesConfigError, match="unknown condition"):
        load_angles_config(
            _write(
                "bad_key.yaml",
                "hypotheses:\n"
                "  - id: Hx\n    market: spread\n"
                "    primary: {conditions: {bogus: 1}}\n"
                "    baseline: {conditions: {}}\n",
            )
        )
    with pytest.raises(AnglesConfigError, match="explicit number"):
        load_angles_config(
            _write(
                "bad_val.yaml",
                "hypotheses:\n"
                "  - id: Hx\n    market: spread\n"
                "    primary: {conditions: {team_rest_days_min: 'thirteen'}}\n"
                "    baseline: {conditions: {}}\n",
            )
        )
    with pytest.raises(AnglesConfigError, match="luck_definition"):
        load_angles_config(
            _write(
                "bad_luck.yaml",
                "hypotheses:\n"
                "  - id: Hx\n    market: spread\n"
                "    primary: {luck_definition: nope, conditions: {}}\n"
                "    baseline: {conditions: {}}\n",
            )
        )
    with pytest.raises(AnglesConfigError, match="variant"):
        load_angles_config(
            _write(
                "bad_var.yaml",
                "hypotheses:\n"
                "  - id: Hx\n    market: spread\n"
                "    primary: {conditions: {}}\n    baseline: {conditions: {}}\n"
                "    variants: [1]\n",
            )
        )

    rows = pd.DataFrame(
        {
            "team_rest_days": [14.0],
            "opp_rest_days": [7.0],
            "team_opener": [False],
            "opp_opener": [False],
            "team_prior_long_rest_count": [1],
            "opp_prior_long_rest_count": [0],
            "opp_consecutive_weeks": [4],
            "team_consecutive_weeks": [2],
            "rivalry_flag": [False],
            "conference_game": [True],
            "opp_luck_last_q": [0.95],
            "opp_luck_ats_last_q": [0.91],
            "luck_window_n": [800],
            "opp_last_game_completed_before_as_of": [True],
        }
    )
    mask = evaluate_conditions(
        rows,
        {
            "team_rest_days_max": 20.0,
            "opp_exclude_opener": True,
            "team_prior_long_rest_count_min": 1,
            "team_prior_long_rest_count_max": 2,
            "opp_consecutive_weeks_min": 3,
            "team_consecutive_weeks_min": 1,
            "team_consecutive_weeks_max": 5,
            "opp_luck_last_q_min": 0.9,
            "opp_luck_last_q_max": 0.99,
            "luck_quantile_min_n": 500,
            "opp_last_game_completed_before_as_of": True,
        },
        luck_definition="default",
    )
    assert bool(mask.iloc[0]) is True
    ats_mask = evaluate_conditions(
        rows, {"opp_luck_last_q_min": 0.9}, luck_definition="ats_component"
    )
    assert bool(ats_mask.iloc[0]) is True
    luck_cs = ConditionSet(
        id="l",
        description="",
        conditions={},
        statistic="mean_ats_residual",
        luck_definition="ats_component",
    )
    assert definition_needs_luck(luck_cs) is True

    games = pd.DataFrame([_game(1, 2019, 3, 10, 20)])
    luck = compute_game_luck(games, pd.DataFrame(), {}, pd.DataFrame(), hfa_epa_per_play=0.025)
    assert "luck_home" in luck.columns
    frame = build_team_game_frame(games, _teams(10, 20))
    no_luck = attach_last_game_luck(frame.rows, pd.DataFrame(), games)
    assert no_luck["opp_luck_last"].isna().all()
    qempty = attach_trailing_luck_quantiles(frame.rows, pd.DataFrame(), games)
    assert qempty["luck_window_n"].eq(0).all()
    none_feat = attach_efficiency_l3d(frame.rows, {})
    assert list(none_feat.columns) == list(frame.rows.columns)
    skip = attach_efficiency_l3d(
        frame.rows, {"adj_off_epa_l3d": pd.DataFrame({"team_id": [10], "season": [2019]})}
    )
    assert "team_adj_off_epa_l3d" not in skip.columns
    flagged = flag_definition(frame.rows, _primary(conference_game=True))
    assert flagged.n_both_baseline_games == 0
    ridge = pd.DataFrame([{"game_id": 1, "pred_margin": 6.0}])
    odds = pd.DataFrame(
        [{"game_id": 1, "spread": -6.5, "line_source": "odds_api_snapshot", "n_books": 3}]
    )
    out = attach_outcomes(
        frame,
        games,
        close_cfbd=None,
        close_odds=odds,
        tuesday_odds=odds,
        ridge_predictions=ridge,
    )
    home = out.rows.loc[out.rows["is_home"]].iloc[0]
    assert home["ridge_pred_margin"] == pytest.approx(6.0)
    assert int(home["n_books_close_odds"]) == 3
    assert spread_bucket_label(0.0, {"only_neg": {"max": -0.1}}) is None
    delta = as_of_minus(datetime(2019, 9, 1, tzinfo=UTC), 2.0)
    assert delta.hour == 22
    no_end = games.drop(columns=["event_time"])
    sched = build_schedule_conditions(no_end)
    assert "prev_game_end" in sched.columns
    empty_luck = compute_game_luck(
        pd.DataFrame(), pd.DataFrame(), {}, pd.DataFrame(), hfa_epa_per_play=0.025
    )
    assert empty_luck.empty
    hist = pd.DataFrame(
        [
            {
                "team_id": 10,
                "season": 2019,
                "event_time": pd.Timestamp("2019-01-01T00:00:00Z"),
                "off_epa": 0.1,
                "def_epa": 0.0,
                "kind": "postgame",
            }
        ]
    )
    with_kind = compute_game_luck(games, pd.DataFrame(), {1: -7.0}, hist, hfa_epa_per_play=0.025)
    assert "luck_home" in with_kind.columns
    with pytest.raises(AnglesConfigError, match="unknown luck_definition"):
        evaluate_conditions(rows, {}, luck_definition="nope")
    skip_opener = evaluate_conditions(rows, {"team_exclude_opener": False})
    assert bool(skip_opener.iloc[0]) is True
    skip_completed = evaluate_conditions(rows, {"opp_last_game_completed_before_as_of": False})
    assert bool(skip_completed.iloc[0]) is True
    empty_feat = attach_efficiency_l3d(frame.rows, {"adj_off_epa_l3d": pd.DataFrame()})
    assert list(empty_feat.columns) == list(frame.rows.columns)
    no_type = games.drop(columns=["season_type", "completed"])
    typed = build_team_game_frame(no_type, _teams(10, 20))
    assert not typed.rows.empty
    trend_cs = ConditionSet(
        id="t",
        description="",
        conditions={"conference_game": True},
        statistic="trend_slope_ats_residual_on_opp_consecutive_weeks",
        trend={"regressor": "opp_consecutive_weeks"},
        regression={"regressor": "opp_luck_last", "scale_per_points": 10.0},
    )
    from ncaa_quant.research.angles import FlaggedFrame

    syn = pd.DataFrame(
        [
            {
                "game_id": 1,
                "season": 2016,
                "week": 3,
                "season_week": "2016-03",
                "qualifies": True,
                "baseline": False,
                "venue": "home",
                "conference_game": True,
                "opp_consecutive_weeks": 4,
                "opp_luck_last": float("nan"),
                "spread_close_cfbd": -3.0,
                "ats_residual_cfbd": 1.0,
                "cover_cfbd": 1.0,
                "spread_close_odds": float("nan"),
                "ats_residual_odds": float("nan"),
                "cover_odds": float("nan"),
                "ridge_residual": 0.5,
                "spread_tue_odds": float("nan"),
                "line_move_toward_angle": float("nan"),
            }
        ]
    )
    syn_flagged = FlaggedFrame(
        rows=syn,
        n_both_qualify_games=0,
        n_both_baseline_games=0,
        both_qualify_game_ids=(),
    )
    hyp = cfg.hypotheses["H3"]
    analyzed = analyze_definition(
        syn_flagged, hyp, trend_cs, cfg, kind="primary", seasons=(2016,), sensitivity_seasons=()
    )
    assert analyzed.by_instrument["cfbd"]["trend"] is not None
    assert analyzed.by_instrument["cfbd"]["continuous_regression"]["n"] == 0
    bh = benjamini_hochberg({"A": float("nan"), "B": 0.01}, q=0.10)
    assert bh["A"].rejected is False
    assert condition_columns(pd.DataFrame({"game_id": [1], "ats_residual_cfbd": [1.0]})) == [
        "game_id"
    ]


def _side_row(
    gid: int,
    team: int,
    opp: int,
    *,
    venue: str = "home",
    spread: float = 3.0,
    **extra: Any,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "game_id": gid,
        "season": 2019,
        "week": 4,
        "team_id": team,
        "opp_id": opp,
        "is_home": venue == "home",
        "venue": venue,
        "conference_game": False,
        "team_opener": False,
        "opp_opener": False,
        "rivalry_flag": False,
        "spread_close_cfbd": spread,
        "opp_last_game_completed_before_as_of": True,
        "team_last_game_completed_before_as_of": True,
    }
    row.update(extra)
    return row


def test_h7_assigns_angle_side_and_baseline() -> None:
    cfg = load_angles_config()
    rows = pd.DataFrame(
        [
            _side_row(
                1,
                3,
                1,
                venue="away",
                spread=7.0,
                opp_last_upset_win=True,
                team_ridge_lt_opp=True,
            ),
            _side_row(1, 1, 3, venue="home", spread=-7.0, opp_last_upset_win=False),
            _side_row(
                2,
                5,
                4,
                venue="away",
                spread=7.0,
                opp_last_upset_win=False,
                team_ridge_lt_opp=True,
            ),
            _side_row(2, 4, 5, venue="home", spread=-7.0, opp_last_upset_win=False),
        ]
    )
    hyp = cfg.hypotheses["H7"]
    flagged = flag_definition(rows, hyp.primary, hyp.baseline)
    q = flagged.rows.loc[flagged.rows["qualifies"]]
    b = flagged.rows.loc[flagged.rows["baseline"]]
    assert list(q["team_id"]) == [3]
    assert list(q["opp_id"]) == [1]
    assert list(b["team_id"]) == [5]


def test_h9_assigns_wounded_vs_ordinary_loss() -> None:
    cfg = load_angles_config()
    rows = pd.DataFrame(
        [
            _side_row(
                1, 10, 20, team_last_ridge_rank=12, team_last_wounded=True, team_last_lost=True
            ),
            _side_row(
                1, 20, 10, team_last_ridge_rank=80, team_last_wounded=False, team_last_lost=True
            ),
            _side_row(
                2, 30, 40, team_last_ridge_rank=8, team_last_wounded=False, team_last_lost=True
            ),
            _side_row(
                2, 40, 30, team_last_ridge_rank=90, team_last_wounded=False, team_last_lost=False
            ),
        ]
    )
    hyp = cfg.hypotheses["H9"]
    flagged = flag_definition(rows, hyp.primary, hyp.baseline)
    assert list(flagged.rows.loc[flagged.rows["qualifies"], "team_id"]) == [10]
    assert list(flagged.rows.loc[flagged.rows["baseline"], "team_id"]) == [30]


def test_h15_assigns_fade_of_plus_three_turnover_team() -> None:
    cfg = load_angles_config()
    rows = pd.DataFrame(
        [
            _side_row(1, 2, 1, opp_last_to_margin=3),
            _side_row(1, 1, 2, opp_last_to_margin=-3),
            _side_row(2, 4, 3, opp_last_to_margin=0),
            _side_row(2, 3, 4, opp_last_to_margin=1),
        ]
    )
    hyp = cfg.hypotheses["H15"]
    flagged = flag_definition(rows, hyp.primary, hyp.baseline)
    assert list(flagged.rows.loc[flagged.rows["qualifies"], "team_id"]) == [2]
    assert list(flagged.rows.loc[flagged.rows["baseline"], "team_id"]) == [4]
    from ncaa_quant.research.angles_context import TURNOVER_PLAY_TYPES

    assert "Pass Interception Return" in TURNOVER_PLAY_TYPES
    assert "Fumble Recovery (Own)" not in TURNOVER_PLAY_TYPES


def test_h22_assigns_home_after_road_loss() -> None:
    cfg = load_angles_config()
    rows = pd.DataFrame(
        [
            _side_row(
                1,
                10,
                20,
                venue="home",
                team_last_was_away=True,
                team_last_lost=True,
                team_last_won=False,
            ),
            _side_row(1, 20, 10, venue="away", team_last_was_away=False, team_last_lost=False),
            _side_row(
                2,
                30,
                40,
                venue="home",
                team_last_was_away=True,
                team_last_lost=False,
                team_last_won=True,
            ),
            _side_row(2, 40, 30, venue="away", team_last_was_away=False, team_last_won=False),
        ]
    )
    hyp = cfg.hypotheses["H22"]
    flagged = flag_definition(rows, hyp.primary, hyp.baseline)
    assert list(flagged.rows.loc[flagged.rows["qualifies"], "team_id"]) == [10]
    assert list(flagged.rows.loc[flagged.rows["baseline"], "team_id"]) == [30]


def test_h23_assigns_fade_after_home_win_over_ranked() -> None:
    cfg = load_angles_config()
    rows = pd.DataFrame(
        [
            _side_row(
                1,
                3,
                1,
                opp_last_won=True,
                opp_last_was_home=True,
                opp_last_opp_ap_rank=10,
                opp_last_opp_ap_unranked=False,
                opp_last_opp_fbs=True,
            ),
            _side_row(1, 1, 3, opp_last_won=False),
            _side_row(
                2,
                6,
                4,
                opp_last_won=True,
                opp_last_was_home=True,
                opp_last_opp_ap_rank=float("nan"),
                opp_last_opp_ap_unranked=True,
                opp_last_opp_fbs=True,
            ),
            _side_row(2, 4, 6, opp_last_won=False),
        ]
    )
    hyp = cfg.hypotheses["H23"]
    flagged = flag_definition(rows, hyp.primary, hyp.baseline)
    assert list(flagged.rows.loc[flagged.rows["qualifies"], "team_id"]) == [3]
    assert list(flagged.rows.loc[flagged.rows["baseline"], "team_id"]) == [6]


def test_h24_assigns_road_dog_vs_home_dog_baseline() -> None:
    cfg = load_angles_config()
    rows = pd.DataFrame(
        [
            _side_row(1, 10, 20, venue="away", spread=7.0),
            _side_row(1, 20, 10, venue="home", spread=-7.0),
            _side_row(2, 30, 40, venue="home", spread=7.0),
            _side_row(2, 40, 30, venue="away", spread=-7.0),
        ]
    )
    hyp = cfg.hypotheses["H24"]
    flagged = flag_definition(rows, hyp.primary, hyp.baseline)
    assert list(flagged.rows.loc[flagged.rows["qualifies"], "team_id"]) == [10]
    assert list(flagged.rows.loc[flagged.rows["baseline"], "team_id"]) == [30]


def test_rankings_ingest_uses_injected_payload(tmp_path: Path) -> None:
    from ncaa_quant.research.angles_rankings import ingest_cfbd_rankings, load_research_rankings

    teams = _teams(10, 20, season=2019)
    payload = [
        {
            "season": 2019,
            "week": 2,
            "seasonType": "regular",
            "pollDate": "2019-09-08T22:00:00Z",
            "polls": [
                {
                    "poll": "AP Top 25",
                    "ranks": [{"rank": 4, "school": "School10", "teamId": 10}],
                },
                {"poll": "Coaches Poll", "ranks": [{"rank": 1, "school": "School20"}]},
            ],
        }
    ]

    def _get(year: int, season_type: str) -> list[dict[str, Any]]:
        return payload if season_type == "regular" else []

    tue = {(2019, 2): datetime(2019, 9, 10, 10, 0, tzinfo=UTC)}
    out = ingest_cfbd_rankings(
        [2019],
        teams,
        tuesdays=tue,
        root=tmp_path,
        get_payload=_get,
    )
    assert len(out) == 1
    assert int(out.iloc[0]["team_id"]) == 10
    assert int(out.iloc[0]["rank"]) == 4
    loaded = load_research_rankings([2019], root=tmp_path)
    assert len(loaded) == 1


def test_ap_poll_never_used_at_or_before_release() -> None:
    from ncaa_quant.research.angles_rankings import attach_ap_ranks_at_kickoff

    release = datetime(2019, 9, 1, 22, 0, tzinfo=UTC)
    rankings = pd.DataFrame(
        [
            {
                "season": 2019,
                "week": 1,
                "season_type": "regular",
                "poll": "AP Top 25",
                "team_id": 10,
                "school": "School10",
                "rank": 5,
                "event_time": release,
            }
        ]
    )
    at_release = pd.DataFrame(
        [
            {
                "game_id": 1,
                "season": 2019,
                "week": 1,
                "start_date": release,
                "home_team_id": 10,
                "away_team_id": 20,
            }
        ]
    )
    after = at_release.copy()
    after["game_id"] = 2
    after["start_date"] = release + timedelta(seconds=1)
    ranks_at = attach_ap_ranks_at_kickoff(at_release, rankings)
    ranks_after = attach_ap_ranks_at_kickoff(after, rankings)
    assert pd.isna(ranks_at.iloc[0]["home_ap_rank"])
    assert ranks_after.iloc[0]["home_ap_rank"] == pytest.approx(5.0)
    assert pd.isna(ranks_after.iloc[0]["away_ap_rank"])


def test_turnover_margins_count_listed_play_types() -> None:
    from ncaa_quant.research.angles_context import turnover_margins_from_plays

    plays = pd.DataFrame(
        [
            {
                "game_id": 1,
                "offense_id": 10,
                "defense_id": 20,
                "play_type": "Pass Interception Return",
            },
            {
                "game_id": 1,
                "offense_id": 10,
                "defense_id": 20,
                "play_type": "Fumble Recovery (Opponent)",
            },
            {
                "game_id": 1,
                "offense_id": 10,
                "defense_id": 20,
                "play_type": "Fumble Recovery (Own)",
            },
            {
                "game_id": 1,
                "offense_id": 20,
                "defense_id": 10,
                "play_type": "Interception Return Touchdown",
            },
        ]
    )
    margins = turnover_margins_from_plays(plays).set_index("team_id")
    assert int(margins.loc[20, "to_margin"]) == 1
    assert int(margins.loc[10, "to_margin"]) == -1
    assert int(margins.loc[20, "int_margin"]) == 0
    assert int(margins.loc[20, "fumble_margin"]) == 1


def test_attach_condition_context_last_game_ridge_to_and_ap() -> None:
    from ncaa_quant.research.angles_context import (
        attach_condition_context,
        attach_current_ridge,
        attach_last_game_context,
        build_side_game_stats,
        net_epa_as_of,
        rank_fbs_ratings,
        turnover_margins_from_plays,
    )

    season = 2019
    w1 = _game(1, season, 1, 10, 20, home_pts=37, away_pts=7)
    w2 = _game(2, season, 2, 10, 20, home_pts=24, away_pts=17)
    games = pd.DataFrame([w1, w2])
    teams = _teams(10, 20, season=season)
    frame = build_team_game_frame(games, teams)
    kick1 = pd.Timestamp(w1["start_date"])
    hist_t = kick1 - timedelta(days=10)
    hist = pd.DataFrame(
        [
            {
                "team_id": 10,
                "season": season,
                "event_time": hist_t,
                "off_epa": 0.20,
                "def_epa": 0.00,
            },
            {
                "team_id": 20,
                "season": season,
                "event_time": hist_t,
                "off_epa": 0.02,
                "def_epa": 0.10,
            },
            {
                "team_id": 10,
                "season": 2018,
                "event_time": hist_t - timedelta(days=365),
                "off_epa": 9.0,
                "def_epa": 0.0,
            },
        ]
    )
    plays = pd.DataFrame(
        [
            {
                "game_id": 1,
                "offense_id": 20,
                "defense_id": 10,
                "play_type": "Pass Interception Return",
            },
            {
                "game_id": 1,
                "offense_id": 20,
                "defense_id": 10,
                "play_type": "Pass Interception Return",
            },
            {
                "game_id": 1,
                "offense_id": 20,
                "defense_id": 10,
                "play_type": "Pass Interception Return",
            },
            {
                "game_id": 1,
                "offense_id": 20,
                "defense_id": 10,
                "play_type": "Fumble Recovery (Opponent)",
            },
            {
                "game_id": 1,
                "offense_id": 10,
                "defense_id": 20,
                "play_type": "Fumble Recovery (Own)",
            },
        ]
    )
    rankings = pd.DataFrame(
        [
            {
                "season": season,
                "week": 1,
                "season_type": "regular",
                "poll": "AP Top 25",
                "team_id": 20,
                "school": "School20",
                "rank": 8,
                "event_time": kick1 - timedelta(days=2),
            }
        ]
    )
    out = attach_condition_context(
        frame.rows,
        games,
        teams,
        close_spread_home={1: -7.0, 2: -3.0},
        filter_history=hist,
        plays=plays,
        rankings=rankings,
    )
    t10 = out.loc[(out["game_id"] == 2) & (out["team_id"] == 10)].iloc[0]
    t20 = out.loc[(out["game_id"] == 2) & (out["team_id"] == 20)].iloc[0]
    assert float(t10["team_last_margin"]) == pytest.approx(30.0)
    assert float(t10["team_last_spread"]) == pytest.approx(-7.0)
    assert bool(t10["team_last_won"]) is True
    assert bool(t10["team_last_upset_win"]) is False
    assert int(t10["team_last_to_margin"]) == 4
    assert int(t10["team_last_int_margin"]) == 3
    assert int(t10["team_last_fumble_margin"]) == 1
    assert int(t10["team_last_opp_ap_rank"]) == 8
    assert bool(t10["team_last_was_home"]) is True
    assert pd.notna(t10["team_ridge_lt_opp"]) and not bool(t10["team_ridge_lt_opp"])
    assert bool(t20["team_last_lost"]) is True
    assert bool(t20["team_last_wounded"]) is True
    assert bool(t20["team_last_was_away"]) is True
    assert int(t20["team_last_ridge_rank"]) <= 40

    empty_keys = pd.DataFrame(columns=["team_id", "season", "as_of"])
    assert net_epa_as_of(empty_keys, hist).empty
    rated_empty_hist = net_epa_as_of(
        pd.DataFrame(
            {
                "team_id": [10],
                "season": [season],
                "as_of": [kick1],
            }
        ),
        pd.DataFrame(),
    )
    assert pd.isna(rated_empty_hist.iloc[0]["ridge"])
    ranked_empty = rank_fbs_ratings(pd.DataFrame(), {})
    assert ranked_empty.empty
    empty_tos = turnover_margins_from_plays(pd.DataFrame())
    assert list(empty_tos.columns) == [
        "game_id",
        "team_id",
        "to_margin",
        "int_margin",
        "fumble_margin",
    ]
    empty_stats = build_side_game_stats(pd.DataFrame(), {}, teams)
    assert empty_stats.empty
    empty_last = attach_last_game_context(frame.rows.copy(), pd.DataFrame())
    assert len(empty_last) == len(frame.rows)
    empty_ridge = attach_current_ridge(pd.DataFrame(), hist, teams)
    assert "team_ridge" in empty_ridge.columns
    empty_rank_out = attach_condition_context(
        frame.rows, games, teams, close_spread_home={1: -7.0, 2: -3.0}
    )
    assert "team_last_won" in empty_rank_out.columns
    assert "ats_momentum" in empty_rank_out.columns
    t10_mom = empty_rank_out.loc[
        (empty_rank_out["game_id"] == 2) & (empty_rank_out["team_id"] == 10)
    ].iloc[0]
    assert float(t10_mom["team_last_ats_residual"]) == pytest.approx(30.0 + (-7.0))


def test_h22r_assigns_fade_of_road_loss_home_team() -> None:
    cfg = load_angles_config()
    rows = pd.DataFrame(
        [
            _side_row(1, 20, 10, venue="away", opp_last_was_away=True, opp_last_lost=True),
            _side_row(1, 10, 20, venue="home", opp_last_was_away=False, opp_last_lost=False),
            _side_row(2, 40, 30, venue="away", opp_last_was_away=True, opp_last_won=True),
            _side_row(2, 30, 40, venue="home", opp_last_was_away=False, opp_last_won=False),
        ]
    )
    hyp = cfg.hypotheses["H22r"]
    flagged = flag_definition(rows, hyp.primary, hyp.baseline)
    assert list(flagged.rows.loc[flagged.rows["qualifies"], "team_id"]) == [20]
    assert list(flagged.rows.loc[flagged.rows["baseline"], "team_id"]) == [40]


def test_h25_assigns_home_universe_and_binary_backs_higher_last_ats() -> None:
    cfg = load_angles_config()
    hyp = cfg.hypotheses["H25"]
    rows = pd.DataFrame(
        [
            _side_row(
                1,
                10,
                20,
                venue="home",
                team_last_game_completed_before_as_of=True,
                opp_last_game_completed_before_as_of=True,
                ats_momentum=12.0,
                ats_momentum_abs_q=0.95,
                momentum_window_n=600,
                team_last_ats_gt_opp=True,
            ),
            _side_row(
                1,
                20,
                10,
                venue="away",
                team_last_game_completed_before_as_of=True,
                opp_last_game_completed_before_as_of=True,
                ats_momentum=-12.0,
                ats_momentum_abs_q=0.95,
                momentum_window_n=600,
                team_last_ats_gt_opp=False,
            ),
            _side_row(
                2,
                30,
                40,
                venue="home",
                team_last_game_completed_before_as_of=True,
                opp_last_game_completed_before_as_of=True,
                ats_momentum=1.0,
                ats_momentum_abs_q=0.4,
                momentum_window_n=600,
                team_last_ats_gt_opp=True,
            ),
            _side_row(
                2,
                40,
                30,
                venue="away",
                team_last_game_completed_before_as_of=True,
                opp_last_game_completed_before_as_of=True,
                ats_momentum=-1.0,
                ats_momentum_abs_q=0.4,
                momentum_window_n=600,
                team_last_ats_gt_opp=False,
            ),
        ]
    )
    flagged = flag_definition(rows, hyp.primary, hyp.baseline)
    assert set(flagged.rows.loc[flagged.rows["qualifies"], "team_id"]) == {10, 30}
    binary = hyp.variants[0]
    bflag = flag_definition(rows, binary, hyp.baseline)
    assert list(bflag.rows.loc[bflag.rows["qualifies"], "team_id"]) == [10]


def test_h25_descriptive_run_stays_out_of_bh_family(tmp_path: Path) -> None:
    cfg = load_angles_config()
    games = pd.concat(
        [
            pd.DataFrame([_game(1, 2019, 1, 10, 20, home_pts=31, away_pts=17)]),
            pd.DataFrame([_game(2, 2019, 2, 10, 20, home_pts=24, away_pts=21)]),
        ],
        ignore_index=True,
    )
    teams = _teams(10, 20, season=2019)
    lines = pd.DataFrame(
        [
            {"game_id": 1, "spread": -7.0, "line_type": "close", "book": "b", "season": 2019},
            {"game_id": 2, "spread": -3.0, "line_type": "close", "book": "b", "season": 2019},
        ]
    )
    family_path = tmp_path / "family.json"
    family_path.write_text(
        '{"H1": {"p": 0.25, "n": 10, "estimate": 0.8, "config_hash": "x", '
        '"name": "H1", "source": "batch1"}}\n',
        encoding="utf-8",
    )
    result = run_angles(
        data=AnglesData(games=games, teams=teams, cfbd_lines=lines),
        ids=("H25",),
        split="discovery",
        config=cfg,
        notes_dir=tmp_path,
        ledger_path=tmp_path / "ledger.jsonl",
        family_path=family_path,
        echo=lambda _s: None,
        write_reports=True,
    )
    family = __import__("json").loads(family_path.read_text(encoding="utf-8"))
    assert "H25" not in family
    assert "H1" in family
    assert result.bh.get("H25") is None
    report = (tmp_path / "H25.md").read_text(encoding="utf-8")
    assert "motivated by discovery; not evidence" in report
    assert "Holdout was **not** opened" in (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert not (tmp_path / "ledger.jsonl").exists()


def test_h22_sanity_all_home_mean_and_vs_home_regression() -> None:
    from ncaa_quant.research.angles_h22_sanity import compute_h22_sanity, format_h22_sanity_md

    cfg = load_angles_config()
    rows = pd.DataFrame(
        [
            {
                "game_id": 1,
                "season": 2019,
                "week": 4,
                "season_week": "2019-04",
                "team_id": 10,
                "opp_id": 20,
                "venue": "home",
                "conference_game": False,
                "team_last_was_away": True,
                "team_last_lost": True,
                "team_last_won": False,
                "team_last_game_completed_before_as_of": True,
                "spread_close_cfbd": -3.0,
                "ats_residual_cfbd": -4.0,
                "cover_cfbd": 0.0,
                "spread_close_odds": -3.0,
                "ats_residual_odds": -4.0,
                "cover_odds": 0.0,
                "ridge_residual": -2.0,
            },
            {
                "game_id": 1,
                "season": 2019,
                "week": 4,
                "season_week": "2019-04",
                "team_id": 20,
                "opp_id": 10,
                "venue": "away",
                "conference_game": False,
                "team_last_was_away": False,
                "team_last_lost": False,
                "team_last_won": True,
                "team_last_game_completed_before_as_of": True,
                "spread_close_cfbd": 3.0,
                "ats_residual_cfbd": 4.0,
                "cover_cfbd": 1.0,
                "spread_close_odds": 3.0,
                "ats_residual_odds": 4.0,
                "cover_odds": 1.0,
                "ridge_residual": 2.0,
            },
            {
                "game_id": 2,
                "season": 2019,
                "week": 5,
                "season_week": "2019-05",
                "team_id": 30,
                "opp_id": 40,
                "venue": "home",
                "conference_game": False,
                "team_last_was_away": True,
                "team_last_lost": False,
                "team_last_won": True,
                "team_last_game_completed_before_as_of": True,
                "spread_close_cfbd": -3.0,
                "ats_residual_cfbd": 2.0,
                "cover_cfbd": 1.0,
                "spread_close_odds": -3.0,
                "ats_residual_odds": 2.0,
                "cover_odds": 1.0,
                "ridge_residual": 1.0,
            },
            {
                "game_id": 2,
                "season": 2019,
                "week": 5,
                "season_week": "2019-05",
                "team_id": 40,
                "opp_id": 30,
                "venue": "away",
                "conference_game": False,
                "team_last_was_away": False,
                "team_last_lost": True,
                "team_last_won": False,
                "team_last_game_completed_before_as_of": True,
                "spread_close_cfbd": 3.0,
                "ats_residual_cfbd": -2.0,
                "cover_cfbd": 0.0,
                "spread_close_odds": 3.0,
                "ats_residual_odds": -2.0,
                "cover_odds": 0.0,
                "ridge_residual": -1.0,
            },
        ]
    )
    stats = compute_h22_sanity(rows, cfg, seasons=(2019,), sensitivity_seasons=())
    assert stats["cfbd"]["h22_mean"]["n"] == 1
    assert stats["cfbd"]["h22_mean"]["mean"] == pytest.approx(-4.0)
    assert stats["cfbd"]["all_home_mean"]["mean"] == pytest.approx(-1.0)
    assert stats["cfbd"]["baseline_mean"]["mean"] == pytest.approx(2.0)
    md = format_h22_sanity_md(stats)
    assert "All non-neutral home teams" in md
    assert "Does the road-loss effect survive" in md


def test_run_angles_refuses_retired_unopened(tmp_path: Path) -> None:
    games = _h1_schedule(2019)
    teams = _teams_for(games)
    cfg = load_angles_config()
    data = AnglesData(games=games, teams=teams, cfbd_lines=pd.DataFrame())
    with pytest.raises(RetiredUnopenedError, match="H22r"):
        run_angles(
            data=data,
            ids=("H22r",),
            split="holdout",
            config=cfg,
            notes_dir=tmp_path,
            ledger_path=tmp_path / "ledger.jsonl",
            echo=lambda _s: None,
            write_reports=False,
        )
    with pytest.raises(RetiredUnopenedError, match="H25"):
        run_angles(
            data=data,
            ids=("H25",),
            split="holdout",
            config=cfg,
            notes_dir=tmp_path,
            ledger_path=tmp_path / "ledger.jsonl",
            echo=lambda _s: None,
            write_reports=False,
        )
    assert not (tmp_path / "ledger.jsonl").exists()
    assert not Path("docs/notes/angles/holdout_ledger.jsonl").exists()


def _diag_row(**kwargs: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "game_id": 1,
        "season": 2019,
        "week": 5,
        "season_week": "2019-05",
        "team_id": 10,
        "is_home": True,
        "venue": "home",
        "team_opener": False,
        "team_rest_days": 7.0,
        "team_last_game_completed_before_as_of": True,
        "team_last_was_away": False,
        "team_last_was_home": True,
        "team_last_won": True,
        "team_last_lost": False,
        "margin": 3.0,
        "spread_close_cfbd": -7.0,
        "ats_residual_cfbd": 3.0 - 7.0,
        "ridge_pred_margin": 7.0,
        "ridge_residual": 3.0 - 7.0,
    }
    row.update(kwargs)
    return row


def test_prior_situation_labels_and_bye_overwrite() -> None:
    from ncaa_quant.research.angles_ridge_diag import prior_situation

    rows = pd.DataFrame(
        [
            _diag_row(
                game_id=1,
                team_last_was_away=True,
                team_last_was_home=False,
                team_last_won=False,
                team_last_lost=True,
                team_rest_days=7.0,
            ),
            _diag_row(
                game_id=2,
                team_last_was_away=True,
                team_last_was_home=False,
                team_last_won=False,
                team_last_lost=True,
                team_rest_days=14.0,
            ),
            _diag_row(
                game_id=3,
                team_opener=True,
                team_rest_days=float("nan"),
                team_last_game_completed_before_as_of=False,
                team_last_was_away=False,
                team_last_was_home=False,
                team_last_won=False,
                team_last_lost=False,
            ),
            _diag_row(
                game_id=4,
                team_last_was_away=False,
                team_last_was_home=True,
                team_last_won=True,
                team_last_lost=False,
                team_rest_days=7.0,
            ),
        ]
    )
    labels = list(prior_situation(rows))
    assert labels[0] == "road_loss"
    assert labels[1] == "bye"
    assert pd.isna(labels[2])
    assert labels[3] == "home_win"


def test_ridge_home_diag_refuses_holdout_seasons() -> None:
    from ncaa_quant.research.angles_ridge_diag import (
        HoldoutSeasonError,
        compute_ridge_home_diag,
    )

    rows = pd.DataFrame([_diag_row(season=2023, season_week="2023-05")])
    with pytest.raises(HoldoutSeasonError, match="holdout"):
        compute_ridge_home_diag(rows, seasons=(2019,), sensitivity_seasons=())


def test_ridge_home_diag_cells_on_fixture() -> None:
    from ncaa_quant.research.angles_ridge_diag import (
        compute_ridge_home_diag,
        format_ridge_home_diag_md,
    )

    rows = pd.DataFrame(
        [
            _diag_row(
                game_id=1,
                season=2019,
                week=5,
                season_week="2019-05",
                is_home=True,
                venue="home",
                team_last_was_away=True,
                team_last_was_home=False,
                team_last_won=False,
                team_last_lost=True,
                ridge_residual=-4.0,
                ats_residual_cfbd=-1.0,
                margin=-4.0,
                ridge_pred_margin=0.0,
            ),
            _diag_row(
                game_id=1,
                season=2019,
                week=5,
                season_week="2019-05",
                team_id=20,
                is_home=False,
                venue="away",
                team_last_was_away=False,
                team_last_was_home=True,
                team_last_won=True,
                team_last_lost=False,
                ridge_residual=4.0,
                ats_residual_cfbd=1.0,
                margin=4.0,
                spread_close_cfbd=7.0,
                ridge_pred_margin=0.0,
            ),
            _diag_row(
                game_id=2,
                season=2021,
                week=6,
                season_week="2021-06",
                team_id=30,
                is_home=True,
                venue="home",
                team_last_was_away=False,
                team_last_was_home=True,
                team_last_won=True,
                team_last_lost=False,
                ridge_residual=-4.0,
                ats_residual_cfbd=0.0,
                margin=3.0,
                ridge_pred_margin=7.0,
            ),
            _diag_row(
                game_id=2,
                season=2021,
                week=6,
                season_week="2021-06",
                team_id=40,
                is_home=False,
                venue="away",
                team_last_was_away=True,
                team_last_was_home=False,
                team_last_won=False,
                team_last_lost=True,
                ridge_residual=4.0,
                ats_residual_cfbd=0.0,
                margin=-3.0,
                spread_close_cfbd=7.0,
                ridge_pred_margin=-7.0,
            ),
        ]
    )
    stats = compute_ridge_home_diag(rows, seasons=(2019, 2021), sensitivity_seasons=())
    home = stats["headline"]["a"]["home"]
    assert home["ridge"]["n"] == 2
    assert home["ridge"]["mean"] == pytest.approx(-4.0)
    assert stats["headline"]["d"]["road_loss"]["ridge"]["n"] == 1
    assert stats["headline"]["d"]["home_win"]["ridge"]["n"] == 1
    assert stats["headline"]["d"]["road_loss"]["ridge"]["mean"] == pytest.approx(-4.0)
    assert stats["verdict"]["hfa_too_large"] is True
    md = format_ridge_home_diag_md(stats)
    assert "STEP 2" in md
    assert "Holdout 2023–2025 was not opened" in md
    assert "| 2023 |" not in md


def test_holdout_ledger_still_absent() -> None:
    from ncaa_quant.research.angles_ridge_diag import assert_ledger_empty

    assert not Path("docs/notes/angles/holdout_ledger.jsonl").exists()
    assert_ledger_empty()


def test_ridge_home_diag_sensitivity_and_guards(tmp_path: Path) -> None:
    from ncaa_quant.research.angles_ridge_diag import (
        HoldoutSeasonError,
        assert_discovery_only,
        assert_ledger_empty,
        compute_ridge_home_diag,
        format_ridge_home_diag_md,
        prior_situation,
    )

    assert_discovery_only(pd.DataFrame())
    ledger = tmp_path / "holdout_ledger.jsonl"
    ledger.write_text("{}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="holdout ledger exists"):
        assert_ledger_empty(ledger)

    rows = pd.DataFrame(
        [
            _diag_row(season=2019, week=5, season_week="2019-05", ridge_residual=-2.0),
            _diag_row(
                game_id=2,
                season=2020,
                week=6,
                season_week="2020-06",
                team_id=30,
                ridge_residual=-3.0,
                ats_residual_cfbd=-1.0,
            ),
        ]
    )
    with pytest.raises(HoldoutSeasonError):
        compute_ridge_home_diag(rows, seasons=(2023,), sensitivity_seasons=())
    stats = compute_ridge_home_diag(rows, seasons=(2019,), sensitivity_seasons=(2020,))
    assert stats["sensitivity"] is not None
    assert stats["sensitivity"]["a"]["home"]["ridge"]["n"] == 1
    md = format_ridge_home_diag_md(stats)
    assert "2020 sensitivity" in md
    assert "| 2023 |" not in md

    no_opener = pd.DataFrame([_diag_row()]).drop(columns=["team_opener"])
    labels = prior_situation(no_opener)
    assert pd.isna(labels.iloc[0]) or labels.iloc[0] == "home_win"


def test_batch5_condition_keys_and_totals_residual() -> None:
    for key in (
        "opp_lookahead",
        "opp_last_opp_service_academy",
        "team_revenge_loss_14",
        "weeks_from_season_end_max",
        "wind_speed_ms_max",
        "either_last_total_over_21",
        "rest_same_bucket_6_8",
        "team_ap_unranked",
        "opp_ap_ranked",
    ):
        assert key in CONDITION_KEYS
    cfg = load_angles_config()
    assert cfg.hypotheses["H20"].market == "total"
    assert cfg.hypotheses["H20"].one_row_per_game is True
    assert cfg.hypotheses["H16"].primary.statistic.startswith("slope_")
    rows = pd.DataFrame(
        {
            "team_rest_days": [7.0, 7.0, 14.0],
            "opp_rest_days": [7.0, 5.0, 7.0],
            "wind_speed_ms": [8.0, 3.0, 10.0],
            "outdoor": [True, True, True],
            "either_last_total_over_21": [True, False, True],
        }
    )
    m = evaluate_conditions(rows, {"rest_same_bucket_6_8": True})
    assert m.tolist() == [True, False, False]
    m2 = evaluate_conditions(rows, {"outdoor": True, "wind_speed_ms_min": 6.7})
    assert m2.tolist() == [True, False, True]
    m3 = evaluate_conditions(rows, {"outdoor": True, "wind_speed_ms_max": 6.699})
    assert m3.tolist() == [False, True, False]
    out = pd.DataFrame(
        {
            "team_points": [31, 24],
            "opp_points": [24, 31],
            "total_close_cfbd": [48.0, 48.0],
        }
    )
    out["total_residual_cfbd"] = out["total_close_cfbd"] - (out["team_points"] + out["opp_points"])
    assert float(out["total_residual_cfbd"].iloc[0]) == pytest.approx(-7.0)


def test_batch5_extra_attaches() -> None:
    from ncaa_quant.research.angles_extra import (
        attach_efficiency_quantiles,
        attach_last_opp_ids_from_context,
        attach_last_total_over,
        attach_lookahead,
        attach_returning_production,
        attach_revenge,
        attach_season_records,
        attach_service_academy_flags,
        attach_totals_close,
        attach_weather_wind,
        service_academy_ids,
    )

    teams = pd.DataFrame(
        [
            {"team_id": 349, "school": "Army", "season": 2019, "classification": "fbs"},
            {"team_id": 10, "school": "A", "season": 2019, "classification": "fbs"},
            {"team_id": 20, "school": "B", "season": 2019, "classification": "fbs"},
        ]
    )
    assert 349 in service_academy_ids(teams)

    lines = pd.DataFrame(
        [
            {"game_id": 1, "line_type": "close", "total": 50.0},
            {"game_id": 1, "line_type": "close", "total": 52.0},
            {"game_id": 2, "line_type": "close", "total": 60.0},
        ]
    )
    rows = pd.DataFrame(
        {
            "game_id": [1, 1, 2, 2],
            "team_id": [10, 20, 10, 20],
            "opp_id": [20, 10, 20, 10],
            "season": [2019, 2019, 2019, 2019],
            "week": [2, 2, 3, 3],
            "is_home": [True, False, True, False],
            "rivalry_flag": [False, False, True, True],
            "kickoff": [
                datetime(2019, 9, 7, 19, tzinfo=UTC),
                datetime(2019, 9, 7, 19, tzinfo=UTC),
                datetime(2019, 9, 14, 19, tzinfo=UTC),
                datetime(2019, 9, 14, 19, tzinfo=UTC),
            ],
            "as_of": [
                datetime(2019, 9, 3, 10, tzinfo=UTC),
                datetime(2019, 9, 3, 10, tzinfo=UTC),
                datetime(2019, 9, 10, 10, tzinfo=UTC),
                datetime(2019, 9, 10, 10, tzinfo=UTC),
            ],
            "team_prev_game_id": [pd.NA, pd.NA, 1, 1],
            "opp_prev_game_id": [pd.NA, pd.NA, 1, 1],
        }
    )
    tot = attach_totals_close(rows, lines)
    assert float(tot.loc[tot["game_id"] == 1, "total_close_cfbd"].iloc[0]) == pytest.approx(51.0)

    games = pd.DataFrame(
        [
            {
                "game_id": 1,
                "season": 2018,
                "week": 5,
                "home_team_id": 10,
                "away_team_id": 20,
                "home_points": 7,
                "away_points": 35,
                "completed": True,
                "start_date": datetime(2018, 10, 1, 19, tzinfo=UTC),
                "event_time": datetime(2018, 10, 1, 19, tzinfo=UTC),
                "neutral_site": False,
            },
            {
                "game_id": 2,
                "season": 2019,
                "week": 2,
                "home_team_id": 10,
                "away_team_id": 20,
                "home_points": 24,
                "away_points": 21,
                "completed": True,
                "start_date": datetime(2019, 9, 7, 19, tzinfo=UTC),
                "event_time": datetime(2019, 9, 7, 19, tzinfo=UTC),
                "neutral_site": False,
            },
            {
                "game_id": 3,
                "season": 2019,
                "week": 3,
                "home_team_id": 10,
                "away_team_id": 20,
                "home_points": 28,
                "away_points": 14,
                "completed": True,
                "start_date": datetime(2019, 9, 14, 19, tzinfo=UTC),
                "event_time": datetime(2019, 9, 14, 19, tzinfo=UTC),
                "neutral_site": False,
            },
        ]
    )
    rematch = rows.loc[rows["game_id"] == 2].copy()
    rematch["season"] = 2019
    rev = attach_revenge(rematch, games)
    assert bool(rev["team_revenge_loss_14"].iloc[0]) is True  # 10 lost by 28 in 2018

    rec_rows = pd.DataFrame(
        {
            "game_id": [3, 3],
            "team_id": [10, 20],
            "opp_id": [20, 10],
            "season": [2019, 2019],
            "week": [3, 3],
            "as_of": [datetime(2019, 9, 10, 10, tzinfo=UTC)] * 2,
        }
    )
    rec = attach_season_records(rec_rows, games)
    assert float(rec["team_season_wins"].iloc[0]) >= 1.0  # game 2 win for team 10

    rp = attach_returning_production(
        rows,
        pd.DataFrame(
            [
                {"team_id": 10, "season": 2019, "overall_pct": 0.8},
                {"team_id": 20, "season": 2019, "overall_pct": 0.2},
            ]
        ),
        teams,
    )
    assert rp["team_returning_pctile"].notna().any()

    weather = pd.DataFrame(
        [
            {
                "game_id": 1,
                "obs_kind": "actual",
                "wind_speed_ms": 8.0,
                "captured_at": datetime(2019, 9, 7, tzinfo=UTC),
            }
        ]
    )
    w = attach_weather_wind(rows.assign(game_dome=False), weather)
    assert float(w.loc[w["game_id"] == 1, "wind_speed_ms"].iloc[0]) == pytest.approx(8.0)
    assert bool(w.loc[w["game_id"] == 1, "outdoor"].iloc[0]) is True

    stats = rows[["game_id", "team_id", "opp_id"]].drop_duplicates()
    with_prev = rows.copy()
    with_prev["team_prev_game_id"] = with_prev["game_id"].where(with_prev["week"] > 2, pd.NA)
    with_prev["opp_prev_game_id"] = with_prev["team_prev_game_id"]
    with_ids = attach_last_opp_ids_from_context(with_prev, stats)
    sa = attach_service_academy_flags(with_ids, teams)
    assert "opp_is_service_academy" in sa.columns

    la = attach_lookahead(rows, games, teams, filter_history=None)
    assert "team_lookahead" in la.columns
    assert "opp_lookahead" in la.columns

    over_lines = pd.DataFrame(
        [{"game_id": 1, "line_type": "close", "total": 40.0}]
    )
    games_pts = games.copy()
    last = attach_last_total_over(
        rows.assign(team_prev_game_id=1, opp_prev_game_id=1),
        games_pts,
        over_lines,
    )
    assert "either_last_total_over_21" in last.columns

    eq = attach_efficiency_quantiles(
        rows.assign(
            opp_adj_off_epa_l3d=0.1,
            opp_adj_def_epa_l3d=0.0,
            is_home=True,
        )
    )
    assert "opp_net_epa_l3d" in eq.columns
