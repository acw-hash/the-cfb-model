"""Discovery-only H22 sanity checks (batch 3). Not a holdout open."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, TextIO

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from ncaa_quant.research.angles import (
    AnglesConfig,
    TeamGameFrame,
    attach_market_lines,
    attach_outcomes,
    build_team_game_frame,
    flag_definition,
    load_angles_config,
)
from ncaa_quant.research.angles_analysis import _design, _mean_block, _spread_buckets
from ncaa_quant.research.angles_report import _n, _p, _pct
from ncaa_quant.research.angles_run import (
    AnglesData,
    cfbd_close_frame,
    close_spread_home_map,
    odds_close_frame,
    odds_tuesday_frame,
)
from ncaa_quant.research.angles_stats import ols_cluster, sign_table


def _finite_mask(rows: pd.DataFrame, resid_col: str, spread_col: str) -> pd.Series:
    return (
        pd.to_numeric(rows[resid_col], errors="coerce").notna()
        & pd.to_numeric(rows[spread_col], errors="coerce").notna()
    )


def _home_mask(rows: pd.DataFrame) -> pd.Series:
    return rows["venue"].astype(str).eq("home")


def _ols_qualifies(
    rows: pd.DataFrame,
    *,
    resid_col: str,
    spread_col: str,
    week_buckets: Sequence[Sequence[int]],
) -> dict[str, Any]:
    if rows.empty:
        return {"n": 0, "n_clusters": 0, "terms": []}
    design, names = _design(
        rows,
        spread_col=spread_col,
        week_buckets=week_buckets,
        extra={"qualifies": rows["qualifies"].astype(float).to_numpy()},
    )
    return ols_cluster(
        rows[resid_col].to_numpy(dtype=float), design, names, rows["season_week"].to_numpy()
    ).as_dict()


def _term(block: MappingLike, name: str) -> dict[str, Any]:
    for term in block.get("terms") or []:
        if term.get("name") == name:
            return dict(term)
    return {}


MappingLike = dict[str, Any]


def compute_h22_sanity(
    rows: pd.DataFrame,
    cfg: AnglesConfig,
    *,
    seasons: Sequence[int],
    sensitivity_seasons: Sequence[int],
) -> dict[str, Any]:
    """All-home means and H22-vs-home regression on discovery seasons."""
    hyp = cfg.hypotheses["H22"]
    flagged = flag_definition(rows, hyp.primary, hyp.baseline)
    work = flagged.rows
    inf = cfg.inference
    ci = float((inf.get("bootstrap") or {}).get("ci", 0.95))
    week_buckets = [
        list(b) for b in (inf.get("week_buckets") or [[1, 4], [5, 8], [9, 12], [13, 20]])
    ]
    spread_buckets = dict(inf.get("spread_buckets") or {})
    in_disc = work["season"].isin([int(s) for s in seasons])

    def _block(instrument: str) -> dict[str, Any]:
        resid = f"ats_residual_{instrument}"
        spread = f"spread_close_{instrument}"
        cover = f"cover_{instrument}"
        has = _finite_mask(work, resid, spread)
        field = work.loc[has & in_disc]
        all_home = field.loc[_home_mask(field)]
        qual = field.loc[field["qualifies"]]
        base = field.loc[field["baseline"]]
        qb = pd.concat([qual, base], ignore_index=True)
        vs_base = _ols_qualifies(qb, resid_col=resid, spread_col=spread, week_buckets=week_buckets)
        vs_home = _ols_qualifies(
            all_home, resid_col=resid, spread_col=spread, week_buckets=week_buckets
        )
        q_term_home = _term(vs_home, "qualifies")
        survive = bool(
            q_term_home.get("p") is not None
            and math_isfinite(q_term_home.get("p"))
            and float(q_term_home["p"]) < 0.05
            and float(q_term_home.get("coef", 0.0)) < 0
        )
        return {
            "instrument": instrument,
            "all_home_mean": _mean_block(all_home, resid, ci=ci),
            "baseline_mean": _mean_block(base, resid, ci=ci),
            "h22_mean": _mean_block(qual, resid, ci=ci),
            "regression_vs_baseline": vs_base,
            "regression_vs_all_home": vs_home,
            "road_loss_effect_survives_vs_all_home": survive,
            "sign_table": sign_table(
                qual["season"].to_numpy(),
                qual[resid].to_numpy(dtype=float),
                qual[cover].to_numpy(dtype=float),
            ),
            "spread_buckets": _spread_buckets(
                qual,
                spread_col=spread,
                resid_col=resid,
                cover_col=cover,
                buckets=spread_buckets,
                ci=ci,
            ),
        }

    ridge_has = pd.to_numeric(work["ridge_residual"], errors="coerce").notna()
    ridge_qual = work.loc[ridge_has & in_disc & work["qualifies"]]
    unused = sensitivity_seasons
    del unused
    return {
        "cfbd": _block("cfbd"),
        "odds": _block("odds"),
        "ridge_mean": _mean_block(ridge_qual, "ridge_residual", ci=ci),
        "ridge_sign_table": sign_table(
            ridge_qual["season"].to_numpy(),
            ridge_qual["ridge_residual"].to_numpy(dtype=float),
            np.full(len(ridge_qual), np.nan),
        ),
    }


def math_isfinite(value: Any) -> bool:
    try:
        return bool(np.isfinite(float(value)))
    except (TypeError, ValueError):
        return False


def format_h22_sanity_md(stats: MappingLike) -> str:
    """Markdown section appended to ``H22.md``."""
    cfbd = stats["cfbd"]
    odds = stats["odds"]
    h22_mean = cfbd["h22_mean"]
    vs_base = _term(cfbd["regression_vs_baseline"], "qualifies")
    vs_home = _term(cfbd["regression_vs_all_home"], "qualifies")
    survive = cfbd["road_loss_effect_survives_vs_all_home"]
    lines = [
        "## Batch 3 sanity checks (discovery only)",
        "",
        "2020 excluded from every headline cell. Instruments never pooled.",
        "",
        "### All non-neutral home teams — CFBD close, 2014–2022 excluding 2020",
        "",
        *_mean_md(cfbd["all_home_mean"], "mean ATS residual (points)"),
        "",
        "### H22 baseline — home after a non-neutral road win (CFBD close)",
        "",
        *_mean_md(cfbd["baseline_mean"], "mean ATS residual (points)"),
        "",
        "### H22 raw mean next to regression `qualifies` (CFBD close)",
        "",
        f"- **raw mean ATS residual** n={_n(h22_mean.get('n'), 0)} mean={_n(h22_mean.get('mean'))} "
        f"SE={_n(h22_mean.get('se'))} p={_p(h22_mean.get('p_two_sided'))}",
        (
            "- **regression vs H22 baseline** (controls: spread + venue + conference_game + "
            f"week bucket): coef={_n(vs_base.get('coef'))} SE={_n(vs_base.get('se'))} "
            f"p={_p(vs_base.get('p'))} (n={_n(cfbd['regression_vs_baseline'].get('n'), 0)})"
        ),
        (
            "- **regression vs all non-neutral home teams** (controls: spread + conference_game + "
            "week bucket; `venue_home` is constant in this sample and is dropped): "
            f"coef={_n(vs_home.get('coef'))} SE={_n(vs_home.get('se'))} "
            f"p={_p(vs_home.get('p'))} (n={_n(cfbd['regression_vs_all_home'].get('n'), 0)})"
        ),
        (
            "- **Does the road-loss effect survive vs all home teams?** "
            + ("yes (coef negative, p < 0.05)" if survive else "no")
        ),
        "",
        "### Per-season sign table (H22 qualifying, CFBD close)",
        "",
        "| season | n | mean | sign | cover |",
        "|---|---:|---:|:---:|---:|",
    ]
    for row in cfbd["sign_table"]:
        lines.append(
            f"| {row.get('season')} | {_n(row.get('n'), 0)} | {_n(row.get('mean'))} "
            f"| {row.get('sign') or '—'} | {_pct(row.get('cover_rate'))} |"
        )
    lines.extend(
        [
            "",
            "### Spread buckets (H22 qualifying, CFBD close)",
            "",
            "| bucket | n | cover | mean ATS residual | SE |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for row in cfbd["spread_buckets"]:
        lines.append(
            f"| {row.get('bucket')} | {_n(row.get('n'), 0)} | {_pct(row.get('cover_rate'))} "
            f"| {_n(row.get('mean_ats_residual'))} | {_n(row.get('se'))} |"
        )
    lines.extend(
        [
            "",
            "### Odds-API close, 2021–2022 (never pooled with CFBD)",
            "",
            *_mean_md(odds["h22_mean"], "H22 mean ATS residual"),
            *_mean_md(odds["all_home_mean"], "all non-neutral home teams mean ATS residual"),
            "",
            "### Mean Ridge residual (H22 qualifying, walk-forward seasons)",
            "",
            *_mean_md(stats["ridge_mean"], "mean Ridge residual (points)"),
            "",
            "| season | n | mean | sign |",
            "|---|---:|---:|:---:|---:|",
        ]
    )
    for row in stats["ridge_sign_table"]:
        lines.append(
            f"| {row.get('season')} | {_n(row.get('n'), 0)} | {_n(row.get('mean'))} "
            f"| {row.get('sign') or '—'} |"
        )
    lines.append("")
    return "\n".join(lines) + "\n"


def _mean_md(block: dict[str, Any], label: str) -> list[str]:
    return [
        f"- **{label}** n={_n(block.get('n'), 0)} clusters={_n(block.get('n_clusters'), 0)} "
        f"mean={_n(block.get('mean'))} SE={_n(block.get('se'))} "
        f"95% [{_n(block.get('ci_lo'))}, {_n(block.get('ci_hi'))}] "
        f"p={_p(block.get('p_two_sided'))}"
    ]


def append_h22_sanity(path: Path | str, markdown: str) -> Path:
    out = Path(path)
    text = out.read_text(encoding="utf-8") if out.is_file() else ""
    marker = "## Batch 3 sanity checks (discovery only)"
    text = text.split(marker)[0].rstrip() + "\n\n" if marker in text else text.rstrip() + "\n\n"
    out.write_text(text + markdown, encoding="utf-8")
    return out


def condition_rows(
    data: AnglesData,
    cfg: AnglesConfig,
    seasons: Sequence[int],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Team-game condition frame (no this-game residuals) and the games slice."""
    games = data.games.copy()
    if "season" in games.columns:
        games = games.loc[games["season"].isin([int(s) for s in seasons])].copy()
    frame = build_team_game_frame(
        games,
        data.teams,
        venues=data.venues,
        rivalries_path=cfg.features.get("rivalries") or cfg.sources.get("rivalries"),
        season_type=str(cfg.universe.get("season_type") or "regular"),
    )
    cfbd_close = cfbd_close_frame(data.cfbd_lines)
    odds_close = odds_close_frame(data.snapshots, games, data.teams)
    tue = odds_tuesday_frame(data.snapshots, games, data.teams)
    spread_map = close_spread_home_map(cfbd_close, odds_close)
    rows = attach_market_lines(
        frame.rows, close_cfbd=cfbd_close, close_odds=odds_close, tuesday_odds=tue
    )
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
    return rows, games


