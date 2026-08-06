"""Data quality: Great Expectations suites, custom validators, quarantine flow.

Downstream consumers should call :func:`ncaa_quant.quality.quarantine.is_quarantined`
and skip quarantined ``(table, season, week)`` partitions rather than crash.
"""

from ncaa_quant.quality.quarantine import is_quarantined, load_validation_results
from ncaa_quant.quality.runner import QualityRunResult, run_quality

__all__ = [
    "QualityRunResult",
    "is_quarantined",
    "load_validation_results",
    "run_quality",
]
