"""Great Expectations suite builders for staged tables."""

from ncaa_quant.quality.expectations.suites import (
    TABLE_SUITES,
    build_suite,
    run_suite_on_dataframe,
)

__all__ = [
    "TABLE_SUITES",
    "build_suite",
    "run_suite_on_dataframe",
]
