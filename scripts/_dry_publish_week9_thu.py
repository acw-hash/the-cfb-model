"""Week-9 Thursday daily_refresh DRY RUN — local artifacts only, no R2 push."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from ncaa_quant.pipelines.predict import RefreshKind, run_isolated_week_export

AS_OF = datetime.now(tz=UTC)
OUT = Path("docs/notes/_artifacts/soc2-weekly-w9-thu-dry/artifacts")
STATE = Path("docs/notes/_artifacts/soc2-weekly-w9-thu-dry/state")

print(f"DRY RUN as_of={AS_OF.isoformat()} refresh_kind=daily_refresh week=9")
print("push=False via run_isolated_week_export (export_enabled=False on config)")
result = run_isolated_week_export(
    season=2026,
    week=9,
    refresh_kind=RefreshKind.DAILY_REFRESH,
    output_dir=OUT,
    state_dir=STATE,
    as_of=AS_OF,
    published_at=AS_OF,
)
print("DONE written=", sorted((result.get("written") or {}).keys()))
