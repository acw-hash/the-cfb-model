# W9-0 — live-season recon (read-only)

**Date:** 2026-08-17  
**Status:** Complete (observations only; no code, no config, no R2 write, no flow run)  
**Authority:** `docs/webapp/DESIGN.md` §3.2; `docs/notes/webapp-w7.md` (W7-ENV-CHECK,
W7-ENVFILE-FIX, W7-BUCKET-AUDIT, W7-TESTPUBLISH-GUARD); `docs/notes/webapp-w8c.md`.

---

## Headline

**No.** A `predict_publish` scheduled for the next Tue 06:00 UTC (**2026-08-18 06:00 UTC
= 2026-08-18 02:00 EDT**) would **not** publish real 2026 week-1 data to `latest/`
without human intervention.

**Broken links (any one is sufficient):**

1. **The `predict_publish` deployments do not exist** in the local Prefect sqlite.
   `prefect deployment ls` shows only leftover `ingest_odds/ingest_odds`. Inspect of
   `predict_publish/predict_publish_tuesday` and `predict_publish/predict_publish_refresh`
   returns **not found**. STOP AND REPORT item 3.
2. **No worker / serve process is running**, and there is **no Windows Service, no
   Task Scheduler entry, and no Makefile/`.ps1` launch** that would start one after
   logout. The only leftover deployment (`ingest_odds`) has `last_polled`
   **2026-08-12T19:02:34Z** — five days stale.
3. **Prefect is self-hosted ephemeral, not Cloud.** There is no dedicated `prefect
   server` daemon on port 4200. CLI reads spin a temporary server and stop it.
4. **2026 week 1 is absent** from `data/staged/games/` (and there is no
   `data/staged/teams/season=2026/`). STOP AND REPORT item 4. No CFBD ids to paste.
5. **Even if (1)–(4) were fixed today,** `predict_publish_flow` calls
   `_default_predict` → `[]` (CLI `predict` is `_not_wired`), and
   `notifications.provider` is `"null"`, so a miss would be silent.

---

## STOP AND REPORT

| # | Condition | Result |
|---|-----------|--------|
| 1 | Read requires a credential we do not have, or would create a resource | **Did not trip** for R2 (existing `.env` credentials, list/get only). Prefect CLI on the `ephemeral` profile **does** start a temporary local server per command, then stops it. No `prefect server start`, no worker start, port 4200 still empty. Reported under §1. |
| 2 | Secret value about to be pasted | **None pasted.** Names and PRESENT/ABSENT only. |
| 3 | Publish deployment does not exist | **TRIPS.** Do not design it. Sizing is in **W9-2 sizing**. |
| 4 | 2026 week 1 absent, or ids fail `^[0-9]{6,12}$` | **TRIPS (absent).** No ids to shape-check. |
| 5 | Unattributable R2 key | **Did not trip.** 39 keys, same prefixes as W8-D. `sandbox/` objects were **rewritten** today (2026-08-17T15:23Z) by test/helper pushes, not a new prefix. |
| 6 | Tier-change instrumentation does not write on the live path | **Did not trip.** `export_publish_artifacts` always passes `record_tier_changes=True`. |

