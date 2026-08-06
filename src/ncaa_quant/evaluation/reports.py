"""HTML weekly and backtest reports (DESIGN §7.3 / §8 item 6).

Rates are rendered only through :func:`format_rate_with_ci` so a bare win%
cannot appear in a report.
"""

from __future__ import annotations

import base64
import html
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from ncaa_quant.evaluation.metrics import (
    DIAGNOSTIC_LABEL,
    EconomicSimulation,
    MetricSuite,
    SliceTable,
    attach_metric_cis,
    build_slice_table,
    compute_metric_suite,
    reliability_bins,
    simulate_economics,
    su_outcomes,
    weekly_error_curve,
)
from ncaa_quant.evaluation.significance import (
    BareRateError,
    ConfidenceInterval,
    RateWithCI,
    format_interval,
    format_rate_with_ci,
)

_CSS = """
:root {
  --bg: #f7f4ef;
  --ink: #1a1a1a;
  --muted: #5c5c5c;
  --accent: #0b3d2e;
  --line: #d4cfc4;
  --warn: #8a4b08;
  --card: #fffdf8;
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 2rem;
  font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
  color: var(--ink); background: var(--bg); line-height: 1.45;
}
h1, h2, h3 { font-family: "IBM Plex Serif", Georgia, serif; color: var(--accent); }
h1 { font-size: 1.8rem; margin-bottom: 0.25rem; }
h2 { font-size: 1.25rem; border-bottom: 1px solid var(--line); padding-bottom: 0.3rem; }
.meta { color: var(--muted); margin-bottom: 1.5rem; }
.banner {
  background: #fff3cd; border: 1px solid #e0c36a; color: var(--warn);
  padding: 0.6rem 0.8rem; margin: 1rem 0; font-size: 0.95rem;
}
table { border-collapse: collapse; width: 100%; margin: 0.8rem 0 1.4rem; background: var(--card); }
th, td {
  border: 1px solid var(--line); padding: 0.4rem 0.55rem;
  text-align: left; font-size: 0.9rem;
}
th { background: #efeae1; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.fig { margin: 1rem 0 1.5rem; }
.fig img { max-width: 100%; height: auto; border: 1px solid var(--line); background: #fff; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
@media (max-width: 800px) { .grid { grid-template-columns: 1fr; } body { padding: 1rem; } }
"""


class SmokeRunMetricsError(ValueError):
    """Headline metrics refused for a smoke-tagged prediction table."""


class VoidAblationConclusionError(ValueError):
    """Ablation / 'component does not help' conclusion refused for an inert component."""


def assert_not_smoke_for_headline_metrics(predictions: pd.DataFrame) -> None:
    """Refuse to emit headline metrics from a smoke run (D2).

    Smoke configs exist to prove wiring; they must be structurally incapable of
    producing a number that can be quoted.
    """
    if predictions.empty or "run_kind" not in predictions.columns:
        return
    kinds = {str(k) for k in predictions["run_kind"].dropna().unique()}
    if "smoke" in kinds:
        raise SmokeRunMetricsError(
            "refusing headline metrics from a smoke-tagged prediction table "
            f"(run_kind={sorted(kinds)}); smoke configs prove wiring only and "
            "must not produce quotable numbers"
        )


def assert_component_varies_before_conclusion(
    component: np.ndarray | pd.Series,
    *,
    component_name: str,
    conclusion: str,
    min_span: float = 1e-12,
) -> None:
    """Standing rule (D4): no ablation / 'X does not help' without X varying.

    Void conclusions (component inert → 'does not help') are worse than absent
    ones. Call this before emitting any such sentence in a report or note.
    """
    arr = np.asarray(
        pd.to_numeric(pd.Series(component), errors="coerce").to_numpy(dtype=float),
        dtype=float,
    )
    finite = arr[np.isfinite(arr)]
    if finite.size < 2:
        raise VoidAblationConclusionError(
            f"refusing conclusion about {component_name!r}: "
            f"need ≥2 finite values to show variance (got {finite.size}); "
            f"void conclusion blocked: {conclusion!r}"
        )
    span = float(np.nanmax(finite) - np.nanmin(finite))
    if span < float(min_span):
        raise VoidAblationConclusionError(
            f"refusing conclusion about {component_name!r}: output is constant "
            f"(span={span:.3g}); component must vary before any "
            f"'does not help' / ablation-null claim. Blocked: {conclusion!r}"
        )


@dataclass
class WeeklyReportInput:
    """Inputs for the weekly operator report."""

    season: int
    week: int
    predictions: pd.DataFrame
    edges: pd.DataFrame | None = None
    rating_movements: pd.DataFrame | None = None
    model_version: str = ""
    notes: str = ""


