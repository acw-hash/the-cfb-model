# W1-QB — QB worklist refresh

**Task:** W1-QB  
**Branch:** `social-s1-s2`  
**`as_of`:** `2026-09-01T22:30:00+00:00`  
**Odds API / publish / R2 / merge:** OFF  
**2025 lockbox / threshold writes / Best Bets:** untouched  

---

## 1 — Step-4 survivors (current staged state)

Gate step counts: `{"step1_snapshot_stale_kickoff_quarantine": 90, "step2_edge_ev_sigma": 84, "step3_model_market_disagree": 28, "step4_exposure_caps": 8, "step5_qb_status_unknown": 2, "start": 91}`

| rank (step-3 pool) | game_id | matchup | edge |
|-------------------:|--------:|---------|-----:|
| 1 | 401869129 | Northwestern State @ Louisiana Tech | 0.1483 |
| 2 | 401856636 | Baylor @ Auburn | 0.1472 |
| 3 | 401860879 | Portland State @ San Diego State | 0.1154 |
| 4 | 401858209 | Tulane @ Duke | 0.1048 |
| 5 | 401858434 | Marshall @ Penn State | 0.0992 |
| 6 | 401866623 | North Carolina A&T @ Georgia State | 0.0978 |
| 25 | 401858422 | Eastern Illinois @ Minnesota | 0.041 |
| 28 | 401864498 | Central Michigan @ New Mexico | 0.0264 |

---

## 2 — QB coverage per step-4 survivor

### Northwestern State @ Louisiana Tech (`401869129`)

**Gate:** `qb_status_known=False` (`unchecked`) — fails step 5

| team | team_id | status | source | event_time | resolves |
|------|--------:|--------|--------|------------|:--------:|
| Louisiana Tech | 2348 | — | — | — | no |
| Northwestern State | 2466 | — | — | — | no |

### Baylor @ Auburn (`401856636`)

**Gate:** `qb_status_known=True` (`staged_asof`) — passes step 5

| team | team_id | status | source | event_time | resolves |
|------|--------:|--------|--------|------------|:--------:|
| Auburn | 2 | starter | manual_v1 | 2026-09-01T22:15:27.497134+00:00 | yes |
| Baylor | 239 | starter | manual_v1 | 2026-09-01T22:15:22.454398+00:00 | yes |

### Portland State @ San Diego State (`401860879`)

**Gate:** `qb_status_known=False` (`unchecked`) — fails step 5

| team | team_id | status | source | event_time | resolves |
|------|--------:|--------|--------|------------|:--------:|
| San Diego State | 21 | — | — | — | no |
| Portland State | 2502 | — | — | — | no |

### Tulane @ Duke (`401858209`)

**Gate:** `qb_status_known=True` (`staged_asof`) — passes step 5

| team | team_id | status | source | event_time | resolves |
|------|--------:|--------|--------|------------|:--------:|
| Duke | 150 | starter | manual_v1 | 2026-09-01T22:15:36.968358+00:00 | yes |
| Tulane | 2655 | starter | manual_v1 | 2026-09-01T22:15:32.064135+00:00 | yes |

### Marshall @ Penn State (`401858434`)

**Gate:** `qb_status_known=False` (`unchecked`) — fails step 5

| team | team_id | status | source | event_time | resolves |
|------|--------:|--------|--------|------------|:--------:|
| Penn State | 213 | unknown | manual_v1 | 2026-09-01T22:24:09.289476+00:00 | no |
| Marshall | 276 | starter | manual_v1 | 2026-09-01T22:15:42.189092+00:00 | yes |

### North Carolina A&T @ Georgia State (`401866623`)

**Gate:** `qb_status_known=False` (`unchecked`) — fails step 5

| team | team_id | status | source | event_time | resolves |
|------|--------:|--------|--------|------------|:--------:|
| Georgia State | 2247 | unknown | manual_v1 | 2026-09-01T22:24:19.204211+00:00 | no |
| North Carolina A&T | 2448 | unknown | manual_v1 | 2026-09-01T22:24:13.972019+00:00 | no |

### Eastern Illinois @ Minnesota (`401858422`)

**Gate:** `qb_status_known=False` (`unchecked`) — fails step 5

| team | team_id | status | source | event_time | resolves |
|------|--------:|--------|--------|------------|:--------:|
| Minnesota | 135 | unknown | manual_v1 | 2026-09-01T22:24:43.691921+00:00 | no |
| Eastern Illinois | 2197 | unknown | manual_v1 | 2026-09-01T22:24:39.025013+00:00 | no |

### Central Michigan @ New Mexico (`401864498`)

**Gate:** `qb_status_known=False` (`unchecked`) — fails step 5

| team | team_id | status | source | event_time | resolves |
|------|--------:|--------|--------|------------|:--------:|
| New Mexico | 167 | unknown | manual_v1 | 2026-09-01T22:24:58.144394+00:00 | no |
| Central Michigan | 2117 | unknown | manual_v1 | 2026-09-01T22:24:48.289809+00:00 | no |

---

## 3 — ORPHAN rows (`manual_v1`, game ∉ current step-4 set)

