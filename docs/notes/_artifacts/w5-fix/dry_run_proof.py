"""W5-FIX B7: isolated export twice — without vs with filter_history.

push=False. Temp tier_state / tier_changes / publish_history only.
week_predictions must match except published_at. Export stays gated False.
"""

from __future__ import annotations

import json
import tempfile
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from ncaa_quant.config import load_config
from ncaa_quant.pipelines.predict import (
    RefreshKind,
    _isolated_publish_config,
    execute_predict_publish,
)
from ncaa_quant.webapp.export import export_publish_artifacts

OUT = Path(__file__).resolve().parent
OSU = 194
IOWA = 2294


def _strip_published_at(obj: object) -> object:
    if isinstance(obj, dict):
        return {
            k: _strip_published_at(v)
            for k, v in obj.items()
            if k != "published_at"
        }
    if isinstance(obj, list):
        return [_strip_published_at(v) for v in obj]
    return obj


def _export_once(
    result: dict,
    *,
    state_dir: Path,
    out_dir: Path,
    clock: datetime,
    filter_history,
    label: str,
) -> dict:
    base = load_config()
    cfg = _isolated_publish_config(base, state_dir)
    assert cfg.webapp.export_enabled is False
    print(f"[{label}] tier_state={cfg.webapp.tier_state_path}")
    print(f"[{label}] publish_history={cfg.webapp.publish_history_path}")
    export_out = export_publish_artifacts(
        result,
        config=cfg,
        published_at=clock,
        push=False,
        filter_history=filter_history,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for name, body in (export_out.get("artifacts") or {}).items():
        dest = out_dir / str(name)
        dest.write_text(str(body), encoding="utf-8")
        written[str(name)] = dest
        print(f"[{label}] wrote {dest}")
    return export_out


def main() -> None:
    base = load_config()
    print("export_enabled", base.webapp.export_enabled)
    as_of = datetime.now(tz=UTC)
    clock = datetime(2026, 10, 2, 15, 0, 0, tzinfo=UTC)

    with tempfile.TemporaryDirectory(prefix="w5fix_predict_") as pred_tmp:
        pred_state = Path(pred_tmp)
        cfg = _isolated_publish_config(base, pred_state)
        print("running execute_predict_publish (isolated; export False)…")
        result = execute_predict_publish(
            season=2026,
            week=5,
            refresh_kind=RefreshKind.TUESDAY_PRIMARY,
            config=cfg,
            as_of=as_of,
        )

    hist = result.get("_filter_history")
    print("n_prediction_rows", len(result.get("prediction_rows") or []))
    print("filter_history_rows", 0 if hist is None else len(hist))
    print("filter_history_is_none", hist is None)

    # Deep-copy result so neither export can mutate shared prediction dicts.
    result_a = deepcopy(result)
    result_b = deepcopy(result)

    with tempfile.TemporaryDirectory(prefix="w5fix_nohist_") as tmp_a:
        with tempfile.TemporaryDirectory(prefix="w5fix_withhist_") as tmp_b:
            out_a = OUT / "export_no_history"
            out_b = OUT / "export_with_history"
            if out_a.exists():
                for p in out_a.iterdir():
                    p.unlink()
            if out_b.exists():
                for p in out_b.iterdir():
                    p.unlink()

            _export_once(
                result_a,
                state_dir=Path(tmp_a),
                out_dir=out_a,
                clock=clock,
                filter_history=None,
                label="no_history",
            )
            _export_once(
                result_b,
                state_dir=Path(tmp_b),
                out_dir=out_b,
                clock=clock,
                filter_history=hist,
                label="with_history",
            )

    wp_a = json.loads((OUT / "export_no_history" / "week_predictions.json").read_text())
    wp_b = json.loads((OUT / "export_with_history" / "week_predictions.json").read_text())
    stripped_a = _strip_published_at(wp_a)
    stripped_b = _strip_published_at(wp_b)
    identical = stripped_a == stripped_b
    print("week_predictions_identical_except_published_at", identical)
    if not identical:
        # Minimal key-level report
        keys_a = set(stripped_a) if isinstance(stripped_a, dict) else set()
        keys_b = set(stripped_b) if isinstance(stripped_b, dict) else set()
        print("key_delta", sorted(keys_a ^ keys_b))
        if isinstance(stripped_a, dict) and isinstance(stripped_b, dict):
            for k in sorted(keys_a & keys_b):
                if stripped_a[k] != stripped_b[k]:
                    print(f"diff_key={k}")
        raise SystemExit("STOP: week_predictions differ beyond published_at")

    # Same published_at was used, so full objects should match too.
    print("week_predictions_fully_identical", wp_a == wp_b)

    ratings_path = OUT / "export_with_history" / "team_ratings_2026.json"
    ratings = json.loads(ratings_path.read_text(encoding="utf-8"))
    teams = ratings.get("teams") or {}
    print("team_count", len(teams))

    for tid, name in ((OSU, "Ohio State"), (IOWA, "Iowa")):
        entry = teams.get(str(tid)) or {}
        weeks = [w.get("week") for w in (entry.get("weeks") or [])]
        print(f"{name} ({tid}) weeks={weeks}")

    empty_path = OUT / "export_no_history" / "team_ratings_2026.json"
    empty = json.loads(empty_path.read_text(encoding="utf-8"))
    print("no_history_team_count", len(empty.get("teams") or {}))
    print("DONE")


if __name__ == "__main__":
    main()
