"""Promotion gate: significance-tested champion/challenger workflow (§8.7).

A candidate is promoted to champion ONLY if it beats the incumbent on the
pre-registered metric set (CRPS + log-loss + CLV) on the SAME walk-forward
seasons with a paired block-bootstrap test at p < 0.10, AND passes calibration
and leakage gates. Failing candidates are archived with the comparison report.

The gate is **non-bypassable from config**. Promoting a failing candidate
requires an explicit human ``force=True`` that writes an immutable override
record.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np

from ncaa_quant.evaluation.significance import bootstrap_distribution
from ncaa_quant.registry.stages import ModelStage
from ncaa_quant.registry.store import (
    ModelRegistry,
    ModelVersionRecord,
    NoChampionError,
    RegistryError,
)
from ncaa_quant.utils.logging import get_logger

log = get_logger(__name__)

# Pre-registered promotion constants — not config-tunable bypasses.
PROMOTION_ALPHA: float = 0.10
REQUIRED_METRICS: tuple[str, ...] = ("crps", "log_loss", "clv")
MetricDirection = Literal["lower", "higher"]
METRIC_DIRECTIONS: dict[str, MetricDirection] = {
    "crps": "lower",
    "log_loss": "lower",
    "clv": "higher",
}
DEFAULT_N_BOOT: int = 1999
DEFAULT_CALIBRATION_SLOPE_LOW: float = 0.85
DEFAULT_CALIBRATION_SLOPE_HIGH: float = 1.15


class PromotionError(RegistryError):
    """Promotion gate failure or invalid gate inputs."""


@dataclass
class MetricComparisonInput:
    """Paired per-game scores for one pre-registered metric.

    ``champion`` / ``candidate`` must share length and row order with ``blocks``
    (typically week labels for block bootstrap).
    """

    name: str
    champion: Sequence[float]
    candidate: Sequence[float]
    blocks: Sequence[Any]
    direction: MetricDirection

    def __post_init__(self) -> None:
        a = np.asarray(self.champion, dtype=float).ravel()
        b = np.asarray(self.candidate, dtype=float).ravel()
        if a.size != b.size:
            raise PromotionError(
                f"metric {self.name!r}: champion length {a.size} != candidate {b.size}"
            )
        if len(self.blocks) != a.size:
            raise PromotionError(
                f"metric {self.name!r}: blocks length {len(self.blocks)} != {a.size}"
            )


@dataclass
class MetricTestResult:
    """One paired block-bootstrap significance result."""

    name: str
    direction: MetricDirection
    champion_mean: float
    candidate_mean: float
    paired_diff_mean: float
    p_value: float
    beats_incumbent: bool
    n: int
    n_boot: int
    alpha: float

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready record."""
        return asdict(self)


