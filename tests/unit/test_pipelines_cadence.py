"""Unit tests for the standalone odds cadence watchdog."""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

from ncaa_quant.config import AppConfig, NotificationConfig, PipelineConfig
from ncaa_quant.pipelines.cadence import (
    _IMPORT_SURFACE,
    check_odds_cadence,
    run_odds_cadence_watchdog,
)
from ncaa_quant.pipelines.notifications import AlertKind, RecordingNotifier


def test_check_odds_cadence_counts_recent_json(tmp_path: Path) -> None:
    day = tmp_path / "2026-09-09"
    day.mkdir(parents=True)
    (day / "a.json").write_text("[]\n", encoding="utf-8")
    now = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
    out = check_odds_cadence(
        raw_root=tmp_path,
        expected_per_day=6,
        tolerance=1,
        now=now,
    )
    assert out["snapshots_24h"] == 1
    assert out["expected_minimum"] == 5
    assert out["shortfall"] is True


def test_check_odds_cadence_ok_when_enough(tmp_path: Path) -> None:
    day = tmp_path / "2026-09-09"
    day.mkdir(parents=True)
    for i in range(5):
        (day / f"{i}.json").write_text("[]\n", encoding="utf-8")
    now = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
    out = check_odds_cadence(
        raw_root=tmp_path,
        expected_per_day=6,
        tolerance=1,
        now=now,
    )
    assert out["shortfall"] is False


def test_run_watchdog_notifies_on_shortfall(tmp_path: Path) -> None:
    empty = tmp_path / "empty_raw"
    empty.mkdir()
    notifier = RecordingNotifier()
    cfg = AppConfig(
        pipeline=PipelineConfig(
            odds_snapshots_per_day=6,
            odds_cadence_tolerance=1,
            notifications=NotificationConfig(provider="null"),
        )
    )
    # Inject recording notifier via notify's override path by calling check+notify
    # through run with empty root and a custom notify via monkeypatch-free direct assert.
    from ncaa_quant.pipelines import notifications as notif_mod

    original = notif_mod.build_notifier

    def _recording(_config=None, *, override=None):  # type: ignore[no-untyped-def]
        return override or notifier

    notif_mod.build_notifier = _recording  # type: ignore[assignment]
    try:
        result = run_odds_cadence_watchdog(
            config=cfg,
            raw_root=empty,
            expected_per_day=6,
            tolerance=1,
        )
    finally:
        notif_mod.build_notifier = original  # type: ignore[assignment]

    assert result["shortfall"] is True
    assert result["notified"] is True
    assert len(notifier.sent) == 1
    assert notifier.sent[0].kind == AlertKind.CADENCE_SHORTFALL


def test_cadence_module_has_no_publish_imports() -> None:
    path = Path("src/ncaa_quant/pipelines/cadence.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = {
        "ncaa_quant.pipelines.predict",
        "ncaa_quant.webapp",
        "ncaa_quant.webapp.export",
        "ncaa_quant.webapp.push",
        "ncaa_quant.social",
        "ncaa_quant.betting.provider",
    }
    assert not (imported & forbidden)
    # Documented surface must stay publish-free.
    assert "ncaa_quant.pipelines.notifications" in _IMPORT_SURFACE
    assert "ncaa_quant.pipelines.predict" not in _IMPORT_SURFACE
