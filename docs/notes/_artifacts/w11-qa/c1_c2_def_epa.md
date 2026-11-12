# W11-QA C1/C2 def_epa

## C1 verdict: **higher def_epa = BETTER defense**

### Measurement (league_state.update_game)
- `h[home_off]=1.0; h[away_def]=-1.0  ⇒  y_home_epa ≈ off_home − def_away (+ HFA + env)`
- `h[away_off]=1.0; h[home_def]=-1.0  ⇒  y_away_epa ≈ off_away − def_home (+ env)`
- Holding offense fixed, a higher def_epa lowers predicted opponent EPA/play (more suppression) ⇒ higher def_epa = BETTER defense.

### Spearman (FBS through week 10)
- `def_epa_vs_opp_raw_off_epa`: n=138 rho=-0.8070864793408052 p=6.382822269612783e-33
- `def_epa_vs_pts_allowed`: n=138 rho=-0.702821929118227 p=7.396177430771106e-22
- `off_epa_vs_own_raw_off_epa`: n=138 rho=0.7867845996132322 p=2.7363574797293264e-30
- `off_epa_vs_pts_scored`: n=138 rho=0.750153840127452 p=3.370815249589255e-26

## C2 FCS prior
- config fcs_prior_mean=-0.05
- pinned fcs def_epa=0.05 off_epa=-0.05
- FBS mean def_epa=0.003184
- pinned FCS defense vs avg FBS: **BETTER**
- FAIL: **True**

## C2 predictor
- src/ncaa_quant/evaluation/production_stack.py:438-445
- fail: **False**
- No explicit sign flip on def_epa in feature builder; LightGBM consumes raw diffs. Under C1=BETTER, h−a for def is the right linear direction.

## STOP GATE: **True** — FCS prior pins defense BETTER than FBS average under C1=BETTER
