# W11-QA C4 — Nov 4 week-10 rating freshness vs EPA gap-check labels

**Verdict: `gap check broken (labels only)`**

Nov 4 `tuesday_primary` week-10 forecasts used ratings that **had already folded week-9 games**. W11-1’s `HAS-WEEK10-RATING` count is almost entirely the synthetic **publish-week** chart point, not evidence that week-10 games were ingested.

Scratch: `docs/notes/_artifacts/w11-qa/c4_rating_freshness.py`, `c4_summary.json`.

---

## 1a. Gap-check classification + W9/W10/W11 label drift

### Quoted W11-1 classifier

```394:422:docs/notes/_artifacts/w11-1/audit.py
    gaps: list[dict] = []
    counts: Counter[str] = Counter()
    for tid in sorted(played):
        rating_weeks = {
            int(w["week"])
            for w in (teams_art.get(str(tid), {}) or {}).get("weeks", [])
        }
        if 10 in rating_weeks:
            klass = "HAS-WEEK10-RATING"
            game_id = None
            opp = None
        else:
            ...
            if gid not in obs_gids and n_plays == 0:
                klass = "NO-PBP"
            elif gid not in obs_gids:
                klass = "FILTER-MISSED"
            else:
                klass = "PENDING"
```

It loads `latest/team_ratings_2026.json`, takes teams that **played the close-out week**, and treats presence of that same integer in `weeks[].week` as “rating folded.”

### Diff across audits (artifact scripts are tracked)

| Audit | Close-out week audited | Checks label | Reported |
|---|---|---|---|
| W9-1 | week **8** participants vs week-8 Tuesday publish | `8 in rating_weeks` → `HAS-WEEK8-RATING` | `PENDING 112` (`docs/notes/w9-1.md`) |
| W10-1 | week **9** vs week-9 Tuesday publish | `9` → `HAS-WEEK9-RATING` | `HAS-WEEK9-RATING=112` (`docs/notes/w10-1.md`) |
| W11-1 | week **10** vs week-10 Tuesday publish | `10` → `HAS-WEEK10-RATING` | `HAS-WEEK10-RATING=125` (`docs/notes/w11-1.md`) |

Git: artifacts landed in sync commits (`61d9a90` for w9-1/w10-1; `f33b0d1` for w11-1). Script-to-script diff is mechanical week bump (`8→9→10`) plus call-grading week; classifier logic unchanged.

### Why W11-1’s “HAS-WEEK10” is a label false positive

Tuesday week-N publish **always** appends a synthetic `kind=weekly` row labeled with the **publish week**, `event_time=as_of`, for chart open circles:

```222:277:src/ncaa_quant/pipelines/predict.py
def _append_current_week_rating_points(
    history: pd.DataFrame,
    *,
    rating_state: Mapping[str, Any],
    season: int,
    week: int,
    as_of: datetime,
    fbs_team_ids: set[int] | None,
) -> pd.DataFrame:
    """Ensure each FBS team has a current-week snapshot for chart open circles."""
    ...
        rows.append(
            {
                "team_id": tid,
                "season": int(season),
                "week": int(week),
                "game_id": None,
                "event_time": as_of_utc,
                "kind": "weekly",
                ...
            }
        )
```

`build_team_ratings` then emits one point per `(team, week)` from filter history (prefer postgame over weekly):

```1258:1308:src/ncaa_quant/webapp/export.py
def build_team_ratings(...):
    ...
        # One point per (team, week): prefer postgame, then latest event_time.
        ...
                weeks.append(
                    {
                        "week": int(row.week),
                        "as_of_utc": _iso_utc(getattr(row, "event_time", None)),
                        ...
                    }
                )
```

So after Nov 4 week-10 publish (before any week-10 kickoff), **every FBS team has `week: 10`** with `as_of_utc ≈ publish as_of`. Checking `10 in rating_weeks` against week-10 participants cannot detect missing week-10 fold-in; week-10 games had not been played yet.

W10-2 already stated the intended semantics: week-10 labels are current after folding week-9 results (`docs/notes/w10-2.md`: “week-10 current on all 138; all 112 week-9 players have week-10. Vs backup_latest_20261104: 108/112 week-9 teams changed EPA”).

### How `team_ratings` week labels work

| Label source | Meaning of `weeks[].week` | Typical `as_of_utc` |
|---|---|---|
| Kalman postgame / weekly from `run_filter` | **Game week** of the observation | Game result-knowable `event_time` |
| `_append_current_week_rating_points` | **Publish week** (slate being forecast) | Operator/calendar publish `as_of` |

Example (Texas in Nov 4 publish artifact):

| week | as_of_utc | role |
|---|---|---|
| 8 | `2026-10-25T04:30:00Z` | postgame (game week 8) |
| 9 | `2026-11-01T04:30:00Z` | postgame vs Mississippi State (game week 9) |
| 10 | `2026-11-04T14:03:14Z` | synthetic publish-week snapshot (same EPA as week 9) |

---

## 1b. Fourteen week-10 card teams — last game folded into Nov 4 state

