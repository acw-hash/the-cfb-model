# W8-2 card probe

- analysis_as_of: `2026-11-12T17:52:11.203054+00:00`
- publish_as_of: `2026-11-12T15:37:19.689466+00:00`
- source: `data/webapp/publish_history/2026_w11.jsonl (record 1, as_of=2026-11-12T15:37:19.689466+00:00)`
- decisions: HFA=NONE, stake=0.014, max=7, weekly_cap=0.1
- books: `draftkings+fanduel+betmgm+williamhill_us+espnbet+fanatics+hardrockbet+ballybet+betrivers`

## FLAG — Tue/Wed kickoffs on card or worklist

(none)

## a. Card (up to max)

| game | kickoff ET | side | book | line | price | Ridge | gap | edge | HFA-adj edge (info) | home/away/neutral |
|------|------------|------|------|-----:|------:|------:|----:|-----:|--------------------:|-------------------|
| North Dakota State @ Hawai'i | 2026-11-14 23:00 EST | Hawai'i | espnbet | 6.5 | 100.0 | -0.10222530168957544 | 6.6 | 0.1726 | 0.0644 | home |
| Troy @ South Alabama | 2026-11-14 19:00 EST | South Alabama | hardrockbet | -3.5 | 110.0 | -8.733481729565387 | 5.23 | 0.1536 | 0.0589 | home |
| App State @ Marshall | 2026-11-14 18:00 EST | Marshall | hardrockbet | 1.5 | -105.0 | -4.706972421288197 | 6.21 | 0.1534 | 0.0505 | home |
| Wisconsin @ Maryland | 2026-11-14 12:00 EST | Maryland | fanduel | 7.5 | 100.0 | 1.8210511274553567 | 5.68 | 0.1435 | 0.0463 | home |
| Kansas @ West Virginia | 2026-11-14 17:30 EST | West Virginia | betrivers | -3.0 | 100.0 | -8.149211425618583 | 5.15 | 0.1391 | 0.0395 | home |
| Ole Miss @ Oklahoma | 2026-11-14 16:15 EST | Oklahoma | betmgm | 1.0 | -112.0 | -5.513005081386684 | 6.51 | 0.1357 | 0.0347 | home |
| Kansas State @ TCU | 2026-11-14 14:00 EST | TCU | betrivers | -10.5 | -109.0 | -16.239239258690965 | 5.74 | 0.135 | 0.0304 | home |

## b. QB worklist

| game_id | kickoff ET | away | home | side | qb_status |
|--------:|------------|------|------|------|-----------|
| 401864537 | 2026-11-14 23:00 EST | North Dakota State | Hawai'i | Hawai'i | Hawai'i=starter, North Dakota State=starter |
| 401871100 | 2026-11-14 19:00 EST | Troy | South Alabama | South Alabama | South Alabama=backup, Troy=starter |
| 401869928 | 2026-11-14 18:00 EST | App State | Marshall | Marshall | Marshall=unknown, App State=starter |
| 401858520 | 2026-11-14 12:00 EST | Wisconsin | Maryland | Maryland | Maryland=unknown, Wisconsin=backup |
| 401856861 | 2026-11-14 17:30 EST | Kansas | West Virginia | West Virginia | West Virginia=starter, Kansas=starter |
| 401856887 | 2026-11-14 16:15 EST | Ole Miss | Oklahoma | Oklahoma | Oklahoma=starter, Ole Miss=starter |
| 401856862 | 2026-11-14 14:00 EST | Kansas State | TCU | TCU | TCU=starter, Kansas State=unknown |
| 401856863 | 2026-11-14 21:30 EST | Texas Tech | Oklahoma State | Oklahoma State | Oklahoma State=starter, Texas Tech=backup |
| 401862829 | 2026-11-14 13:00 EST | UAB | Temple | Temple | Temple=unknown, UAB=starter |

## c. Radar (next 3 after max)

| game | kickoff ET | side | book | line | price | edge | HFA-adj |
|------|------------|------|------|-----:|------:|-----:|--------:|
| Texas Tech @ Oklahoma State | 2026-11-14 21:30 EST | Oklahoma State | draftkings | -3.5 | -105.0 | 0.1335 | 0.0384 |
| Florida Atlantic @ Tulsa | 2026-11-14 15:30 EST | Tulsa | betrivers | -5.5 | -110.0 | 0.1327 | 0.049 |
| Baylor @ BYU | 2026-11-14 15:30 EST | Baylor | hardrockbet | 7.5 | -110.0 | 0.13 | 0.2234 |

## set-qb commands (placeholders)

### 401864537 — North Dakota State @ Hawai'i
`uv run ncaa-quant roster set-qb --game 401864537 --team "North Dakota State" --status ___`
`uv run ncaa-quant roster set-qb --game 401864537 --team "Hawai'i" --status ___`

### 401871100 — Troy @ South Alabama
`uv run ncaa-quant roster set-qb --game 401871100 --team "Troy" --status ___`
`uv run ncaa-quant roster set-qb --game 401871100 --team "South Alabama" --status ___`

### 401869928 — App State @ Marshall
`uv run ncaa-quant roster set-qb --game 401869928 --team "App State" --status ___`
`uv run ncaa-quant roster set-qb --game 401869928 --team "Marshall" --status ___`

### 401858520 — Wisconsin @ Maryland
`uv run ncaa-quant roster set-qb --game 401858520 --team "Wisconsin" --status ___`
`uv run ncaa-quant roster set-qb --game 401858520 --team "Maryland" --status ___`

### 401856861 — Kansas @ West Virginia
`uv run ncaa-quant roster set-qb --game 401856861 --team "Kansas" --status ___`
`uv run ncaa-quant roster set-qb --game 401856861 --team "West Virginia" --status ___`

### 401856887 — Ole Miss @ Oklahoma
`uv run ncaa-quant roster set-qb --game 401856887 --team "Ole Miss" --status ___`
`uv run ncaa-quant roster set-qb --game 401856887 --team "Oklahoma" --status ___`

### 401856862 — Kansas State @ TCU
`uv run ncaa-quant roster set-qb --game 401856862 --team "Kansas State" --status ___`
`uv run ncaa-quant roster set-qb --game 401856862 --team "TCU" --status ___`

### 401856863 — Texas Tech @ Oklahoma State
`uv run ncaa-quant roster set-qb --game 401856863 --team "Texas Tech" --status ___`
`uv run ncaa-quant roster set-qb --game 401856863 --team "Oklahoma State" --status ___`

### 401862829 — UAB @ Temple
`uv run ncaa-quant roster set-qb --game 401862829 --team "UAB" --status ___`
`uv run ncaa-quant roster set-qb --game 401862829 --team "Temple" --status ___`

## QB research prompt

Research starting QB status for these week-8 games. Use only sources dated **October 18, 2026 or later** (injury reports, depth charts, beat writers, official team accounts). For each team return: starter name, status (`starter` / `backup` / `unknown`), and the source URL + date.

Games:

- North Dakota State @ Hawai'i
- Troy @ South Alabama
- App State @ Marshall
- Wisconsin @ Maryland
- Kansas @ West Virginia
- Ole Miss @ Oklahoma
- Kansas State @ TCU
- Texas Tech @ Oklahoma State
- UAB @ Temple
