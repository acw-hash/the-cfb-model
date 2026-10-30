"""CLI orchestration for weekly Ridge social charts."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Sequence

import pandas as pd  # type: ignore[import-untyped]

from ncaa_quant.social.charts.ridge_map import extract_ridge_points, render_ridge_map
from ncaa_quant.social.charts.slate_map import (
    SlateCoherenceError,
    extract_slate_points,
    render_slate_map,
)
from ncaa_quant.social.charts.surprise_strip import (
    extract_surprise_points,
    render_surprise_strip,
)
from ncaa_quant.social.charts.top25 import extract_top25, render_top25
from ncaa_quant.social.copy import assert_clean_texts, footer_line, trim_caption
from ncaa_quant.social.load import (
    ArtifactLoadError,
    LoadedArtifacts,
    load_artifacts,
)
from ncaa_quant.social.logos import DEFAULT_LOGO_DIR, load_abbrev_map
from ncaa_quant.social.theme import parse_sizes, site_host_for_footer, site_url_from_config

REPO_ROOT = Path(__file__).resolve().parents[3]
ChartName = Literal[
    "all",
    "top25",
    "map",
    "slate",
    "surprise",
    "readmap",
    "market-map",
    "learned",
    "movers",
    "slate-ranges",
    "gamecard",
    "soc2",
]
SOC2_CHARTS = frozenset(
    {
        "readmap",
        "market-map",
        "learned",
        "movers",
        "slate-ranges",
        "gamecard",
        "soc2",
    }
)


def _abbrev_map(season: int) -> dict[int, str]:
    path = REPO_ROOT / "data" / "staged" / "teams" / f"season={season}" / "part.parquet"
    if not path.is_file():
        return {}
    df = pd.read_parquet(path)
    out: dict[int, str] = {}
    for row in df.itertuples(index=False):
        abbr = getattr(row, "abbreviation", None)
        if abbr:
            out[int(row.team_id)] = str(abbr)
    return out


def _resolve_abbrev(season: int, logo_dir: Path) -> dict[int, str]:
    """Merge logo-manifest abbreviations with staged teams (staged wins)."""
    return {**load_abbrev_map(logo_dir), **_abbrev_map(season)}


def _model_version(meta: dict[str, Any]) -> str:
    champ = meta.get("champion_model") or {}
    return str(champ.get("model_version") or meta.get("vintage_label") or "unknown")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Render Ridge weekly social charts.")
    p.add_argument("--season", type=int, required=True)
    p.add_argument(
        "--chart",
        choices=[
            "all",
            "top25",
            "map",
            "slate",
            "surprise",
            "readmap",
            "market-map",
            "learned",
            "movers",
            "slate-ranges",
            "gamecard",
            "soc2",
        ],
        default="all",
        help=(
            "Default 'all' renders SOC-1 charts. "
            "'soc2' renders market-map + movers + slate-ranges + default game cards "
            "(readmap remains available via --chart readmap). "
            "'learned' is a one-run alias for 'movers'."
        ),
    )
    p.add_argument("--week", default="auto", help="auto or int (overrides all unless per-chart set)")
    p.add_argument("--map-week", type=int, default=None)
    p.add_argument("--slate-week", type=int, default=None)
    p.add_argument("--surprise-week", type=int, default=None)
    p.add_argument("--source", choices=["r2", "local"], default="r2")
    p.add_argument("--artifact-dir", type=Path, default=None)
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--sizes", default="4x5,16x9")
    p.add_argument("--logo-dir", type=Path, default=DEFAULT_LOGO_DIR)
    p.add_argument("--allow-fixture", action="store_true")
    p.add_argument("--allow-stale", action="store_true")
    p.add_argument("--allow-missing-logos", action="store_true")
    p.add_argument(
        "--game-id",
        action="append",
        default=[],
        dest="game_ids",
        help="Game id for gamecard (repeatable). Default: top 3 toss-up/lean.",
    )
    p.add_argument(
        "--slate-scope",
        choices=["featured", "all"],
        default="featured",
        help="SOC-2 slate-ranges selection (default featured = 16 games).",
    )
    p.add_argument(
        "--theme",
        choices=["light", "dark"],
        default="light",
        help="SOC-2 theme (default light, matching the site).",
    )
    p.add_argument(
        "--skip-render",
        action="store_true",
        help="SOC-2: write specs/alt/captions only (no Node/Playwright).",
    )
    p.add_argument(
        "--specs-only",
        action="store_true",
        help="SOC-2: rebuild specs only (alias of --skip-render).",
    )
    p.add_argument(
        "--render-only",
        action="store_true",
        help="SOC-2: re-render PNGs from existing specs (no rebuild).",
    )
    p.add_argument(
        "--no-lint",
        action="store_true",
        help="SOC-2: skip layout lint (local debug only; refused for final outputs).",
    )
    return p


def _parse_week(value: str) -> int | Literal["auto"]:
    if value == "auto":
        return "auto"
    return int(value)


def run_charts(argv: Sequence[str] | None = None) -> int:
    """Entry point used by ``scripts/make_social_charts.py``."""
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    sizes = parse_sizes(args.sizes)
    week = _parse_week(str(args.week))

    try:
        loaded = load_artifacts(
            season=args.season,
            source=args.source,
            artifact_dir=args.artifact_dir,
            week=week,
            map_week=args.map_week,
            slate_week=args.slate_week,
            surprise_week=args.surprise_week,
            allow_fixture=args.allow_fixture,
            allow_stale=args.allow_stale,
        )
    except ArtifactLoadError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.chart in SOC2_CHARTS:
        return _render_soc2(loaded, args=args, sizes=sizes)
    return _render(loaded, args=args, sizes=sizes)


def _render_soc2(loaded: LoadedArtifacts, *, args: argparse.Namespace, sizes: Any) -> int:
    from ncaa_quant.social.specs.runner import run_soc2_charts

    weeks = loaded.weeks
    print(
        "Resolved weeks — "
        f"map={weeks.map_week} slate={weeks.slate_week} surprise={weeks.surprise_week}"
    )
    for w in weeks.warnings:
        print(w, file=sys.stderr)

    folder_week = weeks.slate_week
    out_root = args.out or (REPO_ROOT / "out" / "social" / str(args.season) / f"w{folder_week:02d}")
    out_root.mkdir(parents=True, exist_ok=True)

    meta = loaded.meta.data
    model_version = _model_version(meta)
    vintage_label = str(meta.get("vintage_label") or "")
    published_at = str(meta.get("published_at") or "")
    abbrev = _resolve_abbrev(args.season, Path(args.logo_dir))

    report_path = out_root / "run_report.json"
    base_report: dict[str, Any] = {}
    if report_path.is_file():
        try:
            base_report = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            base_report = {}

    specs_only = bool(args.specs_only or args.skip_render)
    no_lint = bool(args.no_lint)
    # Final outputs refuse --no-lint; treat non-debug runs as final.
    final = not no_lint
    try:
        soc2 = run_soc2_charts(
            loaded=loaded,
            season=args.season,
            sizes=sizes,
            out_root=out_root,
            logo_dir=Path(args.logo_dir),
            abbrev_by_id=abbrev,
            chart=args.chart,
            theme=args.theme,
            slate_scope=args.slate_scope,
            game_ids=list(args.game_ids or []),
            model_version=model_version,
            vintage_label=vintage_label,
            published_at=published_at,
            allow_missing_logos=args.allow_missing_logos,
            skip_render=False,
            specs_only=specs_only,
            render_only=bool(args.render_only),
            no_lint=no_lint,
            final=final,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    base_report.setdefault("generated_at", datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
    base_report["season"] = args.season
    base_report["soc2"] = soc2
    # Merge chart paths into charts dict for convenience.
    charts = base_report.setdefault("charts", {})
    for name, payload in (soc2.get("charts") or {}).items():
        charts[name] = payload

    report_path.write_text(json.dumps(base_report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"soc2": soc2}, indent=2))
    print(f"Wrote {report_path}")
    return 0


def _render(loaded: LoadedArtifacts, *, args: argparse.Namespace, sizes: Any) -> int:
    weeks = loaded.weeks
    print(
        "Resolved weeks — "
        f"map={weeks.map_week} slate={weeks.slate_week} surprise={weeks.surprise_week}"
    )
    for w in weeks.warnings:
        print(w, file=sys.stderr)

    # Output root uses slate week as the primary weekly folder label.
    folder_week = weeks.slate_week
    out_root = args.out or (REPO_ROOT / "out" / "social" / str(args.season) / f"w{folder_week:02d}")
    out_root.mkdir(parents=True, exist_ok=True)

    meta = loaded.meta.data
    from ncaa_quant.social.charts._frame import published_at_et

    site_host = site_host_for_footer(site_url_from_config())
    model_version = _model_version(meta)
    vintage_label = str(meta.get("vintage_label") or "")
    published_at = str(meta.get("published_at") or "")
    published_et = published_at_et(published_at)

    abbrev = _resolve_abbrev(args.season, Path(args.logo_dir))
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "season": args.season,
        "model_version": model_version,
        "vintage_label": vintage_label,
        "published_at": published_at,
        "published_at_et": published_et,
        "resolved_weeks": {
            "map": weeks.map_week,
            "slate": weeks.slate_week,
            "surprise": weeks.surprise_week,
        },
        "warnings": list(weeks.warnings),
        "artifacts": {
            name: {
                "source": blob.source,
                "sha256": blob.sha256,
                "published_at": blob.published_at,
            }
            for name, blob in {
                "meta": loaded.meta,
                "team_ratings": loaded.team_ratings,
                "week_predictions": loaded.week_predictions,
                "results": loaded.results,
            }.items()
        },
        "fbs_team_count": len(loaded.fbs_team_ids),
        "charts": {},
    }

    chart_sel = args.chart
    texts: list[str] = []
    try:
        if chart_sel in ("all", "top25"):
            footer = footer_line(site_host=site_host)
            top_rows = extract_top25(
                loaded.team_ratings.data,
                week=weeks.map_week,
                fbs_team_ids=loaded.fbs_team_ids,
                abbrev_by_id=abbrev,
            )
            result_t = render_top25(
                rows=top_rows,
                week=weeks.map_week,
                season=args.season,
                sizes=sizes,
                out_dir=out_root,
                footer=footer,
                slate_week=weeks.slate_week,
                logo_dir=args.logo_dir,
                allow_missing_logos=args.allow_missing_logos,
                fixture_watermark=loaded.fixture_watermark,
            )
            alt_t = (
                f"{result_t.alt_text} "
                f"Model {model_version}; vintage {vintage_label}; "
                f"published {published_et}."
            )
            _write_text(out_root / "top25_alt.txt", alt_t)
            _write_text(out_root / "top25_caption.txt", trim_caption(result_t.caption))
            texts.extend([footer, alt_t, result_t.caption, *result_t.footnotes])
            report["charts"]["top25"] = {
                "week": result_t.week,
                "week_mode": result_t.week_mode,
                "plotted": len(result_t.rows),
                "paths": [str(p) for p in result_t.paths],
                "missing_logos": result_t.missing_logos,
                "logo_fallbacks": result_t.logo_fallbacks,
                "notes": result_t.notes,
                "footer": footer,
                "biggest_riser": result_t.biggest_riser,
                "biggest_faller": result_t.biggest_faller,
                "rows": [
                    {
                        "rank": r.rank,
                        "team_id": r.team_id,
                        "school": r.school,
                        "net": r.net,
                        "off_epa": r.off_epa,
                        "def_epa": r.def_epa,
                        "off_rank": r.off_rank,
                        "def_rank": r.def_rank,
                        "prev_rank": r.prev_rank,
                        "rank_delta": r.rank_delta,
                    }
                    for r in result_t.rows
                ],
            }

        if chart_sel in ("all", "map"):
            footer = footer_line(site_host=site_host)
            points = extract_ridge_points(
                loaded.team_ratings.data,
                week=weeks.map_week,
                fbs_team_ids=loaded.fbs_team_ids,
                abbrev_by_id=abbrev,
            )
            result = render_ridge_map(
                points=points,
                week=weeks.map_week,
                season=args.season,
                sizes=sizes,
                out_dir=out_root,
                footer=footer,
                slate_week=weeks.slate_week,
                logo_dir=args.logo_dir,
                allow_missing_logos=args.allow_missing_logos,
                fixture_watermark=loaded.fixture_watermark,
            )
            alt = (
                f"{result.alt_text} "
                f"Model {model_version}; vintage {vintage_label}; "
                f"published {published_et}."
            )
            _write_text(out_root / "ridge_map_alt.txt", alt)
            _write_text(out_root / "ridge_map_caption.txt", trim_caption(result.caption))
            texts.extend([footer, alt, result.caption, *result.footnotes])
            report["charts"]["ridge_map"] = {
                "week": result.week,
                "week_mode": result.week_mode,
                "plotted": len(result.points),
                "mean_off": result.mean_off,
                "mean_def": result.mean_def,
                "paths": [str(p) for p in result.paths],
                "missing_logos": result.missing_logos,
                "logo_fallbacks": result.logo_fallbacks,
                "overlap_fraction": result.overlap_fraction,
                "leader_line_count": result.leader_line_count,
                "notes": result.notes,
                "footer": footer,
                "teams": [
                    {
                        "team_id": p.team_id,
                        "school": p.school,
                        "off_epa": p.off_epa,
                        "def_epa": p.def_epa,
                        "off_pct": p.off_pct,
                        "def_pct": p.def_pct,
                    }
                    for p in result.points
                ],
            }
            print(
                f"Ridge map leader lines: {result.leader_line_count} "
                f"(overlap_fraction={result.overlap_fraction:.3f})"
            )

        if chart_sel in ("all", "slate"):
            try:
                points, excluded = extract_slate_points(loaded.week_predictions.data)
            except SlateCoherenceError as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 1
            footer = footer_line(site_host=site_host, games_not_shown=len(excluded))
            wp = loaded.week_predictions.data
            result_s = render_slate_map(
                points=points,
                excluded=excluded,
                week=weeks.slate_week,
                season=args.season,
                refresh_kind=str(wp.get("refresh_kind") or meta.get("refresh_kind") or ""),
                published_at=str(wp.get("published_at") or meta.get("published_at") or ""),
                sizes=sizes,
                out_dir=out_root,
                footer=footer,
                logo_dir=args.logo_dir,
                allow_missing_logos=args.allow_missing_logos,
                fixture_watermark=loaded.fixture_watermark,
                abbrev_by_id=abbrev,
                source_games=list(wp.get("games") or []),
            )
            alt = (
                f"{result_s.alt_text} "
                f"Model {model_version}; vintage {vintage_label}; "
                f"published {published_et}."
            )
            _write_text(out_root / "slate_map_alt.txt", alt)
            _write_text(
                out_root / "slate_map_caption.txt",
                trim_caption(result_s.caption),
            )
            texts.extend([footer, alt, result_s.caption, *result_s.footnotes])
            report["charts"]["slate_map"] = {
                "week": result_s.week,
                "plotted": len(result_s.points),
                "excluded": len(result_s.excluded),
                "excluded_reasons": result_s.excluded,
                "shipped": result_s.shipped,
                "notes": result_s.notes,
                "footer": footer,
                "sign_disagreements": [
                    {
                        "game_id": d.game_id,
                        "home_team": d.home_team,
                        "away_team": d.away_team,
                        "mu_margin": d.mu_margin,
                        "sigma_margin": d.sigma_margin,
                        "p_win_home": d.p_win_home,
                        "conviction_tier": d.conviction_tier,
                    }
                    for d in result_s.sign_disagreements
                ],
                "phi_diagnostic": {
                    "max_abs_dev": result_s.phi.max_abs_dev,
                    "approximate": result_s.phi.approximate,
                    "mean_dev": result_s.phi.mean_dev,
                    "median_dev": result_s.phi.median_dev,
                    "min_dev": result_s.phi.min_dev,
                    "max_dev": result_s.phi.max_dev,
                    "n_p_below_phi": result_s.phi.n_p_below_phi,
                    "n_p_above_phi": result_s.phi.n_p_above_phi,
                    "worst": result_s.phi.worst,
                },
                "paths": [str(p) for p in result_s.paths],
                "missing_logos": result_s.missing_logos,
                "logo_fallbacks": result_s.logo_fallbacks,
            }

        if chart_sel in ("all", "surprise"):
            points, excluded = extract_surprise_points(
                loaded.results.data,
                week=weeks.surprise_week,
                school_to_team_id=loaded.school_to_team_id,
            )
            footer = footer_line(site_host=site_host, games_not_shown=len(excluded))
            result_u = render_surprise_strip(
                points=points,
                excluded=excluded,
                week=weeks.surprise_week,
                season=args.season,
                sizes=sizes,
                out_dir=out_root,
                footer=footer,
                logo_dir=args.logo_dir,
                allow_missing_logos=args.allow_missing_logos,
                fixture_watermark=loaded.fixture_watermark,
                abbrev_by_id=abbrev,
            )
            alt = (
                f"{result_u.alt_text} "
                f"Model {model_version}; vintage {vintage_label}; "
                f"published {published_et}."
            )
            _write_text(out_root / "surprise_strip_alt.txt", alt)
            _write_text(
                out_root / "surprise_strip_caption.txt",
                trim_caption(result_u.caption),
            )
            texts.extend([footer, alt, result_u.caption, *result_u.footnotes])
            report["charts"]["surprise_strip"] = {
                "week": result_u.week,
                "plotted": len(result_u.points),
                "excluded": len(result_u.excluded),
                "excluded_reasons": result_u.excluded,
                "notes": result_u.notes,
                "footer": footer,
                "headline": {
                    "hits": result_u.headline.hits,
                    "denom": result_u.headline.denom,
                    "pct": result_u.headline.pct,
                    "text": result_u.headline.as_text(),
                },
                "paths": [str(p) for p in result_u.paths],
                "missing_logos": result_u.missing_logos,
                "logo_fallbacks": result_u.logo_fallbacks,
                "annotations": result_u.annotations,
                "score_labels": result_u.score_labels,
            }
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    try:
        assert_clean_texts(texts, label="social chart copy")
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    report_path = out_root / "run_report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"Wrote {report_path}")
    return 0


def main() -> None:
    raise SystemExit(run_charts())
