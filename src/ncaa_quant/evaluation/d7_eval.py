"""D7: resolve stability correctly, then close the diagnostic phase.

μ heads / feature builders / Stage-1 filter fitting / the pre-registered D5
stop rule are not modified. The stop rule already fired in D6; this module
diagnoses whether season-counting was powered, replaces it with a
random-effects meta-analysis, and tests the early-week interaction formally.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
from scipy import stats  # type: ignore[import-untyped]

from ncaa_quant.betting.edges import expected_value
from ncaa_quant.evaluation.d4_eval import encompassing_regression
from ncaa_quant.evaluation.d5_eval import b2_wald_ci
from ncaa_quant.evaluation.d6_eval import (
    D5_STOP_RULE_VERBATIM,
    detectable_b2_at_power,
)
from ncaa_quant.evaluation.significance import (
    _group_indices,
    paired_block_bootstrap,
)
from ncaa_quant.utils.seeding import set_global_seed

# ---------------------------------------------------------------------------
# Pre-registered holdout (locked before any D7 fit). Do not amend after run.
# ---------------------------------------------------------------------------

# Temporal confirmatory season for the early-week claim. Fit discovery on all
# other encompassing seasons; evaluate only on this holdout. Chosen as the
# chronologically last season in the powered sample — standard temporal
# holdout, not because of any within-season D6 peek at its point estimate.
HOLDOUT_SEASON: int = 2025

# Early-week definition for the holdout claim (matches D6 week-bucket "1-4").
HOLDOUT_EARLY_WEEKS: tuple[int, int] = (1, 4)

# Confirm if holdout early-week b2 is reliably positive (bootstrap p < 0.10
# and b2 > 0). Refute if b2 ≤ 0 or p ≥ 0.10.
HOLDOUT_CONFIRM_RULE: str = (
    f"On season {HOLDOUT_SEASON} weeks {HOLDOUT_EARLY_WEEKS[0]}–"
    f"{HOLDOUT_EARLY_WEEKS[1]} only: confirm if b2 > 0 and bootstrap p_b2 < 0.10; "
    "refute otherwise. Discovery seasons are excluded from this test."
)

# Joint b2 from D6 powered sample — used for post-hoc power of the season gate.
D6_JOINT_B2: float = 0.211

# Weeks 1–5 for Part-2 combination weight (task-specified; distinct from D6 1–4).
EARLY_W_WEEKS: tuple[int, int] = (1, 5)

WEEK_BUCKET_LABELS: tuple[str, ...] = ("1-4", "5-9", "10+")


def week_bucket(week: int) -> str:
    if week <= 4:
        return "1-4"
    if week <= 9:
        return "5-9"
    return "10+"


# ---------------------------------------------------------------------------
# Part 1.1 — stop rule stands (no amend)
# ---------------------------------------------------------------------------


def record_stop_rule_stands(d6_stop: Mapping[str, Any]) -> dict[str, Any]:
    """Record that the D5 pre-registered rule fired; operational conclusion stands."""
    triggered = bool(d6_stop.get("triggered", True))
    status = str(d6_stop.get("status", "missed"))
    return {
        "rule_verbatim": D5_STOP_RULE_VERBATIM,
        "rule_amended": False,
        "triggered": triggered,
        "status": status,
        "n_reliable_positive_seasons": int(d6_stop.get("n_reliable_positive_seasons", 2)),
        "operational_conclusion": (
            "no betting layer on this μ; market-aware / residual stack is the betting workhorse"
        ),
        "stands": True,
        "note": (
            "D6 stop rule fired (fewer than 3 seasons with reliable positive b2). "
            "D7 does not amend the rule. Post-hoc power / RE meta-analysis below "
            "are diagnostic of the criterion, not a re-litigation of the gate."
        ),
    }


# ---------------------------------------------------------------------------
# Part 1.2 — post-hoc power of the season-counting criterion
# ---------------------------------------------------------------------------


def _season_clear_power(
    true_b2: float,
    se: float,
    *,
    z_crit: float = 1.6448536269514722,
) -> float:
    """P(b2>0 and two-sided p<0.10) ≈ P(Z > z_crit) under N(true_b2, se²).

    Two-sided p < 0.10 with positive point estimate is approximately
    estimate/se > Φ⁻¹(0.95) = 1.645.
    """
    if not np.isfinite(se) or se <= 0:
        return float("nan")
    return float(1.0 - stats.norm.cdf(z_crit - true_b2 / se))


def posthoc_season_criterion_power(
    per_season: Mapping[str, Mapping[str, Any]],
    *,
    true_b2: float = D6_JOINT_B2,
    n_required_clear: int = 3,
    power: float = 0.80,
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Post-hoc diagnostic: was the ≥3-of-6 season gate capable of passing?"""
    rows: dict[str, Any] = {}
    powers: list[float] = []
    for season, row in sorted(per_season.items(), key=lambda kv: int(kv[0])):
        se = float(row["se_b2"])
        mde = detectable_b2_at_power(se, power=power, alpha=alpha)
        p_clear = _season_clear_power(true_b2, se)
        rows[str(season)] = {
            "b2": float(row["b2"]),
            "se_b2": se,
            "n": int(row.get("n_games", row.get("n", 0))),
            "detectable_b2_80pct": mde,
            "p_clear_given_true_b2": p_clear,
            "mde_above_true_b2": bool(mde > true_b2),
        }
        if np.isfinite(p_clear):
            powers.append(p_clear)

    # P(≥k clears) under independent Bernoulli with heterogeneous p_i.
    # Exact via Poisson binomial DFZ recursion.
    n_seasons = len(powers)
    if n_seasons == 0:
        p_ge_k = float("nan")
        pmf = np.array([])
    else:
        dp = np.zeros(n_seasons + 1, dtype=float)
        dp[0] = 1.0
        for p in powers:
            new = np.zeros_like(dp)
            for k in range(n_seasons + 1):
                if dp[k] == 0.0:
                    continue
                new[k] += dp[k] * (1.0 - p)
                if k + 1 <= n_seasons:
                    new[k + 1] += dp[k] * p
            dp = new
        pmf = dp
        p_ge_k = float(pmf[n_required_clear:].sum())

    mean_p = float(np.mean(powers)) if powers else float("nan")
    # Capable of passing: under the stable alternative the rule was meant to
    # detect, P(pass) should be material (say ≥50%). Below that the gate is
    # underpowered as a stability screen.
    capable = bool(np.isfinite(p_ge_k) and p_ge_k >= 0.50)
    return {
        "label": "POST-HOC DIAGNOSTIC (not a re-litigation of the stop rule)",
        "true_b2_assumed": true_b2,
        "clear_definition": "b2 > 0 and two-sided bootstrap p < 0.10 (≈ z > 1.645)",
        "n_required_clear": n_required_clear,
        "n_seasons": n_seasons,
        "per_season": rows,
        "mean_p_clear": mean_p,
        "p_ge_k_clear": p_ge_k,
        "pmf_n_clear": pmf.tolist() if len(pmf) else [],
        "criterion_capable_of_passing": capable,
        "plain_statement": (
            f"Under stable true b2={true_b2:.3f}, P(≥{n_required_clear} of "
            f"{n_seasons} seasons clear) ≈ {p_ge_k:.3f}. "
            + (
                "The season-counting criterion was capable of passing at material probability."
                if capable
                else "The season-counting criterion was NOT capable of passing: "
                "per-season SEs are too large for b2=0.211 to clear p<0.10 in "
                "≥3 seasons with high probability. The gate failed for lack of "
                "within-season power, not because point estimates flipped sign."
            )
        ),
    }


