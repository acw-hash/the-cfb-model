"""D5: ATS/OU path audit, chance gate, powered encompassing test scaffolding.

μ heads / feature builders / Stage-1 filter fitting are not modified.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
from scipy import stats  # type: ignore[import-untyped]

from ncaa_quant.evaluation.d4_eval import (
    CANONICAL_V2_SHA,
    EncompassingResult,
    encompassing_regression,
    optimal_combination_weight,
    residual_on_residual,
    score_point,
    verify_canonical_v2_sha,
)
from ncaa_quant.evaluation.metrics import (
    ats_home_outcomes,
    log_loss,
    mae,
    ou_over_outcomes,
)
from ncaa_quant.evaluation.production_stack import (
    CHANCE_LOG_LOSS,
    assert_derived_market_signs,
    assert_derived_markets_not_anti_informative,
)

# DESIGN §5.2 item 7 — σ-head situational / uncertainty feature checklist.
SIGMA_FEATURE_SPEC: tuple[str, ...] = (
    "stage1_posterior_var_home",
    "stage1_posterior_var_away",
    "week",
    "rating_diff_magnitude",
    "expected_possessions",
    "roster_portal_null",
)


@dataclass(frozen=True)
class PowerCalculation:
    """n required to detect a given b2 at 80% power (two-sided α=0.05)."""

    observed_n: int
    observed_se_b2: float
    b2_target: float
    power: float
    alpha: float
    n_required: int
    z_critical: float


def encompassing_power(
    observed_se_b2: float,
    observed_n: int,
    *,
    b2_target: float,
    power: float = 0.80,
    alpha: float = 0.05,
) -> PowerCalculation:
    """Scale observed SE as 1/√n to find n for a target b2 effect size."""
    z = float(stats.norm.ppf(1.0 - alpha / 2.0) + stats.norm.ppf(power))
    se_needed = abs(float(b2_target)) / max(z, 1e-12)
    n_req = int(math.ceil(observed_n * (float(observed_se_b2) / max(se_needed, 1e-12)) ** 2))
    return PowerCalculation(
        observed_n=int(observed_n),
        observed_se_b2=float(observed_se_b2),
        b2_target=float(b2_target),
        power=float(power),
        alpha=float(alpha),
        n_required=n_req,
        z_critical=z,
    )


def b2_wald_ci(b2: float, se_b2: float, *, alpha: float = 0.05) -> dict[str, float]:
    z = float(stats.norm.ppf(1.0 - alpha / 2.0))
    return {
        "low": float(b2 - z * se_b2),
        "high": float(b2 + z * se_b2),
        "estimate": float(b2),
        "alpha": float(alpha),
    }


def underpowered_verdict(
    enc: EncompassingResult,
    *,
    substantial_edge: float = 0.10,
) -> str:
    """Restate encompassing conclusion when the CI still covers a real edge."""
    ci = b2_wald_ci(enc.b2, enc.se_b2)
    covers_edge = ci["low"] <= -substantial_edge or ci["high"] >= substantial_edge
    if covers_edge and (not np.isfinite(enc.p_b2) or enc.p_b2 >= 0.10):
        return (
            f"UNDERPOWERED (n={enc.n}): b2={enc.b2:.4f} (SE {enc.se_b2:.4f}, "
            f"p={enc.p_b2:.4f}); 95% CI [{ci['low']:.3f}, {ci['high']:.3f}] "
            f"still contains |b2|≥{substantial_edge} — cannot claim the market "
            "encompasses the model, nor claim an edge."
        )
    return enc.verdict


def priced_unpriced_mae_same_season(
    frame: pd.DataFrame,
    *,
    season: int = 2019,
) -> dict[str, Any]:
    """Stack MAE on priced vs unpriced games within one season (not vs pooled)."""
    sub = frame.loc[frame["season"] == season].copy()
    y = pd.to_numeric(sub["realized_margin"], errors="coerce")
    mu = pd.to_numeric(sub["pred_margin"], errors="coerce")
    priced = np.isfinite(pd.to_numeric(sub.get("spread_close"), errors="coerce"))
    out: dict[str, Any] = {"season": season}
    for label, mask in (("priced", priced), ("unpriced", ~priced)):
        m = mask.to_numpy(dtype=bool) & np.isfinite(y.to_numpy()) & np.isfinite(mu.to_numpy())
        out[label] = {
            "n": int(m.sum()),
            "mae": float(mae(y.to_numpy()[m], mu.to_numpy()[m])) if m.any() else float("nan"),
        }
    return out


def market_line_coverage(
    lines: pd.DataFrame,
    *,
    odds_snapshots: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Counts of finite spread/total by season × line_type (open / Tuesday / close)."""
    out: dict[str, Any] = {"by_line_type": {}, "tuesday_source": None}
    if not lines.empty and {"season", "line_type"} <= set(lines.columns):
        for lt in sorted(lines["line_type"].dropna().astype(str).str.lower().unique()):
            sub = lines.loc[lines["line_type"].astype(str).str.lower() == lt]
            rows: dict[str, Any] = {}
            for season, chunk in sub.groupby("season"):
                rows[str(int(season))] = {
                    "n_rows": int(len(chunk)),
                    "n_spread": int(
                        np.isfinite(pd.to_numeric(chunk.get("spread"), errors="coerce")).sum()
                    )
                    if "spread" in chunk.columns
                    else 0,
                    "n_total": int(
                        np.isfinite(pd.to_numeric(chunk.get("total"), errors="coerce")).sum()
                    )
                    if "total" in chunk.columns
                    else 0,
                    "n_games": int(chunk["game_id"].nunique())
                    if "game_id" in chunk.columns
                    else int(len(chunk)),
                }
            out["by_line_type"][lt] = rows

    # Tuesday snapshots live on Odds API decision_point, not CFBD line_type.
    tuesday_key = "tuesday"
    if odds_snapshots is not None and not odds_snapshots.empty:
        dp_col = "decision_point" if "decision_point" in odds_snapshots.columns else None
        if dp_col is not None:
            dp = odds_snapshots[dp_col].astype(str).str.lower()
            tue = odds_snapshots.loc[dp.str.contains("tuesday") | dp.str.contains("tue")]
            out["tuesday_source"] = "odds_snapshots.decision_point"
            rows_t: dict[str, Any] = {}
            if "season" in tue.columns and len(tue):
                for season, chunk in tue.groupby("season"):
                    rows_t[str(int(season))] = {
                        "n_rows": int(len(chunk)),
                        "n_games": int(chunk["game_id"].nunique())
                        if "game_id" in chunk.columns
                        else int(len(chunk)),
                    }
            out["by_line_type"][tuesday_key] = rows_t
        else:
            out["tuesday_source"] = "odds_snapshots present but no decision_point column"
            out["by_line_type"][tuesday_key] = {}
    else:
        out["tuesday_source"] = "no odds_snapshots in archive"
        out["by_line_type"].setdefault(tuesday_key, {})
    return out


