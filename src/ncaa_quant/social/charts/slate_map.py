"""Slate map — published max(p, 1−p) vs |μ| with vertical tier bands."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
from scipy.stats import norm

from ncaa_quant.social.charts._frame import (
    add_fixture_watermark,
    add_footer,
    add_header,
    add_panel_axes,
    new_figure,
    publish_day_short,
)
from ncaa_quant.social.charts._logos_draw import (
    draw_exact_dot,
    draw_logo,
    draw_text_chip,
)
from ncaa_quant.social.logos import LogoAsset, load_logo_asset, override_path
from ncaa_quant.social.placement import assert_no_text_bbox_overlap
from ncaa_quant.social.theme import (
    LABEL_WHITE_70,
    TEXT_SECONDARY,
    TOSS_UP_GRAY,
    ChartSize,
    save_png,
)
from ncaa_quant.webapp.export import (
    TIER_CLEAR_ENTER,
    TIER_LEAN_ENTER,
    TIER_STRONG_ENTER,
)

PHI_CHECK_TOLERANCE = 0.005

TierName = Literal["toss_up", "lean", "clear_lean", "strong_lean"]

# Navy → slate hue ramp (no orange). Readable on PANEL.
_TIER_BANDS: list[tuple[str, str, float]] = [
    ("toss_up", "#1A2E4A", 0.55),
    ("lean", "#243A58", 0.50),
    ("clear_lean", "#334F6E", 0.45),
    ("strong_lean", "#4A6888", 0.40),
]
_TIER_LABELS = {
    "toss_up": "Toss-up",
    "lean": "Lean",
    "clear_lean": "Clear lean",
    "strong_lean": "Strong lean",
}
_TIER_DOT = {
    "toss_up": "#6A7A90",
    "lean": "#7A8FA8",
    "clear_lean": "#9AADC4",
    "strong_lean": "#C5D2E0",
}


class SlateCoherenceError(RuntimeError):
    """Non-toss-up game where conviction_team's side has p < 0.5."""


@dataclass
class SlatePoint:
    """One published, tiered, sigma-credible game for the slate chart."""

    game_id: str
    home_team: str
    away_team: str
    home_team_id: int
    away_team_id: int
    mu_margin: float
    sigma_margin: float
    p_win_home: float
    conviction_tier: str
    conviction_team: str | None
    #: Chart x = max(p_win_home, 1 − p_win_home).
    p_chart: float
    abs_mu: float
    is_toss_up: bool
    #: For non-toss-up: chip-favored team; unused for toss-up equal-logo layout.
    primary_team_id: int
    secondary_team_id: int
    primary_school: str
    secondary_school: str


@dataclass
class PhiCheckResult:
    """Diagnostic: Φ(|μ|/σ) vs chart p_max (not used for drawing)."""

    max_abs_dev: float
    approximate: bool
    mean_dev: float
    median_dev: float
    min_dev: float
    max_dev: float
    n_p_below_phi: int
    n_p_above_phi: int
    worst: list[dict[str, Any]]


@dataclass
class SignDisagreement:
    """mu_margin sign disagrees with (p_win_home > 0.5)."""

    game_id: str
    home_team: str
    away_team: str
    mu_margin: float
    sigma_margin: float
    p_win_home: float
    conviction_tier: str | None


@dataclass
class SlateMapResult:
    """Render outputs + report fields."""

    points: list[SlatePoint]
    excluded: list[dict[str, Any]]
    week: int
    phi: PhiCheckResult
    sign_disagreements: list[SignDisagreement] = field(default_factory=list)
    shipped: bool = True
    stop_reason: str | None = None
    paths: list[Path] = field(default_factory=list)
    missing_logos: list[dict[str, Any]] = field(default_factory=list)
    logo_fallbacks: list[dict[str, Any]] = field(default_factory=list)
    footnotes: list[str] = field(default_factory=list)
    notes: dict[str, Any] = field(default_factory=dict)
    alt_text: str = ""
    caption: str = ""