def framed_rows_for_sanity(
    data: AnglesData, cfg: AnglesConfig, seasons: Sequence[int]
) -> pd.DataFrame:
    """Condition frame + this-game outcomes for the requested seasons."""
    rows, games = condition_rows(data, cfg, seasons)
    frame = TeamGameFrame(
        rows=rows,
        n_games_universe=int(rows["game_id"].nunique()) if not rows.empty else 0,
        seasons=tuple(sorted({int(s) for s in rows["season"].unique()})) if not rows.empty else (),
    )
    cfbd_close = cfbd_close_frame(data.cfbd_lines)
    odds_close = odds_close_frame(data.snapshots, games, data.teams)
    tue = odds_tuesday_frame(data.snapshots, games, data.teams)
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
    return framed.rows


def run_h22_sanity(
    *,
    data: AnglesData,
    config: AnglesConfig | None = None,
    notes_path: Path | str = "docs/notes/angles/H22.md",
    echo: TextIO | None = None,
) -> dict[str, Any]:
    """Compute discovery sanity stats and append them to ``H22.md``."""
    cfg = config if config is not None else load_angles_config()
    seasons = list(cfg.split_seasons("discovery"))
    sensitivity = list(cfg.split_sensitivity_seasons("discovery"))
    rows = framed_rows_for_sanity(data, cfg, seasons + sensitivity)
    stats = compute_h22_sanity(rows, cfg, seasons=seasons, sensitivity_seasons=sensitivity)
    md = format_h22_sanity_md(stats)
    path = append_h22_sanity(notes_path, md)
    if echo is not None:
        echo.write(f"appended H22 sanity to {path}\n")
    return stats


def expected_holdout_n(
    rows: pd.DataFrame,
    cfg: AnglesConfig,
    *,
    ids: Sequence[str],
    seasons: Sequence[int],
) -> dict[str, int]:
    """Qualifying counts on holdout seasons without attaching this-game residuals.

    Last-game ATS / venue conditions are knowable at the decision point.
    """
    out: dict[str, int] = {}
    sub = rows.loc[rows["season"].isin([int(s) for s in seasons])]
    for hid in ids:
        hyp = cfg.hypotheses[hid]
        flagged = flag_definition(sub, hyp.primary, hyp.baseline)
        out[hid] = int(flagged.rows["qualifies"].sum())
    return out


__all__ = [
    "append_h22_sanity",
    "compute_h22_sanity",
    "condition_rows",
    "expected_holdout_n",
    "format_h22_sanity_md",
    "framed_rows_for_sanity",
    "run_h22_sanity",
]
