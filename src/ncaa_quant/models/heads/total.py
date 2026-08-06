"""LightGBM total μ head — re-exports the shared mu implementation.

DESIGN §11 lists ``heads/total.py`` alongside ``margin.py``. Both targets share
:class:`~ncaa_quant.models.heads.margin.LightGBMMuHead`; this module exists so
the package layout matches the design tree.
"""

from __future__ import annotations

from ncaa_quant.models.heads.margin import LightGBMTotalMuHead

__all__ = ["LightGBMTotalMuHead"]