def tier_enter_thresholds() -> dict[str, float]:
    """Vertical band boundaries from export enter constants (not retyped)."""
    return {
        "lean": float(TIER_LEAN_ENTER),
        "clear_lean": float(TIER_CLEAR_ENTER),
        "strong_lean": float(TIER_STRONG_ENTER),
    }


def tier_boundary_zs() -> dict[str, float]:
    """Legacy z_k = Φ⁻¹(p_k); kept for diagnostic / old tests."""
    th = tier_enter_thresholds()
    return {k: float(norm.ppf(v)) for k, v in th.items()}


def chart_p_max(p_win_home: float) -> float:
    """Published chart x: max(p_win_home, 1 − p_win_home)."""
    p = float(p_win_home)
    return max(p, 1.0 - p)


def favored_win_prob(p_win_home: float, favored_side: Literal["home", "away"]) -> float:
    """p on the named side (legacy helper)."""
    if favored_side == "home":
        return float(p_win_home)
    return float(1.0 - p_win_home)


def find_sign_disagreements(games: list[dict[str, Any]]) -> list[SignDisagreement]:
    """Games where sign(mu_margin) disagrees with (p_win_home > 0.5)."""
    out: list[SignDisagreement] = []
    for g in games:
        mu = g.get("mu_margin")
        p_home = g.get("p_win_home")
        if mu is None or p_home is None:
            continue
        mu_f = float(mu)
        p_f = float(p_home)
        if mu_f == 0.0:
            continue
        if (mu_f > 0.0) != (p_f > 0.5):
            out.append(
                SignDisagreement(
                    game_id=str(g.get("game_id")),
                    home_team=str(g.get("home_team")),
                    away_team=str(g.get("away_team")),
                    mu_margin=mu_f,
                    sigma_margin=(
                        float(g["sigma_margin"])
                        if g.get("sigma_margin") is not None
                        else float("nan")
                    ),
                    p_win_home=p_f,
                    conviction_tier=(
                        str(g["conviction_tier"])
                        if g.get("conviction_tier") is not None
                        else None
                    ),
                )
            )
    return out


def phi_check(points: list[SlatePoint]) -> PhiCheckResult:
    """Diagnostic max |p_chart − Φ(|μ|/σ)| (does not drive drawing)."""
    rows: list[dict[str, Any]] = []
    signed: list[float] = []
    for p in points:
        if p.sigma_margin <= 0:
            continue
        z = abs(p.mu_margin) / p.sigma_margin
        phi = float(norm.cdf(z))
        d = p.p_chart - phi
        signed.append(d)
        rows.append(
            {
                "game_id": p.game_id,
                "home_team": p.home_team,
                "away_team": p.away_team,
                "dev": abs(d),
                "signed_dev": d,
                "p_chart": p.p_chart,
                "phi": phi,
                "z": z,
            }
        )
    rows.sort(key=lambda r: r["dev"], reverse=True)
    max_abs = float(rows[0]["dev"]) if rows else 0.0
    if signed:
        arr = np.asarray(signed, dtype=float)
        mean_d = float(arr.mean())
        median_d = float(np.median(arr))
        min_d = float(arr.min())
        max_d = float(arr.max())
        n_below = int(np.sum(arr < 0))
        n_above = int(np.sum(arr > 0))
    else:
        mean_d = median_d = min_d = max_d = 0.0
        n_below = n_above = 0
    return PhiCheckResult(
        max_abs_dev=max_abs,
        approximate=max_abs > PHI_CHECK_TOLERANCE,
        mean_dev=mean_d,
        median_dev=median_d,
        min_dev=min_d,
        max_dev=max_d,
        n_p_below_phi=n_below,
        n_p_above_phi=n_above,
        worst=rows[:5],
    )


