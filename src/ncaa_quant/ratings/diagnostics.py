"""Kalman filter diagnostics (DESIGN §9.5 / §15 item 14).

Monitors standardized innovations for filter health and regime-change flags.
If standardized innovation variance is far from 1 the noise model is
misspecified — callers must *report* that, not silently retune R/Q away.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-untyped]


@dataclass(frozen=True)
class FilterHealthStats:
    """Summary of standardized innovation health.

    Parameters
    ----------
    mean_z, var_z:
        Sample mean and variance of standardized innovations.
    n:
        Number of innovations.
    misspecified:
        True when ``var_z`` is outside ``[var_low, var_high]`` (default
        ``[0.5, 2.0]``) — noise model likely wrong.
    """

    mean_z: float
    var_z: float
    n: int
    misspecified: bool
    var_low: float = 0.5
    var_high: float = 2.0

    def summary(self) -> str:
        flag = " MISSPECIFIED" if self.misspecified else ""
        return f"innovation_mean={self.mean_z:.4f} innovation_var={self.var_z:.4f} n={self.n}{flag}"


@dataclass(frozen=True)
class InnovationFlag:
    """A team with 3 consecutive same-signed >2σ innovations (DESIGN §9.5)."""

    team_id: Any
    obs_name: str
    season: int
    week: int
    z_values: tuple[float, ...]
    message: str


def standardized_innovations(
    innovations: pd.DataFrame,
    *,
    team_id: Any | None = None,
    obs_name: str | None = None,
) -> pd.DataFrame:
    """Return the standardized innovation series (column ``z``).

    Pass-through with optional filters; ensures ``z`` exists (computes from
    ``innovation / pred_sd`` when missing).
    """
    if innovations.empty:
        return innovations.copy()
    out = innovations.copy()
    if "z" not in out.columns:
        if "innovation" not in out.columns or "pred_sd" not in out.columns:
            msg = "innovations need z or (innovation, pred_sd)"
            raise ValueError(msg)
        sd = pd.to_numeric(out["pred_sd"], errors="coerce").clip(lower=1e-12)
        out["z"] = pd.to_numeric(out["innovation"], errors="coerce") / sd
    if team_id is not None:
        out = out.loc[out["team_id"].astype(str) == str(team_id)]
    if obs_name is not None:
        out = out.loc[out["obs_name"] == obs_name]
    return out.reset_index(drop=True)


def filter_health_stats(
    innovations: pd.DataFrame,
    *,
    var_low: float = 0.5,
    var_high: float = 2.0,
    obs_names: Sequence[str] | None = None,
) -> FilterHealthStats:
    """Compute innovation mean≈0 / variance≈1 health check (DESIGN §9.5).

    Does **not** retune noise parameters. ``misspecified=True`` when sample
    variance of ``z`` falls outside ``[var_low, var_high]``.
    """
    frame = standardized_innovations(innovations)
    if obs_names is not None and not frame.empty:
        frame = frame.loc[frame["obs_name"].isin(list(obs_names))]
    if frame.empty or "z" not in frame.columns:
        return FilterHealthStats(
            mean_z=0.0,
            var_z=0.0,
            n=0,
            misspecified=False,
            var_low=var_low,
            var_high=var_high,
        )
    z = pd.to_numeric(frame["z"], errors="coerce").dropna().to_numpy(dtype=float)
    n = int(z.size)
    if n == 0:
        return FilterHealthStats(
            mean_z=0.0,
            var_z=0.0,
            n=0,
            misspecified=False,
            var_low=var_low,
            var_high=var_high,
        )
    mean_z = float(np.mean(z))
    var_z = float(np.var(z, ddof=1)) if n > 1 else 0.0
    misspecified = bool(n >= 30 and (var_z < var_low or var_z > var_high))
    return FilterHealthStats(
        mean_z=mean_z,
        var_z=var_z,
        n=n,
        misspecified=misspecified,
        var_low=var_low,
        var_high=var_high,
    )


def flag_consecutive_innovations(
    innovations: pd.DataFrame,
    *,
    n_consecutive: int = 3,
    threshold: float = 2.0,
    obs_name: str | None = "home_epa",
) -> list[InnovationFlag]:
    """Flag teams with ``n_consecutive`` same-signed innovations above threshold.

    Per DESIGN §9.5 this is a *review flag*, not an automatic mega-update.
    Scans each ``(team_id, obs_name)`` series in chronological order.
    """
    frame = standardized_innovations(innovations, obs_name=obs_name)
    if frame.empty:
        return []
    frame = frame.sort_values(["team_id", "obs_name", "event_time", "game_id"])
    flags: list[InnovationFlag] = []

    group_cols = ["team_id", "obs_name"]
    for (tid, oname), grp in frame.groupby(group_cols, sort=False):
        zs = pd.to_numeric(grp["z"], errors="coerce").to_numpy(dtype=float)
        seasons = grp["season"].to_numpy() if "season" in grp.columns else np.zeros(len(grp))
        weeks = grp["week"].to_numpy() if "week" in grp.columns else np.zeros(len(grp))
        run_sign = 0
        run_len = 0
        run_vals: list[float] = []
        for i, z in enumerate(zs):
            if not np.isfinite(z) or abs(z) <= threshold:
                run_sign = 0
                run_len = 0
                run_vals = []
                continue
            sgn = 1 if z > 0 else -1
            if sgn == run_sign:
                run_len += 1
                run_vals.append(float(z))
            else:
                run_sign = sgn
                run_len = 1
                run_vals = [float(z)]
            if run_len >= n_consecutive:
                flags.append(
                    InnovationFlag(
                        team_id=tid,
                        obs_name=str(oname),
                        season=int(seasons[i]),
                        week=int(weeks[i]),
                        z_values=tuple(run_vals[-n_consecutive:]),
                        message=(
                            f"team={tid} obs={oname}: {n_consecutive} consecutive "
                            f"same-signed |z|>{threshold} ending season={seasons[i]} "
                            f"week={weeks[i]}"
                        ),
                    )
                )
                # Reset so the same streak does not re-fire every extra game.
                run_sign = 0
                run_len = 0
                run_vals = []
    return flags
