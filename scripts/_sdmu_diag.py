"""SDMU-DIAG — SD(mu)=0 on blocked Tuesday market-aware re-run (read-only diag).

Recomputes nine (season, week) blocks via the production walk-forward harness,
decomposes μ constancy by stack stage, scans published runs for latent zero-SD
blocks, and writes ``docs/notes/sdmu-diag.md``.

Not a production entrypoint. Does not change gates or models.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from ncaa_quant.evaluation.backtest_runner import (
    load_backtest_config,
    load_staged_games,
    walkforward_config_from_mapping,
)
from ncaa_quant.evaluation.production_stack import (
    MARKET_FEATURE_COLS,
    MAX_CREDIBLE_MARGIN_PRED,
    ProductionEnsemblePredictor,
    build_observations_from_staged,
    build_production_stack,
)
from ncaa_quant.evaluation.walkforward import (
    WalkForwardConfig,
    WalkForwardHarness,
    assert_prediction_quality_gate,
    scored_prediction_rows,
)
from ncaa_quant.utils.seeding import set_global_seed

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "docs" / "notes" / "_artifacts" / "sdmu_diag"
MEMO = ROOT / "docs/notes/sdmu-diag.md"
STAGED = ROOT / "data/staged"
CFG_NAME = "task23_market_aware_full_reduced_v2_tue"

FAIL_BLOCKS: tuple[tuple[int, int], ...] = (
    (2019, 2),
    (2019, 3),
    (2019, 4),
    (2023, 1),
    (2023, 2),
    (2023, 3),
    (2023, 4),
)
CONTROL_BLOCKS: tuple[tuple[int, int], ...] = ((2019, 1), (2023, 5))
TARGET_BLOCKS: frozenset[tuple[int, int]] = frozenset([*FAIL_BLOCKS, *CONTROL_BLOCKS])

LATENCY_RUNS: dict[str, Path] = {
    "fundamental_v2": ROOT
    / "data/backtests/task23_fundamental_reduced_v2/full/predictions.parquet",
    "A3_v2": ROOT / "data/backtests/task23_a3_reduced_v2/A3_market_off/predictions.parquet",
    "A6_v2": ROOT / "data/backtests/task23_a6_reduced_v2/A6_cfbd_open_close/predictions.parquet",
    "SLOT_CLOSE": ROOT
    / "data/backtests/task23_market_aware_reduced_v2_slot_close/full/predictions.parquet",
}

SD_THRESHOLD = 0.01


@dataclass(frozen=True)
class StageSd:
    stage: str
    sd: float
    n: int
    min_val: float
    max_val: float
    constant: bool


def _pop_sd(values: np.ndarray) -> StageSd:
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    n = int(v.size)
    if n == 0:
        return StageSd("?", float("nan"), 0, float("nan"), float("nan"), True)
    sd = float(v.std(ddof=0)) if n >= 2 else 0.0
    return StageSd(
        stage="",
        sd=sd,
        n=n,
        min_val=float(v.min()),
        max_val=float(v.max()),
        constant=bool(n >= 2 and sd == 0.0),
    )


def _numeric_feature_cols(features: pd.DataFrame) -> list[str]:
    skip = {"game_id", "game_key", "season", "week", "as_of", "event_time", "market_provenance"}
    return [c for c in features.columns if c not in skip]


def feature_matrix_inventory(features: pd.DataFrame) -> dict[str, Any]:
    """Per-column null and constant share for the matrix a member receives."""
    cols = _numeric_feature_cols(features)
    n_rows = int(len(features))
    col_stats: list[dict[str, Any]] = []
    for col in cols:
        s = pd.to_numeric(features[col], errors="coerce")
        n_null = int(s.isna().sum())
        finite = s.dropna()
        n_finite = int(finite.size)
        const = False
        if n_finite >= 2:
            const = float(finite.std(ddof=0)) < 1e-12
        elif n_finite == 1:
            const = True
        col_stats.append(
            {
                "column": col,
                "null_share": float(n_null / n_rows) if n_rows else float("nan"),
                "constant": const,
                "n_finite": n_finite,
            }
        )
    mkt = [c for c in MARKET_FEATURE_COLS if c in features.columns and c != "market_provenance"]
    return {
        "n_rows": n_rows,
        "n_columns": len(cols),
        "market_columns": {
            c: next((r for r in col_stats if r["column"] == c), None) for c in mkt
        },
        "all_null_columns": [r["column"] for r in col_stats if r["null_share"] == 1.0],
        "constant_columns": [r["column"] for r in col_stats if r["constant"]],
        "columns": col_stats,
    }


def decompose_margin_stages(
    predictor: ProductionEnsemblePredictor,
    features: pd.DataFrame,
    *,
    final_pred: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """SD(mu) at LGBM, ENet, NNLS stack, epistemic mix, final μ."""
    inv = feature_matrix_inventory(features)
    stages: dict[str, Any] = {}

    lgbm = predictor.margin_head.predict(features)
    lgbm_v = lgbm["pred_margin"].astype(float).to_numpy()
    s = _pop_sd(lgbm_v)
    stages["lgbm_raw"] = asdict(
        StageSd("lgbm_raw", s.sd, s.n, s.min_val, s.max_val, s.constant)
    )

    enet_v = np.full_like(lgbm_v, np.nan)
    enet_err: str | None = None
    enet_credible_all = True
    enet_fallback = False
    try:
        enet = predictor.enet_margin.predict(features)
        enet_v = (
            enet.set_index("game_id")
            .reindex(lgbm["game_id"])["pred_margin"]
            .astype(float)
            .to_numpy()
        )
        credible = np.isfinite(enet_v) & (np.abs(enet_v) <= MAX_CREDIBLE_MARGIN_PRED)
        enet_credible_all = bool(np.all(credible))
        if not enet_credible_all:
            enet_fallback = True
            enet_v = np.full_like(lgbm_v, 2.5, dtype=float)
    except Exception as exc:  # noqa: BLE001
        enet_err = str(exc)
        enet_fallback = True
        enet_v = np.full_like(lgbm_v, 2.5, dtype=float)
    s = _pop_sd(enet_v)
    stages["enet_raw"] = asdict(
        StageSd("enet_raw", s.sd, s.n, s.min_val, s.max_val, s.constant)
    )
    stages["enet_meta"] = {
        "error": enet_err,
        "credible_all": enet_credible_all,
        "fallback_constant_2p5": enet_fallback,
        "selected_features": list(getattr(predictor.enet_margin, "_selected_features", [])),
    }

    point = predictor._predict_point(features)  # noqa: SLF001 — diag script
    stack_v = point["pred_margin"].astype(float).to_numpy()
    s = _pop_sd(stack_v)
    stages["nnls_stack"] = asdict(
        StageSd("nnls_stack", s.sd, s.n, s.min_val, s.max_val, s.constant)
    )
    stages["nnls_weights"] = dict(predictor.ensemble_weights)

    if final_pred is not None:
        final_v = final_pred["pred_margin"].astype(float).to_numpy()
    else:
        final_v = predictor.predict(features)["pred_margin"].astype(float).to_numpy()
    s = _pop_sd(final_v)
    stages["final_mu"] = asdict(
        StageSd("final_mu", s.sd, s.n, s.min_val, s.max_val, s.constant)
    )

    # ProductionEnsemblePredictor.predict replaces pred_margin with the epistemic
    # mixture mean when n_epistemic_draws >= 2 — no separate mix call needed.
    if int(predictor.n_epistemic_draws) >= 2:
        stages["epistemic_mix"] = dict(stages["final_mu"])
        stages["epistemic_meta"] = {"n_draws": int(predictor.n_epistemic_draws), "note": "same as final_mu"}
    else:
        stages["epistemic_mix"] = dict(stages["nnls_stack"])
        stages["epistemic_meta"] = {"note": "n_epistemic_draws < 2; stack passes through"}

    stages["feature_inventory"] = inv
    return stages


class _PredictorCaptureProxy:
    """Proxy around ProductionEnsemblePredictor.predict for block capture."""

    def __init__(
        self,
        inner: ProductionEnsemblePredictor,
        *,
        current_block: list[tuple[int, int] | None],
        store: dict[tuple[int, int], dict[str, Any]],
    ) -> None:
        self._inner = inner
        self._current_block = current_block
        self._store = store

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def predict(self, features: pd.DataFrame) -> pd.DataFrame:
        block = self._current_block[0]
        # Non-target weeks only need point μ for the prediction table; skip MC /
        # epistemic (dominant cost). Target blocks use the full predict path.
        if block is None or block not in TARGET_BLOCKS:
            point = self._inner._predict_point(features)  # noqa: SLF001
            out = point.copy()
            n = len(out)
            for col, fill in (
                ("sigma_m", 10.0),
                ("sigma_t", 10.0),
                ("rho", 0.0),
                ("p_ml_home", 0.5),
                ("p_ats_home", 0.5),
                ("p_ou_over", 0.5),
                ("p_ml_home_raw", 0.5),
                ("p_ats_home_raw", 0.5),
                ("p_ou_over_raw", 0.5),
            ):
                if col not in out.columns:
                    out[col] = fill
            del n
            return out

        out = self._inner.predict(features)
        self._store[block] = decompose_margin_stages(
            self._inner,
            features,
            final_pred=out,
        )
        return out

    def fit(self, *args: Any, **kwargs: Any) -> None:
        return self._inner.fit(*args, **kwargs)


def _diag_config(payload: dict[str, Any]) -> WalkForwardConfig:
    """Tuesday market-aware config with gate off and 2024 dropped for speed."""
    wf = dict(payload.get("walkforward", payload))
    wf["enforce_prediction_quality_gate"] = False
    wf["test_seasons"] = [2019, 2021, 2022, 2023]
    wf["run_id"] = "sdmu_diag_tue"
    wf["ablation_id"] = "diag"
    patched = dict(payload)
    patched["walkforward"] = wf
    patched["run_id"] = "sdmu_diag_tue"
    return walkforward_config_from_mapping(patched)


def _load_staged_bundle(replay_seasons: tuple[int, ...]) -> dict[str, Any]:
    from ncaa_quant.cli import (
        load_fitted_priors_frame_for_backtest,
        load_staged_odds_snapshots,
    )
    from ncaa_quant.data.storage import ParquetStore
    from ncaa_quant.features.possessions import build_possessions_training_from_staged

    store = ParquetStore(STAGED)
    games = load_staged_games(STAGED, replay_seasons)
    advanced_frames: list[pd.DataFrame] = []
    plays_frames: list[pd.DataFrame] = []
    lines_frames: list[pd.DataFrame] = []
    teams_frames: list[pd.DataFrame] = []
    drives_frames: list[pd.DataFrame] = []
    for season in replay_seasons:
        for path in store._matching_paths("advanced_box", {"season": int(season)}):  # noqa: SLF001
            advanced_frames.append(pd.read_parquet(path))
        for path in store._matching_paths("plays", {"season": int(season)}):  # noqa: SLF001
            plays_frames.append(pd.read_parquet(path))
        for path in store._matching_paths("lines_historical", {"season": int(season)}):  # noqa: SLF001
            lines_frames.append(pd.read_parquet(path))
        for path in store._matching_paths("teams", {"season": int(season)}):  # noqa: SLF001
            teams_frames.append(pd.read_parquet(path))
        for path in store._matching_paths("drives", {"season": int(season)}):  # noqa: SLF001
            drives_frames.append(pd.read_parquet(path))

    advanced = pd.concat(advanced_frames, ignore_index=True) if advanced_frames else None
    plays = pd.concat(plays_frames, ignore_index=True) if plays_frames else None
    cfbd_lines = pd.concat(lines_frames, ignore_index=True) if lines_frames else None
    teams = pd.concat(teams_frames, ignore_index=True) if teams_frames else pd.DataFrame()
    drives = pd.concat(drives_frames, ignore_index=True) if drives_frames else pd.DataFrame()

    cfg_probe = walkforward_config_from_mapping(load_backtest_config(CFG_NAME))
    obs, n_on, n_off = build_observations_from_staged(
        plays=plays,
        games=games,
        advanced=advanced,
        garbage_time_filter=cfg_probe.garbage_time_filter,
    )
    snapshots = load_staged_odds_snapshots(STAGED, replay_seasons)
    priors = load_fitted_priors_frame_for_backtest(STAGED, replay_seasons)
    possessions = (
        build_possessions_training_from_staged(
            plays=plays if plays is not None else pd.DataFrame(),
            games=games,
            teams=teams,
            drives=drives,
            garbage_time_filter=cfg_probe.garbage_time_filter,
        )
        if plays is not None and not plays.empty and not drives.empty
        else None
    )
    return {
        "games": games,
        "observations": obs,
        "play_counts": (n_on, n_off) if n_off > 0 else None,
        "snapshots": snapshots,
        "cfbd_lines": cfbd_lines,
        "priors_frame": priors,
        "possessions_training": possessions,
    }


def replay_nine_blocks(*, force: bool = False) -> dict[str, Any]:
    """Step 1–2: harness replay + stage decomposition for nine blocks."""
    ART.mkdir(parents=True, exist_ok=True)
    out_path = ART / "replay_predictions.parquet"
    cap_path = ART / "block_captures.json"
    if out_path.is_file() and cap_path.is_file() and not force:
        preds = pd.read_parquet(out_path)
        captures = json.loads(cap_path.read_text(encoding="utf-8"))
        return {"predictions": preds, "captures": captures, "resumed": True}

    payload = load_backtest_config(CFG_NAME)
    cfg = _diag_config(payload)
    bundle = _load_staged_bundle(cfg.all_replay_seasons())
    games = bundle["games"]
    # Stop replay after the last control block (2023, w5); omit 2023 w6+.
    games = games.loc[
        ~((games["season"].astype(int) == 2023) & (games["week"].astype(int) > 5))
    ].copy()
    bundle["games"] = games

    # Cheap draws: SD(mu) constancy is decided at member/stack; epistemic/MC
    # only average. Keep ≥2 epistemic draws so the production mix path runs on
    # target blocks.
    stack = build_production_stack(
        cfg,
        kind="market_aware",
        observations=bundle["observations"],
        priors_frame=bundle["priors_frame"],
        snapshots=bundle["snapshots"],
        cfbd_lines=bundle["cfbd_lines"],
        possessions_training=bundle["possessions_training"],
        play_counts=bundle["play_counts"],
        n_mc_draws=32,
        n_epistemic_draws=4,
    )

    current_block: list[tuple[int, int] | None] = [None]
    captures: dict[tuple[int, int], dict[str, Any]] = {}
    predictor_proxy = _PredictorCaptureProxy(
        stack.predictor,
        current_block=current_block,
        store=captures,
    )

    provider = stack.feature_provider
    orig_compute = provider.compute_game_features

    def compute_with_block(
        week_games: pd.DataFrame,
        as_of: Any,
        **kwargs: Any,
    ) -> pd.DataFrame:
        if not week_games.empty and "season" in week_games.columns and "week" in week_games.columns:
            current_block[0] = (int(week_games.iloc[0]["season"]), int(week_games.iloc[0]["week"]))
        return orig_compute(week_games, as_of, **kwargs)

    provider.compute_game_features = compute_with_block  # type: ignore[method-assign]

    harness = WalkForwardHarness(
        config=cfg,
        predictor=predictor_proxy,
        feature_provider=provider,
        rating_engine=stack.rating_engine,
    )

    t0 = time.perf_counter()
    result = harness.run(
        bundle["games"],
        snapshots=bundle["snapshots"],
        cfbd_lines=bundle["cfbd_lines"],
    )
    elapsed = time.perf_counter() - t0
    preds = result.predictions
    preds.to_parquet(out_path, index=False)

    cap_serial = {f"{s}_{w}": v for (s, w), v in captures.items()}
    cap_path.write_text(json.dumps(cap_serial, indent=2, sort_keys=True), encoding="utf-8")

    gate = assert_prediction_quality_gate(preds, raise_on_fail=False)
    scored = scored_prediction_rows(preds)
    block_sds: list[dict[str, Any]] = []
    for (s, w) in sorted(TARGET_BLOCKS):
        chunk = scored.loc[(scored["season"] == s) & (scored["week"] == w)]
        vals = pd.to_numeric(chunk["pred_margin"], errors="coerce").dropna()
        sd = float(vals.std(ddof=0)) if len(vals) >= 2 else float("nan")
        block_sds.append(
            {
                "season": s,
                "week": w,
                "n_games": int(len(chunk)),
                "sd_mu": sd,
                "n_train_games": int(chunk["n_train_games"].iloc[0]) if len(chunk) else None,
                "status": "FAIL" if (s, w) in FAIL_BLOCKS else "CONTROL",
            }
        )

    summary = {
        "wall_clock_sec": elapsed,
        "gate_passed": gate.passed,
        "gate_failures": list(gate.failures),
        "zero_sd_blocks": list(gate.zero_sd_blocks),
        "block_sds": block_sds,
        "retrain_events": result.retrain_events,
    }
    (ART / "replay_summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    return {
        "predictions": preds,
        "captures": cap_serial,
        "summary": summary,
        "resumed": False,
    }


def latency_table(*, threshold: float = SD_THRESHOLD) -> list[dict[str, Any]]:
    """Step 4: per-block SD(mu) for published runs."""
    rows: list[dict[str, Any]] = []
    for run_name, path in LATENCY_RUNS.items():
        if not path.is_file():
            rows.append({"run": run_name, "path": str(path), "missing": True})
            continue
        df = pd.read_parquet(path)
        scored = scored_prediction_rows(df)
        low: list[dict[str, Any]] = []
        for (s, w), g in scored.groupby(["season", "week"], sort=True):
            vals = pd.to_numeric(g["pred_margin"], errors="coerce").dropna()
            if len(vals) < 2:
                continue
            sd = float(vals.std(ddof=0))
            if sd < threshold:
                low.append(
                    {
                        "season": int(s),
                        "week": int(w),
                        "sd_mu": sd,
                        "n": int(len(vals)),
                        "mu_value": float(vals.iloc[0]),
                    }
                )
        rows.append(
            {
                "run": run_name,
                "path": str(path.relative_to(ROOT)),
                "n_scored": int(len(scored)),
                "n_low_sd_blocks": len(low),
                "low_sd_blocks": low,
            }
        )
    return rows


def _fmt_sd_table(captures: dict[str, Any], block_sds: list[dict[str, Any]]) -> str:
    lines = [
        "| season | week | status | n | SD final | SD LGBM | SD ENet | SD stack | SD epistemic |",
        "|---:|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    key_map = {(int(r["season"]), int(r["week"])): r for r in block_sds}
    for (s, w) in sorted(TARGET_BLOCKS):
        cap = captures.get(f"{s}_{w}", {})
        stages = cap if cap else {}
        row = key_map.get((s, w), {})
        status = row.get("status", "?")
        n = row.get("n_games", "?")

        def _sd(name: str) -> str:
            hit = stages.get(name, {})
            if not hit:
                return "—"
            v = hit.get("sd")
            return f"{v:.6f}" if v is not None and np.isfinite(float(v)) else "—"

        lines.append(
            f"| {s} | {w} | {status} | {n} | {_sd('final_mu')} | {_sd('lgbm_raw')} | "
            f"{_sd('enet_raw')} | {_sd('nnls_stack')} | {_sd('epistemic_mix')} |"
        )
    return "\n".join(lines)


def write_memo(
    *,
    captures: dict[str, Any],
    summary: dict[str, Any],
    latency: list[dict[str, Any]],
) -> None:
    block_sds = summary.get("block_sds", [])
    lines: list[str] = [
        "# TASK SDMU-DIAG — SD(mu)=0 on blocked Tuesday market-aware re-run",
        "",
        f"**Date:** {datetime.now(tz=UTC).date().isoformat()}",
        " **Scope:** Diagnose only; no fix, no gate change, no full-config re-run.",
        f" **Config replayed:** `{CFG_NAME}` (gate off; test seasons through 2023).",
        "",
        "Artifacts: `docs/notes/_artifacts/sdmu_diag/`.",
        "",
        "---",
        "",
        "## STEP 1 — Cheap replay (nine blocks)",
        "",
        f"Wall clock: **{summary.get('wall_clock_sec', '?')} s** "
        f"({'resumed artifact' if summary.get('resumed') else 'fresh harness run'}).",
        "",
        "Quality gate on replay (same D2 rule as production): "
        f"**{'PASS' if summary.get('gate_passed') else 'FAIL'}**.",
        "",
    ]
    if summary.get("gate_failures"):
        lines.append("```")
        for f in summary["gate_failures"]:
            lines.append(str(f))
        lines.append("```")
        lines.append("")

    lines.extend(
        [
            "### Per-block SD(mu) on replayed `pred_margin`",
            "",
            "| season | week | status | n_games | SD(mu) | n_train_games |",
            "|---:|---:|---|---:|---:|---:|",
        ]
    )
    for r in block_sds:
        sd = r.get("sd_mu")
        sd_s = f"{sd:.6f}" if sd is not None and np.isfinite(float(sd)) else "—"
        lines.append(
            f"| {r['season']} | {r['week']} | {r['status']} | {r['n_games']} | {sd_s} | "
            f"{r.get('n_train_games', '—')} |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "## STEP 2 — Stage-by-stage SD decomposition",
            "",
            _fmt_sd_table(captures, block_sds),
            "",
            "### Feature-matrix inventory (constant members only)",
            "",
        ]
    )

    for (s, w) in sorted(TARGET_BLOCKS):
        cap = captures.get(f"{s}_{w}", {})
        inv = cap.get("feature_inventory", {})
        enet = cap.get("enet_meta", {})
        lines.append(f"#### ({s}, w{w})")
        lines.append("")
        lines.append(
            f"- Matrix: **{inv.get('n_rows', '?')}** rows × **{inv.get('n_columns', '?')}** cols"
        )
        mkt = inv.get("market_columns", {})
        if mkt:
            lines.append("- Market columns:")
            for col, stat in mkt.items():
                if stat:
                    lines.append(
                        f"  - `{col}`: null_share={stat.get('null_share', '?')}, "
                        f"constant={stat.get('constant')}"
                    )
        if inv.get("all_null_columns"):
            lines.append(f"- All-null columns ({len(inv['all_null_columns'])}): "
                         f"`{', '.join(inv['all_null_columns'][:12])}`"
                         f"{'…' if len(inv['all_null_columns']) > 12 else ''}")
        enet_st = cap.get("enet_raw", {})
        if enet_st.get("constant"):
            lines.append(
                f"- **ENet constant** (SD={enet_st.get('sd')}); "
                f"fallback_2.5={enet.get('fallback_constant_2p5')}; "
                f"error={enet.get('error')}; "
                f"selected_k={len(enet.get('selected_features', []))}"
            )
            sel = enet.get("selected_features", [])
            if sel:
                lines.append(f"  - ENet selected: `{', '.join(sel[:8])}`{'…' if len(sel)>8 else ''}")
        lgbm_st = cap.get("lgbm_raw", {})
        if lgbm_st.get("constant"):
            lines.append(f"- **LGBM constant** (SD={lgbm_st.get('sd')})")
        lines.append("")

    lines.extend(["---", "", "## STEP 3 — Boundary explanations", "", "*(Filled after mechanism verification.)*", ""])

    lines.extend(["---", "", "## STEP 4 — Published-run latency (SD < 0.01)", ""])
    for row in latency:
        lines.append(f"### `{row['run']}`")
        if row.get("missing"):
            lines.append("- **MISSING**")
            lines.append("")
            continue
        lines.append(f"- Scored rows: **{row['n_scored']}**")
        lines.append(f"- Blocks with SD(mu) < {SD_THRESHOLD}: **{row['n_low_sd_blocks']}**")
        if row["n_low_sd_blocks"] == 0:
            lines.append("- None — published tables for this run did not consume zero-SD blocks.")
        else:
            for b in row["low_sd_blocks"]:
                lines.append(
                    f"  - ({b['season']}, w{b['week']}): SD={b['sd_mu']:.6f}, n={b['n']}, μ≈{b['mu_value']}"
                )
        lines.append("")

    lines.extend(["---", "", "## STEP 5 — Fix scope (not implemented)", "", "*(Filled after mechanisms confirmed.)*", ""])

    MEMO.write_text("\n".join(lines), encoding="utf-8")


def _infer_mechanisms(
    captures: dict[str, Any],
    summary: dict[str, Any],
) -> tuple[str, str, str]:
    """Return (boundary_2019, boundary_2023, fix_scope) markdown sections."""
    b2019: list[str] = []
    b2023: list[str] = []
    fixes: list[str] = []

    def _get(s: int, w: int) -> dict[str, Any]:
        return captures.get(f"{s}_{w}", {})

    # Compare controls vs failures
    c19_w1 = _get(2019, 1)
    f19_w2 = _get(2019, 2)
    f23_w4 = _get(2023, 4)
    c23_w5 = _get(2023, 5)

    def _first_const_stage(cap: dict[str, Any]) -> str | None:
        for name in ("lgbm_raw", "enet_raw", "nnls_stack", "epistemic_mix", "final_mu"):
            st = cap.get(name, {})
            if st.get("constant"):
                return name
        return None

    # 2019 boundary
    w1_const = _first_const_stage(c19_w1)
    w2_const = _first_const_stage(f19_w2)
    w1_mkt_null = (
        c19_w1.get("feature_inventory", {})
        .get("market_columns", {})
        .get("mkt_spread", {})
        .get("null_share")
    )
    w2_mkt_null = (
        f19_w2.get("feature_inventory", {})
        .get("market_columns", {})
        .get("mkt_spread", {})
        .get("null_share")
    )

    if w2_const and not w1_const:
        b2019.append(
            "(2019, w1) **passes** while w2–w4 **fail** under the same offseason-fit model "
            "(retrain schedule `[5, 10]` — no retrain between w1 and w4)."
        )
        b2019.append(
            f"- w1 first constant stage: **{w1_const or 'none'}**; "
            f"w2 first constant stage: **{w2_const}**."
        )
        if w1_mkt_null == 1.0 and w2_mkt_null == 1.0:
            b2019.append(
                "- Both weeks have **100% null** snapshot market columns (MKT-2019-FIX path); "
                "null-market alone does not explain the w1 vs w2–w4 split."
            )
    elif w2_const:
        b2019.append(
            f"2019 failures: constancy enters at **{w2_const}** (includes w1={w1_const})."
        )

    # 2023 boundary
    w4_const = _first_const_stage(f23_w4)
    w5_const = _first_const_stage(c23_w5)
    retrain_at_5 = any(
        e.get("season") == 2023 and e.get("week") == 5 for e in summary.get("retrain_events", [])
    )
    if w4_const and not w5_const and retrain_at_5:
        b2023.append(
            "(2023, w1–w4) fail with SD(mu)=0; **(2023, w5) passes** immediately after "
            "the week-5 retrain gate (`retrain_weeks=[5, 10]`)."
        )
        b2023.append(
            f"- w4 first constant stage: **{w4_const}**; w5: **{w5_const or 'none'}**."
        )
        n_train_w4 = next(
            (r["n_train_games"] for r in summary.get("block_sds", []) if r["season"] == 2023 and r["week"] == 4),
            None,
        )
        n_train_w5 = next(
            (r["n_train_games"] for r in summary.get("block_sds", []) if r["season"] == 2023 and r["week"] == 5),
            None,
        )
        if n_train_w4 is not None and n_train_w5 is not None:
            b2023.append(
                f"- Training set size: w4 **{n_train_w4}** games → w5 **{n_train_w5}** games "
                "(week-5 retrain refits mapping on expanded bank)."
            )

    # Fix scope per mechanism
    for label, blocks in (
        ("2019 w2–w4", [(2019, 2), (2019, 3), (2019, 4)]),
        ("2023 w1–w4", [(2023, 1), (2023, 2), (2023, 3), (2023, 4)]),
    ):
        stages = {_first_const_stage(_get(s, w)) for s, w in blocks}
        stages.discard(None)
        if not stages:
            continue
        fixes.append(f"### {label}")
        fixes.append(f"- Verified constant stage(s): `{', '.join(sorted(stages))}`")
        if "lgbm_raw" in stages or "nnls_stack" in stages:
            fixes.append(
                "- Likely fix locus: `src/ncaa_quant/evaluation/production_stack.py` "
                "(`ProductionEnsemblePredictor._predict_point` / `_epistemic_mix`) and/or "
                "`src/ncaa_quant/models/heads/elasticnet.py` (NaN → sklearn path)."
            )
            fixes.append(
                "- **Training-input change** if ENet/LGBM fit matrix is degenerate at cold-start "
                "retrain → forces full reduced-v2 re-run."
            )
        if "enet_raw" in stages:
            fixes.append(
                "- ENet: `select_top_k_features` skips zero-variance columns; predict passes NaN "
                "through `StandardScaler`/`ElasticNet.predict` — verify imputation behavior; "
                "non-credible fallback replaces **entire block** with 2.5 when any row fails "
                "(`_predict_point` credible check)."
            )
        fixes.append(
            "- Supersedes: blocked `mkt-2019-fix` Tuesday table (`docs/notes/mkt-2019-fix.md` Step 4); "
            "no published v2 Tuesday numbers until gate passes."
        )
        fixes.append("")

    boundary_2019 = "\n".join(b2019) if b2019 else "_Mechanism not isolated — see captures._"
    boundary_2023 = "\n".join(b2023) if b2023 else "_Mechanism not isolated — see captures._"
    fix_scope = "\n".join(fixes) if fixes else "_Pending stage decomposition._"
    return boundary_2019, boundary_2023, fix_scope


def finalize_memo(summary: dict[str, Any], captures: dict[str, Any], latency: list[dict[str, Any]]) -> None:
    b2019, b2023, fix_scope = _infer_mechanisms(captures, summary)
    text = MEMO.read_text(encoding="utf-8")
    text = text.replace(
        "*(Filled after mechanism verification.)*",
        b2019 + "\n\n### 2023 week-5 boundary\n\n" + b2023,
        1,
    )
    text = text.replace("*(Filled after mechanisms confirmed.)*", fix_scope, 1)
    MEMO.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="SDMU-DIAG harness")
    parser.add_argument("--force", action="store_true", help="Re-run harness even if artifacts exist")
    parser.add_argument("--skip-replay", action="store_true", help="Only refresh memo from artifacts")
    args = parser.parse_args()
    ART.mkdir(parents=True, exist_ok=True)

    latency = latency_table()
    (ART / "latency_table.json").write_text(json.dumps(latency, indent=2), encoding="utf-8")

    if args.skip_replay and (ART / "replay_summary.json").is_file():
        summary = json.loads((ART / "replay_summary.json").read_text(encoding="utf-8"))
        captures = json.loads((ART / "block_captures.json").read_text(encoding="utf-8"))
    else:
        out = replay_nine_blocks(force=args.force)
        summary = out.get("summary") or json.loads(
            (ART / "replay_summary.json").read_text(encoding="utf-8")
        )
        captures = out["captures"]

    write_memo(captures=captures, summary=summary, latency=latency)
    finalize_memo(summary, captures, latency)
    print(f"Wrote {MEMO}")


if __name__ == "__main__":
    main()