def _resolve_non_toss_primary(g: dict[str, Any], *, p_home: float) -> tuple[int, int, str, str]:
    """Map conviction_team → (primary_id, secondary_id, primary_school, secondary).

    Asserts the chip team's side has p ≥ 0.5.
    """
    home = str(g["home_team"])
    away = str(g["away_team"])
    home_id = int(g["home_team_id"])
    away_id = int(g["away_team_id"])
    conv = g.get("conviction_team")
    if conv is None:
        msg = f"game {g.get('game_id')}: non-toss-up missing conviction_team"
        raise SlateCoherenceError(msg)
    conv_s = str(conv)
    if conv_s == home:
        p_side = p_home
        if p_side < 0.5:
            msg = (
                f"game {g.get('game_id')}: conviction_team={conv_s!r} is home but "
                f"p_win_home={p_side:.4f} < 0.5"
            )
            raise SlateCoherenceError(msg)
        return home_id, away_id, home, away
    if conv_s == away:
        p_side = 1.0 - p_home
        if p_side < 0.5:
            msg = (
                f"game {g.get('game_id')}: conviction_team={conv_s!r} is away but "
                f"1-p_win_home={p_side:.4f} < 0.5"
            )
            raise SlateCoherenceError(msg)
        return away_id, home_id, away, home
    msg = (
        f"game {g.get('game_id')}: conviction_team={conv_s!r} matches neither "
        f"{home!r} nor {away!r}"
    )
    raise SlateCoherenceError(msg)


def extract_slate_points(
    week_predictions: dict[str, Any],
) -> tuple[list[SlatePoint], list[dict[str, Any]]]:
    """Games with non-null tier and credible sigma; footnote the rest."""
    points: list[SlatePoint] = []
    excluded: list[dict[str, Any]] = []
    for g in week_predictions.get("games") or []:
        tier = g.get("conviction_tier")
        credible = bool(g.get("sigma_margin_credible"))
        if tier is None or not credible:
            reasons = []
            if tier is None:
                reasons.append("null_tier")
            if not credible:
                reasons.append("sigma_not_credible")
            excluded.append({"game_id": g.get("game_id"), "reasons": reasons})
            continue
        mu = g.get("mu_margin")
        sigma = g.get("sigma_margin")
        p_home = g.get("p_win_home")
        if mu is None or sigma is None or p_home is None:
            excluded.append(
                {"game_id": g.get("game_id"), "reasons": ["missing_mu_sigma_or_p"]}
            )
            continue
        p_home_f = float(p_home)
        is_toss = str(tier) == "toss_up"
        home_id = int(g["home_team_id"])
        away_id = int(g["away_team_id"])
        home = str(g["home_team"])
        away = str(g["away_team"])
        if is_toss:
            # Equal-logo layout; primary/secondary unused for size hierarchy.
            primary_id, secondary_id = home_id, away_id
            primary_school, secondary_school = home, away
        else:
            primary_id, secondary_id, primary_school, secondary_school = (
                _resolve_non_toss_primary(g, p_home=p_home_f)
            )
        points.append(
            SlatePoint(
                game_id=str(g["game_id"]),
                home_team=home,
                away_team=away,
                home_team_id=home_id,
                away_team_id=away_id,
                mu_margin=float(mu),
                sigma_margin=float(sigma),
                p_win_home=p_home_f,
                conviction_tier=str(tier),
                conviction_team=(
                    str(g["conviction_team"]) if g.get("conviction_team") is not None else None
                ),
                p_chart=chart_p_max(p_home_f),
                abs_mu=abs(float(mu)),
                is_toss_up=is_toss,
                primary_team_id=primary_id,
                secondary_team_id=secondary_id,
                primary_school=primary_school,
                secondary_school=secondary_school,
            )
        )
    points.sort(key=lambda p: p.game_id)
    return points, excluded