Forbidden actions **not taken:** no deployment create/apply/pause/resume; no
`prefect deployment run`; no `predict_publish` / fixture / chaos helpers; no R2
put/copy/delete; no worker or dedicated server start; no `.env` or code edits;
no power/Task Scheduler changes; no `make test` (the suite with
`EXPORT_ENABLED=true` writes `sandbox/` on real R2 — see today's 15:23Z rewrite).

---

## 1. Is `predict_publish` actually deployed and scheduled?

### Deployment definition (code; not applied)

There is **no** `prefect.yaml` and **no** `Deployment.build_from_flow` usage.
The intended definition is sequential `flow.serve()` in `src/ncaa_quant/pipelines/serve.py`:

```17:43:src/ncaa_quant/pipelines/serve.py
def serve_all() -> None:
    """Register all §10 deployments with their cron schedules."""
    cfg = load_config()
    pipe = cfg.pipeline
    configure_logging()
    log.info("serving_all_deployments")

    ingest_odds_flow.serve(name="ingest_odds", cron=pipe.odds_ingest_cron)
    postgame_ingest_flow.serve(name="postgame_ingest_sat", cron=pipe.postgame_ingest_cron_sat)
    postgame_ingest_flow.serve(name="postgame_ingest_hourly", cron=pipe.postgame_ingest_cron_hourly)
    weekly_update_flow.serve(name="weekly_update", cron=pipe.weekly_update_cron)
    retrain_gate_flow.serve(name="retrain_gate", cron=pipe.weekly_update_cron)
    predict_publish_flow.serve(
        name="predict_publish_tuesday",
        cron=pipe.predict_publish_cron_tuesday,
        parameters={"refresh_kind": RefreshKind.TUESDAY_PRIMARY},
    )
    predict_publish_flow.serve(
        name="predict_publish_refresh",
        cron=pipe.predict_publish_cron_refresh,
        parameters={"refresh_kind": RefreshKind.DAILY_REFRESH},
    )
    settle_clv_flow.serve(name="settle_clv", cron=pipe.settle_clv_cron)


if __name__ == "__main__":
    serve_all()
```

Documented entry point: `python -m ncaa_quant.pipelines.serve` (`docs/notes/24.md`).
Makefile has **no** prefect/serve target. `odds.py` has a separate blocking
`serve_ingest_odds()`.

YAML cron *defaults* (not the schedule Prefect holds for publish, because that
deployment does not exist):

```
load_config.pipeline.predict_publish_cron_tuesday 0 6 * * 2
load_config.pipeline.predict_publish_cron_refresh 0 6 * * 4-6
```

from `configs/pipeline.yaml`. Intended shape in *code*: **two deployments**, not
one deployment with several schedules. **Neither is present** in Prefect.

Observation (not a design): `serve_all()` calls `.serve()` sequentially. Prefect 3
`Flow.serve()` is a blocking runner. That is consistent with sqlite containing
**only** `ingest_odds/ingest_odds` (the first `.serve()` in `serve_all()`, or a
standalone `python -m ncaa_quant.pipelines.odds`). Do not treat this as a proposed
fix.

### `prefect deployment ls`

```
Version:              3.8.1
API version:          0.8.4
Python version:       3.11.15
Profile:              ephemeral
Server type:          ephemeral
```

```
=== prefect deployment ls ===
11:44:11.198 | INFO    | prefect - Starting temporary server on http://127.0.0.1:8955
See https://docs.prefect.io/v3/concepts/server#how-to-guides for more information on running a dedicated Prefect
server.
                                  Deployments
+------------------------------------------------------------------------------+
| Name                          | ID                           | Work Pool     |
|-------------------------------+------------------------------+---------------|
| ingest_odds/ingest_odds       | 1b3bf08b-789f-43ce-8d48-c94… |               |
+------------------------------------------------------------------------------+
11:44:19.690 | INFO    | prefect - Stopping temporary server on http://127.0.0.1:8955
```

Work pool column empty.

### `prefect deployment inspect` — publish deployments (absent)

```
=== prefect deployment inspect predict_publish ===
Invalid deployment name. Expected '<flow-name>/<deployment-name>'
```

```
=== inspect predict_publish/predict_publish_tuesday -o json ===
11:50:12.331 | INFO    | prefect - Starting temporary server on http://127.0.0.1:8704
Deployment 'predict_publish/predict_publish_tuesday' not found!
11:50:18.415 | INFO    | prefect - Stopping temporary server on http://127.0.0.1:8704
```

```
=== inspect predict_publish/predict_publish_refresh -o json ===
11:50:24.146 | INFO    | prefect - Starting temporary server on http://127.0.0.1:8828
Deployment 'predict_publish/predict_publish_refresh' not found!
11:50:30.190 | INFO    | prefect - Stopping temporary server on http://127.0.0.1:8828
```

**Schedule as Prefect holds it for publish:** does not exist. No cron, no timezone,
not active, not paused.

### Leftover deployment (not publish) — `ingest_odds/ingest_odds`

JSON inspect (`-o json`; Pretty inspect crashed on cp1252/`→` in the docstring):

```
{
  "id": "1b3bf08b-789f-43ce-8d48-c94939937f0f",
  "created": "2026-08-04T15:52:22.929829Z",
  "updated": "2026-08-07T00:09:57.263000Z",
  "name": "ingest_odds",
  "paused": false,
  "schedules": [
    {
      "id": "c5f41275-e1ea-4b8c-9873-6423fdfa47ad",
      "created": "2026-08-07T00:09:57.274651Z",
      "schedule": {
        "cron": "0 0,4,8,12,16,20 * * *",
        "timezone": null,
        "day_or": true
      },
      "active": true,
      "parameters": {}
    }
  ],
  "work_queue_name": null,
  "last_polled": "2026-08-12T19:02:34.840066Z",
  "entrypoint": "src\\ncaa_quant\\pipelines\\odds.py:ingest_odds_flow",
  "work_pool_name": null,
  "status": "READY"
}
```

- Cron `0 0,4,8,12,16,20 * * *`, timezone **null** (not set; CLI/docs treat this as UTC).
- Schedule **active**, deployment **not paused**.
- **No work pool.** This is a `.serve()` in-process runner leftover.
- `last_polled` **2026-08-12T19:02:34Z** — runner gone for ~5 days.
- `status: READY` is leftover metadata, not evidence of a live worker.

### `prefect work-pool ls` / `prefect work-queue ls`

```
=== prefect work-pool ls ===
11:44:25.463 | INFO    | prefect - Starting temporary server on http://127.0.0.1:8812
No work pools found.
11:44:31.648 | INFO    | prefect - Stopping temporary server on http://127.0.0.1:8812

=== prefect work-queue ls ===
11:44:37.630 | INFO    | prefect - Starting temporary server on http://127.0.0.1:8961
              Work Queues
+--------------------------------------+
| Name | Pool | ID | Concurrency Limit |
|------+------+----+-------------------|
+--------------------------------------+
      (**) denotes a paused queue
11:44:43.857 | INFO    | prefect - Stopping temporary server on http://127.0.0.1:8961
```

No pool, so no pool has ever had a healthy worker. `ingest_odds` does not target a
pool. Publish has no target because it does not exist.

### Prefect Cloud or self-hosted?

**Self-hosted, ephemeral local sqlite — not Prefect Cloud.**

```
=== prefect config view ===
PREFECT_PROFILE='ephemeral'
PREFECT_SERVER_EPHEMERAL_ENABLED='true' (from profile)
```

No `PREFECT_API_URL` / `PREFECT_API_KEY` in the view (nothing to redact).

```
=== prefect profile ls ===
+---------------------+
| Available Profiles: |
|---------------------+
+---------------------+
   * active profile
```

```
PREFECT_HOME_DEFAULT=C:\Users\alecw\.prefect exists=True
Name            Length  LastWriteTime
.sdk_telemetry          7/15/2026 6:10:42 AM
storage                 8/10/2026 9:03:33 AM
memo_store.toml 94      7/15/2026 6:04:37 AM
prefect.db      4833280 8/12/2026 2:57:39 PM
prefect.db-shm  32768   8/17/2026 11:47:31 AM
prefect.db-wal  4968752 8/17/2026 11:44:19 AM
```

`prefect.db` last content write **2026-08-12 14:57 local** (matches `last_polled`).
WAL/shm updates **today** are from this recon's ephemeral CLI, not a daemon.

How a server would be started (docs / deferred compose; **not running now**):

- Native: `prefect server start` (runbooks: bind `127.0.0.1` only).
- Deferred docker-compose service `prefect` (`prefect server start --host 0.0.0.0`).
  **Not invoked.**

```
=== TCP 4200 listeners ===
(empty)
```

Lifetime problem: the sqlite file outlives the runner. A leftover **active**
schedule with no process is indistinguishable from "scheduled" until you look at
`last_polled` and the process table.

---

## 2. How does the worker actually run, and does it have the environment?

### Launch mechanism

**Does not exist** as an unattended launch.

| Candidate | Result |
|-----------|--------|
| Makefile target | **Absent.** `Makefile` has `install lint typecheck test format ingest features ratings train predict backtest clean` — no `serve` / `prefect`. |
| `scripts/*.ps1` / `.bat` | Only `scripts/_ats_v2_rerun.ps1` (ATS regrade). No prefect/worker script. |
| Windows Service | **0** matches for name `prefect`. |
| Task Scheduler | **0** matches for `prefect\|ridge\|ncaa`. |
| Container | `docker-compose.yml` defines a prefect service; Phase 1 native; **not executed**. |
| Documented interactive | `python -m ncaa_quant.pipelines.serve` or `serve_all()` (`docs/notes/24.md`). Dies with the shell. |

### `Get-ScheduledTask | Where-Object {$_.TaskName -match 'prefect|ridge|ncaa'} | Format-List`

```
=== scheduled tasks prefect|ridge|ncaa ===
(empty Format-List)

=== scheduled task filter count ===
matches=0
```

`Get-ScheduledTaskInfo` **not run** — zero hits. Full task-name dump was taken to
confirm the query worked (Adobe, Office, Defender, OneDrive, `OpenClaw Gateway`,
etc.). None named prefect/ridge/ncaa.

### `Get-CimInstance Win32_Service | Where-Object {$_.Name -match 'prefect'}`

```
=== win32 services prefect ===
(empty)

=== Get-CimInstance Win32_Service prefect count ===
0
```

### Worker process right now?

**No durable worker.** Process scan during recon saw only the recon's own
`prefect deployment ls` / `config view` / ephemeral uvicorn children. After those
commands exited:

- No `ncaa_quant.pipelines.serve`
- No `prefect worker`
- No `prefect server start`

Parent would have been an interactive shell **if** `serve_all` were launched by
hand. Nothing survives logout today.

### Environment (names and presence only)

Query method:

1. `[Environment]::GetEnvironmentVariables('Machine'|'User'|'Process')` for named keys.
2. `Get-ChildItem Env:` filtered to `NCAA_QUANT|R2_|WEBAPP_|PREFECT`.
3. Non-empty keys in repo `.env` (values not printed).
4. `load_config()` / `load_secrets()` from repo cwd (W7-ENVFILE-FIX path).
5. Worker process environment: **N/A — no worker process.**

```
=== env names Machine/User/Process ===
Machine NCAA_QUANT_WEBAPP__EXPORT_ENABLED: ABSENT
Machine NCAA_QUANT_WEBAPP__R2_BUCKET: ABSENT
Machine NCAA_QUANT_WEBAPP__R2_ENDPOINT_URL: ABSENT
Machine NCAA_QUANT_WEBAPP__REVALIDATE_URL: ABSENT
Machine R2_ACCESS_KEY_ID: ABSENT
Machine R2_SECRET_ACCESS_KEY: ABSENT
Machine WEBAPP_REVALIDATE_SECRET: ABSENT
Machine PREFECT_API_URL: ABSENT
Machine PREFECT_API_KEY: ABSENT
Machine PREFECT_HOME: ABSENT
User NCAA_QUANT_WEBAPP__EXPORT_ENABLED: ABSENT
User NCAA_QUANT_WEBAPP__R2_BUCKET: ABSENT
User NCAA_QUANT_WEBAPP__R2_ENDPOINT_URL: ABSENT
User NCAA_QUANT_WEBAPP__REVALIDATE_URL: ABSENT
User R2_ACCESS_KEY_ID: ABSENT
User R2_SECRET_ACCESS_KEY: ABSENT
User WEBAPP_REVALIDATE_SECRET: ABSENT
User PREFECT_API_URL: ABSENT
User PREFECT_API_KEY: ABSENT
User PREFECT_HOME: ABSENT
Process NCAA_QUANT_WEBAPP__EXPORT_ENABLED: ABSENT
Process NCAA_QUANT_WEBAPP__R2_BUCKET: ABSENT
Process NCAA_QUANT_WEBAPP__R2_ENDPOINT_URL: ABSENT
Process R2_ACCESS_KEY_ID: ABSENT
Process R2_SECRET_ACCESS_KEY: ABSENT
Process WEBAPP_REVALIDATE_SECRET: ABSENT
Process PREFECT_API_URL: ABSENT
Process PREFECT_API_KEY: ABSENT
Process PREFECT_HOME: ABSENT

=== process env NCAA_QUANT / R2 / WEBAPP / PREFECT names only ===
(empty)
```

```
dotenv_path C:\Users\alecw\Projects\the-cfb-model\.env exists True
--- .env name presence (non-empty values) ---
NCAA_QUANT_WEBAPP__EXPORT_ENABLED: PRESENT
NCAA_QUANT_WEBAPP__R2_BUCKET: PRESENT
NCAA_QUANT_WEBAPP__R2_ENDPOINT_URL: PRESENT
NCAA_QUANT_WEBAPP__REVALIDATE_URL: PRESENT
R2_ACCESS_KEY_ID: PRESENT
R2_SECRET_ACCESS_KEY: PRESENT
WEBAPP_REVALIDATE_SECRET: PRESENT
```

`load_config()` / `load_secrets()` **from this repo cwd, no process exports:**

```
cwd_for_dotenv_assumed_repo_root
load_config.webapp.export_enabled True
load_config.webapp.r2_bucket PRESENT
load_config.webapp.r2_endpoint_url PRESENT
load_config.webapp.revalidate_url PRESENT
load_config.pipeline.notifications.provider 'null'
secrets.r2_access_key_id PRESENT
secrets.r2_secret_access_key PRESENT
secrets.webapp_revalidate_secret PRESENT
secrets.ntfy_auth_token PRESENT
secrets.telegram_bot_token PRESENT
data.end_season 2025
data.start_season 2014
```

**W7-ENVFILE-FIX coverage:** **yes, for a process whose cwd is the repository
root.** `AppConfig.model_config` has `env_file=".env"` /
`dotenv_filtering="only_existing"`. A worker started as a Service/Task with a
different cwd would **not** see `.env` and would not have Machine/User exports
either. Then `export_enabled` stays the class default **False**.

**There is no worker, so `EXPORT_ENABLED` does not reach a worker.** If a runner
were started from repo cwd, dotenv would currently resolve `export_enabled True`.
If a runner were started without that cwd and without process env, it would read
false, `predict_publish` would succeed, and **nothing would publish** — silent
(see §4). That is still the live failure mode for any non-repo-cwd service.

### Machine sleep and wake

Timezone:

```
tzutil /g
Eastern Standard Time
Id                         : Eastern Standard Time
DisplayName                : (UTC-05:00) Eastern Time (US & Canada)
SupportsDaylightSavingTime : True
```

August → **EDT (UTC−4)**. Tue 06:00 UTC = **02:00 local**.

```
=== powercfg /a ===
The following sleep states are available on this system:
    Standby (S3)
    Hibernate
    Fast Startup
The following sleep states are not available on this system:
    Standby (S1) (firmware)
    Standby (S2) (firmware)
    Standby (S0 Low Power Idle) (firmware)
    Hybrid Sleep (hypervisor does not support)
```

```
=== powercfg -waketimers ===
This command requires administrator privileges and must be executed from an elevated command prompt.
```

```
=== active scheme ===
Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced)
```

`powercfg -query SCHEME_CURRENT SUB_SLEEP` (excerpt):

| Setting | AC index | Meaning |
|---------|----------|---------|
| Sleep after (`STANDBYIDLE`) | `0x00000a8c` | **2700 s = 45 min** |
| Hibernate after (`HIBERNATEIDLE`) | `0x00000000` | Never |
| Allow hybrid sleep | `0x00000001` | On (but hybrid sleep unavailable per `/a`) |
| Allow wake timers (`RTCWAKE`) | `0x00000001` | Enable |

DC sleep after = `0x00000258` = 600 s = 10 min.

**Would the machine be asleep at 02:00 local?** If idle ≥ 45 minutes on AC, **yes
(S3)**. There is **no** prefect/ridge scheduled task to create a wake timer.
`powercfg -waketimers` could not be listed without admin. Scheme allows wake
timers, but nothing observed that would arm one for Tue 06:00 UTC.

---

## 3. Is 2026 schedule data ingested?

**No.** 2026 week 1 is **absent**. STOP AND REPORT item 4.

### Distinct `(season, week)` inventory

Store: `data/staged/games/season={Y}/week={W}/part.parquet` via
`load_schedule_frame`. `n_partitions=184`. **Zero 2026 partitions.**

```
season=2014 n_weeks=16 weeks=[1..16]
season=2015 n_weeks=15 weeks=[1..15]
season=2016 n_weeks=15 weeks=[1..15]
season=2017 n_weeks=15 weeks=[1..15]
season=2018 n_weeks=15 weeks=[1..15]
season=2019 n_weeks=15 weeks=[1..15]
season=2020 n_weeks=16 weeks=[1..16]
season=2021 n_weeks=15 weeks=[1..15]
season=2022 n_weeks=15 weeks=[1..15]
season=2023 n_weeks=15 weeks=[1..15]
season=2024 n_weeks=16 weeks=[1..16]
season=2025 n_weeks=16 weeks=[1..16]
--- 2026 partitions ---
(empty)
teams_2026 False
teams_2025 True
teams_2024 True
staged_games_2026_any False
ABSENT data/staged/games/season=2026/week=0/part.parquet
ABSENT data/staged/games/season=2026/week=1/part.parquet
```

Calendar helpers at recon time (`now_utc 2026-08-17T15:47:46Z`):

```
season_of 2026
week_of_2026 0
```

(CFBD week 1 as the operator's "~11 days out" is not the same as `week_of()` Labor
Day week 1. Flow default is still `week=1` regardless.)

### Flow / CLI that populates it, last run

- CLI: `ncaa-quant ingest cfbd` (`src/ncaa_quant/cli.py` — `run_cfbd_backfill` /
  `run_cfbd_incremental`). **Not executed this task.**
- Prefect: `postgame_ingest` deployments **do not exist** in sqlite.
- `run_cfbd_incremental` clamps `season_of(now)` into
  `[data.start_season, data.end_season]`. **`data.end_season` is 2025.** On
  2026-08-17 that clamp is **2025**, so incremental ingest cannot populate 2026
  until the YAML placeholder is bumped.

Last staged games write clock:

```
newest data/staged/games/season=2025/week=9/part.parquet 2026-08-07T17:13:20Z
oldest data/staged/games/season=2023/week=1/part.parquet 2026-08-07T00:33:30Z
2025w1_mtime 2026-08-07T17:13:20Z bytes 14756 rows 142
```

Raw CFBD dated dirs: `2026-08-04`, `2026-08-05`, `2026-08-07` (ingest calendar
dates, not CFB season 2026). `games_s2026*.json` count **0**. Season tokens on
`games_s*.json`: 2014–2025 only.

Idempotency ledger (`data/pipeline_state/idempotency.json`): **no
`predict_publish:` keys.** `ingest_odds:*` entries are pytest temp paths (latest
`2026-08-17T15:26:35Z`).

### 2026 week 1 games / ids

**Absent.** Cannot paste five ids. Shape `^[0-9]{6,12}$` not checked because there
is no slate.

### What else `predict_publish` needs for 2026

| Need | State |
|------|--------|
| Staged games 2026 w1 | **ABSENT** (blocks `load_schedule_frame`) |
| Staged teams 2026 | **ABSENT** (blocks `load_teams_frame`) |
| Production `predict_fn` | **ABSENT.** `predict_publish_task` does not pass `predict_fn`; `_default_predict` returns `[]`. CLI `predict` is `_not_wired`. |
| Odds ingest inside the flow | **Skipped** unless `odds_ingest_fn` is injected (it is not on the flow). |
| Filter-history team ratings | File **PRESENT** `data/artifacts/state_space/filter_history.parquet` (mtime 2026-08-10). Live `export_publish_artifacts` only uses it if `filter_history=` is passed; the flow does **not**. Empty `teams: {}` stub otherwise. |
| Expected possessions live | **PRESENT** `data/artifacts/expected_possessions/live.json` (mtime 2026-08-10). Unused by stub predict. |
| Conviction hysteresis `tier_state.json` | **PRESENT** (2024 fixture keys; mtime 2026-08-17 from tests). |
| `tier_changes.jsonl` | **PRESENT** (27 lines; fixture/helper ids, not 2026). |
| Calibrator artifacts on disk | **ABSENT** (`data/**/*calibr*` = 0, `**/*pit_recal*` = 0). Calibration lives inside a production stack the flow never constructs. |
| Champion registry | **ABSENT** `registry_index.json` (also not under `data/registry/` or `data/models/`). `mlruns/` directory exists. Export **hardcodes** `champion_version: 3` / `model_version: "production-v0_reduced_v1"` rather than `resolve_champion()`. |
| Season config | `data.end_season: 2025` — 2026 ingest clamp. |
| Flow week/season defaults | `season=now.year` (2026), `week=1` always. Next Tue 08-18 would *aim* at week 1 even though `week_of` is 0, then fail on missing parquet if export ran. |

---

## 4. What does alerting do when a publish never runs?

Notifier construction: `build_notifier()` → `NullNotifier` when
`pipeline.notifications.provider` is `null` / empty. YAML is `provider: "null"`.
`load_config()` confirms `'null'`. Tokens for ntfy/telegram are PRESENT in `.env`
but **unused**.

Delivery channel: **none.** `NullNotifier.send` logs `notification_suppressed` and
returns False. Even if provider were ntfy/telegram, send would run **inside the
workstation process**. A dead/asleep workstation **cannot** emit an alert about
itself. DESIGN §3.2 already accepts site-side stale banner as the public failure
mode; there is no off-box watchdog.

### `AlertKind` table

| Kind | Trigger | Where it fires | Absence vs failure |
|------|---------|----------------|--------------------|
| `FLOW_FAILURE` | Prefect `on_failure` hook after a flow run fails | `predict.py`, `odds.py`, `postgame.py`, `weekly.py`, `retrain.py`, `settle.py` | **Failure of a started run.** Silent if the run never starts. |
| `QUALITY_GATE_FAILURE` | `postgame_ingest_flow` when `quality_hard_failures > 0` | `postgame.py` | Failure of a completed ingest, not absence. |
| `RATING_INNOVATION` | `weekly_update_flow` for each `innovation_flags` item | `weekly.py` | Presence of a flag after a run. |
| `CADENCE_SHORTFALL` | `check_odds_cadence` snapshots_24h < expected−tolerance | `execute_predict_publish` | **Absence of snapshots**, but only if predict_publish **actually runs**. |
| `NEW_BET_CANDIDATE` | Each accepted `BetCandidate` | `execute_predict_publish` | Presence. |
| `CALIBRATION_ALARM` | **No call site.** Enum member only. | nowhere | n/a |
| `CLV_WEEKLY_SUMMARY` | End of `settle_clv_flow` | `settle.py` | After a run. |
| `WEBAPP_EXPORT_FAILURE` | Exception in export/push; **or** revalidation HTTP error | `predict.py` (`except` around export); `push.py` `_maybe_revalidate` | Failure of an *attempted* export. **Not** "export skipped". |

Nothing fires on **absence of the Tue 06:00 run.** Site staleness (`now −
published_at > 36h` and past next slot) is UI-only (`DESIGN.md` §3.2), and
`latest/meta.json` is still the 2024 fixture stamp (`2024-09-24T06:00:00Z`), so
the site is already in that long-horizon fixture posture rather than a live missed
Tuesday.

### `WEBAPP_EXPORT_FAILURE` vs export disabled

```204:220:src/ncaa_quant/pipelines/predict.py
    if cfg.webapp.export_enabled:
        try:
            from ncaa_quant.webapp.export import export_publish_artifacts
            export_out = export_publish_artifacts(result, config=cfg, push=True, notifier=n)
            result["webapp_export"] = {"ok": True, "push": export_out.get("push")}
        except Exception as exc:
            ...
            notify(AlertKind.WEBAPP_EXPORT_FAILURE, "Ridge artifact export/push failed", ...)
            result["webapp_export"] = {"ok": False, "error": str(exc)}
    return result
```

If `export_enabled` is false the `if` is skipped: **no alert, success return.**
A live run completing with export disabled is **currently silent.** Combined with
provider `"null"`, even a real export exception would only hit the structlog
suppress path.

---

## 5. R2 per-prefix accounting

Read-only `list_objects_v2` + `get_object`. `key_count=39`. Same count as W8-D.

### Full key inventory (2026-08-17 recon)

```
     925  2026-08-14T22:21:34.423000+00:00  latest/meta.json
   59717  2026-08-14T22:21:32.222000+00:00  latest/results_2024.json
  991908  2026-08-14T22:21:33.347000+00:00  latest/team_ratings_2024.json
    5624  2026-08-14T22:21:33.671000+00:00  latest/track_record.json
  107264  2026-08-14T22:21:34.035000+00:00  latest/week_predictions.json
     906  2026-08-17T15:23:20.778000+00:00  sandbox/latest/meta.json
     107  2026-08-17T15:23:19.601000+00:00  sandbox/latest/team_ratings_2024.json
    5605  2026-08-17T15:23:19.980000+00:00  sandbox/latest/track_record.json
    3625  2026-08-17T15:23:20.395000+00:00  sandbox/latest/week_predictions.json
     904  2026-08-17T15:23:15.040000+00:00  sandbox/v1/2024/w5/daily_refresh/meta.json
     107  2026-08-17T15:23:13.918000+00:00  sandbox/v1/2024/w5/daily_refresh/team_ratings_2024.json
    5605  2026-08-17T15:23:14.293000+00:00  sandbox/v1/2024/w5/daily_refresh/track_record.json
    2451  2026-08-17T15:23:14.679000+00:00  sandbox/v1/2024/w5/daily_refresh/week_predictions.json
     906  2026-08-17T15:23:16.838000+00:00  sandbox/v1/2024/w5/tuesday_primary/meta.json
     107  2026-08-17T15:23:15.606000+00:00  sandbox/v1/2024/w5/tuesday_primary/team_ratings_2024.json
    5605  2026-08-17T15:23:15.986000+00:00  sandbox/v1/2024/w5/tuesday_primary/track_record.json
    3625  2026-08-17T15:23:16.412000+00:00  sandbox/v1/2024/w5/tuesday_primary/week_predictions.json
     906  2026-08-17T15:23:20.574000+00:00  sandbox/v1/2024/w6/tuesday_primary/meta.json
     107  2026-08-17T15:23:19.405000+00:00  sandbox/v1/2024/w6/tuesday_primary/team_ratings_2024.json
    5605  2026-08-17T15:23:19.785000+00:00  sandbox/v1/2024/w6/tuesday_primary/track_record.json
    3625  2026-08-17T15:23:20.197000+00:00  sandbox/v1/2024/w6/tuesday_primary/week_predictions.json
     904  2026-08-14T14:28:55.848000+00:00  v1/2024/w5/daily_refresh/meta.json
     107  2026-08-14T14:28:54.690000+00:00  v1/2024/w5/daily_refresh/team_ratings_2024.json
    5605  2026-08-14T14:28:55.061000+00:00  v1/2024/w5/daily_refresh/track_record.json
    2569  2026-08-14T14:28:55.434000+00:00  v1/2024/w5/daily_refresh/week_predictions.json
     925  2026-08-14T22:21:34.235000+00:00  v1/2024/w5/tuesday_primary/meta.json
   59717  2026-08-14T22:21:32.043000+00:00  v1/2024/w5/tuesday_primary/results_2024.json
  991908  2026-08-14T22:21:32.781000+00:00  v1/2024/w5/tuesday_primary/team_ratings_2024.json
    5624  2026-08-14T22:21:33.513000+00:00  v1/2024/w5/tuesday_primary/track_record.json
  107264  2026-08-14T22:21:33.859000+00:00  v1/2024/w5/tuesday_primary/week_predictions.json
     906  2026-08-14T14:28:59.772000+00:00  v1/2024/w6/tuesday_primary/meta.json
     107  2026-08-14T14:28:58.684000+00:00  v1/2024/w6/tuesday_primary/team_ratings_2024.json
    5605  2026-08-14T14:28:59.045000+00:00  v1/2024/w6/tuesday_primary/track_record.json
    3861  2026-08-14T14:28:59.442000+00:00  v1/2024/w6/tuesday_primary/week_predictions.json
     906  2026-08-14T14:06:44.706000+00:00  v2/2024/w5/tuesday_primary/meta.json
   59698  2026-08-14T14:06:41.917000+00:00  v2/2024/w5/tuesday_primary/results_2024.json
  991889  2026-08-14T14:06:43.074000+00:00  v2/2024/w5/tuesday_primary/team_ratings_2024.json
    5605  2026-08-14T14:06:43.817000+00:00  v2/2024/w5/tuesday_primary/track_record.json
  107245  2026-08-14T14:06:44.243000+00:00  v2/2024/w5/tuesday_primary/week_predictions.json
```

### Prefix counts and origin

| Prefix | n | Origin |
|--------|---|--------|
| `latest/` | 5 | W7-BUCKET-AUDIT restore of committed 2024 w5 fixtures; later W8-A live-scope stamp was restored again (W8-D). Last-modified 2026-08-14T22:21Z. |
| `v1/` | 13 | Versioned copies: `w5/tuesday_primary` = restore; `w5/daily_refresh` = W7 chaos `g-chaos-1`; `w6/tuesday_primary` = W7 `g-fix-*` leak leftovers. |
| `v2/` | 5 | W7-CLOSE-2 doctored `schema_version=2.0.0`. |
| `sandbox/` | 16 | W7-TESTPUBLISH-GUARD helper routing. **Rewritten 2026-08-17T15:23Z** (pytest/helpers with export on); still 16 keys, still synthetic ids, now schema 1.2.0. |

No other top-level prefixes. No `v1/2026/`.

### Reconciliation of the ~16 new keys vs W7-BUCKET-AUDIT's 23

W7-BUCKET-AUDIT baseline **23** = `latest/`5 + `v1/`13 + `v2/`5.

W7-TESTPUBLISH-GUARD then added **`sandbox/` 16** → **39**.

| Task | Net new keys | Notes |
|------|--------------|-------|
| W8-A | 0 | Live-scope push then restore of `latest/` + `v1/2024/w5/tuesday_primary` (overwrite, not insert). |
| W8-D | 0 | Read-only inventory recorded 39. |
| W8-C | 0 | Explicitly no R2 write. Production `latest/` still 1.1.0 with withdrawn keys, as W8-C predicted. |
| Today (tests) | 0 net | Same 16 sandbox keys overwritten at 15:23Z. |

**No unexplained prefix growth.** Unexplained would have been a new prefix or a
count ≠ 39 with an unknown key; that did not happen.

### `latest/meta.json` current state

```
schema_version 1.1.0
fixture True
published_at 2024-09-24T06:00:00Z
next_expected_publish_utc 2024-09-26T06:00:00Z
season 2024
week 5
refresh_kind tuesday_primary
```

Game count from `latest/week_predictions.json`: **56** (`fixture: true`).

### Withdrawn keys vs `SUPPORTED_SCHEMA_MAJOR = 1`

`webapp/site/src/lib/artifacts/types.ts`: `export const SUPPORTED_SCHEMA_MAJOR = 1;`

| Object | schema | withdrawn four keys | fixture |
|--------|--------|---------------------|---------|
| `latest/week_predictions.json` | 1.1.0 | **present** | true |
| `v1/2024/w5/tuesday_primary/...` | 1.1.0 | **present** | true |
| `v1/2024/w5/daily_refresh/...` | 1.1.0 | **present** | null |
| `v1/2024/w6/tuesday_primary/...` | 1.1.0 | **present** | null |
| `v2/2024/w5/tuesday_primary/...` | **2.0.0** | present | null |
| all four `sandbox/**/week_predictions.json` | **1.2.0** | **absent** | null |

**All pre-1.2.0 objects still carry the W8-C withdrawn keys**, as expected.
**Inert for the live site:** the app reads `latest/` (major 1 → not maintenance).
Withdrawn fields are ignored by 1.x consumers. `v2/` would trip maintenance **only
if** it became `latest/`; it is not. Sandbox is not `latest/`.

---

## 6. Two confirmations from earlier tasks

### Tier-change instrumentation on the live path

**Writes on the live `predict_publish` path**, not test-only.

`execute_predict_publish` → `export_publish_artifacts(..., push=True)` when
`export_enabled`. That function always calls `build_week_predictions(...,
record_tier_changes=True, tier_changes_path=Path(cfg.webapp.tier_changes_path))`.
`build_week_predictions` then `append_tier_change_records` to
`data/webapp/tier_changes.jsonl`.

There is no `if test` / sandbox guard on that write. Helpers also write the same
file (they call `export_publish_artifacts` after suppressing the inner live
export). On-disk file exists (27 JSONL lines) from fixture/helper runs, not from
a 2026 live Tuesday.

W1A-FIX four-week flap measurement is **code-ready** and will stay dark until a
live export actually runs (currently blocked by §1 and §3).

### Export allowlist scope

`PublishedKeyAllowlistError` is raised from `assert_game_prediction_allowlist`
with **no skip flags**. It is called at the end of `build_game_prediction` on
every game object, which is the only builder used by `build_week_predictions` →
`export_publish_artifacts` (live push, sandbox helper with `push=False` then
explicit sandbox push, dry export).

`push_artifacts_to_r2(..., publish_scope="live")` additionally
`validate_live_publish_game_ids` (`^[0-9]{6,12}$`) **before** any upload; sandbox
skips **that** id guard only, **not** the allowlist.

No dry-run / sandbox / feature-flag bypass of `PublishedKeyAllowlistError`.

---

## W9-2 sizing

Do **not** design the missing deployment here. Numbered gaps only.

| # | Gap | Status | Sessions | Blocks first live `latest/` publish? |
|---|-----|--------|----------|--------------------------------------|
| 1 | `predict_publish` deployments do not exist in Prefect (Tue + Thu–Sat as two `.serve()` names in code; zero in sqlite). | **absent** | 1 | **Yes** |
| 2 | Durable runner: no Service, no Task Scheduler, no Makefile/`.ps1`, no container in use; leftover `ingest_odds` last_polled 2026-08-12. Sequential blocking `serve_all()`. | **absent** | 1–2 | **Yes** |
| 3 | Dedicated Prefect server (or an always-on `.serve()` process). Today: ephemeral CLI sqlite, nothing on :4200. Same lifetime problem as the worker. | **absent** | 1 (fold with #2 if `.serve()`-only) | **Yes** (unattended) |
| 4 | Workstation sleep: S3 after 45 min AC; Tue 06:00 UTC = 02:00 EDT; no observed wake timer for this app. | **present-but-fragile** | 0.5–1 | **Yes** if #2 is a login-session process |
| 5 | 2026 games + teams not staged; `data.end_season=2025` clamps incremental ingest to 2025; raw `games_s2026*` = 0. | **absent** | 1 | **Yes** |
| 6 | Production `predict_fn` not wired (`_default_predict` → `[]`; CLI `predict` `_not_wired`). Odds ingest not called by the flow. Ratings/calibrators/champion not resolved at publish time. | **absent** | 1–2 | **Yes** (would publish empty/stub even with a schedule) |
| 7 | Week/season parameterization: flow defaults `week=1`, `season=now.year`. Next Tue 08-18 would *request* 2026w1 eleven days early; `week_of` is 0. | **present-but-fragile** | 0.5 | Not if params are set by a human; **yes** for naive schedule+defaults |
| 8 | Alert on missed publish / export-disabled success. `WEBAPP_EXPORT_FAILURE` does not fire when export is off. `CALIBRATION_ALARM` is dead enum. Provider `"null"`. No off-box watchdog. | **absent** | 1 | No (site stale banner is the accepted public mode) but **unattended ops stay silent** |
| 9 | Notifier tokens present in `.env` while `provider: "null"`. | **present-but-fragile** | fold into #8 | No |
| 10 | W7-ENVFILE-FIX dotenv at repo cwd. Machine/User env all ABSENT. Service with wrong cwd → silent no-publish. | **present-and-sound** *if* cwd is repo | 0.5 to pin cwd in #2 | Yes, if launch cwd is wrong |
| 11 | Live allowlist + CFBD id guard + sandbox prefix. | **present-and-sound** | 0 | No |
| 12 | `tier_changes.jsonl` on live export path. | **present-and-sound** | 0 | No (measurement stays empty until a live run) |
| 13 | Synthetic/doctored non-`latest/` prefixes (`v1` leftovers, `v2` 2.0.0, `sandbox/`). Site reads `latest/` 1.1.0 fixture. | **present-but-fragile** (inert) | 0 for first publish; operator/W8-R2-PUBLIC later | No |

**Minimum that blocks the first real 2026 week-1 `latest/` object:** #1 + #2 + #5 +
#6 (and #3/#4 unless the operator sits at the desk at 02:00 EDT). That is
**4–6 sessions** if grouped as: (schedule+runner+sleep), (2026 ingest +
`end_season`), (wire predict), (optional alerting). Do not start #1 in this task.

---

*End of W9-0.*
