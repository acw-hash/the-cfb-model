"""Orchestrate SOC-2 spec build + Node render into the SOC-1 week folder."""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from ncaa_quant.social.charts._frame import require_brand_mark
from ncaa_quant.social.copy import assert_clean_texts, trim_caption
from ncaa_quant.social.load import LoadedArtifacts
from ncaa_quant.social.specs.contact_sheet import write_contact_sheet
from ncaa_quant.social.specs.gamecard import build_gamecard_specs, select_default_gamecards
from ncaa_quant.social.specs.learned import build_learned_specs
from ncaa_quant.social.specs.net import resolve_margin_scale
from ncaa_quant.social.specs.readmap import build_readmap_specs
from ncaa_quant.social.specs.render_invoke import render_spec, write_spec
from ncaa_quant.social.specs.slate_ranges import build_slate_ranges_specs
from ncaa_quant.social.theme import BRAND_MARK_PATH, ChartSize, site_url_from_config

Soc2Chart = Literal[
    "readmap", "learned", "movers", "slate-ranges", "gamecard", "soc2"
]


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _stem_for(spec: dict[str, Any]) -> str:
    chart = spec["chart"]
    week = int(spec["frame"]["week"])
    size = spec["frame"]["size"]
    if chart == "readmap":
        return f"readmap_w{week}_{size}"
    if chart in ("movers", "learned"):
        return f"movers_w{week}_{size}"
    if chart == "slate_ranges":
        suffix = spec.get("page_suffix") or ""
        return f"slate_ranges_w{week}{suffix}_{size}"
    if chart == "gamecard":
        return f"gamecard_{spec['game_id']}_{size}"
    msg = f"Unknown chart {chart}"
    raise ValueError(msg)


def _collect_spec_texts(spec: dict[str, Any]) -> list[str]:
    texts = [
        spec["frame"]["footer"],
        spec["frame"].get("title") or "",
        spec["frame"].get("subtitle") or "",
        spec.get("alt") or "",
        spec.get("caption") or "",
        *(spec.get("footnotes") or []),
    ]
    callout = spec.get("callout") or {}
    if callout.get("label"):
        texts.append(str(callout["label"]))
    return [t for t in texts if t]


