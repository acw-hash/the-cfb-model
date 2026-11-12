## W11-QA-4 — Card-range calibration (READ-ONLY)

**Date:** 2026-11-12  
**Command:** `uv run python -u scripts/_qa_w11_qa4_card_calib.py`  
**Artifacts:** `docs/notes/_artifacts/w11-qa/qa4_card_calib.json`, `qa4_ledger_rows.json`, `qa4_run.out`  
**Export:** False. No publish / R2 / config edits.

Source: W11-QA-3 A4 path (2026 w2–w10, FBS–FBS, staged pre-kickoff regulated lines, production MC `n_draws=20_000`, seed `42+game_id`).

### 1. Full A4 table (from W11-QA-3)

| bucket | n | mean predicted P(cover) | realized |
|--------|--:|------------------------:|---------:|
| 50–55 | 115 | 0.5286 | 0.5130 |
| 55–60 | 94 | 0.5757 | 0.4574 |
| 60–65 | 98 | 0.6243 | 0.5000 |
| 65+ | 134 | 0.7388 | 0.4925 |

n_used = 441 (qa3_part_a.json).

### 2. Same, card gates only (edge >= 4.5% and |Ridge − line| <= 7.0)

| bucket | n | mean predicted P(cover) | realized |
|--------|--:|------------------------:|---------:|
| 50–55 | 20 | 0.5459 | 0.6500 |
| 55–60 | 94 | 0.5757 | 0.4574 |
| 60–65 | 80 | 0.6205 | 0.4875 |
| 65+ | 6 | 0.6611 | 0.5000 |

| pooled | n | mean predicted | realized | 90% Wilson CI (realized) |
|--------|--:|---------------:|---------:|--------------------------|
| gated | 200 | 0.5932 | 0.4900 | [0.4324, 0.5479] |

### 3. Posted card plays w2–w10 (ledgers on this machine + w10 task-card 7)

Ledgers: `data/social/posted_calls_2026_w{2,4,5,6,7,8,9}.json`. Week 10: seven plays from W11-QA addendum (no local w10 ledger). Week 3 absent. P(cover) via same production MC at the posted side/line using that week’s published μ/σ.

| pooled | n | mean predicted | realized | 90% Wilson CI (realized) |
|--------|--:|---------------:|---------:|--------------------------|
| posted cards | 52 | 0.6237 | 0.4808 | [0.3706, 0.5928] |

(n_calls_loaded includes all ledger rows + 7 w10; n_used=52 after drops for push / missing publish join.)

### 4. Week-11 top-25 × FCS

Under staged 2026 `teams.classification`, **no** top-25 candidate involves a non-FBS school (`non_fbs_in_top25 = []`).

Rank-1 matchup `North Dakota State @ Hawai'i`: staged row for North Dakota State is `team_id=2449`, **classification=`fbs`**, conference=`Mountain West` (not FCS on this machine’s teams table).

Code path for the single shared pinned FCS state (used when `home_is_fcs` / `away_is_fcs` is true):

```350:363:src/ncaa_quant/ratings/league_state.py
        h_key = FCS_TEAM_KEY if home_is_fcs else str(home_id)
        a_key = FCS_TEAM_KEY if away_is_fcs else str(away_id)
        ...
        fcs = fcs_pinned_state(cfg)
```

```529:541:src/ncaa_quant/ratings/state_space.py
def fcs_pinned_state(config: StateSpaceConfig) -> GaussianState:
    """Pooled FCS prior with large variance (DESIGN §9.3)."""
    d = config.n_dims
    mean = np.full(d, config.fcs_prior_mean, dtype=float)
    ...
    if "def_epa" in config.state_dims:
        mean[config.dim_index("def_epa")] = config.fcs_prior_mean
    cov = np.eye(d, dtype=float) * config.fcs_prior_var
    return GaussianState(mean=mean, cov=cov)
```

```743:746:src/ncaa_quant/ratings/state_space.py
def _team_key(team_id: Any, *, is_fcs: bool) -> str:
    if is_fcs:
        return FCS_TEAM_KEY
    return str(team_id)
```

Filter wiring: `state_space.py:1210-1241` (`home_is_fcs` / `away_is_fcs` → `_team_key` / `league.update_game`).