Do **not** delete — report only.

### S6-displaced (written for old step-4 set — Duquesne @ Air Force, Fordham @ NDSU)

| game_id | team_id | status | source | event_time |
|--------:|--------:|--------|--------|------------|
| 401864496 | 2005 | unknown | manual_v1 | 2026-09-01T22:24:29.043706+00:00 |
| 401864496 | 2184 | unknown | manual_v1 | 2026-09-01T22:24:24.020171+00:00 |
| 401864499 | 2230 | unknown | manual_v1 | 2026-09-01T22:24:34.039367+00:00 |
| 401864499 | 2449 | starter | manual_v1 | 2026-09-01T22:15:56.826099+00:00 |

### Other (`manual_v1` on games outside current W1 step-4 — week-0 historical)

| game_id | team_id | status | source | event_time |
|--------:|--------:|--------|--------|------------|
| 401858201 | 24 | starter | manual_v1 | 2026-08-25T23:13:33.548340+00:00 |
| 401858201 | 62 | starter | manual_v1 | 2026-08-25T23:13:25.581134+00:00 |
| 401858202 | 152 | starter | manual_v1 | 2026-08-25T23:06:37.865405+00:00 |
| 401858202 | 258 | starter | manual_v1 | 2026-08-25T22:58:49.130903+00:00 |
| 401862693 | 235 | starter | manual_v1 | 2026-08-25T23:07:31.490898+00:00 |
| 401862693 | 2439 | unknown | manual_v1 | 2026-08-25T23:11:05.418853+00:00 |
| 401864577 | 55 | unknown | manual_v1 | 2026-08-25T23:11:26.402097+00:00 |
| 401864577 | 2449 | starter | manual_v1 | 2026-08-25T23:11:35.668266+00:00 |
| 401866408 | 16 | unknown | manual_v1 | 2026-08-25T23:11:12.268814+00:00 |
| 401866408 | 2199 | starter | manual_v1 | 2026-08-25T23:11:19.676273+00:00 |


---

## 4 — Operator lookup worklist

Teams in the current step-4 set with **no row** or **`status=unknown`** (does not resolve). Operator writes these by hand — do not infer.

| game_id | matchup | team | team_id | reason | current status | current source | current event_time |
|--------:|---------|------|--------:|--------|----------------|----------------|-------------------|
| 401869129 | Northwestern State @ Louisiana Tech | Louisiana Tech | 2348 | no row | — | — | — |
| 401869129 | Northwestern State @ Louisiana Tech | Northwestern State | 2466 | no row | — | — | — |
| 401860879 | Portland State @ San Diego State | San Diego State | 21 | no row | — | — | — |
| 401860879 | Portland State @ San Diego State | Portland State | 2502 | no row | — | — | — |
| 401858434 | Marshall @ Penn State | Penn State | 213 | status=unknown | unknown | manual_v1 | 2026-09-01T22:24:09.289476+00:00 |
| 401866623 | North Carolina A&T @ Georgia State | Georgia State | 2247 | status=unknown | unknown | manual_v1 | 2026-09-01T22:24:19.204211+00:00 |
| 401866623 | North Carolina A&T @ Georgia State | North Carolina A&T | 2448 | status=unknown | unknown | manual_v1 | 2026-09-01T22:24:13.972019+00:00 |
| 401858422 | Eastern Illinois @ Minnesota | Minnesota | 135 | status=unknown | unknown | manual_v1 | 2026-09-01T22:24:43.691921+00:00 |
| 401858422 | Eastern Illinois @ Minnesota | Eastern Illinois | 2197 | status=unknown | unknown | manual_v1 | 2026-09-01T22:24:39.025013+00:00 |
| 401864498 | Central Michigan @ New Mexico | New Mexico | 167 | status=unknown | unknown | manual_v1 | 2026-09-01T22:24:58.144394+00:00 |
| 401864498 | Central Michigan @ New Mexico | Central Michigan | 2117 | status=unknown | unknown | manual_v1 | 2026-09-01T22:24:48.289809+00:00 |

---

## STOP AND REPORT

**Future-stamped rows:** none — all `qb_status.event_time ≤ as_of`.

**Step-5 survivor set:** unchanged — `(Baylor @ Auburn, Tulane @ Duke)`.

### Step-5 detail (current `manual_v1` rows, no operator additions)

- **Baylor @ Auburn** (`401856636`, edge 0.1472)
  - Auburn: `starter` (manual_v1, 2026-09-01T22:15:27.497134+00:00)
  - Baylor: `starter` (manual_v1, 2026-09-01T22:15:22.454398+00:00)
- **Tulane @ Duke** (`401858209`, edge 0.1048)
  - Duke: `starter` (manual_v1, 2026-09-01T22:15:36.968358+00:00)
  - Tulane: `starter` (manual_v1, 2026-09-01T22:15:32.064135+00:00)

---

## 6 — Post-operator re-run (pending)

After the operator writes `qb_status` rows for §4 worklist teams, re-run:

```bash
uv run python scripts/_w1_qb_refresh.py
```

Then append the new §1–§5 output (step-5 survivor set with full per-game QB detail).