def _shade_tier_bands(
    ax: Any,
    *,
    y_max: float,
    thresholds: dict[str, float],
    label_fontsize: float = 14,
) -> None:
    """Exact vertical bands at export enter thresholds; navy→slate labels."""
    p_lean = thresholds["lean"]
    p_clear = thresholds["clear_lean"]
    p_strong = thresholds["strong_lean"]
    edges = [
        (0.50, p_lean, "toss_up"),
        (p_lean, p_clear, "lean"),
        (p_clear, p_strong, "clear_lean"),
        (p_strong, 1.00, "strong_lean"),
    ]
    color_by_tier = {k: (c, a) for k, c, a in _TIER_BANDS}
    for x0, x1, tier in edges:
        color, alpha = color_by_tier[tier]
        ax.axvspan(x0, x1, facecolor=color, alpha=alpha, edgecolor="none", zorder=0)
        ax.text(
            (x0 + x1) / 2.0,
            y_max * 0.97,
            _TIER_LABELS[tier],
            ha="center",
            va="top",
            fontsize=label_fontsize,
            color=LABEL_WHITE_70,
            zorder=1,
            clip_on=True,
        )
    for x in (p_lean, p_clear, p_strong):
        ax.axvline(x, color="#5A6A80", linewidth=0.7, zorder=1)


