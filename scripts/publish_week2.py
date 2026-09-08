from datetime import UTC, datetime
from ncaa_quant.pipelines.predict import RefreshKind, execute_predict_publish

AS_OF = datetime(2026, 9, 8, 10, 0, tzinfo=UTC)   # <-- the load-bearing line

print(f"as_of = {AS_OF.isoformat()}  refresh_kind = tuesday_primary")
result = execute_predict_publish(
    season=2026,
    week=2,
    refresh_kind=RefreshKind.TUESDAY_PRIMARY,
    as_of=AS_OF,
)
print(f"n_prediction_rows = {len(result.get('prediction_rows') or [])}")
print(f"n_candidates = {result.get('n_candidates')}")
print(f"webapp_export = {(result.get('webapp_export') or {}).get('ok')}")