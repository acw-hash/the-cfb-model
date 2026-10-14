"""Surprise strip — favored-perspective z beeswarm for graded results."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from scipy.stats import norm

from ncaa_quant.social.charts._frame import (
    add_fixture_watermark,
    add_footer,
    add_header,
    add_panel_axes,
    new_figure,
)
from ncaa_quant.social.charts._logos_draw import draw_logo, draw_text_chip
from ncaa_quant.social.logos import LogoAsset, load_logo_asset, override_path
from ncaa_quant.social.placement import assert_no_text_bbox_overlap, beeswarm_offsets
from ncaa_quant.social.theme import (
    HIT_RING,
    MISS_ACCENT,
    TEXT,
    TEXT_SECONDARY,
    TEXT_TERTIARY,
    ChartSize,
    fmt_margin_abs,
    save_png,
)
from ncaa_quant.webapp.export import compute_favored_side


@dataclass
class SurprisePoint:
    """One graded game on the favored-perspective z axis."""

    game_id: str
    week: int
    home_team: str
    away_team: str
    home_team_id: int | None
    away_team_id: int | None
    home_points: int
    away_points: int
    actual_margin: float
    mu_margin: float
    sigma_margin: float
    favored_side: Literal["home", "away"]
    z: float
    outperform_team_id: int | None
    outperform_school: str
    margin_interval_hit: bool | None
    margin_interval_nominal: float | None


@dataclass
class HeadlineStat:
    """Interval-hit headline from published ``margin_interval_hit``."""

    hits: int
    denom: int
    pct: float

    def as_text(self, *, nominal: float | None = 0.80) -> str:
        nom = 0.80 if nominal is None else float(nominal)
        label = f"{nom:.0%}"
        target = int(round(nom * 100.0))
        return (
            f"{self.hits} of {self.denom} inside Ridge's "
            f"{label} range (target {target}%)"
        )


@dataclass
class SurpriseStripResult:
    """Render outputs + report fields."""

    points: list[SurprisePoint]
    excluded: list[dict[str, Any]]
    week: int
    headline: HeadlineStat
    paths: list[Path] = field(default_factory=list)
    missing_logos: list[dict[str, Any]] = field(default_factory=list)
    logo_fallbacks: list[dict[str, Any]] = field(default_factory=list)
    footnotes: list[str] = field(default_factory=list)
    notes: dict[str, Any] = field(default_factory=dict)
    alt_text: str = ""
    caption: str = ""
    annotations: list[str] = field(default_factory=list)
    score_labels: list[dict[str, Any]] = field(default_factory=list)


def favored_perspective_z(
    *,
    actual_margin: float,
    mu_margin: float,
    sigma_margin: float,
    p_win_home: float | None = None,
) -> tuple[float, Literal["home", "away"]]:
    """Favored-perspective standardized residual.

    ``s = +1 if mu_margin ≥ 0 else −1`` with DESIGN §2.1 tie-break when
    ``mu_margin == 0``; ``z = s · (actual − mu) / sigma``.
    """
    if sigma_margin <= 0:
        msg = "sigma_margin must be positive"
        raise ValueError(msg)
    side = compute_favored_side(mu_margin, p_win_home)
    s = 1.0 if side == "home" else -1.0
    # When mu_margin > 0, side is home and s=+1; when mu < 0, away and s=-1.
    # Tie mu==0 uses p_win_home via compute_favored_side.
    z = s * (actual_margin - mu_margin) / sigma_margin
    return float(z), side


def interval_hit_headline(games: list[dict[str, Any]]) -> HeadlineStat:
    """X of Y from non-null ``margin_interval_hit`` only."""
    flags = [g.get("margin_interval_hit") for g in games if g.get("margin_interval_hit") is not None]
    denom = len(flags)
    hits = sum(1 for f in flags if f is True)
    pct = (100.0 * hits / denom) if denom else 0.0
    return HeadlineStat(hits=hits, denom=denom, pct=pct)


def extract_surprise_points(
    results: dict[str, Any],
    *,
    week: int,
    school_to_team_id: dict[str, int],
) -> tuple[list[SurprisePoint], list[dict[str, Any]]]:
    """Graded games in ``week`` with non-null mu/sigma."""
    points: list[SurprisePoint] = []
    excluded: list[dict[str, Any]] = []
    for g in results.get("games") or []:
        if int(g.get("week", -1)) != week:
            continue
        if g.get("grade_status") != "graded":
            excluded.append({"game_id": g.get("game_id"), "reasons": ["not_graded"]})
            continue
        mu = g.get("mu_margin")
        sigma = g.get("sigma_margin")
        if mu is None or sigma is None:
            excluded.append({"game_id": g.get("game_id"), "reasons": ["missing_mu_or_sigma"]})
            continue
        actual = g.get("actual_margin")
        if actual is None:
            excluded.append({"game_id": g.get("game_id"), "reasons": ["missing_actual_margin"]})
            continue
        home = str(g["home_team"])
        away = str(g["away_team"])
        home_id = school_to_team_id.get(home)
        away_id = school_to_team_id.get(away)
        z, side = favored_perspective_z(
            actual_margin=float(actual),
            mu_margin=float(mu),
            sigma_margin=float(sigma),
            p_win_home=g.get("p_win_home"),
        )
        if z > 0:
            out_school = home if side == "home" else away
            out_id = home_id if side == "home" else away_id
        elif z < 0:
            out_school = away if side == "home" else home
            out_id = away_id if side == "home" else home_id
        else:
            out_school = home if side == "home" else away
            out_id = home_id if side == "home" else away_id
        points.append(
            SurprisePoint(
                game_id=str(g["game_id"]),
                week=week,
                home_team=home,
                away_team=away,
                home_team_id=home_id,
                away_team_id=away_id,
                home_points=int(g.get("home_points") or 0),
                away_points=int(g.get("away_points") or 0),
                actual_margin=float(actual),
                mu_margin=float(mu),
                sigma_margin=float(sigma),
                favored_side=side,
                z=z,
                outperform_team_id=out_id,
                outperform_school=out_school,
                margin_interval_hit=g.get("margin_interval_hit"),
                margin_interval_nominal=(
                    float(g["margin_interval_nominal"])
                    if g.get("margin_interval_nominal") is not None
                    else None
                ),
            )
        )
    points.sort(key=lambda p: p.game_id)
    return points, excluded


def _annotation_line(p: SurprisePoint) -> str:
    """TEAM 38, TEAM 14 · Ridge expected TEAM by 3.5 (off-image / alt)."""
    fav = p.home_team if p.favored_side == "home" else p.away_team
    return (
        f"{p.home_team} {p.home_points}, {p.away_team} {p.away_points} · "
        f"Ridge expected {fav} by {fmt_margin_abs(p.mu_margin)}"
    )


def score_label_for_point(p: SurprisePoint, abbrev: str) -> str:
    """Short on-image score next to a logo, e.g. ``EMU 38–14``."""
    if p.outperform_school == p.home_team or p.outperform_team_id == p.home_team_id:
        return f"{abbrev} {p.home_points}–{p.away_points}"
    return f"{abbrev} {p.away_points}–{p.home_points}"


def render_surprise_strip(
    *,
    points: list[SurprisePoint],
    excluded: list[dict[str, Any]],
    week: int,
    season: int,
    sizes: list[ChartSize],
    out_dir: Path,
    footer: str,
    logo_dir: Path | None = None,
    allow_missing_logos: bool = False,
    fixture_watermark: bool = False,
    abbrev_by_id: dict[int, str] | None = None,
    logo_zoom: float = 0.05,
) -> SurpriseStripResult:
    """Render surprise-strip PNGs."""
    abbrev_by_id = abbrev_by_id or {}
    games_for_headline = [
        {
            "margin_interval_hit": p.margin_interval_hit,
        }
        for p in points
    ]
    # Headline over plotted graded games with non-null hit flags.
    headline = interval_hit_headline(games_for_headline)

    needed = {p.outperform_team_id for p in points if p.outperform_team_id is not None}
    assets: dict[int, LogoAsset] = {}
    missing: list[dict[str, Any]] = []
    fallbacks: list[dict[str, Any]] = []
    for tid in sorted(needed):
        asset = load_logo_asset(tid, logo_dir=logo_dir)
        if asset is None:
            missing.append(
                {
                    "team_id": tid,
                    "override_path": str(
                        override_path(logo_dir or Path("data/social/logos"), tid)
                    ),
                }
            )
            if allow_missing_logos:
                fallbacks.append({"team_id": tid, "chip": abbrev_by_id.get(tid, str(tid))})
        else:
            assets[tid] = asset
    if missing and not allow_missing_logos:
        msg = "Missing logos for team_ids: " + ", ".join(str(m["team_id"]) for m in missing)
        raise RuntimeError(msg)

    nominal = next(
        (p.margin_interval_nominal for p in points if p.margin_interval_nominal is not None),
        0.80,
    )
    band_z = float(norm.ppf(0.5 + float(nominal) / 2.0))

    top = sorted(points, key=lambda p: abs(p.z), reverse=True)[:3]
    annotations = [_annotation_line(p) for p in top]
    score_labels: list[dict[str, Any]] = []
    for p in top:
        tid = p.outperform_team_id
        abbr = (
            abbrev_by_id.get(tid, "")
            if tid is not None
            else ""
        ) or p.outperform_school[:4].upper()
        score_labels.append(
            {
                "game_id": p.game_id,
                "z": p.z,
                "label": score_label_for_point(p, abbr),
                "abbreviation": abbr,
                "team_id": tid,
            }
        )

    band_guide = (
        "Band is a normal-guide only; color reflects the published conformal interval."
    )
    exclusion_note = (
        f"Excluded {len(excluded)} games (not graded or missing μ/σ)."
        if excluded
        else "All graded games with μ/σ shown."
    )
    footnotes = [exclusion_note, band_guide]
    notes: dict[str, Any] = {
        "exclusion_count": len(excluded),
        "exclusion_note": exclusion_note,
        "band_guide_disclaimer": band_guide,
        "nominal": float(nominal),
        "score_labels": score_labels,
        "largest_surprises": annotations,
    }

    subtitle = f"Week {week} results · {headline.as_text(nominal=nominal)}"
    alt = (
        f"Surprise strip for week {week} {season}. "
        f"{headline.as_text(nominal=nominal)}. {exclusion_note} {band_guide} "
        f"Largest surprises: " + "; ".join(annotations) + "."
    )
    caption = (
        f"Week {week} graded: {headline.as_text(nominal=nominal)}. "
        + (f"Biggest moves: {top[0].outperform_school}." if top else "")
    )

    xs = [p.z for p in points]
    # Beeswarm radii in z-space.
    radii = [0.18] * len(points)
    team_ids = [
        (p.outperform_team_id if p.outperform_team_id is not None else -abs(hash(p.game_id)) % 10_000_000)
        for p in points
    ]
    ys = beeswarm_offsets(xs=xs, radii=radii, team_ids=team_ids)
    x_lim = max(3.0, max((abs(x) for x in xs), default=3.0) * 1.1)
    by_gid = {p.game_id: i for i, p in enumerate(points)}

    paths: list[Path] = []
    for size in sizes:
        fig = new_figure(size)
        add_header(
            fig,
            title="Last week, graded",
            subtitle=subtitle,
            size=size,
        )
        ax = add_panel_axes(fig, rect=(0.08, 0.12, 0.84, 0.62))
        ax.set_xlim(-x_lim, x_lim)
        y_span = max(1.0, max((abs(y) for y in ys), default=1.0) + 0.5)
        ax.set_ylim(-y_span, y_span)
        ax.axvspan(-band_z, band_z, color="#FFFFFF", alpha=0.06, zorder=0)
        ax.axvline(0.0, color=TEXT_TERTIARY, linewidth=0.8, zorder=1)
        band_label = f"{float(nominal):.0%} range"
        pos = ax.get_position()
        fig.text(
            pos.x0 + pos.width / 2.0,
            pos.y1 + 0.012,
            band_label,
            ha="center",
            va="bottom",
            fontsize=12,
            color=TEXT_SECONDARY,
            transform=fig.transFigure,
        )
        ax.set_yticks([])
        ax.set_xlabel("")
        dir_fs = 10 if size.width >= 1200 else 7
        ax.text(
            -x_lim,
            -y_span * 0.98,
            "← Underdog outperformed",
            ha="left",
            va="top",
            fontsize=dir_fs,
            color=TEXT_SECONDARY,
            clip_on=True,
        )
        ax.text(
            x_lim,
            -y_span * 0.98,
            "Favorite outperformed →",
            ha="right",
            va="top",
            fontsize=dir_fs,
            color=TEXT_SECONDARY,
            clip_on=True,
        )
        for i, p in enumerate(points):
            is_hit = p.margin_interval_hit is True
            is_miss = p.margin_interval_hit is False
            # Misses: full opacity + accent ring. Hits: 70% opacity.
            if is_miss:
                ring = MISS_ACCENT
                marker_alpha = 1.0
            elif is_hit:
                ring = HIT_RING
                marker_alpha = 0.70
            else:
                ring = TEXT_TERTIARY
                marker_alpha = 0.70
            ax.scatter(
                [p.z],
                [ys[i]],
                s=220,
                facecolors="none",
                edgecolors=ring,
                linewidths=2.2 if is_miss else 1.6,
                alpha=marker_alpha,
                zorder=3,
            )
            tid = p.outperform_team_id
            if tid is not None and tid in assets:
                draw_logo(
                    ax,
                    assets[tid],
                    p.z,
                    ys[i],
                    zoom=logo_zoom,
                    alpha=marker_alpha,
                )
            else:
                chip = abbrev_by_id.get(tid or -1, p.outperform_school[:4].upper())
                draw_text_chip(ax, chip, p.z, ys[i])

        # Label the 3 largest |z| logos with short score labels anchored to each logo.
        label_fs = 11 if size.width >= 1200 else 8
        # Alternate above/below so neighbors at similar z do not collide.
        for k, sl in enumerate(score_labels):
            idx = by_gid.get(str(sl["game_id"]))
            if idx is None:
                continue
            x = xs[idx]
            y = ys[idx]
            above = k % 2 == 0
            y_off = 0.42 if above else -0.42
            ax.text(
                x,
                y + y_off,
                str(sl["label"]),
                fontsize=label_fs,
                color=TEXT,
                ha="center",
                va="bottom" if above else "top",
                zorder=5,
                clip_on=True,
            )

        add_footer(fig, footer)
        if fixture_watermark:
            add_fixture_watermark(fig)
        assert_no_text_bbox_overlap(fig)
        out_path = out_dir / f"surprise_strip_w{week}_{size.name}.png"
        save_png(fig, out_path)
        paths.append(out_path)
        from matplotlib import pyplot as plt

        plt.close(fig)

    return SurpriseStripResult(
        points=points,
        excluded=excluded,
        week=week,
        headline=headline,
        paths=paths,
        missing_logos=missing,
        logo_fallbacks=fallbacks,
        footnotes=footnotes,
        notes=notes,
        alt_text=alt,
        caption=caption,
        annotations=annotations,
        score_labels=score_labels,
    )
