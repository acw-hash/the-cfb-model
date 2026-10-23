"""Orchestration for the situational-angle research harness.

Loads staged data and walk-forward artifacts read-only, builds the team-game
frame, prints qualifying counts **before** attaching this-game outcomes, then
runs analysis and writes reports. Holdout opens go through the ledger.

This module is research tooling. It is not imported by production features,
models, or the publish path.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, TextIO

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from ncaa_quant.evaluation.lockbox import LOCKBOX_SEASON, assert_lockbox_excluded
from ncaa_quant.evaluation.walkforward import SNAPSHOT_BACKED_FROM_SEASON, WalkForwardConfig
from ncaa_quant.features.market_lines import filter_home_side_spreads
from ncaa_quant.research.angles import (
    DEFAULT_ANGLES_CONFIG,
    STATUS_RETIRED_UNOPENED,
    AnglesConfig,
    RetiredUnopenedError,
    TeamGameFrame,
    attach_efficiency_l3d,
    attach_last_game_luck,
    attach_market_lines,
    attach_outcomes,
    attach_trailing_luck_quantiles,
    build_team_game_frame,
    compute_game_luck,
    flag_definition,
    hypothesis_needs_ap,
    hypothesis_needs_last_game,
    hypothesis_needs_lines,
    hypothesis_needs_luck,
    hypothesis_needs_ridge,
    hypothesis_needs_turnovers,
    load_angles_config,
    qualifying_counts_by_season,
    tuesday_as_of_by_week,
)
from ncaa_quant.research.angles_analysis import DefinitionResult, analyze_definition
from ncaa_quant.research.angles_ledger import (
    DEFAULT_LEDGER_PATH,
    LedgerEntry,
    open_holdout,
)
from ncaa_quant.research.angles_report import (
    patch_hypothesis_bh,
    write_hypothesis_report,
    write_summary_report,
)
from ncaa_quant.research.angles_stats import BhDecision, benjamini_hochberg

EchoFn = Callable[[str], None]


@dataclass
class AnglesData:
    """Injected (or disk-loaded) inputs. Outcomes of the *current* game stay off
    the team-game frame until :func:`attach_outcomes` runs."""

    games: pd.DataFrame
    teams: pd.DataFrame
    venues: pd.DataFrame = field(default_factory=pd.DataFrame)
    cfbd_lines: pd.DataFrame = field(default_factory=pd.DataFrame)
    snapshots: pd.DataFrame = field(default_factory=pd.DataFrame)
    ridge_predictions: pd.DataFrame = field(default_factory=pd.DataFrame)
    filter_history: pd.DataFrame = field(default_factory=pd.DataFrame)
    team_game_epa: pd.DataFrame = field(default_factory=pd.DataFrame)
    efficiency_l3d: dict[str, pd.DataFrame] = field(default_factory=dict)
    plays: pd.DataFrame = field(default_factory=pd.DataFrame)
    rankings: pd.DataFrame = field(default_factory=pd.DataFrame)


@dataclass
class AnglesRunResult:
    """One CLI / programmatic run."""

    split: str
    hypothesis_ids: tuple[str, ...]
    config_hashes: dict[str, str]
    seasons: tuple[int, ...]
    sensitivity_seasons: tuple[int, ...]
    count_tables: dict[str, pd.DataFrame]
    primaries: dict[str, DefinitionResult]
    variants: dict[str, list[DefinitionResult]]
    bh: dict[str, BhDecision]
    holdout_entries: dict[str, LedgerEntry]
    report_paths: dict[str, Path]
    n_both_qualify: dict[str, int]
    notes: list[str] = field(default_factory=list)
    family: dict[str, dict[str, Any]] = field(default_factory=dict)


def _echo(stream: TextIO | None, fn: EchoFn | None, line: str) -> None:
    if fn is not None:
        fn(line)
    elif stream is not None:
        stream.write(line + "\n")
        stream.flush()


def parse_ids(raw: str, cfg: AnglesConfig) -> tuple[str, ...]:
    """Parse ``H1,H3,H14`` (or ``all``) into hypothesis ids present in config."""
    text = raw.strip()
    if not text or text.casefold() == "all":
        return tuple(
            hid for hid, hyp in cfg.hypotheses.items() if hyp.status != STATUS_RETIRED_UNOPENED
        )
    ids = tuple(part.strip() for part in text.split(",") if part.strip())
    unknown = [i for i in ids if i not in cfg.hypotheses]
    if unknown:
        known = ", ".join(cfg.hypotheses)
        msg = f"unknown hypothesis id(s) {unknown}; known: {known}"
        raise ValueError(msg)
    return ids


def cfbd_close_frame(lines: pd.DataFrame) -> pd.DataFrame:
    """Home-perspective median CFBD close spread per game."""
    cols = ["game_id", "spread", "line_source", "n_books"]
    if lines is None or lines.empty or "game_id" not in lines.columns:
        return pd.DataFrame(columns=cols)
    work = lines.copy()
    if "line_type" in work.columns:
        close = work.loc[work["line_type"].astype(str).str.casefold() == "close"]
        work = close if not close.empty else work
    if "spread" not in work.columns:
        return pd.DataFrame(columns=cols)
    work["game_id"] = pd.to_numeric(work["game_id"], errors="coerce")
    work = work.loc[work["game_id"].notna()].copy()
    if work.empty:
        return pd.DataFrame(columns=cols)
    work["game_id"] = work["game_id"].astype(int)
    books = work.groupby("game_id")["book"].nunique() if "book" in work.columns else None
    spread = work.groupby("game_id")["spread"].median()
    n_books = books.reindex(spread.index).fillna(0).astype(int) if books is not None else 0
    return pd.DataFrame(
        {
            "game_id": spread.index.astype(int),
            "spread": spread.to_numpy(dtype=float),
            "line_source": "cfbd_close",
            "n_books": n_books if isinstance(n_books, int) else n_books.to_numpy(),
        }
    )


def _home_school_map(games: pd.DataFrame, teams: pd.DataFrame) -> dict[int, str]:
    schools: dict[tuple[int, int], str] = {}
    if not teams.empty and "school" in teams.columns:
        for r in teams.itertuples(index=False):
            schools[(int(r.season) if hasattr(r, "season") else 0, int(r.team_id))] = str(r.school)
    fallback = (
        {int(r.team_id): str(r.school) for r in teams.itertuples(index=False)}
        if (not teams.empty and "school" in teams.columns)
        else {}
    )
    out: dict[int, str] = {}
    for r in games.itertuples(index=False):
        gid = int(r.game_id)
        if hasattr(r, "home_team") and pd.notna(getattr(r, "home_team", None)):
            text = str(r.home_team).strip()
            if text:
                out[gid] = text
                continue
        hid = int(r.home_team_id)
        season = int(r.season) if hasattr(r, "season") else 0
        out[gid] = schools.get((season, hid), fallback.get(hid, ""))
    return out


def _median_home_spread_by_game(
    spread_rows: pd.DataFrame,
    home_school: Mapping[int, str],
) -> pd.DataFrame:
    """Median home-side spread and n_books per game_id."""
    cols = ["game_id", "spread", "line_source", "n_books"]
    if spread_rows is None or spread_rows.empty:
        return pd.DataFrame(columns=cols)
    work = spread_rows.copy()
    if "market" in work.columns:
        work = work.loc[work["market"].astype(str) == "spread"]
    if work.empty:
        return pd.DataFrame(columns=cols)
    work["game_id"] = pd.to_numeric(work["game_id"], errors="coerce")
    work = work.loc[work["game_id"].notna()].copy()
    if work.empty:
        return pd.DataFrame(columns=cols)
    work["game_id"] = work["game_id"].astype(int)
    rows: list[dict[str, Any]] = []
    for gid, grp in work.groupby("game_id", sort=False):
        side = home_school.get(int(gid), "")
        if side and "side" in grp.columns:
            home = filter_home_side_spreads(grp, side)
        elif "side" not in grp.columns:
            home = grp
        else:
            home = grp.iloc[0:0]
        if home.empty or "line" not in home.columns:
            continue
        lines = pd.to_numeric(home["line"], errors="coerce").dropna()
        if lines.empty:
            continue
        n_books = (
            int(home.loc[lines.index, "book"].nunique())
            if "book" in home.columns
            else int(len(lines))
        )
        if "n_books_available" in home.columns and home["n_books_available"].notna().any():
            n_books = int(home["n_books_available"].dropna().iloc[0])
        rows.append(
            {
                "game_id": int(gid),
                "spread": float(lines.median()),
                "line_source": "odds_api_snapshot",
                "n_books": n_books,
            }
        )
    return pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)


def odds_tuesday_frame(
    snapshots: pd.DataFrame,
    games: pd.DataFrame,
    teams: pd.DataFrame,
    *,
    as_of_by_game: pd.Series | None = None,
) -> pd.DataFrame:
    """Tuesday 06:00 ET snapshot spread (home perspective). Never fills from CFBD."""
    cols = ["game_id", "spread", "line_source", "n_books"]
    if snapshots is None or snapshots.empty:
        return pd.DataFrame(columns=cols)
    work = snapshots.copy()
    if "market" in work.columns:
        work = work.loc[work["market"].astype(str) == "spread"]
    named = pd.DataFrame()
    if "decision_point" in work.columns:
        named = work.loc[work["decision_point"].astype(str) == "tuesday_0600_et"]
    home = _home_school_map(games, teams)
    if not named.empty:
        return _median_home_spread_by_game(named, home)
    if as_of_by_game is None or as_of_by_game.empty:
        return pd.DataFrame(columns=cols)
    work["event_time"] = pd.to_datetime(work["event_time"], utc=True)
    cfg = WalkForwardConfig()
    rows: list[dict[str, Any]] = []
    for gid, bound in as_of_by_game.items():
        sub = work.loc[work["game_id"].astype("Int64") == int(gid)]
        if sub.empty:
            continue
        bound_ts = pd.Timestamp(bound)
        kick = pd.Timestamp(bound)
        if "start_date" in games.columns:
            ghit = games.loc[games["game_id"] == int(gid)]
            if not ghit.empty:
                kick = pd.to_datetime(ghit["start_date"].iloc[0], utc=True)
        eligible = sub.loc[(sub["event_time"] <= bound_ts) & (sub["event_time"] < kick)]
        if eligible.empty:
            continue
        latest = eligible["event_time"].max()
        window = eligible.loc[eligible["event_time"] == latest]
        side = home.get(int(gid), "")
        if side:
            window = filter_home_side_spreads(window, side)
        if window.empty:
            continue
        lines = pd.to_numeric(window["line"], errors="coerce").dropna()
        if lines.empty:
            continue
        tol_pre = pd.Timedelta(minutes=cfg.snapshot_tolerance_minutes_pre_2022_09)
        source = (
            "odds_api_snapshot" if latest >= bound_ts - tol_pre else "odds_api_snapshot_fallback"
        )
        rows.append(
            {
                "game_id": int(gid),
                "spread": float(lines.median()),
                "line_source": source,
                "n_books": int(window["book"].nunique())
                if "book" in window.columns
                else int(len(lines)),
            }
        )
    return pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)


def odds_close_frame(
    snapshots: pd.DataFrame,
    games: pd.DataFrame,
    teams: pd.DataFrame,
) -> pd.DataFrame:
    """Last Odds snapshot strictly before kickoff (home perspective). No CFBD fill."""
    cols = ["game_id", "spread", "line_source", "n_books"]
    if snapshots is None or snapshots.empty:
        return pd.DataFrame(columns=cols)
    work = snapshots.copy()
    if "market" in work.columns:
        work = work.loc[work["market"].astype(str) == "spread"]
    named = pd.DataFrame()
    if "decision_point" in work.columns:
        named = work.loc[work["decision_point"].astype(str) == "slot_close"]
    home = _home_school_map(games, teams)
    if not named.empty:
        out = _median_home_spread_by_game(named, home)
        if not out.empty:
            return out
    if work.empty or "event_time" not in work.columns:
        return pd.DataFrame(columns=cols)
    work["game_id"] = pd.to_numeric(work["game_id"], errors="coerce")
    work = work.loc[work["game_id"].notna()].copy()
    if work.empty:
        return pd.DataFrame(columns=cols)
    work["game_id"] = work["game_id"].astype(int)
    work["event_time"] = pd.to_datetime(work["event_time"], utc=True)
    kick = games.drop_duplicates("game_id").set_index("game_id")
    kick_ts = pd.to_datetime(
        kick["start_date"] if "start_date" in kick.columns else kick["event_time"], utc=True
    )
    rows: list[dict[str, Any]] = []
    for gid, grp in work.groupby("game_id", sort=False):
        if int(gid) not in kick_ts.index:
            continue
        bound = kick_ts.at[int(gid)]
        eligible = grp.loc[grp["event_time"] < bound]
        if eligible.empty:
            continue
        latest = eligible["event_time"].max()
        window = eligible.loc[eligible["event_time"] == latest]
        framed = _median_home_spread_by_game(window, home)
        if framed.empty:
            continue
        row = framed.iloc[0].to_dict()
        row["game_id"] = int(gid)
        rows.append(row)
    return pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)


def close_spread_home_map(cfbd: pd.DataFrame, odds: pd.DataFrame) -> dict[int, float]:
    """Home-perspective close for luck: Odds when present, else CFBD (never pooled in reports)."""
    out: dict[int, float] = {}
    for frame in (cfbd, odds):
        if frame is None or frame.empty:
            continue
        for r in frame.itertuples(index=False):
            sp = float(r.spread)
            if not np.isfinite(sp):
                continue
            gid = int(r.game_id)
            if gid not in out:
                out[gid] = sp
    # Prefer odds: rewrite
    if odds is not None and not odds.empty:
        for r in odds.itertuples(index=False):
            sp = float(r.spread)
            if np.isfinite(sp):
                out[int(r.game_id)] = sp
    return out


DEFAULT_FAMILY_PATH: Final[Path] = Path("docs/notes/angles/discovery_primaries.json")

# Batch-1 discovery primaries. Seeded when the family file is missing so BH
# stays cumulative across every primary ever run on discovery.
_BATCH1_FAMILY_SEED: Final[dict[str, dict[str, Any]]] = {
    "H1": {
        "p": 0.2514,
        "n": 321,
        "estimate": 0.836,
        "config_hash": "e6d4fab2337003b3",
        "name": "Rested angle team vs opponent on a normal week that has not yet had a bye",
        "source": "batch1",
    },
    "H3": {
        "p": 0.2854,
        "n": 889,
        "estimate": 0.297,
        "config_hash": "a3a5c38955cfe780",
        "name": (
            "Rested angle team; ATS residual by opponent's consecutive weeks played (linear trend)"
        ),
        "source": "batch1",
    },
    "H14": {
        "p": 0.1258,
        "n": 808,
        "estimate": -0.937,
        "config_hash": "0b0ff2ea2c161a2e",
        "name": 'Fade the opponent after a top-decile "lucky" game',
        "source": "batch1",
    },
}

_FAMILY_DISPLAY_ORDER: Final[tuple[str, ...]] = (
    "H1",
    "H3",
    "H14",
    "H7",
    "H9",
    "H15",
    "H22",
    "H23",
    "H24",
)


def family_display_order(ids: Iterable[str]) -> tuple[str, ...]:
    rank = {hid: i for i, hid in enumerate(_FAMILY_DISPLAY_ORDER)}
    return tuple(sorted(ids, key=lambda x: (rank.get(x, 99), x)))


def load_discovery_family(path: Path | str = DEFAULT_FAMILY_PATH) -> dict[str, dict[str, Any]]:
    """Load the cumulative discovery-primary ledger (missing → batch-1 seed)."""
    p = Path(path)
    if not p.is_file():
        return {k: dict(v) for k, v in _BATCH1_FAMILY_SEED.items()}
    raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {k: dict(v) for k, v in _BATCH1_FAMILY_SEED.items()}
    out: dict[str, dict[str, Any]] = {}
    for key, rec in raw.items():
        if isinstance(rec, Mapping):
            out[str(key)] = dict(rec)
    for hid, rec in _BATCH1_FAMILY_SEED.items():
        out.setdefault(hid, dict(rec))
    return out


def save_discovery_family(
    family: Mapping[str, Mapping[str, Any]],
    path: Path | str = DEFAULT_FAMILY_PATH,
) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {k: dict(v) for k, v in family.items()}
    p.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
    )
    return p


def _format_counts(table: pd.DataFrame, *, both: int) -> list[str]:
    lines = ["season  n_qualifies  n_baseline  n_games"]
    for r in table.itertuples(index=False):
        lines.append(
            f"{int(r.season):<7} {int(r.n_qualifies):>12} "
            f"{int(r.n_baseline):>11} {int(r.n_games):>8}"
        )
    lines.append(f"both-qualify games dropped: {int(both)}")
    return lines


def run_angles(
    *,
    data: AnglesData,
    ids: Sequence[str],
    split: str,
    config: AnglesConfig | None = None,
    config_path: Path | str | None = None,
    notes_dir: Path | str = "docs/notes/angles",
    ledger_path: Path | str = DEFAULT_LEDGER_PATH,
    force: bool = False,
    confirm_lockbox_read: bool = False,
    echo: EchoFn | None = None,
    stream: TextIO | None = None,
    write_reports: bool = True,
    family_path: Path | str = DEFAULT_FAMILY_PATH,
) -> AnglesRunResult:
    """Run the harness. Step 1 always prints qualifying counts before outcomes."""
    cfg = config if config is not None else load_angles_config(config_path or DEFAULT_ANGLES_CONFIG)
    hid_tuple = tuple(ids)
    for hid in hid_tuple:
        if hid not in cfg.hypotheses:
            msg = f"unknown hypothesis {hid!r}"
            raise ValueError(msg)
        hyp = cfg.hypotheses[hid]
        if hyp.status == STATUS_RETIRED_UNOPENED and split == "holdout":
            reason = hyp.retired_reason.strip() or "retired without opening"
            msg = f"{hid} is retired_unopened; holdout stays closed. {reason}"
            raise RetiredUnopenedError(msg)

    seasons = list(cfg.split_seasons(split))
    sensitivity = list(cfg.split_sensitivity_seasons(split))
    lockbox = list(cfg.split_lockbox_seasons(split))
    notes: list[str] = []
    if split == "holdout" and lockbox and not confirm_lockbox_read:
        dropped = [s for s in seasons if s in set(lockbox)]
        seasons = [s for s in seasons if s not in set(lockbox)]
        if dropped:
            notes.append(
                f"lockbox season(s) {dropped} excluded from this holdout (pass "
                f"--confirm-lockbox-read and log docs/lockbox_access.md "
                f"to include {LOCKBOX_SEASON})"
            )
    if split == "holdout":
        assert_lockbox_excluded(
            seasons,
            context="angles research holdout",
            confirmatory_read=confirm_lockbox_read,
        )

    universe_seasons = sorted(set(seasons) | set(sensitivity))
    games = data.games.copy()
    if "season" in games.columns and universe_seasons:
        games = games.loc[games["season"].isin(universe_seasons)].copy()

    frame = build_team_game_frame(
        games,
        data.teams,
        venues=data.venues,
        rivalries_path=cfg.features.get("rivalries") or cfg.sources.get("rivalries"),
        season_type=str(cfg.universe.get("season_type") or "regular"),
    )
    rows = frame.rows
    if cfg.features.get("efficiency_l3d") and data.efficiency_l3d:
        rows = attach_efficiency_l3d(rows, data.efficiency_l3d)

    need_luck = any(hypothesis_needs_luck(cfg.hypotheses[i]) for i in hid_tuple)
    need_context = any(
        hypothesis_needs_last_game(cfg.hypotheses[i])
        or hypothesis_needs_ridge(cfg.hypotheses[i])
        or hypothesis_needs_turnovers(cfg.hypotheses[i])
        or hypothesis_needs_ap(cfg.hypotheses[i])
        for i in hid_tuple
    )
    need_lines = any(hypothesis_needs_lines(cfg.hypotheses[i]) for i in hid_tuple) or need_context
    cfbd_close = cfbd_close_frame(data.cfbd_lines)
    odds_close = odds_close_frame(data.snapshots, games, data.teams)
    tue = odds_tuesday_frame(data.snapshots, games, data.teams)
    spread_map = close_spread_home_map(cfbd_close, odds_close)
    if need_luck:
        luck = compute_game_luck(
            games,
            data.team_game_epa,
            spread_map,
            data.filter_history,
            hfa_epa_per_play=float(cfg.sources.get("hfa_epa_per_play") or 0.025),
        )
        rows = attach_last_game_luck(rows, luck, games)
        rows = attach_trailing_luck_quantiles(rows, luck, games)
    if need_lines:
        rows = attach_market_lines(
            rows, close_cfbd=cfbd_close, close_odds=odds_close, tuesday_odds=tue
        )
    if need_context:
        from ncaa_quant.research.angles_context import attach_condition_context

        rows = attach_condition_context(
            rows,
            games,
            data.teams,
            close_spread_home=spread_map,
            filter_history=data.filter_history,
            plays=data.plays,
            rankings=data.rankings,
        )
    frame = TeamGameFrame(
        rows=rows,
        n_games_universe=frame.n_games_universe,
        seasons=frame.seasons,
        has_outcomes=False,
        notes=dict(frame.notes),
    )

    count_tables: dict[str, pd.DataFrame] = {}
    n_both: dict[str, int] = {}
    _echo(stream, echo, "Step 1 - qualifying counts (this-game residuals not yet attached)")
    for hid in hid_tuple:
        hyp = cfg.hypotheses[hid]
        flagged = flag_definition(frame.rows, hyp.primary, hyp.baseline)
        table = qualifying_counts_by_season(flagged, seasons + sensitivity)
        count_tables[hid] = table
        n_both[hid] = flagged.n_both_qualify_games
        _echo(stream, echo, f"== {hid} {hyp.name} ==")
        for line in _format_counts(table, both=flagged.n_both_qualify_games):
            _echo(stream, echo, line)

    holdout_entries: dict[str, LedgerEntry] = {}
    if split == "holdout":
        for hid in hid_tuple:
            holdout_entries[hid] = open_holdout(
                hid,
                cfg.hypothesis_hash(hid),
                path=ledger_path,
                force=force,
                reason=f"angles research --split holdout --ids {hid}",
            )

    _echo(stream, echo, "Step 2 - attaching outcomes (spread / ATS / Ridge / Tuesday line)")
    ridge = data.ridge_predictions
    if ridge is not None and not ridge.empty and "pred_margin" not in ridge.columns:
        ridge = pd.DataFrame()
    framed = attach_outcomes(
        frame,
        games,
        close_cfbd=cfbd_close,
        close_odds=odds_close,
        tuesday_odds=tue,
        ridge_predictions=ridge,
    )

    primaries: dict[str, DefinitionResult] = {}
    variants: dict[str, list[DefinitionResult]] = {}
    pvalues: dict[str, float] = {}
    for hid in hid_tuple:
        hyp = cfg.hypotheses[hid]
        flagged = flag_definition(framed.rows, hyp.primary, hyp.baseline)
        primary = analyze_definition(
            flagged,
            hyp,
            hyp.primary,
            cfg,
            kind="primary",
            seasons=seasons,
            sensitivity_seasons=sensitivity,
        )
        if hid == "H15":
            from ncaa_quant.research.angles_context import TURNOVER_PLAY_TYPE_NOTE

            primary.notes.append(TURNOVER_PLAY_TYPE_NOTE)
        primaries[hid] = primary
        pvalues[hid] = primary.primary_p
        var_results: list[DefinitionResult] = []
        for variant in hyp.variants:
            vf = flag_definition(framed.rows, variant, hyp.baseline)
            var_results.append(
                analyze_definition(
                    vf,
                    hyp,
                    variant,
                    cfg,
                    kind="variant",
                    seasons=seasons,
                    sensitivity_seasons=sensitivity,
                )
            )
        variants[hid] = var_results

    q = float(cfg.multiple_testing.get("q", 0.10))
    hashes = {hid: cfg.hypothesis_hash(hid) for hid in hid_tuple}
    family: dict[str, dict[str, Any]] = {}
    if split == "discovery":
        family = load_discovery_family(family_path)
        for hid in hid_tuple:
            if not cfg.hypotheses[hid].include_in_bh:
                primaries[hid].notes.insert(
                    0, "motivated by discovery; not evidence (excluded from cumulative BH family)"
                )
                notes.append(f"{hid}: descriptive discovery; excluded from cumulative BH family")
                continue
            p_run = float(primaries[hid].primary_p)
            prior = family.get(hid)
            if prior is not None and not math.isfinite(p_run):
                notes.append(
                    f"{hid}: this-run primary p is null; kept prior family entry "
                    f"(source={prior.get('source')})"
                )
                continue
            family[hid] = {
                "p": p_run if math.isfinite(p_run) else None,
                "n": int((primaries[hid].by_instrument.get("cfbd") or {}).get("n_qualifying") or 0),
                "estimate": primaries[hid].primary_estimate,
                "config_hash": hashes[hid],
                "name": cfg.hypotheses[hid].name,
                "source": "this_run",
            }
        family_p = {
            k: float(v["p"])
            for k, v in family.items()
            if v.get("p") is not None and math.isfinite(float(v["p"]))
        }
        bh = benjamini_hochberg(family_p, q=q)
        save_discovery_family(family, family_path)
        notes.append(
            "BH is cumulative at q="
            + str(q)
            + " across every primary ever run on discovery: "
            + ", ".join(family_display_order(family))
        )
    else:
        bh = {}

    report_paths: dict[str, Path] = {}
    if write_reports:
        out_dir = Path(notes_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for hid in hid_tuple:
            path = write_hypothesis_report(
                out_dir / f"{hid}.md",
                cfg=cfg,
                hypothesis=cfg.hypotheses[hid],
                split=split,
                primary=primaries[hid],
                variants=variants[hid],
                bh=bh.get(hid),
                holdout=holdout_entries.get(hid),
                counts=count_tables[hid],
            )
            report_paths[hid] = path
        if split == "discovery":
            for hid, decision in bh.items():
                if hid in hid_tuple:
                    continue
                prior_report = out_dir / f"{hid}.md"
                if prior_report.is_file():
                    patch_hypothesis_bh(prior_report, decision, q=q)
                    report_paths[hid] = prior_report
        descriptive_ids = [h for h in hid_tuple if not cfg.hypotheses[h].include_in_bh]
        bh_run_ids = [h for h in hid_tuple if cfg.hypotheses[h].include_in_bh]
        if bh_run_ids or not (out_dir / "summary.md").is_file():
            summary = write_summary_report(
                out_dir / "summary.md",
                cfg=cfg,
                split=split,
                ids=family_display_order(family) if family else hid_tuple,
                primaries=primaries,
                variants=variants,
                bh=bh,
                holdout_entries=holdout_entries,
                notes=notes,
                family=family,
            )
            report_paths["summary"] = summary
        if descriptive_ids:
            from ncaa_quant.research.angles_report import append_descriptive_summary

            summary = append_descriptive_summary(
                out_dir / "summary.md",
                cfg=cfg,
                ids=descriptive_ids,
                primaries=primaries,
                variants=variants,
            )
            report_paths["summary"] = summary

    return AnglesRunResult(
        split=split,
        hypothesis_ids=hid_tuple,
        config_hashes=hashes,
        seasons=tuple(seasons),
        sensitivity_seasons=tuple(sensitivity),
        count_tables=count_tables,
        primaries=primaries,
        variants=variants,
        bh=bh,
        holdout_entries=holdout_entries,
        report_paths=report_paths,
        n_both_qualify=n_both,
        notes=notes,
        family=family,
    )


def _read_table_seasons(
    store_root: Path,
    table: str,
    seasons: Sequence[int],
    *,
    columns: Sequence[str] | None = None,
    where: str | None = None,
) -> pd.DataFrame:
    """Read hive partitions; optional column prune / WHERE via DuckDB."""
    import duckdb

    if not seasons:
        return pd.DataFrame()
    root = Path(store_root)
    table_root = root / table
    if not table_root.exists():
        return pd.DataFrame()
    files: list[Path] = []
    for season in seasons:
        files.extend(table_root.glob(f"season={int(season)}/**/part.parquet"))
        files.extend(table_root.glob(f"season={int(season)}/part.parquet"))
    uniq = sorted({p.resolve() for p in files if p.is_file()})
    if not uniq:
        return pd.DataFrame()
    select = ", ".join(f'"{c}"' for c in columns) if columns else "*"
    clause = f" WHERE {where}" if where else ""
    con = duckdb.connect(database=":memory:")
    try:
        parts = ", ".join(f"'{p.as_posix()}'" for p in uniq)
        sql = f"SELECT {select} FROM read_parquet([{parts}], hive_partitioning=true){clause}"
        return con.execute(sql).df()
    except Exception:
        frames = [pd.read_parquet(p) for p in uniq]
        out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if where == "market = 'spread'" and not out.empty and "market" in out.columns:
            out = out.loc[out["market"].astype(str) == "spread"].copy()
        if columns:
            keep = [c for c in columns if c in out.columns]
            out = out[keep]
        return out
    finally:
        con.close()


def _read_optional_parquet(path: Path) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame()
    return pd.read_parquet(path)


def _read_efficiency_l3d(features_root: Path, seasons: Sequence[int]) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    root = Path(features_root)
    for name in ("adj_off_epa_l3d", "adj_def_epa_l3d"):
        feat_root = root / name
        if not feat_root.exists():
            continue
        files = [
            p
            for season in seasons
            for p in feat_root.glob(f"v*/season={int(season)}/week=*/part.parquet")
        ]
        if not files:
            continue
        frames = [pd.read_parquet(p) for p in files]
        out[name] = pd.concat(frames, ignore_index=True)
    return out


def load_angles_data(
    cfg: AnglesConfig,
    *,
    seasons: Sequence[int],
    need_luck: bool,
    staged_root: Path | str | None = None,
    confirm_lockbox_read: bool = False,
    need_turnovers: bool = False,
    need_ridge: bool = False,
    need_rankings: bool = False,
) -> AnglesData:
    """Read staged tables and artifacts for ``seasons`` (lockbox-guarded)."""
    season_list = [int(s) for s in seasons]
    assert_lockbox_excluded(
        season_list,
        context="angles research data load",
        confirmatory_read=confirm_lockbox_read,
    )
    staged = Path(staged_root or cfg.sources.get("staged_root") or "data/staged")
    games = _read_table_seasons(staged, "games", season_list)
    teams = _read_table_seasons(staged, "teams", season_list)
    venues = _read_table_seasons(staged, "venues", season_list)
    lines = _read_table_seasons(staged, "lines_historical", season_list)
    snap_seasons = [s for s in season_list if s >= int(SNAPSHOT_BACKED_FROM_SEASON)]
    snapshots = _read_table_seasons(
        staged,
        "odds_snapshots",
        snap_seasons,
        where="market = 'spread'",
    )

    ridge_path = Path(str(cfg.sources.get("ridge_predictions") or ""))
    ridge = _read_optional_parquet(ridge_path)
    if not ridge.empty and "season" in ridge.columns:
        ridge = ridge.loc[ridge["season"].isin(season_list)].copy()

    filter_history = pd.DataFrame()
    team_game_epa = pd.DataFrame()
    plays = pd.DataFrame()
    if need_luck or need_ridge:
        fh_path = Path(
            str(
                cfg.sources.get("filter_history")
                or "data/artifacts/state_space/filter_history.parquet"
            )
        )
        filter_history = _read_optional_parquet(fh_path)
    if need_luck or need_turnovers:
        plays = _read_table_seasons(staged, "plays", season_list)
    if need_luck:
        if not plays.empty:
            from ncaa_quant.ratings.state_space import build_game_observations_from_plays

            team_game_epa = build_game_observations_from_plays(plays, games, drop_garbage=True)
        elif not _read_table_seasons(staged, "advanced_box", season_list).empty:
            from ncaa_quant.ratings.state_space import build_game_observations_from_advanced

            team_game_epa = build_game_observations_from_advanced(
                _read_table_seasons(staged, "advanced_box", season_list), games
            )

    rankings = pd.DataFrame()
    if need_rankings:
        from ncaa_quant.research.angles_rankings import (
            ingest_cfbd_rankings,
            load_research_rankings,
        )

        rankings = load_research_rankings(season_list)
        have = set(int(s) for s in rankings["season"].unique()) if not rankings.empty else set()
        missing = [s for s in season_list if s not in have]
        if missing:
            tuesdays = tuesday_as_of_by_week(games) if not games.empty else {}
            rankings = ingest_cfbd_rankings(season_list, teams, tuesdays=tuesdays)

    features_dir = Path("data/features")
    efficiency = (
        _read_efficiency_l3d(features_dir, season_list)
        if cfg.features.get("efficiency_l3d")
        else {}
    )
    return AnglesData(
        games=games,
        teams=teams,
        venues=venues,
        cfbd_lines=lines,
        snapshots=snapshots,
        ridge_predictions=ridge,
        filter_history=filter_history,
        team_game_epa=team_game_epa,
        efficiency_l3d=efficiency,
        plays=plays,
        rankings=rankings,
    )


__all__ = [
    "AnglesData",
    "AnglesRunResult",
    "cfbd_close_frame",
    "close_spread_home_map",
    "load_angles_data",
    "load_discovery_family",
    "odds_close_frame",
    "odds_tuesday_frame",
    "parse_ids",
    "run_angles",
    "save_discovery_family",
    "RetiredUnopenedError",
]