def run_soc2_charts(
    *,
    loaded: LoadedArtifacts,
    season: int,
    sizes: Sequence[ChartSize],
    out_root: Path,
    logo_dir: Path,
    abbrev_by_id: dict[int, str],
    chart: Soc2Chart,
    theme: Literal["light", "dark"] = "light",
    slate_scope: Literal["featured", "all"] = "featured",
    game_ids: list[str] | None = None,
    model_version: str,
    vintage_label: str,
    published_at: str,
    allow_missing_logos: bool = False,
    skip_render: bool = False,
    specs_only: bool = False,
    render_only: bool = False,
    no_lint: bool = False,
    final: bool = True,
) -> dict[str, Any]:
    """Build specs, optionally render PNGs, return report fragment."""
    if specs_only and render_only:
        msg = "Cannot set both specs_only and render_only"
        raise ValueError(msg)
    if final and no_lint:
        msg = "--no-lint is refused when producing final outputs"
        raise ValueError(msg)

    brand = require_brand_mark(BRAND_MARK_PATH)
    site_url = site_url_from_config() or "https://the-cfb-model.vercel.app"
    weeks = loaded.weeks
    specs_dir = out_root / "specs"
    specs_dir.mkdir(parents=True, exist_ok=True)

    scale = resolve_margin_scale()
    report: dict[str, Any] = {
        "library": {
            "name": "hand-rolled React/SVG",
            "webapp_component": ("webapp/site/src/components/GameDetail/RatingTrajectoryChart.tsx"),
            "react": "^19.0.0",
            "react_dom": "^19.0.0",
            "playwright": "^1.51.1",
            "chart_library": None,
            "note": (
                "Site Stage-1 ratings chart is custom SVG (no Recharts/D3/etc.). "
                "SOC-2 pins the same React + Playwright versions as webapp/site."
            ),
        },
        "def_sign": {
            "def_good": "def_epa",
            "export": (
                "src/ncaa_quant/webapp/export.py build_team_ratings passes "
                "getattr(row, 'def_epa') through without a sign flip"
            ),
            "display": (
                "RatingTrajectoryChart markersFor uses p.def_epa directly; "
                "helper text: Higher is better on defense… EPA suppressed"
            ),
        },
        "margin_scale": scale.value,
        "margin_scale_source": scale.source,
        "theme": theme,
        "model_version": model_version,
        "vintage_label": vintage_label,
        "charts": {},
        "texts": [],
        "determinism": None,
        "render_engine": "playwright-chromium",
        "render_times_ms": {},
        "lint": {},
        "device_scale_factor": 2,
        "layout_css_px": {"4x5": [800, 1000], "16x9": [960, 540]},
    }

    want_read = chart in ("soc2", "readmap")
    want_learned = chart in ("soc2", "learned", "movers")
    want_slate = chart in ("soc2", "slate-ranges")
    want_card = chart in ("soc2", "gamecard")

    do_build = not render_only
    do_render = not (skip_render or specs_only)

    texts: list[str] = []
    rendered_paths: list[Path] = []
    all_specs: list[dict[str, Any]] = []

    def _process_specs(
        specs: list[dict[str, Any]],
        *,
        chart_key: str,
        alt_name: str | None,
        caption_name: str | None,
        extra_report: dict[str, Any] | None = None,
    ) -> None:
        paths: list[Path] = []
        times: dict[str, int] = {}
        lint_by_stem: dict[str, Any] = {}
        for spec in specs:
            if not allow_missing_logos and spec.get("meta", {}).get("missing_logos"):
                miss = spec["meta"]["missing_logos"]
                msg = f"Missing logos for {chart_key}: {miss[:5]}"
                raise RuntimeError(msg)
            stem = _stem_for(spec)
            sp = specs_dir / f"{stem}.json"
            if do_build:
                write_spec(spec, sp)
            elif not sp.is_file():
                msg = f"render-only: missing spec {sp}"
                raise RuntimeError(msg)
            else:
                spec = json.loads(sp.read_text(encoding="utf-8"))
            all_specs.append(spec)
            png = out_root / f"{stem}.png"
            if do_render:
                t0 = time.perf_counter()
                meta = render_spec(sp, png, no_lint=no_lint, final=final)
                ms = int(round((time.perf_counter() - t0) * 1000))
                times[stem] = int(meta.get("render_ms") or ms)
                lint_by_stem[stem] = {
                    "ok": meta.get("lint_ok", True),
                    "errors": meta.get("lint_errors") or [],
                }
                paths.append(png)
                rendered_paths.append(png)
            if alt_name:
                _write_text(out_root / alt_name, spec["alt"])
            if caption_name:
                _write_text(out_root / caption_name, trim_caption(spec["caption"]))
            # Per-gamecard alt/caption
            if chart_key == "gamecard":
                gid = spec["game_id"]
                _write_text(out_root / f"gamecard_{gid}_alt.txt", spec["alt"])
                _write_text(
                    out_root / f"gamecard_{gid}_caption.txt",
                    trim_caption(spec["caption"]),
                )
            texts.extend(_collect_spec_texts(spec))
        payload = {**(extra_report or {}), "paths": [str(p) for p in paths]}
        if specs:
            if "highlights" in specs[0]:
                payload["highlights"] = specs[0]["highlights"]
            if "callout" in specs[0]:
                payload["callout"] = specs[0]["callout"]
            payload["title"] = specs[0]["frame"]["title"]
            if specs[0].get("meta", {}).get("sign_prob_mismatches") is not None:
                payload["sign_prob_mismatches"] = specs[0]["meta"]["sign_prob_mismatches"]
            if specs[0].get("meta", {}).get("logo_slot_px") is not None:
                payload["logo_slot_px"] = specs[0]["meta"]["logo_slot_px"]
                payload["max_displacement_css_px"] = specs[0]["meta"].get(
                    "max_displacement_css_px"
                )
                payload["worst_logo_overlap_frac"] = specs[0]["meta"].get(
                    "worst_logo_overlap_frac"
                )
        report["charts"][chart_key] = payload
        report["render_times_ms"].update(times)
        report["lint"].update(lint_by_stem)

    odds_data = (
        loaded.odds_snapshot.data if loaded.odds_snapshot is not None else None
    )
    if odds_data is not None:
        report["market"] = {
            "field": "market_home_margin",
            "module": "workers/odds-refresh/src/consensus.ts:homeSpreadToMarketMargin",
            "snapshot_at": odds_data.get("snapshot_at"),
            "week": odds_data.get("week"),
            "consensus_method": odds_data.get("consensus_method"),
            "sha256": loaded.odds_snapshot.sha256 if loaded.odds_snapshot else None,
        }

    if want_read and do_build:
        specs = build_readmap_specs(
            team_ratings=loaded.team_ratings.data,
            week=weeks.map_week,
            slate_week=weeks.slate_week,
            season=season,
            fbs_team_ids=loaded.fbs_team_ids,
            abbrev_by_id=abbrev_by_id,
            logo_dir=logo_dir,
            sizes=list(sizes),
            theme=theme,
            model_version=model_version,
            vintage_label=vintage_label,
            published_at=published_at,
            site_url=site_url,
            brand_mark=brand,
        )
        _process_specs(
            specs,
            chart_key="readmap",
            alt_name="readmap_alt.txt",
            caption_name="readmap_caption.txt",
            extra_report={
                "week": weeks.map_week,
                "median_sd": specs[0]["meta"]["median_sd"] if specs else None,
                "n_fbs": specs[0]["meta"]["n_fbs"] if specs else 0,
                "logo_slot_px": specs[0]["meta"].get("logo_slot_px") if specs else None,
                "max_displacement_css_px": (
                    specs[0]["meta"].get("max_displacement_css_px") if specs else None
                ),
                "worst_logo_overlap_frac": (
                    specs[0]["meta"].get("worst_logo_overlap_frac") if specs else None
                ),
            },
        )
    elif want_read and render_only:
        specs = [
            json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(specs_dir.glob("readmap_*.json"))
        ]
        _process_specs(
            specs,
            chart_key="readmap",
            alt_name="readmap_alt.txt",
            caption_name="readmap_caption.txt",
        )

    if want_learned and do_build:
        specs_l, learned_report = build_learned_specs(
            team_ratings=loaded.team_ratings.data,
            results=loaded.results.data if loaded.results else None,
            map_week=weeks.map_week,
            slate_week=weeks.slate_week,
            season=season,
            fbs_team_ids=loaded.fbs_team_ids,
            abbrev_by_id=abbrev_by_id,
            logo_dir=logo_dir,
            sizes=list(sizes),
            theme=theme,
            model_version=model_version,
            vintage_label=vintage_label,
            published_at=published_at,
            site_url=site_url,
            brand_mark=brand,
            school_to_team_id=loaded.school_to_team_id,
        )
        report["bye_week_handling"] = learned_report
        if specs_l is None:
            report["charts"]["movers"] = learned_report
            print(f"Skipping movers chart: {learned_report.get('skip_reason')}")
        else:
            _process_specs(
                specs_l,
                chart_key="movers",
                alt_name="movers_alt.txt",
                caption_name="movers_caption.txt",
                extra_report=learned_report,
            )
            report["charts"]["movers"]["filename_alias"] = learned_report.get(
                "filename_alias"
            )
    elif want_learned and render_only:
        specs_l = [
            json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(
                list(specs_dir.glob("movers_*.json"))
                + list(specs_dir.glob("learned_*.json"))
            )
        ]
        _process_specs(
            specs_l,
            chart_key="movers",
            alt_name="movers_alt.txt",
            caption_name="movers_caption.txt",
        )

    if want_slate and do_build:
        specs_s, slate_report = build_slate_ranges_specs(
            week_predictions=loaded.week_predictions.data,
            team_ratings=loaded.team_ratings.data,
            ratings_week=weeks.map_week,
            week=weeks.slate_week,
            season=season,
            fbs_team_ids=loaded.fbs_team_ids,
            abbrev_by_id=abbrev_by_id,
            logo_dir=logo_dir,
            sizes=list(sizes),
            theme=theme,
            scope=slate_scope,
            model_version=model_version,
            vintage_label=vintage_label,
            published_at=published_at,
            site_url=site_url,
            brand_mark=brand,
            meta=loaded.meta.data,
            odds_snapshot=odds_data,
        )
        _process_specs(
            specs_s,
            chart_key="slate_ranges",
            alt_name="slate_ranges_alt.txt",
            caption_name="slate_ranges_caption.txt",
            extra_report=slate_report,
        )
    elif want_slate and render_only:
        specs_s = [
            json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(specs_dir.glob("slate_ranges_*.json"))
        ]
        _process_specs(
            specs_s,
            chart_key="slate_ranges",
            alt_name="slate_ranges_alt.txt",
            caption_name="slate_ranges_caption.txt",
        )

    if want_card and do_build:
        ids = list(game_ids or [])
        if not ids:
            ids = select_default_gamecards(
                week_predictions=loaded.week_predictions.data,
                team_ratings=loaded.team_ratings.data,
                ratings_week=weeks.map_week,
                fbs_team_ids=loaded.fbs_team_ids,
            )
        specs_g, card_report = build_gamecard_specs(
            game_ids=ids,
            week_predictions=loaded.week_predictions.data,
            team_ratings=loaded.team_ratings.data,
            ratings_week=weeks.map_week,
            week=weeks.slate_week,
            season=season,
            logo_dir=logo_dir,
            sizes=list(sizes),
            theme=theme,
            model_version=model_version,
            vintage_label=vintage_label,
            published_at=published_at,
            site_url=site_url,
            brand_mark=brand,
            odds_snapshot=odds_data,
            fbs_team_ids=loaded.fbs_team_ids,
            abbrev_by_id=abbrev_by_id,
        )
        _process_specs(
            specs_g,
            chart_key="gamecard",
            alt_name=None,
            caption_name=None,
            extra_report=card_report,
        )
    elif want_card and render_only:
        specs_g = [
            json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(specs_dir.glob("gamecard_*.json"))
        ]
        _process_specs(
            specs_g,
            chart_key="gamecard",
            alt_name=None,
            caption_name=None,
        )

    assert_clean_texts(texts, label="soc2 chart copy")
    report["texts_checked"] = len(texts)

    if do_render and rendered_paths:
        first = rendered_paths[0]
        stem = first.stem
        spec_path = specs_dir / f"{stem}.json"
        if spec_path.is_file():
            tmp = out_root / f".det_{stem}.png"
            try:
                render_spec(spec_path, tmp, no_lint=no_lint, final=final)
                import numpy as np
                from PIL import Image

                a = np.asarray(Image.open(first).convert("RGBA"))
                b = np.asarray(Image.open(tmp).convert("RGBA"))
                identical = a.shape == b.shape and bool(np.array_equal(a, b))
                report["determinism"] = {
                    "path": str(first),
                    "pixel_identical": identical,
                    "byte_identical": first.read_bytes() == tmp.read_bytes(),
                    "output_shape": list(a.shape),
                }
            finally:
                if tmp.exists():
                    tmp.unlink()

        sheet = write_contact_sheet(
            rendered_paths,
            out_path=out_root / f"contact_sheet_w{weeks.slate_week}.png",
        )
        if sheet is not None:
            report["contact_sheet"] = str(sheet)

    total_ms = sum(report["render_times_ms"].values())
    report["render_total_ms"] = total_ms
    return report
