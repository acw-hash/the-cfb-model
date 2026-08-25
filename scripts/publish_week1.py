from datetime import UTC, datetime
from ncaa_quant.pipelines.predict import RefreshKind, execute_predict_publish

AS_OF = datetime(2026, 8, 25, 10, 0, tzinfo=UTC)   # <-- the load-bearing line

print(f"as_of = {AS_OF.isoformat()}  refresh_kind = tuesday_primary")
result = execute_predict_publish(
    season=2026,
    week=1,
    refresh_kind=RefreshKind.TUESDAY_PRIMARY,
    as_of=AS_OF,
)
print(result)