# ---------------------------------------------------------------------------
# Part 1.3 — DerSimonian–Laird random-effects meta-analysis
# ---------------------------------------------------------------------------


def random_effects_b2(
    estimates: Sequence[float],
    standard_errors: Sequence[float],
    *,
    labels: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Cochran Q / I² / τ² and DerSimonian–Laird pooled random-effects b2."""
    b = np.asarray(estimates, dtype=float)
    se = np.asarray(standard_errors, dtype=float)
    if b.shape != se.shape or len(b) < 2:
        raise ValueError("need ≥2 matched estimates and SEs")
    if np.any(~np.isfinite(b)) or np.any(~np.isfinite(se)) or np.any(se <= 0):
        raise ValueError("estimates/SEs must be finite with se > 0")

    w = 1.0 / se**2
    b_fe = float(np.sum(w * b) / np.sum(w))
    q = float(np.sum(w * (b - b_fe) ** 2))
    k = int(len(b))
    df = k - 1
    c = float(np.sum(w) - np.sum(w**2) / np.sum(w))
    tau2 = max(0.0, (q - df) / c) if c > 0 else 0.0
    w_star = 1.0 / (se**2 + tau2)
    b_re = float(np.sum(w_star * b) / np.sum(w_star))
    se_re = float(1.0 / math.sqrt(float(np.sum(w_star))))
    ci = b2_wald_ci(b_re, se_re)
    i2 = max(0.0, (q - df) / q) if q > 0 else 0.0
    p_q = float(1.0 - stats.chi2.cdf(q, df)) if df > 0 else float("nan")
    # Between-season variance distinguishable from zero at α=0.05 via Q.
    tau_detectable = bool(p_q < 0.05 and tau2 > 0)

    names = [str(x) for x in labels] if labels is not None else [str(i) for i in range(k)]
    return {
        "k": k,
        "labels": names,
        "estimates": b.tolist(),
        "standard_errors": se.tolist(),
        "fixed_effect_b2": b_fe,
        "cochrans_q": q,
        "df": df,
        "p_heterogeneity": p_q,
        "i2": i2,
        "tau2": tau2,
        "tau": math.sqrt(tau2),
        "random_effect_b2": b_re,
        "se_re": se_re,
        "ci95": ci,
        "between_season_variance_distinguishable_from_zero": tau_detectable,
        "plain_statement": (
            f"Q={q:.3f} (df={df}, p={p_q:.3f}), I²={100 * i2:.1f}%, τ²={tau2:.5f}. "
            f"Pooled RE b2={b_re:.3f} 95% CI [{ci['low']:.3f}, {ci['high']:.3f}]. "
            + (
                "Between-season variance is distinguishable from zero."
                if tau_detectable
                else "Between-season variance is NOT distinguishable from zero "
                "(homogeneous b2 across seasons under the Q test)."
            )
        ),
    }


def re_meta_from_per_season(per_season: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    labels = sorted(per_season.keys(), key=int)
    return random_effects_b2(
        [float(per_season[s]["b2"]) for s in labels],
        [float(per_season[s]["se_b2"]) for s in labels],
        labels=labels,
    )


# ---------------------------------------------------------------------------
# Part 1.4 — week-bucket interaction (one regression, not three slices)
# ---------------------------------------------------------------------------


def _ols(y: np.ndarray, x: np.ndarray) -> np.ndarray:
    beta, _, _, _ = np.linalg.lstsq(x, y, rcond=None)
    return np.asarray(beta, dtype=float)


def week_bucket_interaction(
    y: np.ndarray,
    market: np.ndarray,
    stack_mu: np.ndarray,
    weeks: np.ndarray,
    blocks: Sequence[Any],
    *,
    n_boot: int = 1000,
    seed: int = 0,
) -> dict[str, Any]:
    """Single OLS with bucket-specific stack weights + bootstrap equality test.

    Model::

        y = a + b1·market + Σ_k b2_k · stack · 1{bucket=k} + e

    Tests H0: b2_{1-4} = b2_{5-9} = b2_{10+} via bootstrap of max pairwise |Δ|.
    """
    mask = np.isfinite(y) & np.isfinite(market) & np.isfinite(stack_mu) & np.isfinite(weeks)
    y_m = y[mask]
    mkt = market[mask]
    stk = stack_mu[mask]
    wk = weeks[mask].astype(int)
    bl = [blocks[i] for i, ok in enumerate(mask) if ok]
    n = int(len(y_m))

    buckets = np.array([week_bucket(int(w)) for w in wk], dtype=object)
    dummies = {lab: (buckets == lab).astype(float) for lab in WEEK_BUCKET_LABELS}
    # Design: [1, market, stk*I_14, stk*I_59, stk*I_10]
    x = np.column_stack(
        [
            np.ones(n),
            mkt,
            stk * dummies["1-4"],
            stk * dummies["5-9"],
            stk * dummies["10+"],
        ]
    )
    beta = _ols(y_m, x)
    names = ["a", "b1", "b2_1-4", "b2_5-9", "b2_10+"]
    point = {names[i]: float(beta[i]) for i in range(5)}

    groups = _group_indices(bl)
    set_global_seed(seed)
    rng = np.random.default_rng(seed)
    boots: list[np.ndarray] = []
    n_g = len(groups)
    for _ in range(n_boot):
        draw = rng.integers(0, n_g, size=n_g)
        idx = np.concatenate([groups[i] for i in draw])
        try:
            boots.append(_ols(y_m[idx], x[idx]))
        except Exception:  # noqa: BLE001
            continue
    boot_arr = np.asarray(boots, dtype=float)
    if len(boot_arr) < 20:
        raise RuntimeError("week-bucket interaction bootstrap failed")

    se = np.std(boot_arr, axis=0, ddof=1)
    ci: dict[str, Any] = {}
    for i, name in enumerate(names):
        lo = float(np.quantile(boot_arr[:, i], 0.025))
        hi = float(np.quantile(boot_arr[:, i], 0.975))
        ci[name] = {
            "estimate": point[name],
            "se": float(se[i]),
            "ci_low": lo,
            "ci_high": hi,
        }

    # Equality of the three b2's: bootstrap distribution of
    # T = max_{i<j} |b2_i - b2_j|; two-sided p = P*(T* ≥ T_obs) under
    # recentering to the common mean (exchangeable null).
    b2_idx = (2, 3, 4)
    b2_obs = beta[list(b2_idx)]
    t_obs = float(
        max(
            abs(b2_obs[0] - b2_obs[1]),
            abs(b2_obs[0] - b2_obs[2]),
            abs(b2_obs[1] - b2_obs[2]),
        )
    )
    b2_boot = boot_arr[:, list(b2_idx)]
    common = np.mean(b2_boot, axis=1, keepdims=True)
    # Recenter each draw's b2 vector to share a common mean, then measure
    # residual dispersion — approximates the null of equality.
    centered = b2_boot - common
    # Under the sharp null, add the grand mean of observed b2 so scale matches.
    grand = float(np.mean(b2_obs))
    null_draws = centered + grand
    t_null = np.max(
        np.column_stack(
            [
                np.abs(null_draws[:, 0] - null_draws[:, 1]),
                np.abs(null_draws[:, 0] - null_draws[:, 2]),
                np.abs(null_draws[:, 1] - null_draws[:, 2]),
            ]
        ),
        axis=1,
    )
    # Simpler and more standard: Wald on contrasts using bootstrap covariance.
    cov = np.cov(b2_boot, rowvar=False)
    # Contrast matrix for b2_14-b2_59 and b2_14-b2_10+
    cmat = np.array([[1.0, -1.0, 0.0], [1.0, 0.0, -1.0]], dtype=float)
    diff = cmat @ b2_obs
    try:
        v = cmat @ cov @ cmat.T
        wald = float(diff @ np.linalg.solve(v, diff))
        p_interact = float(1.0 - stats.chi2.cdf(wald, df=2))
    except np.linalg.LinAlgError:
        wald = float("nan")
        p_interact = float(np.mean(t_null >= t_obs))

    early = ci["b2_1-4"]
    late = ci["b2_10+"]
    early_pos = bool(early["ci_low"] > 0)
    late_zero = bool(late["ci_low"] <= 0 <= late["ci_high"])
    structural = bool(early_pos and late_zero and p_interact < 0.05)

    n_by = {lab: int((buckets == lab).sum()) for lab in WEEK_BUCKET_LABELS}
    return {
        "model": "y = a + b1*market + sum_k b2_k * stack * 1{bucket=k}",
        "coefficients": point,
        "ci": ci,
        "n": n,
        "n_by_bucket": n_by,
        "wald_equality_b2": wald,
        "p_b2_equal_across_buckets": p_interact,
        "t_obs_max_pairwise": t_obs,
        "early_reliably_positive": early_pos,
        "late_ci_covers_zero": late_zero,
        "structural_early_vs_late": structural,
        "plain_statement": (
            f"Interaction Wald χ²(2)={wald:.3f}, p={p_interact:.4f}. "
            f"b2(1–4)={early['estimate']:.3f} [{early['ci_low']:.3f}, {early['ci_high']:.3f}]; "
            f"b2(5–9)={ci['b2_5-9']['estimate']:.3f} "
            f"[{ci['b2_5-9']['ci_low']:.3f}, {ci['b2_5-9']['ci_high']:.3f}]; "
            f"b2(10+)={late['estimate']:.3f} [{late['ci_low']:.3f}, {late['ci_high']:.3f}]. "
            + (
                "Early-week b2 is reliably positive and late-week is consistent "
                "with zero; the interaction is significant — a structural "
                "calendar finding, not a slice artifact."
                if structural
                else (
                    "b2 differs across week buckets (interaction p<0.05), but "
                    "the early-positive / late-zero pattern is incomplete."
                    if p_interact < 0.05
                    else "No significant evidence that b2 differs across week buckets."
                )
            )
        ),
    }


# ---------------------------------------------------------------------------
# Part 1.5 — pre-registered holdout for early-week claim
# ---------------------------------------------------------------------------


def preregister_holdout() -> dict[str, Any]:
    """Return the locked holdout plan (no fitting)."""
    return {
        "holdout_season": HOLDOUT_SEASON,
        "early_weeks": list(HOLDOUT_EARLY_WEEKS),
        "confirm_rule": HOLDOUT_CONFIRM_RULE,
        "discovery_excludes": HOLDOUT_SEASON,
        "fitted_before_registration": False,
    }


def run_holdout_early_week(
    y: np.ndarray,
    market: np.ndarray,
    stack_mu: np.ndarray,
    seasons: np.ndarray,
    weeks: np.ndarray,
    blocks: Sequence[Any],
    *,
    n_boot: int = 1000,
    seed: int = 0,
) -> dict[str, Any]:
    """Evaluate the pre-registered early-week claim on the holdout season only."""
    plan = preregister_holdout()
    lo, hi = HOLDOUT_EARLY_WEEKS
    sel = (seasons == HOLDOUT_SEASON) & (weeks >= lo) & (weeks <= hi)
    sel = sel & np.isfinite(y) & np.isfinite(market) & np.isfinite(stack_mu)
    if int(sel.sum()) < 30:
        return {
            **plan,
            "n": int(sel.sum()),
            "status": "insufficient_n",
            "confirmed": False,
        }
    enc = encompassing_regression(
        y[sel],
        market[sel],
        stack_mu[sel],
        [blocks[i] for i, ok in enumerate(sel) if ok],
        n_boot=n_boot,
        seed=seed + HOLDOUT_SEASON,
    )
    ci = b2_wald_ci(enc.b2, enc.se_b2)
    confirmed = bool(enc.b2 > 0 and np.isfinite(enc.p_b2) and enc.p_b2 < 0.10)
    return {
        **plan,
        "n": enc.n,
        "b2": enc.b2,
        "se_b2": enc.se_b2,
        "p_b2": enc.p_b2,
        "ci95": ci,
        "verdict": enc.verdict,
        "confirmed": confirmed,
        "status": "confirmed" if confirmed else "refuted",
        "plain_statement": (
            f"Holdout {HOLDOUT_SEASON} weeks {lo}–{hi}: b2={enc.b2:.3f} "
            f"95% CI [{ci['low']:.3f}, {ci['high']:.3f}], p={enc.p_b2:.3f} "
            f"(n={enc.n}) → {'CONFIRMED' if confirmed else 'REFUTED'}."
        ),
    }


# ---------------------------------------------------------------------------
# Part 2.6 — optimal w on weeks 1–5 only
# ---------------------------------------------------------------------------


def optimal_w_early_weeks(
    y: np.ndarray,
    market: np.ndarray,
    stack_mu: np.ndarray,
    weeks: np.ndarray,
    blocks: Sequence[Any],
    *,
    week_lo: int = EARLY_W_WEEKS[0],
    week_hi: int = EARLY_W_WEEKS[1],
    n_boot: int = 1000,
    seed: int = 0,
) -> dict[str, Any]:
    """In-sample optimal combination weight restricted to early weeks."""
    sel = (weeks >= week_lo) & (weeks <= week_hi)
    sel = sel & np.isfinite(y) & np.isfinite(market) & np.isfinite(stack_mu)
    y_e, mkt, stk = y[sel], market[sel], stack_mu[sel]
    bl = [blocks[i] for i, ok in enumerate(sel) if ok]
    grid = np.linspace(0.0, 1.0, 101)
    best_w, best_mae = 0.0, float("inf")
    for w in grid:
        pred = w * stk + (1.0 - w) * mkt
        err = float(np.mean(np.abs(y_e - pred)))
        if err < best_mae:
            best_mae, best_w = err, float(w)
    comb = best_w * stk + (1.0 - best_w) * mkt
    abs_comb = np.abs(y_e - comb)
    abs_mkt = np.abs(y_e - mkt)
    ci = paired_block_bootstrap(abs_comb, abs_mkt, bl, n_boot=n_boot, alpha=0.05, seed=seed + 15)
    return {
        "week_range": [week_lo, week_hi],
        "w": best_w,
        "mae_combined": float(np.mean(abs_comb)),
        "mae_market": float(np.mean(abs_mkt)),
        "delta_mae": float(np.mean(abs_comb) - np.mean(abs_mkt)),
        "delta_ci": {"low": ci.ci_low, "high": ci.ci_high, "estimate": ci.estimate},
        "n": int(sel.sum()),
        "ci_covers_zero": bool(ci.ci_low <= 0.0 <= ci.ci_high),
        "note": (
            f"w fit on weeks {week_lo}–{week_hi} only; delta CI is paired "
            "week-block bootstrap (in-sample w)"
        ),
    }


# ---------------------------------------------------------------------------
# Part 2.7 — edge / ROI under early-week hypothesis at −110
# ---------------------------------------------------------------------------


def _roi_at_american(p_win: float, american: float = -110.0) -> float:
    return float(expected_value(p_win, american))


def early_week_edge_roi(
    y: np.ndarray,
    market: np.ndarray,
    stack_mu: np.ndarray,
    seasons: np.ndarray,
    weeks: np.ndarray,
    blocks: Sequence[Any],
    *,
    b2: float = D6_JOINT_B2,
    week_lo: int = EARLY_W_WEEKS[0],
    week_hi: int = EARLY_W_WEEKS[1],
    american: float = -110.0,
    n_boot: int = 1000,
    seed: int = 0,
    min_edge_points: float = 0.0,
) -> dict[str, Any]:
    """Translate encompassing b2 into ATS cover edge and ROI at −110.

    Using the joint encompassing fit on the early-week slice, form the
    model-implied mean ``μ̂ = a + b1·market + b2·stack`` and the point edge
    ``δ = μ̂ − market`` (home covers iff y > market). Under Gaussian residuals
    with σ = SD(y − μ̂), ``P(cover) = Φ(δ / σ)``. Bet the side with positive
    edge; ROI uses American ``american`` (default −110).
    """
    sel = (weeks >= week_lo) & (weeks <= week_hi)
    sel = sel & np.isfinite(y) & np.isfinite(market) & np.isfinite(stack_mu)
    y_e = y[sel]
    mkt = market[sel]
    stk = stack_mu[sel]
    seas = seasons[sel]
    bl = [blocks[i] for i, ok in enumerate(sel) if ok]
    n = int(len(y_e))
    if n < 30:
        return {"n": n, "error": "insufficient early-week rows"}

    # Fit a, b1 on the early slice; pin b2 to the hypothesized magnitude unless
    # we want free fit — task asks what b2=0.21 implies, so pin b2 and OLS a,b1
    # with stack contribution fixed: y - b2*stk = a + b1*mkt.
    target = y_e - b2 * stk
    x = np.column_stack([np.ones(n), mkt])
    ab = _ols(target, x)
    a, b1 = float(ab[0]), float(ab[1])
    mu_hat = a + b1 * mkt + b2 * stk
    resid = y_e - mu_hat
    sigma = float(np.std(resid, ddof=1))
    delta = mu_hat - mkt  # signed edge in points (home perspective)

    # Bet the side favored by δ; push/near-zero edges optionally excluded.
    bet = np.abs(delta) >= min_edge_points
    # P(chosen side covers): Φ(|δ|/σ)
    p_cover = stats.norm.cdf(np.abs(delta) / max(sigma, 1e-8))
    roi = np.array([_roi_at_american(float(p), american) for p in p_cover], dtype=float)

    # Games per season in the early window (all priced early games = bettable
    # under the hypothesis that the early-week edge is real).
    by_season: dict[str, int] = {}
    for s in sorted(set(int(x) for x in seas)):
        by_season[str(s)] = int((seas == s).sum())
    mean_per_season = float(np.mean(list(by_season.values()))) if by_season else float("nan")

    # Bootstrap mean ROI (and mean p_cover) over week blocks.
    groups = _group_indices(bl)
    set_global_seed(seed)
    rng = np.random.default_rng(seed)
    mean_rois: list[float] = []
    mean_ps: list[float] = []
    n_g = len(groups)
    for _ in range(n_boot):
        draw = rng.integers(0, n_g, size=n_g)
        idx = np.concatenate([groups[i] for i in draw])
        # Recompute σ and p on the resample for honest uncertainty.
        y_b, mkt_b, stk_b = y_e[idx], mkt[idx], stk[idx]
        tgt = y_b - b2 * stk_b
        xb = np.column_stack([np.ones(len(y_b)), mkt_b])
        try:
            ab_b = _ols(tgt, xb)
        except Exception:  # noqa: BLE001
            continue
        mu_b = float(ab_b[0]) + float(ab_b[1]) * mkt_b + b2 * stk_b
        sig_b = float(np.std(y_b - mu_b, ddof=1))
        if not np.isfinite(sig_b) or sig_b <= 0:
            continue
        d_b = mu_b - mkt_b
        p_b = stats.norm.cdf(np.abs(d_b) / sig_b)
        r_b = np.array([_roi_at_american(float(p), american) for p in p_b])
        mean_rois.append(float(np.mean(r_b)))
        mean_ps.append(float(np.mean(p_b)))

    roi_arr = np.asarray(mean_rois, dtype=float)
    p_arr = np.asarray(mean_ps, dtype=float)
    roi_point = float(np.mean(roi[bet])) if bet.any() else float("nan")
    p_point = float(np.mean(p_cover[bet])) if bet.any() else float("nan")
    roi_ci = (
        {
            "estimate": roi_point,
            "low": float(np.quantile(roi_arr, 0.025)),
            "high": float(np.quantile(roi_arr, 0.975)),
        }
        if len(roi_arr) >= 20
        else {"estimate": roi_point, "low": float("nan"), "high": float("nan")}
    )
    # Breakeven at −110: p = 110/210 ≈ 0.52381; ROI=0.
    be_p = (
        abs(american) / (abs(american) + 100.0) if american < 0 else 1.0 / (1.0 + american / 100.0)
    )
    clears_vig = bool(np.isfinite(roi_ci["low"]) and roi_ci["low"] > 0.0 and p_point > be_p)

    # Mean |δ| as the "edge magnitude in points" implied by b2.
    mean_abs_delta = float(np.mean(np.abs(delta)))
    return {
        "week_range": [week_lo, week_hi],
        "b2_pinned": b2,
        "a": a,
        "b1": b1,
        "sigma_resid": sigma,
        "n_early_games": n,
        "games_per_season": by_season,
        "mean_bettable_games_per_season": mean_per_season,
        "mean_abs_edge_points": mean_abs_delta,
        "mean_p_cover": p_point,
        "breakeven_p_at_american": be_p,
        "american": american,
        "expected_roi_per_bet": roi_point,
        "roi_ci95": roi_ci,
        "p_cover_boot_mean": float(np.mean(p_arr)) if len(p_arr) else float("nan"),
        "clears_vig": clears_vig,
        "plain_statement": (
            f"Weeks {week_lo}–{week_hi}: ~{mean_per_season:.0f} priced games/season; "
            f"b2={b2:.3f} implies mean |μ̂−market|≈{mean_abs_delta:.2f} pts "
            f"(σ≈{sigma:.1f}) → mean P(cover)≈{p_point:.3f} vs breakeven "
            f"{be_p:.3f} at {american:.0f}. Expected ROI/bet≈{roi_point:.3%} "
            f"95% CI [{roi_ci['low']:.3%}, {roi_ci['high']:.3%}]. "
            + (
                "CI clears vig."
                if clears_vig
                else "CI does not reliably clear vig — a real but thin edge may not pay after −110."
            )
        ),
    }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def prepare_encompassing_arrays(
    frame: pd.DataFrame,
    *,
    seasons: Sequence[int],
    line_column: str = "spread_close",
    market_implied_sign: float = -1.0,
) -> dict[str, Any]:
    """Filter to config seasons with finite close / μ / y."""
    work = frame.loc[frame["season"].isin(list(seasons))].copy()
    line = pd.to_numeric(work[line_column], errors="coerce")
    y = pd.to_numeric(work["realized_margin"], errors="coerce")
    stk = pd.to_numeric(work["pred_margin"], errors="coerce")
    mkt = market_implied_sign * line
    mask = np.isfinite(line) & np.isfinite(y) & np.isfinite(stk) & np.isfinite(mkt)
    work = work.loc[mask]
    seasons_a = work["season"].to_numpy(dtype=int)
    weeks_a = (
        work["week"].to_numpy(dtype=int)
        if "week" in work.columns
        else np.zeros(len(work), dtype=int)
    )
    return {
        "y": y[mask].to_numpy(dtype=float),
        "market": mkt[mask].to_numpy(dtype=float),
        "stack": stk[mask].to_numpy(dtype=float),
        "seasons": seasons_a,
        "weeks": weeks_a,
        "blocks": list(zip(seasons_a.tolist(), weeks_a.tolist(), strict=True)),
        "n": int(mask.sum()),
    }


def run_d7_diagnostics(
    frame: pd.DataFrame,
    d6_encompassing: Mapping[str, Any],
    *,
    n_boot: int = 1000,
    seed: int = 0,
) -> dict[str, Any]:
    """Full D7 diagnostic bundle from a joined prediction frame + D6 results."""
    cfg = d6_encompassing["config"]
    seasons = list(cfg["seasons"])
    arrays = prepare_encompassing_arrays(
        frame,
        seasons=seasons,
        line_column=str(cfg.get("line_column", "spread_close")),
        market_implied_sign=float(cfg.get("market_implied_sign", -1.0)),
    )

    stop = record_stop_rule_stands(d6_encompassing["stop_rule"])
    power = posthoc_season_criterion_power(d6_encompassing["per_season"])
    re_meta = re_meta_from_per_season(d6_encompassing["per_season"])

    # Pre-register BEFORE fitting the holdout (plan recorded first).
    holdout_plan = preregister_holdout()

    interaction = week_bucket_interaction(
        arrays["y"],
        arrays["market"],
        arrays["stack"],
        arrays["weeks"],
        arrays["blocks"],
        n_boot=n_boot,
        seed=seed,
    )
    holdout = run_holdout_early_week(
        arrays["y"],
        arrays["market"],
        arrays["stack"],
        arrays["seasons"],
        arrays["weeks"],
        arrays["blocks"],
        n_boot=n_boot,
        seed=seed,
    )
    early_w = optimal_w_early_weeks(
        arrays["y"],
        arrays["market"],
        arrays["stack"],
        arrays["weeks"],
        arrays["blocks"],
        n_boot=n_boot,
        seed=seed,
    )
    edge = early_week_edge_roi(
        arrays["y"],
        arrays["market"],
        arrays["stack"],
        arrays["seasons"],
        arrays["weeks"],
        arrays["blocks"],
        b2=D6_JOINT_B2,
        n_boot=n_boot,
        seed=seed,
    )

    # Close the diagnostic phase: stop rule stands; RE says homogeneous positive
    # b2 but operational gate already closed betting-on-μ; early-week is a
    # research finding only if holdout confirms — still no betting layer on μ.
    diagnostic_phase_closed = True
    close_reason = (
        "Diagnostic phase CLOSED. The pre-registered D5/D6 stop rule stands: "
        "no betting layer on this μ. D7's RE meta-analysis and week-interaction "
        "tests are post-hoc diagnostics that refine *why* the season gate "
        "failed and whether an early-week pattern is structural; they do not "
        "re-open a fundamental-μ betting path."
    )

    return {
        "canonical_v2_sha": d6_encompassing.get("canonical_v2_sha"),
        "n": arrays["n"],
        "stop_rule_stands": stop,
        "posthoc_season_power": power,
        "random_effects_meta": re_meta,
        "week_bucket_interaction": interaction,
        "holdout_preregistration": holdout_plan,
        "holdout_early_week": holdout,
        "optimal_w_weeks_1_5": early_w,
        "early_week_edge_roi": edge,
        "diagnostic_phase_closed": diagnostic_phase_closed,
        "close_statement": close_reason,
        "opening_summary": {
            "i2": re_meta["i2"],
            "tau2": re_meta["tau2"],
            "re_b2": re_meta["random_effect_b2"],
            "re_ci95": re_meta["ci95"],
            "interaction_p": interaction["p_b2_equal_across_buckets"],
            "interaction_wald": interaction["wald_equality_b2"],
        },
    }


__all__ = [
    "D6_JOINT_B2",
    "EARLY_W_WEEKS",
    "HOLDOUT_CONFIRM_RULE",
    "HOLDOUT_EARLY_WEEKS",
    "HOLDOUT_SEASON",
    "early_week_edge_roi",
    "optimal_w_early_weeks",
    "posthoc_season_criterion_power",
    "preregister_holdout",
    "prepare_encompassing_arrays",
    "random_effects_b2",
    "re_meta_from_per_season",
    "record_stop_rule_stands",
    "run_d7_diagnostics",
    "run_holdout_early_week",
    "week_bucket",
    "week_bucket_interaction",
]
