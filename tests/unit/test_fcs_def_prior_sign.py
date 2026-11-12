"""W11-FIX-1: FCS def_epa prior must be worse than average under off − def."""

from __future__ import annotations

from ncaa_quant.ratings.state_space import StateSpaceConfig, fcs_pinned_state


def test_fcs_pinned_def_worse_than_average_under_off_minus_def() -> None:
    """Higher def_epa = better defense (y ≈ off − def).

    FCS should be pinned slightly worse than average: off_epa < 0 and def_epa < 0
    when fcs_prior_mean is negative. Also def_epa must sit below a zero-mean
    FBS reference (league projection target).
    """
    cfg = StateSpaceConfig()
    assert cfg.fcs_prior_mean < 0.0
    pinned = fcs_pinned_state(cfg)
    i_off = cfg.dim_index("off_epa")
    i_def = cfg.dim_index("def_epa")
    fcs_off = float(pinned.mean[i_off])
    fcs_def = float(pinned.mean[i_def])
    fbs_mean_def = 0.0  # identifiability projects FBS def to mean zero

    assert fcs_off < 0.0, fcs_off
    assert fcs_def < 0.0, fcs_def
    assert fcs_def < fbs_mean_def, fcs_def
    assert fcs_def == cfg.fcs_prior_mean
    assert fcs_off == cfg.fcs_prior_mean