@dataclass
class ComparisonReport:
    """Immutable promotion comparison artifact (§10)."""

    candidate_version: int
    champion_version: int | None
    seasons: list[int]
    passed: bool
    metric_results: list[MetricTestResult]
    calibration_passed: bool
    calibration_slope: float
    calibration_slope_low: float
    calibration_slope_high: float
    leakage_passed: bool
    force_override: bool
    alpha: float
    created_at: str
    reason: str = ""
    override_record: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON artifact."""
        return {
            "candidate_version": self.candidate_version,
            "champion_version": self.champion_version,
            "seasons": list(self.seasons),
            "passed": self.passed,
            "metric_results": [m.to_dict() for m in self.metric_results],
            "calibration_passed": self.calibration_passed,
            "calibration_slope": self.calibration_slope,
            "calibration_slope_low": self.calibration_slope_low,
            "calibration_slope_high": self.calibration_slope_high,
            "leakage_passed": self.leakage_passed,
            "force_override": self.force_override,
            "alpha": self.alpha,
            "created_at": self.created_at,
            "reason": self.reason,
            "override_record": self.override_record,
        }

    def to_html(self) -> str:
        """Minimal HTML comparison report for archival."""
        rows = []
        for m in self.metric_results:
            rows.append(
                "<tr>"
                f"<td>{_esc(m.name)}</td>"
                f"<td>{m.direction}</td>"
                f"<td>{m.champion_mean:.6f}</td>"
                f"<td>{m.candidate_mean:.6f}</td>"
                f"<td>{m.paired_diff_mean:.6f}</td>"
                f"<td>{m.p_value:.4f}</td>"
                f"<td>{'yes' if m.beats_incumbent else 'no'}</td>"
                "</tr>"
            )
        verdict = "PROMOTE" if self.passed else "BLOCK / ARCHIVE"
        if self.force_override:
            verdict = "FORCE PROMOTE (override recorded)"
        return (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>Promotion comparison</title>"
            "<style>"
            "body{font-family:system-ui,sans-serif;margin:2rem;}"
            "table{border-collapse:collapse;}"
            "th,td{border:1px solid #ccc;padding:0.4rem 0.6rem;}"
            "th{background:#f4f4f4;}"
            ".fail{color:#a30;}.pass{color:#060;}"
            "</style></head><body>"
            f"<h1>Promotion comparison — {verdict}</h1>"
            f"<p>candidate=v{self.candidate_version} "
            f"champion={'v' + str(self.champion_version) if self.champion_version else 'none'} "
            f"seasons={self.seasons} alpha={self.alpha}</p>"
            f"<p class='{'pass' if self.calibration_passed else 'fail'}'>"
            f"calibration slope={self.calibration_slope:.4f} "
            f"band=[{self.calibration_slope_low}, {self.calibration_slope_high}] "
            f"→ {'PASS' if self.calibration_passed else 'FAIL'}</p>"
            f"<p class='{'pass' if self.leakage_passed else 'fail'}'>"
            f"leakage gate → {'PASS' if self.leakage_passed else 'FAIL'}</p>"
            "<table><thead><tr>"
            "<th>metric</th><th>dir</th><th>champion</th><th>candidate</th>"
            "<th>advantage</th><th>p</th><th>beats?</th>"
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
            f"<p>{_esc(self.reason)}</p>"
            "</body></html>"
        )


@dataclass
class PromotionResult:
    """Outcome of :func:`promote`."""

    report: ComparisonReport
    promoted: bool
    archived_version: int | None = None
    champion_version: int | None = None


def paired_block_pvalue(
    champion: Sequence[float] | np.ndarray,
    candidate: Sequence[float] | np.ndarray,
    blocks: Sequence[Any],
    *,
    direction: MetricDirection,
    n_boot: int = DEFAULT_N_BOOT,
    seed: int = 0,
) -> tuple[float, float, float, float]:
    """One-sided paired block-bootstrap p-value that candidate beats champion.

    For ``direction='lower'`` (CRPS, log-loss): advantage = champion − candidate
    (positive ⇒ candidate better). For ``direction='higher'`` (CLV): advantage =
    candidate − champion.

    Returns ``(champion_mean, candidate_mean, advantage_mean, p_value)``.
    """
    a = np.asarray(champion, dtype=float).ravel()
    b = np.asarray(candidate, dtype=float).ravel()
    if a.size == 0:
        return float("nan"), float("nan"), float("nan"), 1.0

    advantage = a - b if direction == "lower" else b - a

    champ_mean = float(np.mean(a))
    cand_mean = float(np.mean(b))
    adv_mean = float(np.mean(advantage))

    boots = bootstrap_distribution(
        advantage,
        blocks,
        statistic=lambda arr: float(np.mean(arr)),
        n_boot=n_boot,
        seed=seed,
        block=True,
    )
    # One-sided p: fraction of bootstrap advantages ≤ 0 (no improvement),
    # with +1 correction to avoid zero p-values.
    n_bad = int(np.sum(boots <= 0.0))
    p_value = (n_bad + 1.0) / (n_boot + 1.0)
    return champ_mean, cand_mean, adv_mean, float(p_value)


def evaluate_gate(
    metrics: Sequence[MetricComparisonInput],
    *,
    seasons: Sequence[int],
    calibration_slope: float,
    leakage_gate_passed: bool,
    candidate_version: int,
    champion_version: int | None,
    calibration_slope_low: float = DEFAULT_CALIBRATION_SLOPE_LOW,
    calibration_slope_high: float = DEFAULT_CALIBRATION_SLOPE_HIGH,
    alpha: float = PROMOTION_ALPHA,
    n_boot: int = DEFAULT_N_BOOT,
    seed: int = 0,
    require_metric_significance: bool = True,
) -> ComparisonReport:
    """Evaluate the pre-registered promotion gate (no side effects)."""
    by_name = {m.name: m for m in metrics}
    missing = [name for name in REQUIRED_METRICS if name not in by_name]
    if missing:
        raise PromotionError(f"promotion gate missing required metrics: {missing}")

    results: list[MetricTestResult] = []
    for name in REQUIRED_METRICS:
        m = by_name[name]
        direction = m.direction
        expected = METRIC_DIRECTIONS[name]
        if direction != expected:
            raise PromotionError(
                f"metric {name!r} direction must be {expected!r}, got {direction!r}"
            )
        champ_mean, cand_mean, adv, p_value = paired_block_pvalue(
            m.champion,
            m.candidate,
            m.blocks,
            direction=direction,
            n_boot=n_boot,
            seed=seed + (sum(ord(c) for c in name) % 10_000),
        )
        results.append(
            MetricTestResult(
                name=name,
                direction=direction,
                champion_mean=champ_mean,
                candidate_mean=cand_mean,
                paired_diff_mean=adv,
                p_value=p_value,
                beats_incumbent=bool(p_value < alpha and adv > 0.0),
                n=int(np.asarray(m.champion).size),
                n_boot=n_boot,
                alpha=alpha,
            )
        )

    cal_ok = bool(
        np.isfinite(calibration_slope)
        and calibration_slope_low <= calibration_slope <= calibration_slope_high
    )
    metrics_ok = all(r.beats_incumbent for r in results) if require_metric_significance else True
    passed = bool(metrics_ok and cal_ok and leakage_gate_passed)

    reasons: list[str] = []
    if not require_metric_significance:
        reasons.append("cold-start: no incumbent; metric significance not required")
    elif not metrics_ok:
        failed = [r.name for r in results if not r.beats_incumbent]
        reasons.append(f"metrics failed significance gate: {failed}")
    if not cal_ok:
        reasons.append(
            f"calibration slope {calibration_slope:.4f} outside "
            f"[{calibration_slope_low}, {calibration_slope_high}]"
        )
    if not leakage_gate_passed:
        reasons.append("leakage gate failed")
    if passed and require_metric_significance:
        reasons.append("all pre-registered gates passed")
    elif passed:
        reasons.append("calibration + leakage passed (cold-start)")

    return ComparisonReport(
        candidate_version=candidate_version,
        champion_version=champion_version,
        seasons=[int(s) for s in seasons],
        passed=passed,
        metric_results=results,
        calibration_passed=cal_ok,
        calibration_slope=float(calibration_slope),
        calibration_slope_low=float(calibration_slope_low),
        calibration_slope_high=float(calibration_slope_high),
        leakage_passed=bool(leakage_gate_passed),
        force_override=False,
        alpha=float(alpha),
        created_at=datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        reason="; ".join(reasons),
    )


def promote(
    registry: ModelRegistry,
    candidate_version: int,
    metrics: Sequence[MetricComparisonInput],
    *,
    seasons: Sequence[int],
    calibration_slope: float,
    leakage_gate_passed: bool,
    force: bool = False,
    force_reason: str = "",
    force_actor: str = "",
    calibration_slope_low: float = DEFAULT_CALIBRATION_SLOPE_LOW,
    calibration_slope_high: float = DEFAULT_CALIBRATION_SLOPE_HIGH,
    alpha: float = PROMOTION_ALPHA,
    n_boot: int = DEFAULT_N_BOOT,
    seed: int = 0,
    to_challenger_first: bool = True,
) -> PromotionResult:
    """Run the gate and promote or archive.

    Parameters
    ----------
    force:
        Human override only. Config cannot set this. Writes an override record
        and promotes even when the gate fails. ``force_reason`` and
        ``force_actor`` are required when ``force=True``.
    """
    if force and (not force_reason.strip() or not force_actor.strip()):
        raise PromotionError("--force requires non-empty force_reason and force_actor")

    candidate = registry.get_version(candidate_version)
    if candidate.stage_enum is ModelStage.ARCHIVED:
        raise PromotionError(f"version {candidate_version} is already archived")
    if candidate.stage_enum is ModelStage.CHAMPION:
        raise PromotionError(f"version {candidate_version} is already champion")

    try:
        incumbent = registry.resolve_champion()
        champ_version: int | None = incumbent.version
    except NoChampionError:
        champ_version = None

    report = evaluate_gate(
        metrics,
        seasons=seasons,
        calibration_slope=calibration_slope,
        leakage_gate_passed=leakage_gate_passed,
        candidate_version=candidate_version,
        champion_version=champ_version,
        calibration_slope_low=calibration_slope_low,
        calibration_slope_high=calibration_slope_high,
        alpha=alpha,
        n_boot=n_boot,
        seed=seed,
        require_metric_significance=champ_version is not None,
    )

    override_record: dict[str, Any] | None = None
    if not report.passed and force:
        override_record = registry.record_force_override(
            {
                "version": candidate_version,
                "prior_champion_version": champ_version,
                "actor": force_actor,
                "reason": force_reason,
                "gate_report": report.to_dict(),
            }
        )
        report = ComparisonReport(
            candidate_version=candidate_version,
            champion_version=champ_version,
            seasons=list(seasons),
            passed=True,
            metric_results=list(report.metric_results),
            calibration_passed=report.calibration_passed,
            calibration_slope=report.calibration_slope,
            calibration_slope_low=report.calibration_slope_low,
            calibration_slope_high=report.calibration_slope_high,
            leakage_passed=report.leakage_passed,
            force_override=True,
            alpha=report.alpha,
            created_at=datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            reason=f"FORCE override by {force_actor}: {force_reason}",
            override_record=override_record,
        )

    html = report.to_html()
    report_payload = report.to_dict()

    if not report.passed:
        registry.archive(
            candidate_version,
            comparison_report=report_payload,
            report_html=html,
        )
        log.info(
            "promotion_blocked",
            candidate=candidate_version,
            reason=report.reason,
        )
        return PromotionResult(
            report=report,
            promoted=False,
            archived_version=candidate_version,
            champion_version=champ_version,
        )

    if to_challenger_first and candidate.stage_enum is ModelStage.CANDIDATE:
        registry.set_stage(candidate_version, ModelStage.CHALLENGER)

    registry.set_stage(
        candidate_version,
        ModelStage.CHAMPION,
        allow_champion_pin=True,
        prior_champion_version=champ_version,
    )
    artifact_dir = Path(registry.get_version(candidate_version).artifact_dir)
    (artifact_dir / "comparison_report.json").write_text(
        json.dumps(report_payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    (artifact_dir / "comparison_report.html").write_text(html, encoding="utf-8")

    log.info(
        "promotion_succeeded",
        candidate=candidate_version,
        prior_champion=champ_version,
        force=force,
    )
    return PromotionResult(
        report=report,
        promoted=True,
        archived_version=None,
        champion_version=candidate_version,
    )


def load_metric_comparisons_from_mlflow_runs(
    *,
    tracking_uri: str,
    champion_run_id: str,
    candidate_run_id: str,
    artifact_filename: str = "promotion_metrics.json",
) -> list[MetricComparisonInput]:
    """Resolve promotion comparison inputs from logged MLflow run artifacts.

    Each evaluation run must have logged ``promotion_metrics.json`` with keys
    ``crps``, ``log_loss``, ``clv`` mapping to ``{scores: [...], blocks: [...]}``.
    Hand-passed metric dicts are no longer the preferred gate input (Task 22B).
    """
    from mlflow.tracking import MlflowClient

    client = MlflowClient(tracking_uri=tracking_uri)

    def _load(run_id: str) -> dict[str, Any]:
        local = client.download_artifacts(run_id, artifact_filename)
        path = Path(local)
        if path.is_dir():
            path = path / artifact_filename
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise PromotionError(f"artifact {artifact_filename} on run {run_id} is not a mapping")
        return payload

    champ = _load(champion_run_id)
    cand = _load(candidate_run_id)
    out: list[MetricComparisonInput] = []
    for name in REQUIRED_METRICS:
        if name not in champ or name not in cand:
            raise PromotionError(f"logged runs missing metric {name!r} in {artifact_filename}")
        direction = METRIC_DIRECTIONS[name]
        out.append(
            MetricComparisonInput(
                name=name,
                champion=list(champ[name]["scores"]),
                candidate=list(cand[name]["scores"]),
                blocks=list(cand[name]["blocks"]),
                direction=direction,
            )
        )
    return out


def promote_from_mlflow_runs(
    registry: ModelRegistry,
    candidate_version: int,
    *,
    tracking_uri: str,
    champion_run_id: str | None,
    candidate_run_id: str,
    seasons: Sequence[int],
    calibration_slope: float,
    leakage_gate_passed: bool,
    force: bool = False,
    force_reason: str = "",
    force_actor: str = "",
    **kwargs: Any,
) -> PromotionResult:
    """Promotion gate that resolves comparison series from logged MLflow runs."""
    if champion_run_id is None:
        # Cold start: empty metrics; evaluate_gate skips significance when no champ.
        metrics: list[MetricComparisonInput] = [
            MetricComparisonInput(
                name=name,
                champion=[],
                candidate=[],
                blocks=[],
                direction=METRIC_DIRECTIONS[name],
            )
            for name in REQUIRED_METRICS
        ]
        # Empty series will fail length checks — for cold start call promote with
        # a sentinel path: use evaluate_gate via promote after fabricating zeros.
        metrics = [
            MetricComparisonInput(
                name=name,
                champion=[0.0],
                candidate=[0.0],
                blocks=["cold"],
                direction=METRIC_DIRECTIONS[name],
            )
            for name in REQUIRED_METRICS
        ]
    else:
        metrics = load_metric_comparisons_from_mlflow_runs(
            tracking_uri=tracking_uri,
            champion_run_id=champion_run_id,
            candidate_run_id=candidate_run_id,
        )
    return promote(
        registry,
        candidate_version,
        metrics,
        seasons=seasons,
        calibration_slope=calibration_slope,
        leakage_gate_passed=leakage_gate_passed,
        force=force,
        force_reason=force_reason,
        force_actor=force_actor,
        **kwargs,
    )


def rollback(
    registry: ModelRegistry,
    target_version: int | None = None,
) -> ModelVersionRecord:
    """Re-pin a prior champion. Default: immediately previous in history.

    Inference continues to resolve ``champion`` at runtime — this only moves the
    pin.
    """
    history = registry.champion_history()
    if not history:
        raise PromotionError("no champion history to roll back")

    if target_version is None:
        try:
            current = registry.resolve_champion()
            tip = current.version
            if tip in history:
                idx = history.index(tip)
                if idx == 0:
                    raise PromotionError(
                        f"champion v{tip} is the only historical champion; "
                        "specify --to VERSION of an archived prior champion"
                    )
                target_version = history[idx - 1]
            else:
                target_version = history[-1]
        except NoChampionError:
            target_version = history[-1]

    assert target_version is not None
    if target_version not in history:
        raise PromotionError(
            f"version {target_version} was never a champion (history={history}); refusing rollback"
        )

    try:
        current = registry.resolve_champion()
        if current.version != target_version:
            registry.archive(current.version)
            prior: int | None = current.version
        else:
            return current
    except NoChampionError:
        prior = None

    # Rollback is the explicit exception to archived→champion: rewrite the pin.
    index = registry._read_index()  # noqa: SLF001 — controlled rollback path
    for v in index.versions:
        if v.stage == ModelStage.CHAMPION.value and v.version != target_version:
            v.stage = ModelStage.ARCHIVED.value
            prior = v.version
    for v in index.versions:
        if v.version == target_version:
            v.stage = ModelStage.CHAMPION.value
            v.prior_champion_version = prior
            registry._write_index(index)  # noqa: SLF001
            registry._sync_mlflow_stage(v)  # noqa: SLF001
            log.info("registry_rollback", to_version=target_version, demoted=prior)
            return v
    raise PromotionError(f"version {target_version} not found")


def _esc(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
