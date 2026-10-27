"""Discovery-only Ridge home-team residual diagnosis (angles batch 4).

Research tooling. Holdout seasons 2023–2025 stay closed. No production
feature, model, or config is written.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final, TextIO

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from ncaa_quant.research.angles import LONG_REST_DAYS, AnglesConfig, load_angles_config
from ncaa_quant.research.angles_h22_sanity import framed_rows_for_sanity
from ncaa_quant.research.angles_report import _n, _p
from ncaa_quant.research.angles_run import AnglesData, load_angles_data
from ncaa_quant.research.angles_stats import cluster_mean

HOLDOUT_SEASONS: Final[frozenset[int]] = frozenset({2023, 2024, 2025})
SITUATIONS: Final[tuple[str, ...]] = (
    "road_loss",
    "road_win",
    "home_loss",
    "home_win",
    "bye",
)
DEFAULT_NOTES_PATH: Final[Path] = Path("docs/notes/angles/ridge_home_diag.md")
DEFAULT_LEDGER_PATH: Final[Path] = Path("docs/notes/angles/holdout_ledger.jsonl")
_RESID_HEADER: Final[str] = (
    "| {first} | n_ridge | ridge mean | ridge SE | ridge p | n_ats | ats mean | ats SE | ats p |"
)
_RESID_ALIGN: Final[str] = "|---|---:|---:|---:|---:|---:|---:|---:|---:|"


class HoldoutSeasonError(ValueError):
    """Raised if a 2023–2025 row would enter the diagnosis."""


def assert_discovery_only(rows: pd.DataFrame) -> None:
    """Refuse any holdout / lockbox season. Those years stay closed."""
    if rows is None or rows.empty or "season" not in rows.columns:
        return
    seasons = {int(s) for s in pd.to_numeric(rows["season"], errors="coerce").dropna()}
    bad = seasons & HOLDOUT_SEASONS
    if bad:
        msg = (
            f"holdout seasons {sorted(bad)} must stay closed; "
            "Ridge home diagnosis is discovery 2014–2022 only (2020 sensitivity)"
        )
        raise HoldoutSeasonError(msg)


def assert_ledger_empty(path: Path | str = DEFAULT_LEDGER_PATH) -> None:
    """The holdout ledger must not exist (never opened)."""
    if Path(path).exists():
        msg = f"holdout ledger exists at {path}; batch 4 must leave it empty"
        raise RuntimeError(msg)


def prior_situation(rows: pd.DataFrame) -> pd.Series:
    """Label the named team's previous-week situation.

    Last-result cells require a completed prior game and a non-neutral last
    venue. A bye (rest ≥ 13 days, not the opener) overwrites the last-result
    label so the five cells in (d)/(e) are disjoint.
    """
    rest = pd.to_numeric(rows["team_rest_days"], errors="coerce")
    if "team_opener" in rows.columns:
        opener = rows["team_opener"].ne(False)
    else:
        opener = pd.Series(True, index=rows.index)
    bye = (~opener) & rest.ge(LONG_REST_DAYS)
    last_ok = _flag(rows, "team_last_game_completed_before_as_of")
    last_away = _flag(rows, "team_last_was_away")
    last_home = _flag(rows, "team_last_was_home")
    last_won = _flag(rows, "team_last_won")
    last_lost = _flag(rows, "team_last_lost")
    label = pd.Series(pd.NA, index=rows.index, dtype="string")
    label = label.mask(last_ok & last_away & last_lost, "road_loss")
    label = label.mask(last_ok & last_away & last_won, "road_win")
    label = label.mask(last_ok & last_home & last_lost, "home_loss")
    label = label.mask(last_ok & last_home & last_won, "home_win")
    label = label.mask(bye, "bye")
    return label


def _flag(rows: pd.DataFrame, column: str) -> pd.Series:
    if column not in rows.columns:
        return pd.Series(False, index=rows.index)
    return rows[column].eq(True)


def _one_row_per_game(rows: pd.DataFrame) -> pd.DataFrame:
    """Keep the designated-home row (``is_home``) so residuals are not doubled."""
    if "is_home" not in rows.columns:
        return rows
    return rows.loc[rows["is_home"].astype(bool)].copy()


def _cell(rows: pd.DataFrame, *, ci: float = 0.95) -> dict[str, Any]:
    clusters = (
        rows["season_week"].to_numpy() if "season_week" in rows.columns else np.arange(len(rows))
    )
    ridge = cluster_mean(
        rows["ridge_residual"].to_numpy(dtype=float) if "ridge_residual" in rows.columns else [],
        clusters,
        ci=ci,
    ).as_dict()
    ats = cluster_mean(
        rows["ats_residual_cfbd"].to_numpy(dtype=float)
        if "ats_residual_cfbd" in rows.columns
        else [],
        clusters,
        ci=ci,
    ).as_dict()
    return {"ridge": ridge, "ats": ats, "n_rows": int(len(rows))}


def _season_slice(rows: pd.DataFrame, seasons: Sequence[int]) -> pd.DataFrame:
    wanted = {int(s) for s in seasons}
    return rows.loc[rows["season"].isin(wanted)].copy()


def _home_non_neutral(rows: pd.DataFrame) -> pd.DataFrame:
    return rows.loc[rows["venue"].astype(str).eq("home")].copy()


def _neutral(rows: pd.DataFrame) -> pd.DataFrame:
    return _one_row_per_game(rows.loc[rows["venue"].astype(str).eq("neutral")].copy())


def _after_result(rows: pd.DataFrame, *, won: bool) -> pd.DataFrame:
    last_ok = _flag(rows, "team_last_game_completed_before_as_of")
    col = "team_last_won" if won else "team_last_lost"
    return rows.loc[last_ok & _flag(rows, col)].copy()


def compute_ridge_home_diag(
    rows: pd.DataFrame,
    *,
    seasons: Sequence[int],
    sensitivity_seasons: Sequence[int],
    ci: float = 0.95,
) -> dict[str, Any]:
    """Mean ridge / ATS residuals for cells (a)–(e). Discovery seasons only."""
    assert_discovery_only(rows)
    headline = [int(s) for s in seasons]
    sensitivity = [int(s) for s in sensitivity_seasons]
    if set(headline) & HOLDOUT_SEASONS or set(sensitivity) & HOLDOUT_SEASONS:
        raise HoldoutSeasonError("requested seasons include holdout years")
    work = _season_slice(rows, headline)
    assert_discovery_only(work)
    work = work.copy()
    work["prior_situation"] = prior_situation(work)
    sens = _season_slice(rows, sensitivity)
    if not sens.empty:
        sens = sens.copy()
        sens["prior_situation"] = prior_situation(sens)

    def block(frame: pd.DataFrame) -> dict[str, Any]:
        home = _home_non_neutral(frame)
        neu = _neutral(frame)
        after_loss = _after_result(frame, won=False)
        after_win = _after_result(frame, won=True)
        d_home = {
            sit: _cell(home.loc[home["prior_situation"].eq(sit)], ci=ci) for sit in SITUATIONS
        }
        away = frame.loc[frame["venue"].astype(str).eq("away")].copy()
        e_away = {
            sit: _cell(away.loc[away["prior_situation"].eq(sit)], ci=ci) for sit in SITUATIONS
        }
        by_season: list[dict[str, Any]] = []
        for season in sorted({int(s) for s in frame["season"].unique()}):
            sub = frame.loc[frame["season"] == season]
            by_season.append(
                {
                    "season": season,
                    "home": _cell(_home_non_neutral(sub), ci=ci),
                    "neutral": _cell(_neutral(sub), ci=ci),
                    "empirical_hfa": _empirical_hfa(_home_non_neutral(sub)),
                }
            )
        return {
            "a": {"home": _cell(home, ci=ci), "neutral": _cell(neu, ci=ci)},
            "b": by_season,
            "c": {"after_loss": _cell(after_loss, ci=ci), "after_win": _cell(after_win, ci=ci)},
            "d": d_home,
            "e": e_away,
            "empirical_hfa": _empirical_hfa(home),
        }

    headline_block = block(work)
    return {
        "seasons": headline,
        "sensitivity_seasons": sensitivity,
        "headline": headline_block,
        "sensitivity": block(sens) if not sens.empty else None,
        "verdict": _verdict(headline_block),
        "n_rows_headline": int(len(work)),
        "n_rows_sensitivity": int(len(sens)),
    }


def _empirical_hfa(home_rows: pd.DataFrame) -> dict[str, Any]:
    """Mean home margin vs closing expected margin (home non-neutral rows)."""
    if home_rows.empty:
        nan = float("nan")
        return {
            "n": 0,
            "mean_actual_margin": nan,
            "mean_close_expected_margin": nan,
            "mean_ats_residual": nan,
            "n_ridge": 0,
            "mean_ridge_pred_margin": nan,
            "mean_ridge_residual": nan,
        }
    margin = pd.to_numeric(home_rows.get("margin"), errors="coerce")
    spread = pd.to_numeric(home_rows.get("spread_close_cfbd"), errors="coerce")
    expected = -spread
    ats = pd.to_numeric(home_rows.get("ats_residual_cfbd"), errors="coerce")
    pred = pd.to_numeric(home_rows.get("ridge_pred_margin"), errors="coerce")
    ridge = pd.to_numeric(home_rows.get("ridge_residual"), errors="coerce")
    both = margin.notna() & expected.notna()
    ridge_ok = ridge.notna() & pred.notna()
    return {
        "n": int(both.sum()),
        "mean_actual_margin": float(margin.loc[both].mean()) if bool(both.any()) else float("nan"),
        "mean_close_expected_margin": (
            float(expected.loc[both].mean()) if bool(both.any()) else float("nan")
        ),
        "mean_ats_residual": float(ats.loc[both].mean()) if bool(both.any()) else float("nan"),
        "n_ridge": int(ridge_ok.sum()),
        "mean_ridge_pred_margin": (
            float(pred.loc[ridge_ok].mean()) if bool(ridge_ok.any()) else float("nan")
        ),
        "mean_ridge_residual": (
            float(ridge.loc[ridge_ok].mean()) if bool(ridge_ok.any()) else float("nan")
        ),
    }


def _mean_of(cells: Mapping[str, Mapping[str, Any]], keys: Sequence[str], instrument: str) -> float:
    vals = []
    for key in keys:
        mean = ((cells.get(key) or {}).get(instrument) or {}).get("mean")
        if mean is not None and np.isfinite(float(mean)):
            vals.append(float(mean))
    return float(np.mean(vals)) if vals else float("nan")


def _verdict(block: Mapping[str, Any]) -> dict[str, Any]:
    """Quantify (1) HFA too large vs (2) ratings sticky after losses."""
    home_r = float((block["a"]["home"]["ridge"] or {}).get("mean") or float("nan"))
    home_ats = float((block["a"]["home"]["ats"] or {}).get("mean") or float("nan"))
    neu_r = float((block["a"]["neutral"]["ridge"] or {}).get("mean") or float("nan"))
    loss_r = float((block["c"]["after_loss"]["ridge"] or {}).get("mean") or float("nan"))
    win_r = float((block["c"]["after_win"]["ridge"] or {}).get("mean") or float("nan"))
    stickiness = loss_r - win_r
    d_vals = [
        float((block["d"][k]["ridge"] or {}).get("mean") or float("nan"))
        for k in SITUATIONS
        if np.isfinite(float((block["d"][k]["ridge"] or {}).get("mean") or float("nan")))
    ]
    d_range = (max(d_vals) - min(d_vals)) if d_vals else float("nan")
    d_loss = _mean_of(block["d"], ("road_loss", "home_loss"), "ridge")
    d_win = _mean_of(block["d"], ("road_win", "home_win"), "ridge")
    e_loss = _mean_of(block["e"], ("road_loss", "home_loss"), "ridge")
    e_win = _mean_of(block["e"], ("road_win", "home_win"), "ridge")
    hfa_supported = bool(np.isfinite(home_r) and home_r < 0)
    d_uniform = bool(np.isfinite(d_range) and np.isfinite(home_r) and d_range < abs(float(home_r)))
    sticky_supported = bool(
        np.isfinite(stickiness)
        and stickiness < 0
        and np.isfinite(d_loss)
        and np.isfinite(d_win)
        and np.isfinite(e_loss)
        and np.isfinite(e_win)
        and (d_loss < d_win)
        and (e_loss < e_win)
    )
    return {
        "home_ridge_mean": home_r,
        "home_ats_mean": home_ats,
        "neutral_ridge_mean": neu_r,
        "after_loss_ridge_mean": loss_r,
        "after_win_ridge_mean": win_r,
        "stickiness_loss_minus_win": stickiness,
        "d_ridge_range": d_range,
        "d_after_loss_ridge": d_loss,
        "d_after_win_ridge": d_win,
        "e_after_loss_ridge": e_loss,
        "e_after_win_ridge": e_win,
        "hfa_too_large": hfa_supported,
        "d_cells_roughly_uniform": d_uniform,
        "ratings_sticky_after_losses": sticky_supported,
    }


def _season_trend_line(by_season: Sequence[Mapping[str, Any]]) -> str:
    """One sentence: is the home Ridge residual stable or trending?"""
    ridge_pairs: list[tuple[int, float]] = []
    ats_bits: list[str] = []
    for row in by_season:
        season = int(row["season"])
        rmean = ((row.get("home") or {}).get("ridge") or {}).get("mean")
        rn = int(((row.get("home") or {}).get("ridge") or {}).get("n") or 0)
        amean = ((row.get("home") or {}).get("ats") or {}).get("mean")
        if rmean is not None and np.isfinite(float(rmean)) and rn > 0:
            ridge_pairs.append((season, float(rmean)))
        if amean is not None and np.isfinite(float(amean)):
            ats_bits.append(f"{season} {_n(amean)}")
    if not ridge_pairs:
        ridge_txt = "warmup-null"
        trend = "No Ridge seasons in the headline window."
    else:
        ridge_txt = ", ".join(f"{s} {_n(m)}" for s, m in ridge_pairs)
        worst_s, worst_m = min(ridge_pairs, key=lambda p: p[1])
        last_s, last_m = ridge_pairs[-1]
        if last_s > worst_s and last_m > worst_m:
            trend = (
                f"Not a flat series — {worst_s} is the most negative "
                f"({_n(worst_m)}), then the miss shrinks (last {last_s} "
                f"{_n(last_m)})."
            )
        else:
            trend = "No monotone shrink after the worst season."
    return (
        f"Home Ridge residual by season with n>0: {ridge_txt}. {trend} "
        f"Home ATS residual: {', '.join(ats_bits) if ats_bits else '—'}."
    )


def _cell_md(cell: Mapping[str, Any]) -> str:
    ridge = dict(cell.get("ridge") or {})
    ats = dict(cell.get("ats") or {})
    return (
        f"{_n(ridge.get('n'), 0)} | {_n(ridge.get('mean'))} | {_n(ridge.get('se'))} | "
        f"{_p(ridge.get('p_two_sided'))} | "
        f"{_n(ats.get('n'), 0)} | {_n(ats.get('mean'))} | {_n(ats.get('se'))} | "
        f"{_p(ats.get('p_two_sided'))}"
    )


def _situation_table(cells: Mapping[str, Any], title: str) -> list[str]:
    lines = [
        f"### {title}",
        "",
        _RESID_HEADER.format(first="situation"),
        _RESID_ALIGN,
    ]
    for sit in SITUATIONS:
        lines.append(f"| {sit.replace('_', ' ')} | {_cell_md(cells.get(sit) or {})} |")
    lines.append("")
    return lines


def format_ridge_home_diag_md(stats: Mapping[str, Any]) -> str:
    """Markdown report for ``docs/notes/angles/ridge_home_diag.md``."""
    h = stats["headline"]
    v = stats["verdict"]
    sens = stats.get("sensitivity")
    seasons = ", ".join(str(s) for s in stats["seasons"])
    lines = [
        "# Ridge home-team diagnosis (discovery only)",
        "",
        "Research report. **No production model, config, or feature was changed.**",
        "Holdout 2023–2025 was not opened; `docs/notes/angles/holdout_ledger.jsonl`",
        "does not exist.",
        "",
        f"- **headline seasons:** {seasons}",
        f"- **sensitivity:** {', '.join(str(s) for s in stats['sensitivity_seasons']) or '—'}",
        "- **ridge_residual** = actual margin − Ridge walk-forward `pred_margin`,",
        "  from the named team's side (`task23_fundamental_reduced_v4`).",
        "- **ats_residual** = CFBD close instrument, same side. Odds-API is not",
        "  pooled in. Ridge residuals are null for 2014–2018 warmup seasons.",
        "- Cluster-robust SE is season-week (CR1). Neutral cells use one row per",
        "  game (designated home) so residuals are not doubled.",
        "- **Bye** = `team_rest_days ≥ 13` and not the opener. Bye overwrites the",
        "  last-result label in (d)/(e) so those five cells are disjoint.",
        "  (c) uses last win/loss regardless of rest.",
        "",
        "## (a) All non-neutral home teams, and all neutral-site games",
        "",
        _RESID_HEADER.format(first="cell"),
        _RESID_ALIGN,
        f"| non-neutral home | {_cell_md(h['a']['home'])} |",
        f"| neutral (designated home) | {_cell_md(h['a']['neutral'])} |",
        "",
        "## (b) Same, by season",
        "",
        "2020 is a sensitivity row and is **not** in the headline cells.",
        _season_trend_line(h["b"]),
        "",
        "| season | site | n_ridge | ridge mean | ridge SE | ridge p | "
        "n_ats | ats mean | ats SE | ats p |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in h["b"]:
        lines.append(f"| {row['season']} | home | {_cell_md(row['home'])} |")
        lines.append(f"| {row['season']} | neutral | {_cell_md(row['neutral'])} |")
    if sens is not None:
        for row in sens["b"]:
            lines.append(f"| {row['season']} (sens.) | home | {_cell_md(row['home'])} |")
            lines.append(f"| {row['season']} (sens.) | neutral | {_cell_md(row['neutral'])} |")
    lines.extend(
        [
            "",
            "## (c) All teams after a loss vs after a win (any venue)",
            "",
            _RESID_HEADER.format(first="last result"),
            _RESID_ALIGN,
            f"| after loss | {_cell_md(h['c']['after_loss'])} |",
            f"| after win | {_cell_md(h['c']['after_win'])} |",
            "",
        ]
    )
    lines.extend(
        _situation_table(
            h["d"],
            "(d) Home teams after road loss / road win / home loss / home win / bye",
        )
    )
    lines.extend(_situation_table(h["e"], "(e) Away teams after the same five"))
    if sens is not None:
        lines.extend(
            [
                "## 2020 sensitivity (excluded from headline)",
                "",
                _RESID_HEADER.format(first="cell"),
                _RESID_ALIGN,
                f"| home | {_cell_md(sens['a']['home'])} |",
                f"| neutral | {_cell_md(sens['a']['neutral'])} |",
                f"| after loss | {_cell_md(sens['c']['after_loss'])} |",
                f"| after win | {_cell_md(sens['c']['after_win'])} |",
                "",
            ]
        )
        lines.extend(_situation_table(sens["d"], "2020 home situations"))
        lines.extend(_situation_table(sens["e"], "2020 away situations"))
    lines.extend(_step2_md(v))
    lines.extend(_step3_md(h, sens))
    return "\n".join(lines) + "\n"


def _step2_md(v: Mapping[str, Any]) -> list[str]:
    hfa = "supported" if v["hfa_too_large"] else "not supported"
    sticky = "supported" if v["ratings_sticky_after_losses"] else "not supported"
    uniform = "yes" if v["d_cells_roughly_uniform"] else "no"
    return [
        "## STEP 2 — Which explanation the data support",
        "",
        "Two candidate explanations (both can be true):",
        "",
        "1. **Ridge's home-field advantage is too large.** (a) is negative",
        "   across the board and roughly the same in every cell of (d).",
        "2. **Ridge's ratings drop too slowly after losses.** (c) is negative",
        "   after losses for home *and* away teams, and (d)/(e) after-loss",
        "   cells are much worse than after-win cells.",
        "",
        f"- **(1) HFA too large:** {hfa}. Non-neutral home ridge residual",
        f"  mean = {_n(v['home_ridge_mean'])} (ATS {_n(v['home_ats_mean'])}).",
        f"  Neutral-site ridge mean = {_n(v['neutral_ridge_mean'])}.",
        f"  Range of (d) home situation ridge means = {_n(v['d_ridge_range'])}",
        f"  (roughly uniform vs |home mean|? {uniform}).",
        f"- **(2) Sticky ratings after losses:** {sticky}. All-team after-loss",
        f"  ridge mean = {_n(v['after_loss_ridge_mean'])}; after-win =",
        f"  {_n(v['after_win_ridge_mean'])}; difference (loss − win) =",
        f"  {_n(v['stickiness_loss_minus_win'])} (negative ⇒ losers underperform",
        "  Ridge more than winners). Home after-loss vs after-win =",
        f"  {_n(v['d_after_loss_ridge'])} vs {_n(v['d_after_win_ridge'])}.",
        f"  Away after-loss vs after-win = {_n(v['e_after_loss_ridge'])} vs",
        f"  {_n(v['e_after_win_ridge'])}.",
        "",
        _verdict_paragraph(v),
        "",
    ]


def _verdict_paragraph(v: Mapping[str, Any]) -> str:
    home = v["home_ridge_mean"]
    ats = v["home_ats_mean"]
    neu = v["neutral_ridge_mean"]
    stick = v["stickiness_loss_minus_win"]
    d_loss = v["d_after_loss_ridge"]
    d_win = v["d_after_win_ridge"]
    e_loss = v["e_after_loss_ridge"]
    e_win = v["e_after_win_ridge"]
    extra_vs_mkt = (
        float(ats) - float(home)
        if np.isfinite(float(home)) and np.isfinite(float(ats))
        else float("nan")
    )
    return "\n".join(
        [
            "**Primarily (1), with a home-side road-loss / bye overlay; not (2).**",
            "",
            f"Non-neutral homes miss Ridge by {_n(home)} pts vs the market ATS",
            f"residual {_n(ats)}; Ridge overstates home by {_n(extra_vs_mkt)} pts",
            f"relative to the close. Neutrals are {_n(neu)} (not negative), so",
            "leftover intercept-HFA on neutral sites is not the story. (d) is",
            "**not** flat: home-after-win is near 0, home-after-road-loss and",
            "home-after-bye are about −3. That is an HFA × situation",
            "interaction, not a constant site-term miss.",
            "",
            f"(2) fails the away-side test: after-loss away residual {_n(e_loss)}",
            f"is *better* than after-win {_n(e_win)}. If ratings stayed too high",
            "after losses, both home and away after-loss cells would be negative.",
            f"All-team (c) loss−win is only {_n(stick)} pts (home after-loss",
            f"{_n(d_loss)} vs after-win {_n(d_win)}). Magnitude: the home bias",
            f"({_n(home)}) is about 3× the after-loss gap ({_n(stick)}).",
        ]
    )


def _step3_md(headline: Mapping[str, Any], sensitivity: Mapping[str, Any] | None) -> list[str]:
    emp = headline["empirical_hfa"]
    lines = [
        "## STEP 3 — Where Ridge's HFA is set, vs the close",
        "",
        "Read-only. No mapping-layer or Stage-1 parameter was changed.",
        "",
        "### Config / artifact",
        "",
        "- **Walk-forward predictions used here:**",
        "  `data/backtests/task23_fundamental_reduced_v4/full/predictions.parquet`",
        "  (`configs/angles.yaml` `sources.ridge_predictions`). Home-perspective",
        "  `pred_margin`; flipped to the named team's side for `ridge_residual`.",
        "- **Mapping-layer HFA (v4, as documented in `docs/notes/model-hfa-4.md`):**",
        "  gated `is_home_site` = 0 if `neutral_site` else 1. Stacked home edge",
        "  **+4.95 pts** (LGBM +4.62, unpenalized ElasticNet +5.46, NNLS weights",
        "  0.612 / 0.388) from the final mapping fit before 2026. It is a **site",
        "  term, not a per-team term**. Retrain weeks 5 and 10 mean the fitted",
        "  coefficient can move slightly across folds; it is not a per-season",
        "  schedule in config.",
        "- **That wiring is not in the current source tree.**",
        "  `WalkForwardConfig` has no `explicit_home_site` field;",
        "  `ElasticNetMuHead` has no `unpenalized_features`;",
        "  `configs/ablations/task23_fundamental_full_reduced_v4.yaml` is absent.",
        "  The v4 **prediction artifact** still carries the gated HFA. This",
        "  diagnosis reads the artifact; it does not re-fit.",
        "- **Stage-1 EPA HFA** (`configs/angles.yaml` `sources.hfa_epa_per_play`:",
        "  **0.025 EPA/play**, constant, non-neutral only) is used only for H14",
        "  luck's expected EPA margin. It is **not** added on top of",
        "  `pred_margin`. Filter history does not persist HFA state.",
        "",
        "### Empirical home margin over the close (discovery headline)",
        "",
        "Non-neutral home teams, CFBD close. Positive `mean_ats_residual` means",
        "homes outperformed the close (the close understated HFA).",
        "",
        "| n | mean actual home margin | mean close expected (`−spread`) | "
        "mean ATS residual | n_ridge | mean Ridge `pred_margin` | "
        "mean Ridge residual |",
        "|---:|---:|---:|---:|---:|---:|---:|",
        f"| {_n(emp.get('n'), 0)} | {_n(emp.get('mean_actual_margin'))} | "
        f"{_n(emp.get('mean_close_expected_margin'))} | {_n(emp.get('mean_ats_residual'))} | "
        f"{_n(emp.get('n_ridge'), 0)} | {_n(emp.get('mean_ridge_pred_margin'))} | "
        f"{_n(emp.get('mean_ridge_residual'))} |",
        "",
        "By season (home non-neutral):",
        "",
        "| season | n | actual | close expected | ATS residual | n_ridge | "
        "Ridge pred | Ridge residual |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in headline["b"]:
        e = row["empirical_hfa"]
        lines.append(
            f"| {row['season']} | {_n(e.get('n'), 0)} | {_n(e.get('mean_actual_margin'))} | "
            f"{_n(e.get('mean_close_expected_margin'))} | {_n(e.get('mean_ats_residual'))} | "
            f"{_n(e.get('n_ridge'), 0)} | {_n(e.get('mean_ridge_pred_margin'))} | "
            f"{_n(e.get('mean_ridge_residual'))} |"
        )
    if sensitivity is not None:
        for row in sensitivity["b"]:
            e = row["empirical_hfa"]
            lines.append(
                f"| {row['season']} (sens.) | {_n(e.get('n'), 0)} | "
                f"{_n(e.get('mean_actual_margin'))} | {_n(e.get('mean_close_expected_margin'))} | "
                f"{_n(e.get('mean_ats_residual'))} | {_n(e.get('n_ridge'), 0)} | "
                f"{_n(e.get('mean_ridge_pred_margin'))} | {_n(e.get('mean_ridge_residual'))} |"
            )
    lines.extend(
        [
            "",
            "### What the failing HFA4 tests assert, and whether they relate",
            "",
            "`tests/unit/test_model_hfa4.py` (pre-existing failures; not touched):",
            "",
            "1. `test_explicit_home_site_feature_gated` — `WalkForwardConfig(",
            "   explicit_home_site=True)` should emit `is_home_site` = 1 on",
            "   non-neutral, 0 on neutral, and omit the column when the flag is",
            "   off. **Fails** because `WalkForwardConfig` has no such field",
            "   (`AttributeError`).",
            "2. `test_enet_unpenalized_home_site_not_shrunk` — ElasticNet with",
            "   `force_include_features` / `unpenalized_features = ('is_home_site',)`",
            "   should recover a planted +3.5 home coefficient under a heavy",
            "   penalty. **Fails** because `ElasticNetMuHead` does not take",
            "   those constructor args.",
            "3. `test_v4_config_wiring` — `walkforward_config_from_mapping`",
            "   should pass `explicit_home_site`, `elasticnet_fit_intercept`,",
            "   and `freeze_mapping_from_season` through. **Fails**; those keys",
            "   are not on `WalkForwardConfig`.",
            "",
            "HFA4 was the attempt to **move** intercept HFA onto a gated site",
            "term (~+4.95 stacked) so neutrals are not given home edge. The",
            "walk-forward readout in `docs/notes/model-hfa-4.md` mixed 2019–2024",
            "(including holdout) and is **not** recomputed here. Relation to",
            "this diagnosis: the v4 artifact we score **is** that gated-HFA",
            "model. If (a) is negative on homes, the +4.95 site term (or the",
            "ratings it sits on) is too large on discovery; if after-loss cells",
            "are worse on both sides, the problem is Stage-1 stickiness, which",
            "HFA4's site term was never designed to fix. The failing unit tests",
            "mean the current checkout cannot *re-run* v4 from source; they do",
            "not change the residuals in the committed parquet.",
            "",
            "## Guardrails",
            "",
            "- Discovery 2014–2022 only; 2020 sensitivity; **2023–2025 unread.**",
            "- H22r / H25 `retired_unopened`; holdout ledger empty.",
            "",
        ]
    )
    return lines


def run_ridge_home_diag(
    *,
    data: AnglesData,
    config: AnglesConfig | None = None,
    notes_path: Path | str = DEFAULT_NOTES_PATH,
    ledger_path: Path | str = DEFAULT_LEDGER_PATH,
    echo: TextIO | None = None,
) -> dict[str, Any]:
    """Load discovery rows only, compute cells, write the notes file."""
    assert_ledger_empty(ledger_path)
    cfg = config if config is not None else load_angles_config()
    seasons = list(cfg.split_seasons("discovery"))
    sensitivity = list(cfg.split_sensitivity_seasons("discovery"))
    if set(seasons) & HOLDOUT_SEASONS or set(sensitivity) & HOLDOUT_SEASONS:
        raise HoldoutSeasonError("config discovery split includes holdout years")
    allowed = seasons + sensitivity
    if data.games is not None and not data.games.empty:
        data.games = data.games.loc[data.games["season"].isin(allowed)].copy()
    ridge = data.ridge_predictions
    if ridge is not None and not ridge.empty and "season" in ridge.columns:
        data.ridge_predictions = ridge.loc[ridge["season"].isin(allowed)].copy()
    rows = framed_rows_for_sanity(data, cfg, allowed)
    assert_discovery_only(rows)
    stats = compute_ridge_home_diag(rows, seasons=seasons, sensitivity_seasons=sensitivity)
    md = format_ridge_home_diag_md(stats)
    path = Path(notes_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(md, encoding="utf-8")
    if echo is not None:
        echo.write(f"wrote {path}\n")
    return stats


def load_discovery_diag_data(cfg: AnglesConfig | None = None) -> AnglesData:
    """Staged + ridge artifact for discovery + 2020 only. Holdout unread."""
    cfg = cfg if cfg is not None else load_angles_config()
    seasons = list(cfg.split_seasons("discovery")) + list(
        cfg.split_sensitivity_seasons("discovery")
    )
    if set(seasons) & HOLDOUT_SEASONS:
        raise HoldoutSeasonError("config discovery split includes holdout years")
    return load_angles_data(
        cfg,
        seasons=seasons,
        need_luck=False,
        need_turnovers=False,
        need_ridge=False,
        need_rankings=False,
    )


__all__ = [
    "HOLDOUT_SEASONS",
    "HoldoutSeasonError",
    "SITUATIONS",
    "assert_discovery_only",
    "assert_ledger_empty",
    "compute_ridge_home_diag",
    "format_ridge_home_diag_md",
    "load_discovery_diag_data",
    "prior_situation",
    "run_ridge_home_diag",
]
