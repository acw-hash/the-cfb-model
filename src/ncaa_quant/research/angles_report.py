"""Markdown reports for the situational-angle harness."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pandas as pd  # type: ignore[import-untyped]

from ncaa_quant.research.angles import AnglesConfig, Hypothesis
from ncaa_quant.research.angles_analysis import DefinitionResult
from ncaa_quant.research.angles_ledger import LedgerEntry
from ncaa_quant.research.angles_stats import BhDecision


def _n(v: Any, digits: int = 3) -> str:
    if v is None:
        return "—"
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    if not math.isfinite(x):
        return "—"
    if abs(x - round(x)) < 1e-12 and abs(x) >= 1:
        return str(int(round(x)))
    return f"{x:.{digits}f}"


def _pct(v: Any) -> str:
    if v is None:
        return "—"
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(x):
        return "—"
    return f"{100.0 * x:.1f}%"


def _p(v: Any) -> str:
    if v is None:
        return "—"
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(x):
        return "—"
    if x < 0.0001:
        return "<0.0001"
    return f"{x:.4f}"


def _conditions_block(conditions: Mapping[str, Any]) -> str:
    if not conditions:
        return "_none_"
    lines = [f"- `{k}`: `{v}`" for k, v in conditions.items()]
    return "\n".join(lines)


def _mean_block(block: Mapping[str, Any] | None, label: str) -> list[str]:
    b = dict(block or {})
    return [
        f"- **{label}** n={_n(b.get('n'), 0)} clusters={_n(b.get('n_clusters'), 0)} "
        f"mean={_n(b.get('mean'))} SE={_n(b.get('se'))} "
        f"95% [{_n(b.get('ci_lo'))}, {_n(b.get('ci_hi'))}] "
        f"p={_p(b.get('p_two_sided'))}"
    ]


def _cover_block(block: Mapping[str, Any] | None) -> list[str]:
    b = dict(block or {})
    return [
        f"- **cover rate** n={_n(b.get('n'), 0)} rate={_pct(b.get('rate'))} "
        f"block-bootstrap 95% [{_pct(b.get('ci_lo'))}, {_pct(b.get('ci_hi'))}] "
        f"(reps={_n(b.get('reps'), 0)}, seed={_n(b.get('seed'), 0)})"
    ]


def _ols_term(block: Mapping[str, Any] | None, name: str) -> str:
    if not block:
        return "—"
    for term in block.get("terms") or []:
        if term.get("name") == name:
            return (
                f"coef={_n(term.get('coef'))} SE={_n(term.get('se'))} "
                f"t={_n(term.get('t'))} p={_p(term.get('p'))} "
                f"(n={_n(block.get('n'), 0)}, clusters={_n(block.get('n_clusters'), 0)})"
            )
    return "—"


def _instrument_md(inst: Mapping[str, Any]) -> list[str]:
    lines = [
        f"### Instrument: `{inst.get('line_source_label')}`",
        "",
        f"Seasons with lines: {inst.get('seasons_with_lines') or []}",
        f"n qualifying={_n(inst.get('n_qualifying'), 0)}  "
        f"n baseline={_n(inst.get('n_baseline'), 0)}  "
        f"n field={_n(inst.get('n_field'), 0)}  "
        f"tested={inst.get('tested')}",
        "",
        "**Headline (qualifying)**",
        "",
        *_mean_block(inst.get("mean_ats_residual"), "mean ATS residual (points)"),
        *_cover_block(inst.get("cover")),
        "",
        "**Baseline group**",
        "",
        *_mean_block(inst.get("baseline_mean_ats_residual"), "mean ATS residual"),
        *_cover_block(inst.get("baseline_cover")),
        "",
        (
            "**Regression vs baseline** `qualifies` term: "
            + _ols_term(inst.get("regression_vs_baseline"), "qualifies")
        ),
        (
            "**Regression vs field** `qualifies` term: "
            + _ols_term(inst.get("regression_vs_field"), "qualifies")
        ),
        "",
        "Controls: spread + venue (home/neutral vs away) + conference_game + week bucket.",
        "",
        "**Spread buckets (angle side)**",
        "",
        "| bucket | n | cover | mean ATS residual | SE |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in inst.get("spread_buckets") or []:
        lines.append(
            f"| {row.get('bucket')} | {_n(row.get('n'), 0)} | {_pct(row.get('cover_rate'))} "
            f"| {_n(row.get('mean_ats_residual'))} | {_n(row.get('se'))} |"
        )
    lines.extend(
        [
            "",
            "**Per-season sign table**",
            "",
            "| season | n | mean | sign | cover |",
            "|---|---:|---:|:---:|---:|",
        ]
    )
    for row in inst.get("sign_table") or []:
        lines.append(
            f"| {row.get('season')} | {_n(row.get('n'), 0)} | {_n(row.get('mean'))} "
            f"| {row.get('sign') or '—'} | {_pct(row.get('cover_rate'))} |"
        )
    sens = inst.get("sensitivity") or {}
    lines.extend(
        [
            "",
            (
                f"**2020 sensitivity** seasons={sens.get('seasons')} "
                f"n={_n(sens.get('n_qualifying'), 0)}"
            ),
            "",
            *_mean_block(sens.get("mean_ats_residual"), "mean ATS residual"),
            *_cover_block(sens.get("cover")),
        ]
    )
    trend = inst.get("trend")
    if trend:
        slope = trend.get("slope") or {}
        lines.extend(
            [
                "",
                f"**Linear trend** on `{trend.get('regressor')}` (cap={trend.get('cap')})",
                f"slope={_n(slope.get('coef'))} SE={_n(slope.get('se'))} p={_p(slope.get('p'))} "
                f"n={_n(trend.get('n'), 0)}",
                "",
                "| streak bucket | n | mean ATS residual | SE | cover |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for row in trend.get("buckets") or []:
            lines.append(
                f"| {row.get('bucket')} | {_n(row.get('n'), 0)} | "
                f"{_n(row.get('mean_ats_residual'))} | {_n(row.get('se'))} | "
                f"{_pct(row.get('cover_rate'))} |"
            )
    cregr = inst.get("continuous_regression")
    if cregr:
        simple = cregr.get("slope_simple") or {}
        ctrl = cregr.get("slope_with_controls") or {}
        lines.extend(
            [
                "",
                (
                    f"**Continuous regression** `{cregr.get('regressor')}` "
                    f"per {cregr.get('scale_per_points')} pts"
                ),
                f"simple slope={_n(simple.get('coef'))} p={_p(simple.get('p'))}; "
                f"with controls slope={_n(ctrl.get('coef'))} p={_p(ctrl.get('p'))} "
                f"n={_n(cregr.get('n'), 0)}",
            ]
        )
    move = inst.get("line_movement")
    if move:
        lines.extend(
            [
                "",
                "**Tuesday → close line movement toward the angle** "
                "(Odds API, 2021+; never pooled with CFBD)",
                (
                    f"n={_n(move.get('n'), 0)} mean move (pts)="
                    f"{_n(move.get('mean_move_toward_angle_pts'))} "
                    f"SE={_n(move.get('se'))} p={_p(move.get('p_two_sided'))}"
                ),
                (
                    f"share toward={_pct(move.get('share_toward'))} "
                    f"away={_pct(move.get('share_away'))} "
                    f"unchanged={_pct(move.get('share_unchanged'))} "
                    f"seasons={move.get('seasons')}"
                ),
            ]
        )
    lines.append("")
    return lines


def _ridge_md(ridge: Mapping[str, Any]) -> list[str]:
    lines = [
        "### Ridge walk-forward residual",
        "",
        f"Seasons with predictions: {ridge.get('seasons_with_predictions') or []}",
        f"n qualifying={_n(ridge.get('n_qualifying'), 0)}",
        "",
        *_mean_block(ridge.get("mean_ridge_residual"), "mean Ridge residual (points)"),
        *_mean_block(ridge.get("baseline_mean_ridge_residual"), "baseline mean Ridge residual"),
        "",
        "| season | n | mean | sign |",
        "|---|---:|---:|:---:|",
    ]
    for row in ridge.get("sign_table") or []:
        lines.append(
            f"| {row.get('season')} | {_n(row.get('n'), 0)} | "
            f"{_n(row.get('mean'))} | {row.get('sign') or '—'} |"
        )
    sens = ridge.get("sensitivity") or {}
    lines.extend(
        [
            "",
            f"**2020 sensitivity** n={_n(sens.get('n_qualifying'), 0)}",
            *_mean_block(sens.get("mean_ridge_residual"), "mean Ridge residual"),
            "",
        ]
    )
    return lines


def write_hypothesis_report(
    path: Path | str,
    *,
    cfg: AnglesConfig,
    hypothesis: Hypothesis,
    split: str,
    primary: DefinitionResult,
    variants: Sequence[DefinitionResult],
    bh: BhDecision | None,
    holdout: LedgerEntry | None,
    counts: pd.DataFrame,
) -> Path:
    """Write ``docs/notes/angles/<id>.md``."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    hid = hypothesis.id
    hhash = cfg.hypothesis_hash(hid)
    lines: list[str] = [
        f"# {hid} — {hypothesis.name}",
        "",
        (
            f"- **split:** `{split}` seasons={list(primary.seasons)} "
            f"sensitivity={list(primary.sensitivity_seasons)}"
        ),
        f"- **side:** `{hypothesis.side}`  **market:** `{hypothesis.market}`",
        f"- **config hash (locked):** `{hhash}`",
        f"- **source config:** `{cfg.source_path}` sha `{cfg.source_sha256}`",
        f"- **primary statistic:** `{primary.statistic}` direction=`{primary.direction}`",
        f"- **both-qualify games dropped:** {primary.n_both_qualify_games}",
        "",
        "## Pre-registered primary definition",
        "",
        _conditions_block(hypothesis.primary.conditions),
        "",
        "## Baseline group",
        "",
        hypothesis.baseline.description or "_unnamed_",
        "",
        _conditions_block(hypothesis.baseline.conditions),
        "",
        "## Qualifying counts (printed before outcomes were attached)",
        "",
        "| season | n_qualifies | n_baseline | n_games |",
        "|---|---:|---:|---:|",
    ]
    for r in counts.itertuples(index=False):
        lines.append(
            f"| {int(r.season)} | {int(r.n_qualifies)} | {int(r.n_baseline)} | {int(r.n_games)} |"
        )
    if bh is not None:
        lines.extend(
            [
                "",
                "## Multiple testing (Benjamini–Hochberg, primary / discovery / CFBD)",
                "",
                (
                    f"- p={_p(bh.p)}  rank={bh.rank}  "
                    f"threshold={_n(bh.threshold, 4)}  "
                    f"p_adj={_p(bh.p_adjusted)}  **rejected={bh.rejected}** "
                    f"(q={cfg.multiple_testing.get('q')})"
                ),
            ]
        )
    elif not hypothesis.include_in_bh:
        lines.extend(
            [
                "",
                "## Multiple testing",
                "",
                "Excluded from the cumulative BH family. **motivated by discovery; not evidence.**",
            ]
        )
    else:
        lines.extend(["", "## Multiple testing", "", "BH is applied on the discovery split only."])
    if holdout is not None:
        lines.extend(
            [
                "",
                "## Holdout ledger",
                "",
                (
                    f"- opened_at `{holdout.opened_at}` git `{holdout.git_sha}` "
                    f"hash `{holdout.config_hash}`"
                ),
                f"- forced={holdout.forced} override_kind={holdout.override_kind} "
                f"n_prior_opens={holdout.n_prior_opens} reason={holdout.reason!r}",
            ]
        )
    if primary.notes:
        lines.extend(["", "## Notes", "", *[f"- {n}" for n in primary.notes]])
    lines.extend(["", "## Primary results (instruments never pooled)", ""])
    for key in ("cfbd", "odds"):
        if key in primary.by_instrument:
            lines.extend(_instrument_md(primary.by_instrument[key]))
    lines.extend(_ridge_md(primary.ridge))
    lines.extend(
        [
            "## Exploratory variants (excluded from BH)",
            "",
        ]
    )
    if not variants:
        lines.append("_none_")
    for var in variants:
        lines.extend(
            [
                f"### `{var.definition_id}` — exploratory",
                "",
                f"statistic=`{var.statistic}` estimate={_n(var.primary_estimate)} "
                f"p={_p(var.primary_p)} (not in BH)",
                f"n both-qualify dropped={var.n_both_qualify_games}",
                "",
            ]
        )
        cfbd = var.by_instrument.get("cfbd") or {}
        lines.extend(_mean_block(cfbd.get("mean_ats_residual"), "mean ATS residual (CFBD close)"))
        lines.extend(_cover_block(cfbd.get("cover")))
        lines.append("")
    lines.extend(
        [
            "## Line-source regime",
            "",
            "CFBD-close and Odds-API-close are different measurement instruments "
            "(DESIGN §7.2 item 8 / ADR 0002). Numbers above are never pooled. "
            "Tuesday 06:00 ET snapshot lines exist only for 2021+; missing snapshots "
            "stay null (CFBD open/close is never substituted).",
            "",
        ]
    )
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def patch_hypothesis_bh(path: Path | str, bh: BhDecision, *, q: float) -> Path:
    """Rewrite the BH paragraph of an existing hypothesis report (cumulative family)."""
    out = Path(path)
    text = out.read_text(encoding="utf-8")
    line = (
        f"- p={_p(bh.p)}  rank={bh.rank}  "
        f"threshold={_n(bh.threshold, 4)}  "
        f"p_adj={_p(bh.p_adjusted)}  **rejected={bh.rejected}** "
        f"(q={q}; cumulative family)"
    )
    marker = "- p="
    lines = text.splitlines()
    replaced = False
    for i, existing in enumerate(lines):
        if existing.startswith(marker) and "p_adj=" in existing:
            lines[i] = line
            replaced = True
            break
    if not replaced:
        lines.extend(["", "## Multiple testing (updated, cumulative BH)", "", line])
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def write_summary_report(
    path: Path | str,
    *,
    cfg: AnglesConfig,
    split: str,
    ids: Sequence[str],
    primaries: Mapping[str, DefinitionResult],
    variants: Mapping[str, Sequence[DefinitionResult]],
    bh: Mapping[str, BhDecision],
    holdout_entries: Mapping[str, LedgerEntry],
    notes: Sequence[str],
    family: Mapping[str, Mapping[str, Any]] | None = None,
) -> Path:
    """Write the cross-hypothesis summary table (includes failures)."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    q = cfg.multiple_testing.get("q")
    lines: list[str] = [
        f"# Situational angles — {split} summary",
        "",
        f"Pre-registered config `{cfg.source_path}` sha `{cfg.source_sha256}`. "
        f"BH at q={q} across **every primary ever run on discovery** (cumulative family). "
        "Variants are exploratory. Holdout was not part of this table unless listed.",
        "",
        "| id | name | n | estimate | p | p_adj | BH reject | sign vs registered | notes |",
        "|---|---|---:|---:|---:|---:|:---:|:---:|---|",
    ]
    family = dict(family or {})
    for hid in ids:
        prim = primaries.get(hid)
        rec = family.get(hid) or {}
        hyp_name = (
            cfg.hypotheses[hid].name if hid in cfg.hypotheses else str(rec.get("name") or hid)
        )
        decision = bh.get(hid)
        p_adj = decision.p_adjusted if decision is not None else float("nan")
        rejected = decision.rejected if decision is not None else False
        if prim is not None:
            estimate = prim.primary_estimate
            p_val = prim.primary_p
            n_q = prim.by_instrument.get("cfbd", {}).get("n_qualifying")
            direction = prim.direction
            extra_notes = list(prim.notes or [])
        else:
            estimate = float(rec.get("estimate", float("nan")))
            p_val = float(rec.get("p", float("nan")))
            n_q = rec.get("n")
            direction = "positive"
            extra_notes = [f"from {rec.get('source', 'prior discovery')} (not recomputed this run)"]
        sign_note = ""
        if direction and math.isfinite(float(estimate)):
            want_pos = direction == "positive"
            ok = (float(estimate) > 0) == want_pos
            sign_note = "ok" if ok else "DISAGREES"
        failed = "failed" if (decision is not None and not rejected) else ""
        passed = "passed" if (decision is not None and rejected) else ""
        note = "; ".join([*extra_notes, failed or passed]) or "—"
        lines.append(
            f"| {hid} | {hyp_name} | "
            f"{_n(n_q, 0)} "
            f"| {_n(estimate)} | {_p(p_val)} | {_p(p_adj)} "
            f"| {rejected} | {sign_note or '—'} | {note} |"
        )
    lines.extend(["", "## Variants (exploratory, not in BH)", ""])
    for hid in list(ids) + [k for k in variants if k not in set(ids)]:
        for var in variants.get(hid) or []:
            lines.append(
                f"- `{var.definition_id}` estimate={_n(var.primary_estimate)} "
                f"p={_p(var.primary_p)} "
                f"n={_n(var.by_instrument.get('cfbd', {}).get('n_qualifying'), 0)}"
            )
    if holdout_entries:
        lines.extend(["", "## Holdout ledger entries this run", ""])
        for hid, entry in holdout_entries.items():
            lines.append(
                f"- {hid}: opened_at={entry.opened_at} hash={entry.config_hash} "
                f"forced={entry.forced} override={entry.override_kind}"
            )
    else:
        lines.extend(["", "Holdout was **not** opened this run.", ""])
    if notes:
        lines.extend(["", "## Run notes", "", *[f"- {n}" for n in notes]])
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def append_descriptive_summary(
    path: Path | str,
    *,
    cfg: AnglesConfig,
    ids: Sequence[str],
    primaries: Mapping[str, DefinitionResult],
    variants: Mapping[str, Sequence[DefinitionResult]],
) -> Path:
    """Append descriptive (non-BH) rows without rewriting the BH table."""
    out = Path(path)
    existing = out.read_text(encoding="utf-8") if out.is_file() else ""
    marker = "## Descriptive (excluded from BH)"
    block: list[str] = [
        marker,
        "",
        "These primaries were run on discovery as description only. "
        "They are **motivated by discovery; not evidence** and are not in the "
        "cumulative BH family.",
        "",
        "| id | name | n | estimate | p | notes |",
        "|---|---|---:|---:|---:|---|",
    ]
    for hid in ids:
        prim = primaries.get(hid)
        if prim is None:
            continue
        n_q = prim.by_instrument.get("cfbd", {}).get("n_qualifying")
        extra = "; ".join(prim.notes or []) or "—"
        name = cfg.hypotheses[hid].name if hid in cfg.hypotheses else hid
        block.append(
            f"| {hid} | {name} | {_n(n_q, 0)} | {_n(prim.primary_estimate)} "
            f"| {_p(prim.primary_p)} | {extra} |"
        )
    block.append("")
    for hid in ids:
        for var in variants.get(hid) or []:
            block.append(
                f"- `{var.definition_id}` estimate={_n(var.primary_estimate)} "
                f"p={_p(var.primary_p)} "
                f"n={_n(var.by_instrument.get('cfbd', {}).get('n_qualifying'), 0)}"
            )
    if block[-1] != "":
        block.append("")
    text = existing
    if marker in existing:
        pre = existing.split(marker)[0].rstrip()
        text = pre + "\n\n"
    elif existing:
        text = existing.rstrip() + "\n\n"
    out.write_text(text + "\n".join(block) + "\n", encoding="utf-8")
    return out


__all__ = [
    "append_descriptive_summary",
    "patch_hypothesis_bh",
    "write_hypothesis_report",
    "write_summary_report",
]