**Sources (not today’s post-week-10 re-fit):**

- Publish ratings: R2 `v1/2026/w10/tuesday_primary/team_ratings_2026.json` (`published_at` `2026-11-04T14:07:21Z`; SHA matches `backup_latest_20261112/team_ratings_2026.json`)
- Pre-publish control: R2 `backup_latest_20261104/team_ratings_2026.json` (`published_at` `2026-10-28T14:08:37Z`, week-9 Tuesday)
- Decision `as_of`: `2026-11-04T14:03:14.629521+00:00` (`data/webapp/publish_history/2026_w10.jsonl`)
- Schedule: staged `games/season=2026/week={8,9}/part.parquet`

**PASS criterion:** week-9 game present in fold (postgame `as_of_utc` matches game `event_time`) **or** week-9 bye.

| School | Last folded game `event_time` | Opponent | Week-9 |
|---|---|---|---|
| Texas | `2026-11-01T04:30:00Z` | Mississippi State | present (postgame match) |
| Missouri | `2026-11-01T04:45:00Z` | Arkansas | present |
| Georgia | `2026-11-01T00:30:00Z` | Florida | present |
| Ole Miss | `2026-10-31T21:45:00Z` | Auburn | present |
| Alabama | `2026-10-25T00:30:00Z` | Texas A&M | **bye** (last fold = week 8) |
| LSU | `2026-10-24T21:00:00Z` | Auburn | **bye** |
| North Carolina | `2026-11-01T04:30:00Z` | Miami | present |
| UConn | `2026-11-01T00:00:00Z` | Air Force | present |
| Arkansas | `2026-11-01T04:45:00Z` | Missouri | present |
| Auburn | `2026-10-31T21:45:00Z` | Ole Miss | present |
| Texas State | `2026-11-01T07:00:00Z` | Boise State | present |
| Oregon State | `2026-11-01T08:00:00Z` | Fresno State | present |
| Kentucky | `2026-10-25T04:30:00Z` | Vanderbilt | **bye** |
| Tennessee | `2026-10-25T00:30:00Z` | South Carolina | **bye** |

**Card fail count: 0.** All 14 teams also have publish-week label 10 at `2026-11-04T14:03:14Z`.

Population check: among 112 week-9 players, EPA vs pre-publish backup **changed 108 / unchanged 4** (Delaware, Western Kentucky, Sam Houston, Missouri State — Mon-night week-9 games already inside the Oct 28 week-9 Tuesday artifact; not missing fold-in). Matches W10-2’s 108/112 note.

---

## 1c. Twenty random FBS teams (`seed=20261112`)

| School | Last folded `event_time` | Opponent | Week-9 |
|---|---|---|---|
| Sacramento State | `2026-10-31T07:30:00Z` | Kent State | present |
| Hawai'i | `2026-10-25T00:30:00Z` | Northern Illinois | bye |
| Kentucky | `2026-10-25T04:30:00Z` | Vanderbilt | bye |
| Louisville | `2026-11-01T00:15:00Z` | Stanford | present |
| Wake Forest | `2026-11-01T00:45:00Z` | Virginia | present |
| Miami (OH) | `2026-10-24T22:00:00Z` | Central Michigan | bye |
| Ohio State | `2026-11-01T04:30:00Z` | USC | present |
| Penn State | `2026-11-01T01:15:00Z` | Purdue | present |
| Clemson | `2026-11-01T04:00:00Z` | Florida State | present |
| Memphis | `2026-11-01T05:00:00Z` | Army | present |
| Washington State | `2026-11-01T04:00:00Z` | San Diego State | present |
| Mississippi State | `2026-11-01T04:30:00Z` | Texas | present |
| Ball State | `2026-10-25T00:30:00Z` | Sacramento State | bye |
| Liberty | `2026-11-01T00:30:00Z` | Florida International | present |
| Louisiana Tech | `2026-11-01T00:00:00Z` | South Alabama | present |
| Oregon | `2026-11-01T00:30:00Z` | Northwestern | present |
| South Carolina | `2026-11-01T01:15:00Z` | Oklahoma | present |
| Tennessee | `2026-10-25T00:30:00Z` | South Carolina | bye |
| Toledo | `2026-10-24T21:00:00Z` | Western Michigan | bye |
| Tulane | `2026-10-31T06:00:00Z` | Charlotte | present |

**Random fail count: 0.**

---

## 1d. Verdict

### **`gap check broken (labels only)`**

Not **`week-10 forecasts built on stale ratings`**.

Evidence chain:

1. Nov 4 publish predates week-10 games (`as_of` `2026-11-04T14:03:14Z`; verifier `8_kickoff_gt_as_of` PASS per W10-2).
2. Card + random samples show week-9 postgame points (or bye) in that publish’s `team_ratings`.
3. W11-1 `HAS-WEEK10-RATING=125` is expected from `_append_current_week_rating_points(week=10)` alone; it does **not** mean week-10 results were folded.
4. Correct close-out gap check for “did last weekend fold?” must look for **game-week N−1 postgame** (or EPA delta vs prior Tuesday), not publish-week N presence.