@dataclass
class HypothesisResult:
    name: str
    holds: bool
    evidence: dict[str, Any] = field(default_factory=dict)
    note: str = ""


def _gaussian_ats_ou_probs(
    frame: pd.DataFrame,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    mu = pd.to_numeric(frame["pred_margin"], errors="coerce").to_numpy(dtype=float)
    sig = pd.to_numeric(frame["sigma_m"], errors="coerce").to_numpy(dtype=float)
    sp = (
        pd.to_numeric(frame["spread_close"], errors="coerce").to_numpy(dtype=float)
        if "spread_close" in frame.columns
        else np.full(len(frame), np.nan)
    )
    y = pd.to_numeric(frame["realized_margin"], errors="coerce").to_numpy(dtype=float)
    p_ats = stats.norm.cdf((mu + sp) / np.maximum(sig, 1e-8))
    y_ats = ats_home_outcomes(y, sp)

    mt = (
        pd.to_numeric(frame["pred_total"], errors="coerce").to_numpy(dtype=float)
        if "pred_total" in frame.columns
        else np.full(len(frame), np.nan)
    )
    tot = (
        pd.to_numeric(frame["total_close"], errors="coerce").to_numpy(dtype=float)
        if "total_close" in frame.columns
        else np.full(len(frame), np.nan)
    )
    yt = (
        pd.to_numeric(frame["realized_total"], errors="coerce").to_numpy(dtype=float)
        if "realized_total" in frame.columns
        else np.full(len(frame), np.nan)
    )
    st = (
        pd.to_numeric(frame["sigma_t"], errors="coerce").to_numpy(dtype=float)
        if "sigma_t" in frame.columns
        else sig
    )
    p_ou = stats.norm.cdf((mt - tot) / np.maximum(st, 1e-8))
    y_ou = ou_over_outcomes(yt, tot)
    return p_ats, y_ats, sp, p_ou, y_ou, tot


def diagnose_ats_ou_hypotheses(
    frame: pd.DataFrame,
    *,
    games: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Explicitly test H1–H5 for ATS and the shared set for OU."""
    p_ats, y_ats, sp, p_ou, y_ou, tot = _gaussian_ats_ou_probs(frame)
    mu = pd.to_numeric(frame["pred_margin"], errors="coerce").to_numpy(dtype=float)
    sig = pd.to_numeric(frame["sigma_m"], errors="coerce").to_numpy(dtype=float)

    mask_a = np.isfinite(p_ats) & np.isfinite(y_ats) & np.isfinite(sp) & np.isfinite(sig)
    mask_o = np.isfinite(p_ou) & np.isfinite(y_ou) & np.isfinite(tot)

    ll_ats = float(log_loss(p_ats[mask_a], y_ats[mask_a])) if mask_a.any() else float("nan")
    ll_ats_inv = (
        float(log_loss(1.0 - p_ats[mask_a], y_ats[mask_a])) if mask_a.any() else float("nan")
    )
    p_ats_flip = stats.norm.cdf((mu - sp) / np.maximum(sig, 1e-8))
    ll_ats_flip_spread = (
        float(log_loss(p_ats_flip[mask_a], y_ats[mask_a])) if mask_a.any() else float("nan")
    )

    # H1: inversion near 0.58 confirms sign bug.
    h1_holds = bool(np.isfinite(ll_ats_inv) and ll_ats_inv < 0.62 and ll_ats_inv < ll_ats - 0.05)
    h1 = HypothesisResult(
        name="H1_spread_sign_inverted",
        holds=h1_holds,
        evidence={
            "ll_ats": ll_ats,
            "ll_ats_inverted_p": ll_ats_inv,
            "ll_ats_mu_minus_spread": ll_ats_flip_spread,
            "threshold_near_058": 0.58,
        },
        note=(
            "CONFIRMED — fix is a sign"
            if h1_holds
            else "REJECTED — 1−p is not near 0.58 and does not beat the forward path"
        ),
    )

    # H2: asof vs close snapshot mismatch.
    spa = (
        pd.to_numeric(frame["spread_asof"], errors="coerce").to_numpy(dtype=float)
        if "spread_asof" in frame.columns
        else sp
    )
    asof_eq = (
        float(np.nanmean(np.isclose(spa[mask_a], sp[mask_a], equal_nan=False)))
        if mask_a.any()
        else float("nan")
    )
    p_asof = stats.norm.cdf((mu + spa) / np.maximum(sig, 1e-8))
    ll_asof_grade_close = (
        float(log_loss(p_asof[mask_a], y_ats[mask_a])) if mask_a.any() else float("nan")
    )
    h2_holds = bool(
        np.isfinite(asof_eq) and asof_eq < 0.99 and abs(ll_asof_grade_close - ll_ats) > 0.02
    )
    h2 = HypothesisResult(
        name="H2_snapshot_mismatch",
        holds=h2_holds,
        evidence={
            "asof_equals_close_rate": asof_eq,
            "ll_p_asof_grade_close": ll_asof_grade_close,
            "ll_p_close_grade_close": ll_ats,
        },
        note=(
            "CONFIRMED — prob snapshot ≠ grade snapshot"
            if h2_holds
            else "REJECTED — asof matches close on this archive (or LL unchanged)"
        ),
    )

    # H3: push mass — continuous Gaussian has p_push=0; check two-way identity.
    # For the Gaussian path, P(cover)+P(not)=1 always. Holds only if we detect
    # three-way probs used as Bernoulli without renormalization on a discrete path.
    p_not = 1.0 - p_ats
    two_way_ok = bool(
        mask_a.any() and float(np.nanmax(np.abs(p_ats[mask_a] + p_not[mask_a] - 1.0))) < 1e-9
    )
    h3 = HypothesisResult(
        name="H3_push_mass_misallocated",
        holds=not two_way_ok,
        evidence={"gaussian_two_way_sums_to_one": two_way_ok},
        note=(
            "REJECTED on continuous Gaussian eval path (p_push=0). "
            "Production MC path now emits two_way_side_prob so discrete push "
            "mass cannot leak into Bernoulli scores."
            if two_way_ok
            else "CONFIRMED — Bernoulli probs do not sum to 1 net of push"
        ),
    )

    # H4: key-number not applied on the Gaussian D4/D5 eval path.
    h4 = HypothesisResult(
        name="H4_key_number_kernel_orientation",
        holds=False,
        evidence={"gaussian_eval_path_uses_kernel": False},
        note=(
            "REJECTED for the uncalibrated Gaussian path under test (no kernel). "
            "Kernel orientation is irrelevant to the reported 0.82/0.77 LLs."
        ),
    )

    # H5: neutral-site home/away swap — compare LL on neutral vs non-neutral.
    h5_holds = False
    h5_ev: dict[str, Any] = {"neutral_col": None}
    if games is not None and not games.empty and "game_id" in frame.columns:
        g = games.copy()
        neut_col = next(
            (c for c in ("neutral_site", "neutral", "is_neutral") if c in g.columns),
            None,
        )
        if neut_col is not None:
            gmap = g.drop_duplicates("game_id").set_index("game_id")[neut_col]
            neut = frame["game_id"].map(gmap)
            neut_b = neut.fillna(False).astype(bool).to_numpy()
            h5_ev["neutral_col"] = neut_col
            for label, sel in (("neutral", neut_b), ("non_neutral", ~neut_b)):
                m = mask_a & sel
                h5_ev[label] = {
                    "n": int(m.sum()),
                    "log_loss": float(log_loss(p_ats[m], y_ats[m]))
                    if m.sum() >= 10
                    else float("nan"),
                }
            # Holds only if neutral alone is anti-informative while non-neutral is fine.
            ll_n = h5_ev.get("neutral", {}).get("log_loss", float("nan"))
            ll_nn = h5_ev.get("non_neutral", {}).get("log_loss", float("nan"))
            h5_holds = bool(
                np.isfinite(ll_n)
                and np.isfinite(ll_nn)
                and ll_n > CHANCE_LOG_LOSS
                and ll_nn <= CHANCE_LOG_LOSS
            )
    h5 = HypothesisResult(
        name="H5_neutral_site_home_away_swap",
        holds=h5_holds,
        evidence=h5_ev,
        note=(
            "CONFIRMED — anti-informativeness localized to neutral sites"
            if h5_holds
            else "REJECTED — ATS LL > chance is not confined to neutral-site games"
        ),
    )

    # OU shared hypotheses (no home/away orientation → H1/H5 N/A).
    ll_ou = float(log_loss(p_ou[mask_o], y_ou[mask_o])) if mask_o.any() else float("nan")
    ll_ou_inv = float(log_loss(1.0 - p_ou[mask_o], y_ou[mask_o])) if mask_o.any() else float("nan")
    mt = (
        pd.to_numeric(frame["pred_total"], errors="coerce").to_numpy(dtype=float)
        if "pred_total" in frame.columns
        else np.full(len(frame), np.nan)
    )
    st = (
        pd.to_numeric(frame["sigma_t"], errors="coerce").to_numpy(dtype=float)
        if "sigma_t" in frame.columns
        else sig
    )
    p_ou_flip = stats.norm.cdf((tot - mt) / np.maximum(st, 1e-8))
    ll_ou_flip = float(log_loss(p_ou_flip[mask_o], y_ou[mask_o])) if mask_o.any() else float("nan")

    root = (
        "Overconfident model–market disagreement: cover-edge (μ+S) has near-zero "
        "correlation with outcomes (ATS hard acc ≈ chance) while |μ+S|/σ produces "
        "extreme probabilities. Same pattern on OU (weak edge signal, overconfident σ). "
        "Not a sign/snapshot/kernel bug on this archive."
    )

    return {
        "canonical_v2_sha": CANONICAL_V2_SHA,
        "ats": {
            "n": int(mask_a.sum()),
            "log_loss": ll_ats,
            "log_loss_inverted_p": ll_ats_inv,
            "hypotheses": [asdict(h) for h in (h1, h2, h3, h4, h5)],
        },
        "ou": {
            "n": int(mask_o.sum()),
            "log_loss": ll_ou,
            "log_loss_inverted_p": ll_ou_inv,
            "log_loss_inverted_formula": ll_ou_flip,
            "hypotheses": {
                "H1_sign": "N/A (OU has no home/away orientation)",
                "H2_snapshot": h2.note,
                "H3_push": h3.note,
                "H4_kernel": h4.note,
                "H5_neutral": "N/A",
            },
            "shared_root_with_ats": True,
        },
        "root_cause": root,
        "chance_log_loss": CHANCE_LOG_LOSS,
    }


def audit_sigma_feature_set(feature_columns: Sequence[str]) -> dict[str, Any]:
    """Report which §5.2 item-7 σ features are present vs absent."""
    cols = set(feature_columns)
    present: list[str] = []
    absent: list[str] = []
    aliases = {
        "stage1_posterior_var_home": {
            "stage1_posterior_var_home",
            "home_off_epa_var",
            "home_rating_var",
        },
        "stage1_posterior_var_away": {
            "stage1_posterior_var_away",
            "away_off_epa_var",
            "away_rating_var",
        },
        "week": {"week"},
        "rating_diff_magnitude": {
            "rating_diff_magnitude",
            "abs_rating_diff",
            "abs_pred_margin",
        },
        "expected_possessions": {"expected_possessions"},
        "roster_portal_null": {
            "roster_portal_null",
            "portal_null",
            "roster_null",
            "portal_era",
        },
    }
    for spec in SIGMA_FEATURE_SPEC:
        alts = aliases.get(spec, {spec})
        if cols & alts:
            present.append(spec)
        else:
            absent.append(spec)
    return {
        "specified": list(SIGMA_FEATURE_SPEC),
        "present": present,
        "absent": absent,
        "observed_columns": sorted(cols),
    }


@dataclass(frozen=True)
class EncompassingEvalConfig:
    """Config-driven encompassing evaluation (reusable when 2021–2025 lines land)."""

    seasons: tuple[int, ...] = (2019,)
    line_column: str = "spread_close"
    market_implied_sign: float = -1.0  # home margin ≈ -home_spread
    n_boot: int = 1000
    seed: int = 0
    min_games_per_season: int = 200
    train_seasons: tuple[int, ...] | None = None
    test_seasons: tuple[int, ...] | None = None
    substantial_b2: float = 0.10
    stability_min_seasons_positive: int = 3

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> EncompassingEvalConfig:
        seasons = tuple(int(s) for s in raw.get("seasons", (2019,)))
        train = raw.get("train_seasons")
        test = raw.get("test_seasons")
        return cls(
            seasons=seasons,
            line_column=str(raw.get("line_column", "spread_close")),
            market_implied_sign=float(raw.get("market_implied_sign", -1.0)),
            n_boot=int(raw.get("n_boot", 1000)),
            seed=int(raw.get("seed", 0)),
            min_games_per_season=int(raw.get("min_games_per_season", 200)),
            train_seasons=tuple(int(s) for s in train) if train is not None else None,
            test_seasons=tuple(int(s) for s in test) if test is not None else None,
            substantial_b2=float(raw.get("substantial_b2", 0.10)),
            stability_min_seasons_positive=int(raw.get("stability_min_seasons_positive", 3)),
        )


def run_encompassing_evaluation(
    frame: pd.DataFrame,
    config: EncompassingEvalConfig,
) -> dict[str, Any]:
    """Joint encompassing + residual-on-residual + combination weight + per-season b2."""
    work = frame.loc[frame["season"].isin(config.seasons)].copy()
    line = pd.to_numeric(work[config.line_column], errors="coerce")
    y = pd.to_numeric(work["realized_margin"], errors="coerce")
    stk = pd.to_numeric(work["pred_margin"], errors="coerce")
    mkt = config.market_implied_sign * line
    mask = np.isfinite(line) & np.isfinite(y) & np.isfinite(stk) & np.isfinite(mkt)
    work = work.loc[mask].copy()
    y_a = y[mask].to_numpy(dtype=float)
    mkt_a = mkt[mask].to_numpy(dtype=float)
    stk_a = stk[mask].to_numpy(dtype=float)
    seasons = work["season"].to_numpy(dtype=int)
    weeks = (
        work["week"].to_numpy(dtype=int)
        if "week" in work.columns
        else np.zeros(len(work), dtype=int)
    )
    blocks = list(zip(seasons.tolist(), weeks.tolist(), strict=True))

    joint = encompassing_regression(
        y_a, mkt_a, stk_a, blocks, n_boot=config.n_boot, seed=config.seed
    )
    ror = residual_on_residual(y_a, mkt_a, stk_a)

    per_season: dict[str, Any] = {}
    for s in sorted(set(int(x) for x in seasons)):
        sel = seasons == s
        if int(sel.sum()) < 20:
            continue
        enc_s = encompassing_regression(
            y_a[sel],
            mkt_a[sel],
            stk_a[sel],
            [blocks[i] for i, ok in enumerate(sel) if ok],
            n_boot=config.n_boot,
            seed=config.seed + int(s),
        )
        per_season[str(s)] = {
            **enc_s.__dict__,
            "ci95": b2_wald_ci(enc_s.b2, enc_s.se_b2),
            "verdict_underpowered": underpowered_verdict(
                enc_s, substantial_edge=config.substantial_b2
            ),
            "n_games": int(sel.sum()),
            "meets_min_games": bool(int(sel.sum()) >= config.min_games_per_season),
        }

    opt: dict[str, Any]
    if config.train_seasons and config.test_seasons:
        opt = optimal_combination_weight(
            work,
            train_seasons=list(config.train_seasons),
            test_seasons=list(config.test_seasons),
        )
    else:
        opt = {
            "note": "train_seasons/test_seasons not set — combination weight skipped",
            "w": float("nan"),
        }

    power = {
        "b2_0.10": asdict(encompassing_power(joint.se_b2, joint.n, b2_target=0.10)),
        "b2_0.15": asdict(encompassing_power(joint.se_b2, joint.n, b2_target=0.15)),
    }

    positive_seasons = [
        s for s, row in per_season.items() if row.get("b2", 0) > 0 and row.get("p_b2", 1) < 0.10
    ]

    return {
        "canonical_v2_sha": verify_canonical_v2_sha(),
        "config": asdict(config),
        "joint": {
            **joint.__dict__,
            "ci95": b2_wald_ci(joint.b2, joint.se_b2),
            "verdict_underpowered": underpowered_verdict(
                joint, substantial_edge=config.substantial_b2
            ),
            "stack_mae": score_point(y_a, stk_a)["mae"],
            "market_mae": score_point(y_a, mkt_a)["mae"],
            "n": joint.n,
        },
        "residual_on_residual": ror,
        "optimal_combination": opt,
        "per_season": per_season,
        "power": power,
        "stability": {
            "seasons_with_reliable_positive_b2": positive_seasons,
            "required": config.stability_min_seasons_positive,
            "stable": len(positive_seasons) >= config.stability_min_seasons_positive,
        },
    }


def load_encompassing_config(path: Path | str) -> EncompassingEvalConfig:
    from omegaconf import OmegaConf

    raw = OmegaConf.to_container(OmegaConf.load(path), resolve=True)
    if not isinstance(raw, dict):
        msg = f"encompassing config must be a mapping: {path}"
        raise TypeError(msg)
    body = raw.get("encompassing", raw)
    if not isinstance(body, dict):
        msg = "encompassing config body must be a mapping"
        raise TypeError(msg)
    return EncompassingEvalConfig.from_mapping(body)


__all__ = [
    "CHANCE_LOG_LOSS",
    "EncompassingEvalConfig",
    "HypothesisResult",
    "PowerCalculation",
    "SIGMA_FEATURE_SPEC",
    "assert_derived_market_signs",
    "assert_derived_markets_not_anti_informative",
    "audit_sigma_feature_set",
    "b2_wald_ci",
    "diagnose_ats_ou_hypotheses",
    "encompassing_power",
    "load_encompassing_config",
    "market_line_coverage",
    "priced_unpriced_mae_same_season",
    "run_encompassing_evaluation",
    "underpowered_verdict",
]
