"""SOC-2 chart specs: artifact numbers → JSON → Node renderer."""

from __future__ import annotations

from ncaa_quant.social.specs.net import (
    MARGIN_SCALE_SOURCE,
    def_good,
    net_epa,
    net_pts,
    net_sd_pts,
    resolve_margin_scale,
)
from ncaa_quant.social.specs.runner import run_soc2_charts

__all__ = [
    "MARGIN_SCALE_SOURCE",
    "def_good",
    "net_epa",
    "net_pts",
    "net_sd_pts",
    "resolve_margin_scale",
    "run_soc2_charts",
]