@dataclass
class BacktestReportInput:
    """Inputs for the full-season (or multi-season) backtest HTML report."""

    season: int
    predictions: pd.DataFrame
    bets: pd.DataFrame | None = None
    shap_summary: pd.DataFrame | None = None
    """Precomputed SHAP importance table (feature, mean_abs_shap, …)."""

    model_version: str = ""
    title: str = ""
    n_boot: int = 500
    seed: int = 0
    initial_bankroll: float = 100.0


@dataclass
class BacktestArtifacts:
    """Computed objects underlying a backtest report."""

    suite: MetricSuite
    cis: dict[str, ConfidenceInterval | RateWithCI]
    slices: SliceTable
    weekly_curve: pd.DataFrame
    economics: EconomicSimulation | None
    reliability_ml: pd.DataFrame
    extras: dict[str, Any] = field(default_factory=dict)


def _esc(value: Any) -> str:
    return html.escape(str(value))


def _fmt_float(value: float, digits: int = 4) -> str:
    if value is None or not np.isfinite(value):
        return "—"
    return f"{value:.{digits}f}"


def _df_to_html(frame: pd.DataFrame, *, float_digits: int = 4) -> str:
    if frame is None or len(frame) == 0:
        return "<p><em>No rows.</em></p>"
    cols = list(frame.columns)
    head = "".join(f"<th>{_esc(c)}</th>" for c in cols)
    body_rows: list[str] = []
    for _, row in frame.iterrows():
        cells: list[str] = []
        for c in cols:
            v = row[c]
            if isinstance(v, (float, np.floating)):
                cells.append(f'<td class="num">{_fmt_float(float(v), float_digits)}</td>')
            elif isinstance(v, (int, np.integer)) and not isinstance(v, bool):
                cells.append(f'<td class="num">{int(v)}</td>')
            else:
                cells.append(f"<td>{_esc(v)}</td>")
        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"


def _fig_to_data_uri(fig: Any) -> str:
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
    plt.close(fig)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _reliability_figure(curve: pd.DataFrame, title: str) -> str:
    fig, ax = plt.subplots(figsize=(4.5, 4.5))
    ax.plot([0, 1], [0, 1], ls="--", color="gray", label="ideal")
    if curve is not None and len(curve):
        ax.plot(
            curve["mean_pred"], curve["mean_outcome"], marker="o", color="#0b3d2e", label="model"
        )
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_xlabel("Predicted probability")
    ax.set_ylabel("Observed frequency")
    ax.set_title(title)
    ax.legend(loc="upper left")
    fig.tight_layout()
    return _fig_to_data_uri(fig)


def _pit_figure(pit: np.ndarray, title: str) -> str:
    fig, ax = plt.subplots(figsize=(4.5, 3.5))
    if pit.size:
        ax.hist(pit, bins=10, range=(0, 1), density=True, color="#3d6b5a", edgecolor="white")
    ax.axhline(1.0, color="gray", ls="--", label="uniform")
    ax.set_xlabel("PIT")
    ax.set_ylabel("Density")
    ax.set_title(title)
    ax.legend(loc="upper right")
    fig.tight_layout()
    return _fig_to_data_uri(fig)


def _weekly_error_figure(curve: pd.DataFrame, title: str) -> str:
    fig, ax = plt.subplots(figsize=(6, 3.5))
    if curve is not None and len(curve):
        ax.plot(curve["week"], curve["mae"], marker="o", color="#0b3d2e")
    ax.set_xlabel("Week")
    ax.set_ylabel("MAE")
    ax.set_title(title)
    fig.tight_layout()
    return _fig_to_data_uri(fig)


def _equity_figure(econ: EconomicSimulation, title: str) -> str:
    fig, ax = plt.subplots(figsize=(6.5, 3.8))
    for path, color, label in (
        (econ.flat, "#666666", "flat"),
        (econ.quarter_kelly, "#0b3d2e", "¼-Kelly"),
        (econ.half_kelly, "#8a4b08", "½-Kelly"),
    ):
        x = np.arange(path.bankroll.size)
        ax.plot(x, path.bankroll, color=color, label=label)
    ax.set_xlabel("Bet index")
    ax.set_ylabel("Bankroll")
    ax.set_title(title)
    ax.legend(loc="best")
    fig.tight_layout()
    return _fig_to_data_uri(fig)


def _wrap_html(title: str, body: str) -> str:
    return (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'/>"
        f"<title>{_esc(title)}</title><style>{_CSS}</style></head>"
        f"<body>{body}</body></html>"
    )


