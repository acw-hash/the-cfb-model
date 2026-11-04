# W8-2 card probe

- analysis_as_of: `2026-10-28T14:44:13.440248+00:00`
- publish_as_of: `2026-10-28T14:04:32.174965+00:00`
- source: `data/webapp/publish_history/2026_w9.jsonl (record 1, as_of=2026-10-28T14:04:32.174965+00:00)`
- decisions: HFA=NONE, stake=0.014, max=7, weekly_cap=0.1
- books: `draftkings+fanduel+betmgm+williamhill_us+espnbet+fanatics+hardrockbet+ballybet+betrivers`

## FLAG — Tue/Wed kickoffs on card or worklist

(none)

## a. Card (up to max)

| game | kickoff ET | side | book | line | price | Ridge | gap | edge | HFA-adj edge (info) | home/away/neutral |
|------|------------|------|------|-----:|------:|------:|----:|-----:|--------------------:|-------------------|
| Auburn @ Ole Miss | 2026-10-31 12:45 EDT | Auburn | draftkings | 13.5 | -112.0 | 6.59072803464252 | 6.91 | 0.1546 | 0.2489 | away |
| Notre Dame @ Navy | 2026-10-31 12:00 EDT | Navy | espnbet | 30.5 | -105.0 | 24.37922580217108 | 6.12 | 0.1544 | 0.0473 | neutral |
| Minnesota @ Indiana | 2026-10-31 15:30 EDT | Minnesota | draftkings | 20.5 | -108.0 | 13.631493858498832 | 6.87 | 0.1537 | 0.2427 | away |
| Clemson @ Florida State | 2026-10-31 19:00 EDT | Florida State | fanduel | -6.5 | -102.0 | -12.720577666227989 | 6.22 | 0.1495 | 0.0591 | home |
| South Carolina @ Oklahoma | 2026-10-31 16:15 EDT | South Carolina | espnbet | 5.5 | -110.0 | -1.4233892869624412 | 6.92 | 0.1433 | 0.2264 | away |
| Tulane @ Charlotte | 2026-10-30 19:00 EDT | Charlotte | espnbet | 10.5 | -110.0 | 4.387466549010237 | 6.11 | 0.1431 | 0.0365 | home |
| Coastal Carolina @ Georgia State | 2026-10-31 14:00 EDT | Georgia State | ballybet | -6.5 | -109.0 | -13.320504746518418 | 6.82 | 0.1425 | 0.0465 | home |

## b. QB worklist

| game_id | kickoff ET | away | home | side | qb_status |
|--------:|------------|------|------|------|-----------|
| 401856735 | 2026-10-31 12:45 EDT | Auburn | Ole Miss | Auburn | Ole Miss=starter, Auburn=starter |
| 401859185 | 2026-10-31 12:00 EDT | Notre Dame | Navy | Navy | Navy=starter, Notre Dame=starter |
| 401858501 | 2026-10-31 15:30 EDT | Minnesota | Indiana | Minnesota | Indiana=starter, Minnesota=starter |
| 401858280 | 2026-10-31 19:00 EDT | Clemson | Florida State | Florida State | Florida State=starter, Clemson=starter |
| 401856736 | 2026-10-31 16:15 EDT | South Carolina | Oklahoma | South Carolina | Oklahoma=starter, South Carolina=starter |
| 401862813 | 2026-10-30 19:00 EDT | Tulane | Charlotte | Charlotte | Charlotte=backup, Tulane=backup |
| 401869944 | 2026-10-31 14:00 EDT | Coastal Carolina | Georgia State | Georgia State | Georgia State=unknown, Coastal Carolina=starter |
| 401856737 | 2026-10-31 19:30 EDT | Mississippi State | Texas | Mississippi State | Texas=starter, Mississippi State=unknown |

## c. Radar (next 3 after max)

| game | kickoff ET | side | book | line | price | edge | HFA-adj |
|------|------------|------|------|-----:|------:|-----:|--------:|
| Army @ Memphis | 2026-10-31 20:00 EDT | Army | draftkings | 6.0 | -110.0 | 0.1411 | 0.2424 |
| Mississippi State @ Texas | 2026-10-31 19:30 EDT | Mississippi State | ballybet | 20.5 | -108.0 | 0.1396 | 0.2157 |
| Baylor @ UCF | 2026-10-30 19:30 EDT | UCF | fanduel | -1.5 | -104.0 | 0.1357 | 0.0374 |

## set-qb commands (placeholders)

### 401856735 — Auburn @ Ole Miss
`uv run ncaa-quant roster set-qb --game 401856735 --team "Auburn" --status ___`
`uv run ncaa-quant roster set-qb --game 401856735 --team "Ole Miss" --status ___`

### 401859185 — Notre Dame @ Navy
`uv run ncaa-quant roster set-qb --game 401859185 --team "Notre Dame" --status ___`
`uv run ncaa-quant roster set-qb --game 401859185 --team "Navy" --status ___`

### 401858501 — Minnesota @ Indiana
`uv run ncaa-quant roster set-qb --game 401858501 --team "Minnesota" --status ___`
`uv run ncaa-quant roster set-qb --game 401858501 --team "Indiana" --status ___`

### 401858280 — Clemson @ Florida State
`uv run ncaa-quant roster set-qb --game 401858280 --team "Clemson" --status ___`
`uv run ncaa-quant roster set-qb --game 401858280 --team "Florida State" --status ___`

### 401856736 — South Carolina @ Oklahoma
`uv run ncaa-quant roster set-qb --game 401856736 --team "South Carolina" --status ___`
`uv run ncaa-quant roster set-qb --game 401856736 --team "Oklahoma" --status ___`

### 401862813 — Tulane @ Charlotte
`uv run ncaa-quant roster set-qb --game 401862813 --team "Tulane" --status ___`
`uv run ncaa-quant roster set-qb --game 401862813 --team "Charlotte" --status ___`

### 401869944 — Coastal Carolina @ Georgia State
`uv run ncaa-quant roster set-qb --game 401869944 --team "Coastal Carolina" --status ___`
`uv run ncaa-quant roster set-qb --game 401869944 --team "Georgia State" --status ___`

### 401856737 — Mississippi State @ Texas
`uv run ncaa-quant roster set-qb --game 401856737 --team "Mississippi State" --status ___`
`uv run ncaa-quant roster set-qb --game 401856737 --team "Texas" --status ___`

## QB research prompt

Research starting QB status for these week-8 games. Use only sources dated **October 18, 2026 or later** (injury reports, depth charts, beat writers, official team accounts). For each team return: starter name, status (`starter` / `backup` / `unknown`), and the source URL + date.

Games:

- Auburn @ Ole Miss
- Notre Dame @ Navy
- Minnesota @ Indiana
- Clemson @ Florida State
- South Carolina @ Oklahoma
- Tulane @ Charlotte
- Coastal Carolina @ Georgia State
- Mississippi State @ Texas