def render_slate_map(
    *,
    points: list[SlatePoint],
    excluded: list[dict[str, Any]],
    week: int,
    season: int,
    refresh_kind: str | None,
    published_at: str | None,
    sizes: list[ChartSize],
    out_dir: Path,
    footer: str,
    logo_dir: Path | None = None,
    allow_missing_logos: bool = False,
    fixture_watermark: bool = False,
    abbrev_by_id: dict[int, str] | None = None,
    logo_zoom: float = 0.055,
    source_games: list[dict[str, Any]] | None = None,
) -> SlateMapResult:
    """Render slate map PNGs (x=max(p,1−p), y=|μ|)."""
    abbrev_by_id = abbrev_by_id or {}
    phi = phi_check(points)
    thresholds = tier_enter_thresholds()

    games_for_sign = source_games
    if games_for_sign is None:
        games_for_sign = [
            {
                "game_id": p.game_id,
                "home_team": p.home_team,
                "away_team": p.away_team,
                "mu_margin": p.mu_margin,
                "sigma_margin": p.sigma_margin,
                "p_win_home": p.p_win_home,
                "conviction_tier": p.conviction_tier,
            }
            for p in points
        ]
    disagreements = find_sign_disagreements(games_for_sign)

    needed_ids: set[int] = set()
    for p in points:
        needed_ids.add(p.home_team_id)
        needed_ids.add(p.away_team_id)
    assets: dict[int, LogoAsset] = {}
    missing: list[dict[str, Any]] = []
    fallbacks: list[dict[str, Any]] = []
    for tid in sorted(needed_ids):
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

    hysteresis_note = (
        "Tiers include hysteresis, so games near a boundary may sit just across a wedge."
    )
    threshold_note = (
        "Vertical bands are exact export enter thresholds on max(p_win_home, 1−p)."
    )
    exclusion_note = (
        f"{len(excluded)} games without a published tier not shown"
        if excluded
        else "All slate games with a published tier shown."
    )
    toss_up_sign_note = (
        f"{len(disagreements)} toss-ups where expected margin and win "
        "probability lean opposite ways."
        if disagreements
        else None
    )
    footnotes = [exclusion_note, hysteresis_note, threshold_note]
    if toss_up_sign_note:
        footnotes.append(toss_up_sign_note)
    notes: dict[str, Any] = {
        "exclusion_count": len(excluded),
        "exclusion_note": exclusion_note,
        "hysteresis_note": hysteresis_note,
        "threshold_note": threshold_note,
        "toss_up_sign_note": toss_up_sign_note,
        "band_guide_disclaimer": (
            "Band labels are Title Case guides on published conviction tiers; "
            "dots use the published tier (hysteresis may place a game just across a band)."
        ),
    }

    notable = sorted(points, key=lambda p: p.p_chart, reverse=True)[:3]
    day = publish_day_short(published_at)
    alt = (
        f"Slate map for week {week} {season}: max published win chance vs "
        f"|expected margin|. {len(points)} games shown. {exclusion_note} "
        f"{hysteresis_note} {threshold_note}"
        + (f" {toss_up_sign_note}" if toss_up_sign_note else "")
        + " Highest p: "
        + "; ".join(
            f"{p.home_team}/{p.away_team} (p={p.p_chart:.3f}, |μ|={p.abs_mu:.1f})"
            for p in notable
        )
        + "."
    )
    caption = (
        f"Week {week} slate — how sure is Ridge? "
        + ", ".join(
            (p.primary_school if not p.is_toss_up else p.home_team) for p in notable
        )
        + f" top the conviction map. {len(points)} games shown."
    )

    x_min, x_max = 0.50, 1.00
    y_max = max((p.abs_mu for p in points), default=1.0) * 1.15
    y_max = max(y_max, 5.0)

    subtitle = f"Week {week} · {len(points)} games · how sure Ridge is, as of {day}"
    # refresh_kind kept for report/alt provenance (not on-image).
    _ = refresh_kind

    paths: list[Path] = []
    for size in sizes:
        fig = new_figure(size)
        add_header(
            fig,
            title="This week's slate: how sure is Ridge?",
            subtitle=subtitle,
            size=size,
        )
        ax = add_panel_axes(fig)
        ax.set_xlim(x_min, x_max)
        ax.set_ylim(0.0, y_max)
        band_fs = 14 if size.width >= 1200 else 8
        _shade_tier_bands(
            ax, y_max=y_max, thresholds=thresholds, label_fontsize=band_fs
        )

        for p in points:
            color = _TIER_DOT.get(p.conviction_tier, TOSS_UP_GRAY)
            draw_exact_dot(ax, p.p_chart, p.abs_mu, color=color, size=12)
            if p.is_toss_up:
                # Equal logos side by side (home left, away right).
                dx = 0.014
                for tid, x_off in (
                    (p.home_team_id, -dx),
                    (p.away_team_id, dx),
                ):
                    asset = assets.get(tid)
                    if asset is not None:
                        draw_logo(
                            ax,
                            asset,
                            p.p_chart + x_off,
                            p.abs_mu,
                            zoom=logo_zoom * 0.85,
                        )
                    else:
                        draw_text_chip(
                            ax,
                            abbrev_by_id.get(tid, "?"),
                            p.p_chart + x_off,
                            p.abs_mu,
                            fontsize=6,
                        )
            else:
                dx = 0.012
                dy = -y_max * 0.025
                fav = assets.get(p.primary_team_id)
                opp = assets.get(p.secondary_team_id)
                if fav is not None:
                    draw_logo(ax, fav, p.p_chart, p.abs_mu, zoom=logo_zoom)
                else:
                    draw_text_chip(
                        ax,
                        abbrev_by_id.get(p.primary_team_id, "?"),
                        p.p_chart,
                        p.abs_mu,
                    )
                if opp is not None:
                    draw_logo(
                        ax,
                        opp,
                        p.p_chart + dx,
                        p.abs_mu + dy,
                        zoom=logo_zoom * 0.55,
                        zorder=3.5,
                    )
                else:
                    draw_text_chip(
                        ax,
                        abbrev_by_id.get(p.secondary_team_id, "?"),
                        p.p_chart + dx,
                        p.abs_mu + dy,
                        fontsize=6,
                    )

        ax.set_xlabel("Published max(p_win_home, 1−p)", color=TEXT_SECONDARY)
        ax.set_ylabel("|Expected margin| (points)", color=TEXT_SECONDARY)
        add_footer(fig, footer)
        if fixture_watermark:
            add_fixture_watermark(fig)
        assert_no_text_bbox_overlap(fig)
        out_path = out_dir / f"slate_map_w{week}_{size.name}.png"
        save_png(fig, out_path)
        paths.append(out_path)
        from matplotlib import pyplot as plt

        plt.close(fig)

    return SlateMapResult(
        points=points,
        excluded=excluded,
        week=week,
        phi=phi,
        sign_disagreements=disagreements,
        shipped=True,
        stop_reason=None,
        paths=paths,
        missing_logos=missing,
        logo_fallbacks=fallbacks,
        footnotes=footnotes,
        notes=notes,
        alt_text=alt,
        caption=caption,
    )