def _metric_table_html(
    suite: MetricSuite,
    cis: dict[str, ConfidenceInterval | RateWithCI],
) -> str:
    rows = suite.to_rows()
    head = (
        "<tr><th>Tier</th><th>Metric</th><th>Model</th><th>Market</th>"
        "<th>Δ</th><th>With CI</th><th>Notes</th></tr>"
    )
    body: list[str] = []
    for r in rows:
        name = str(r["metric"])
        ci_cell = "—"
        if name in cis:
            obj = cis[name]
            try:
                if isinstance(obj, RateWithCI):
                    ci_cell = format_rate_with_ci(obj, digits=1, as_percent=True)
                else:
                    ci_cell = format_interval(obj, digits=4, label="")
            except BareRateError as exc:
                ci_cell = f"ERROR: {_esc(exc)}"
        body.append(
            "<tr>"
            f"<td class='num'>{r['tier']}</td>"
            f"<td>{_esc(name)}</td>"
            f"<td class='num'>{_fmt_float(float(r['model']))}</td>"
            f"<td class='num'>{_fmt_float(float(r['market']))}</td>"
            f"<td class='num'>{_fmt_float(float(r['delta']))}</td>"
            f"<td>{_esc(ci_cell)}</td>"
            f"<td>{_esc(r['notes'])}</td>"
            "</tr>"
        )
    return f"<table><thead>{head}</thead><tbody>{''.join(body)}</tbody></table>"


def render_weekly_report(data: WeeklyReportInput) -> str:
    """HTML weekly report: predictions, edges, confidence, rating movements."""
    pred = data.predictions
    title = f"Week {data.week} · Season {data.season}"
    parts: list[str] = [
        f"<h1>{_esc(title)}</h1>",
        f"<p class='meta'>model={_esc(data.model_version)} · n_games={len(pred)}</p>",
    ]
    if data.notes:
        parts.append(f"<p>{_esc(data.notes)}</p>")

    show_cols = [
        c
        for c in (
            "game_id",
            "home_team_id",
            "away_team_id",
            "pred_margin",
            "pred_total",
            "sigma_m",
            "sigma_t",
            "p_ml_home",
            "p_ats_home",
            "p_ou_over",
            "spread_asof",
            "total_asof",
        )
        if c in pred.columns
    ]
    parts.append("<h2>Predictions</h2>")
    parts.append(_df_to_html(pred[show_cols] if show_cols else pred.head(50)))

    if data.edges is not None and len(data.edges):
        parts.append("<h2>Edges</h2>")
        parts.append(_df_to_html(data.edges))
        if "confidence" in data.edges.columns:
            parts.append("<h2>Confidence</h2>")
            conf_cols = ["game_id", "side", "confidence"]
            conf_frame = (
                data.edges[conf_cols] if set(conf_cols) <= set(data.edges.columns) else data.edges
            )
            parts.append(_df_to_html(conf_frame))

    if data.rating_movements is not None and len(data.rating_movements):
        parts.append("<h2>Rating movements</h2>")
        parts.append(_df_to_html(data.rating_movements))

    return _wrap_html(title, "\n".join(parts))


def build_backtest_artifacts(data: BacktestReportInput) -> BacktestArtifacts:
    """Compute suite, CIs, slices, curves, and economics for a backtest report."""
    assert_not_smoke_for_headline_metrics(data.predictions)
    suite = compute_metric_suite(data.predictions, bets=data.bets)
    cis = attach_metric_cis(
        suite,
        data.predictions,
        bets=data.bets,
        n_boot=data.n_boot,
        seed=data.seed,
    )
    slices = build_slice_table(data.predictions)
    weekly = weekly_error_curve(data.predictions, target="margin")
    econ: EconomicSimulation | None = None
    if (
        data.bets is not None
        and len(data.bets)
        and {"won", "american_odds", "week"} <= set(data.bets.columns)
    ):
        econ = simulate_economics(
            data.bets,
            initial_bankroll=data.initial_bankroll,
            n_boot=data.n_boot,
            seed=data.seed,
        )

    rel = pd.DataFrame()
    if {"p_ml_home", "home_points", "away_points"} <= set(data.predictions.columns):
        y = su_outcomes(
            data.predictions["home_points"].to_numpy(),
            data.predictions["away_points"].to_numpy(),
        )
        rel = reliability_bins(data.predictions["p_ml_home"].to_numpy(), y)

    return BacktestArtifacts(
        suite=suite,
        cis=cis,
        slices=slices,
        weekly_curve=weekly,
        economics=econ,
        reliability_ml=rel,
    )


