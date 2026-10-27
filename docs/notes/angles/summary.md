# Situational angles — discovery summary

Pre-registered config `configs\angles.yaml` sha `291027193d663244`. BH at q=0.1 across **every primary ever run on discovery** (cumulative family). Variants are exploratory. Holdout was not opened.

MDE = 2.8 × clustered SE (≈80% power, two-sided α≈0.05). Verdict: `supported` (BH pass, registered sign); `opposite` (BH pass, wrong sign); `no evidence` (fails BH, MDE ≤ 1.5 pts); `too few games` (fails BH, MDE > 1.5 pts).

| id | name | n | estimate | SE | MDE | p | p_adj | BH | sign | verdict |
|---|---|---:|---:|---:|---:|---:|---:|:---:|:---:|---|
| H1 | Rested angle team vs opponent on a normal week that has not yet had a bye | 321 | 0.836 | 0.723 | 2.02 | 0.2514 | 0.5604 | False | ok | too few games |
| H3 | Rested angle team; ATS residual by opponent's consecutive weeks played (linear trend) | 889 | 0.297 | 0.277 | 0.78 | 0.2854 | 0.5708 | False | ok | no evidence |
| H4 | Short week — angle rest 6–8 days, opponent rest under 6 | 107 | -0.262 | 1.679 | 4.70 | 0.8767 | 0.9053 | False | DISAGREES | too few games |
| H5 | Body clock — opponent crossed 2+ zones east with home-TZ kickoff before 13:00 | 89 | 0.430 | 1.710 | 4.79 | 0.8024 | 0.9030 | False | ok | too few games |
| H6 | Post-triple-option — fade a team coming off Army/Navy/Air Force | 208 | -1.792 | 0.943 | 2.64 | 0.0606 | 0.5048 | False | DISAGREES | too few games |
| H7 | Letdown — fade a favorite coming off a 7+ dog upset win vs a lower-rated opponent | 98 | -0.982 | 1.346 | 3.77 | 0.4683 | 0.7434 | False | DISAGREES | too few games |
| H8 | Lookahead — fade a 10+ favorite looking ahead to top-25 / rivalry | 841 | -0.203 | 0.484 | 1.36 | 0.6758 | 0.8746 | False | DISAGREES | no evidence |
| H9 | Wounded animal — back a Ridge top-40 team after a bad loss | 312 | 0.195 | 0.819 | 2.29 | 0.8126 | 0.9030 | False | ok | too few games |
| H10 | Revenge — lost to this opponent last season by 14+ | 1745 | 0.244 | 0.366 | 1.02 | 0.5069 | 0.7434 | False | ok | no evidence |
| H11 | Rivalry underdogs | 518 | 0.484 | 0.814 | 2.28 | 0.5533 | 0.7608 | False | ok | too few games |
| H12 | Bowl desperation — 5-win team in last 3 regular-season games | 133 | 1.519 | 1.183 | 3.31 | 0.2175 | 0.5604 | False | ok | too few games |
| H14 | Fade the opponent after a top-decile "lucky" game | 808 | -0.937 | 0.607 | 1.70 | 0.1258 | 0.5535 | False | DISAGREES | too few games |
| H15 | Turnover luck — fade a team coming off +3 or better turnover margin | 630 | -0.135 | 0.596 | 1.67 | 0.8209 | 0.9030 | False | DISAGREES | too few games |
| H16 | Early-season returning production (weeks 1–4 continuous) | 1566 | 0.225 | 0.134 | 0.37 | 0.0918 | 0.5048 | False | ok | no evidence |
| H17 | Conference home dogs +3 to +10 | 861 | -0.621 | 0.542 | 1.52 | 0.2547 | 0.5604 | False | DISAGREES | too few games |
| H18 | Big road favorites 14+ — back the home dog | 522 | -0.936 | 0.688 | 1.93 | 0.1767 | 0.5554 | False | DISAGREES | too few games |
| H19 | Ranked favorite vs unranked — back the unranked | 1506 | -0.050 | 0.420 | 1.18 | 0.9053 | 0.9053 | False | DISAGREES | no evidence |
| H20 | Wind unders — outdoor, kickoff wind >= 6.7 m/s (15 mph) | 30 | 2.583 | 2.836 | 7.94 | 0.3927 | 0.7199 | False | ok | too few games |
| H21 | Totals after a shootout — either side's last game over by 21+ | 1106 | 0.930 | 0.520 | 1.46 | 0.0766 | 0.5048 | False | ok | no evidence |
| H22 | Road-loss bounce-back — home after a non-neutral road loss | 1939 | -1.072 | 0.344 | 0.96 | 0.0023 | 0.0516 | True | DISAGREES | opposite |
| H23 | After a home win over a ranked team — fade this week's opponent | 337 | 0.616 | 0.901 | 2.52 | 0.4961 | 0.7434 | False | ok | too few games |
| H24 | Road underdogs — back the away underdog at the close (non-neutral) | 3511 | 0.352 | 0.246 | 0.69 | 0.1541 | 0.5554 | False | ok | no evidence |

## Not run (data missing)

| id | name | reason |
|---|---|---|
| H2 | H1 + slipping opponent (opp net adj EPA L3D bottom 25%) | adj_off_epa_l3d / adj_def_epa_l3d are not materialized under data/features; H2 not run (data missing) |
| H13 | Interim coach — first game after a midseason HC change | Staged coaches table is one row per coach-season with no midseason change event_time; cannot identify first game after a midseason HC change |

## Retired unopened (not in BH)

- H22r, H25 — retired without opening holdout (batch 4).

## Batch-5 notes

- H12: discovery is entirely pre-2024; 2024+ needs holdout (closed).
- H16: `one_row_per_game` required (week filter alone both-qualifies every game).
- H20: weather is actuals-only → **upper bound, not bettable**.

Holdout was **not** opened this run. Ledger stays empty.
