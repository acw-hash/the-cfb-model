# Situational angles — discovery summary

Pre-registered config `configs\angles.yaml` sha `0120478c6f034d77`. BH at q=0.1 across **every primary ever run on discovery** (cumulative family). Variants are exploratory. Holdout was not part of this table unless listed.

| id | name | n | estimate | p | p_adj | BH reject | sign vs registered | notes |
|---|---|---:|---:|---:|---:|:---:|:---:|---|
| H1 | Rested angle team vs opponent on a normal week that has not yet had a bye | 321 | 0.836 | 0.2514 | 0.5137 | False | ok | from batch1 (not recomputed this run); failed |
| H3 | Rested angle team; ATS residual by opponent's consecutive weeks played (linear trend) | 889 | 0.297 | 0.2854 | 0.5137 | False | ok | from batch1 (not recomputed this run); failed |
| H14 | Fade the opponent after a top-decile "lucky" game | 808 | -0.937 | 0.1258 | 0.4622 | False | DISAGREES | from batch1 (not recomputed this run); failed |
| H7 | Letdown — fade a favorite coming off a 7+ dog upset win vs a lower-rated opponent | 98 | -0.982 | 0.4683 | 0.6378 | False | DISAGREES | estimate sign disagrees with the pre-registered direction; failed |
| H9 | Wounded animal — back a Ridge top-40 team after a bad loss | 312 | 0.195 | 0.8126 | 0.8209 | False | ok | failed |
| H15 | Turnover luck — fade a team coming off +3 or better turnover margin | 630 | -0.135 | 0.8209 | 0.8209 | False | DISAGREES | estimate sign disagrees with the pre-registered direction; play_type values counted as turnovers: interceptions = Interception Return Touchdown, Pass Interception Return; fumbles lost = Fumble Recovery (Opponent), Fumble Return Touchdown. Fumble Recovery (Own) is not a turnover.; failed |
| H22 | Road-loss bounce-back — home after a non-neutral road loss | 1939 | -1.072 | 0.0023 | 0.0211 | True | DISAGREES | estimate sign disagrees with the pre-registered direction; passed |
| H23 | After a home win over a ranked team — fade this week's opponent | 337 | 0.616 | 0.4961 | 0.6378 | False | ok | failed |
| H24 | Road underdogs — back the away underdog at the close (non-neutral) | 3511 | 0.352 | 0.1541 | 0.4622 | False | ok | failed |

## Variants (exploratory, not in BH)

- `H7v_top15_victim` estimate=-10.167 p=— n=9
- `H9v_home` estimate=-0.825 p=0.4997 n=144
- `H9v_road` estimate=1.127 p=0.3747 n=165
- `H15v_fumble` estimate=0.696 p=0.3043 n=459
- `H15v_int` estimate=0.616 p=0.1281 n=1108
- `H22v_blowout` estimate=-1.431 p=0.0013 n=1063
- `H24v_dog_0_7` estimate=0.884 p=0.0428 n=1261
- `H24v_dog_7_14` estimate=-0.346 p=0.5751 n=888
- `H24v_dog_14plus` estimate=0.266 p=0.5324 n=1349
- `H24v_conference` estimate=0.545 p=0.0702 n=2387

## Batch-1 variants (not recomputed this run)

- `H1v_rest10` estimate=0.555 p=0.4004 n=353
- `H1v_no_rivalry` estimate=0.611 p=0.3920 n=302
- `H1v_conference_only` estimate=0.677 p=0.4908 n=213
- `H3v_ge6_vs_le3` estimate=1.531 p=0.0902 n=263
- `H3v_any_team_rest` estimate=0.189 p=0.4745 n=927
- `H14v_top_quintile` estimate=-0.136 p=0.7337 n=1432
- `H14v_back_unlucky` estimate=0.640 p=0.2685 n=783
- `H14v_ats_component_only` estimate=-0.685 p=0.2104 n=932
- `H14v_epa_component_only` estimate=-0.223 p=0.7053 n=801

`H1v_both_rested` was removed (n=0 by construction) and is not in this table.

Holdout was **not** opened this run.


## Run notes

- BH is cumulative at q=0.1 across every primary ever run on discovery: H1, H3, H14, H7, H9, H15, H22, H23, H24
- H1 / H3 / H14 **pass/fail verdicts did not change** (still fail). Their p_adj rose because the family grew from 3 to 9.
- H22 is the only BH reject. The estimate is **negative** (home teams coming off a road loss were 1.07 pts worse ATS than the close). That disagrees with the pre-registered positive direction, so it is not a confirmed bounce-back angle.
- Batch 3 H22 sanity (discovery): all-home CFBD mean −0.473; H22 vs all-home `qualifies` coef −0.860 p=0.0883 — the road-loss effect does not survive that control. Holdout was not opened.

## Descriptive (excluded from BH)

These primaries were run on discovery as description only. They are **motivated by discovery; not evidence** and are not in the cumulative BH family.

| id | name | n | estimate | p | notes |
|---|---|---:|---:|---:|---|
| H25 | ATS momentum — previous-game ATS residual difference (home minus away) | 5349 | 0.101 | 0.2747 | motivated by discovery; not evidence (excluded from cumulative BH family) |

- `H25v_top_decile` estimate=-0.053 p=0.9366 n=475