def render_backtest_report(
    data: BacktestReportInput,
    *,
    artifacts: BacktestArtifacts | None = None,
) -> str:
    """HTML backtest report with metric CIs, plots, slices, equity, SHAP."""
    art = artifacts if artifacts is not None else build_backtest_artifacts(data)
    title = data.title or f"Backtest report · Season {data.season}"
    parts: list[str] = [
        f"<h1>{_esc(title)}</h1>",
        (
            f"<p class='meta'>model={_esc(data.model_version)} · "
            f"n_games={art.suite.n_games} · n_boot={data.n_boot}</p>"
        ),
        "<h2>Metric tables (with CIs)</h2>",
        "<p>Probabilistic metrics include the de-vigged market baseline.</p>",
        _metric_table_html(art.suite, art.cis),
    ]

    # Anti-metric showcase: ATS rate only via RateWithCI
    if "ats_accuracy" in art.cis and isinstance(art.cis["ats_accuracy"], RateWithCI):
        parts.append("<h2>Rates (anti-metric rule)</h2>")
        parts.append(f"<p>{_esc(format_rate_with_ci(art.cis['ats_accuracy']))}</p>")
        if "pct_positive_clv" in art.cis and isinstance(art.cis["pct_positive_clv"], RateWithCI):
            parts.append(f"<p>{_esc(format_rate_with_ci(art.cis['pct_positive_clv']))}</p>")

    parts.append("<h2>Reliability &amp; PIT</h2>")
    parts.append('<div class="grid">')
    rel_uri = _reliability_figure(art.reliability_ml, "ML reliability")
    pit_uri = _pit_figure(art.suite.pit_margin, "Margin PIT")
    parts.append(f"<div class='fig'><img alt='reliability' src='{rel_uri}'/></div>")
    parts.append(f"<div class='fig'><img alt='pit' src='{pit_uri}'/></div>")
    parts.append("</div>")

    parts.append("<h2>Weekly error curve</h2>")
    week_uri = _weekly_error_figure(art.weekly_curve, "Margin MAE by week")
    parts.append(f"<div class='fig'><img alt='weekly' src='{week_uri}'/></div>")
    parts.append(_df_to_html(art.weekly_curve))

    parts.append("<h2>Slice analysis</h2>")
    parts.append(f"<div class='banner'>{_esc(DIAGNOSTIC_LABEL)}</div>")
    parts.append(f"<p class='meta'>{_esc(art.slices.label)}</p>")
    parts.append(_df_to_html(art.slices.table))

    if art.economics is not None:
        parts.append("<h2>Economic simulation / equity curves</h2>")
        eq_uri = _equity_figure(art.economics, "Bankroll paths")
        parts.append(f"<div class='fig'><img alt='equity' src='{eq_uri}'/></div>")
        econ_rows = pd.DataFrame(
            [
                {
                    "policy": "flat",
                    "roi": art.economics.flat.roi,
                    "roi_ci": format_interval(art.economics.roi_ci_flat, digits=3),
                    "max_dd": art.economics.flat.max_drawdown,
                    "max_dd_ci": format_interval(art.economics.max_drawdown_ci_flat, digits=3),
                    "sharpe_per_bet": art.economics.flat.sharpe_per_bet,
                },
                {
                    "policy": "quarter_kelly",
                    "roi": art.economics.quarter_kelly.roi,
                    "roi_ci": format_interval(art.economics.roi_ci_quarter, digits=3),
                    "max_dd": art.economics.quarter_kelly.max_drawdown,
                    "max_dd_ci": format_interval(art.economics.max_drawdown_ci_quarter, digits=3),
                    "sharpe_per_bet": art.economics.quarter_kelly.sharpe_per_bet,
                },
                {
                    "policy": "half_kelly",
                    "roi": art.economics.half_kelly.roi,
                    "roi_ci": format_interval(art.economics.roi_ci_half, digits=3),
                    "max_dd": art.economics.half_kelly.max_drawdown,
                    "max_dd_ci": format_interval(art.economics.max_drawdown_ci_half, digits=3),
                    "sharpe_per_bet": art.economics.half_kelly.sharpe_per_bet,
                },
            ]
        )
        parts.append(_df_to_html(econ_rows, float_digits=4))

    parts.append("<h2>SHAP summaries</h2>")
    if data.shap_summary is not None and len(data.shap_summary):
        parts.append(_df_to_html(data.shap_summary))
    else:
        parts.append(
            "<p><em>No SHAP summary provided "
            "(pass a precomputed feature-importance table).</em></p>"
        )

    return _wrap_html(title, "\n".join(parts))


def write_weekly_report(data: WeeklyReportInput, path: Path | str) -> Path:
    """Write weekly HTML report to ``path``."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_weekly_report(data), encoding="utf-8")
    return out


def write_backtest_report(data: BacktestReportInput, path: Path | str) -> Path:
    """Write backtest HTML report to ``path``."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_backtest_report(data), encoding="utf-8")
    return out
