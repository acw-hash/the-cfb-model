"""One-off Task 15 acceptance: preseason prior weights + Week-1 predictions.

Not part of the package surface — run with
``uv run python scripts/_priors_acceptance_15.py``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from ncaa_quant.config import load_config, load_secrets
from ncaa_quant.features.builders.roster import build_roster_frame
from ncaa_quant.ingestion.cfbd import (
    CFBDClient,
    normalize_coaches_payload,
    normalize_portal_payload,
    normalize_recruiting_payload,
    normalize_returning_payload,
    normalize_talent_payload,
    normalize_teams_payload,
)
from ncaa_quant.ingestion.teams import load_team_name_map
from ncaa_quant.ratings.priors import (
    PREDICTOR_NAMES,
    PriorConfig,
    build_design_frame,
    build_preseason_priors_frame,
    fit_prior_weights,
    out_of_sample_r2,
    prior_evidence_crossover_games,
    prior_variance,
    store_week1_predictions,
)

SS14 = Path("data/tmp/state_space_acceptance_14")
CACHE = Path("data/tmp/priors_acceptance_15")
PRED_PATH = Path("data/predictions/week1_priors_2023_2024.parquet")

# Fit window and holdouts (DESIGN §9.6 / Task 15 acceptance).
TRAIN_SEASONS = list(range(2015, 2023))  # 2015–2022
HOLD_SEASONS = [2023, 2024]
ROSTER_SEASONS = list(range(2015, 2025))  # features for target seasons
TEAM_SEASONS = list(range(2014, 2025))  # need S-1 conferences too


def _client() -> CFBDClient:
    secrets = load_secrets()
    cfg = load_config()
    return CFBDClient(
        secrets.cfbd_api_key.get_secret_value(),
        requests_per_second=cfg.data.cfbd_requests_per_second,
        rate_limit_reserve=5,
    )


def _school_to_id(teams: pd.DataFrame, season: int) -> dict[str, int]:
    sub = teams.loc[teams["season"] == season]
    return {str(r.school): int(r.team_id) for r in sub.itertuples(index=False)}


def _load_teams(client: CFBDClient) -> pd.DataFrame:
    path = CACHE / "teams.parquet"
    if path.exists():
        return pd.read_parquet(path)
    # Prefer Task 14 cache, fill gaps.
    frames: list[pd.DataFrame] = []
    have: set[int] = set()
    ss14 = SS14 / "teams.parquet"
    if ss14.exists():
        t = pd.read_parquet(ss14)
        frames.append(t)
        have = {int(s) for s in t["season"].unique()}
    cfg = load_config()
    team_map = load_team_name_map(cfg.data.team_names_path)
    now = datetime.now(tz=UTC)
    for season in TEAM_SEASONS:
        if season in have:
            continue
        print(f"fetch teams {season}", flush=True)
        body = client.fetch_teams(season)
        frames.append(
            normalize_teams_payload(
                body, season=season, ingested_at=now, team_map=team_map
            )
        )
    teams = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    teams = teams.drop_duplicates(subset=["team_id", "season"], keep="last")
    path.parent.mkdir(parents=True, exist_ok=True)
    teams.to_parquet(path, index=False)
    return teams


def _fetch_table(
    client: CFBDClient,
    *,
    name: str,
    seasons: list[int],
    fetch_fn: str,
    normalize,
    teams: pd.DataFrame,
) -> pd.DataFrame:
    path = CACHE / f"{name}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    cfg = load_config()
    team_map = load_team_name_map(cfg.data.team_names_path)
    now = datetime.now(tz=UTC)
    frames: list[pd.DataFrame] = []
    for season in seasons:
        print(f"fetch {name} {season}", flush=True)
        body = getattr(client, fetch_fn)(season)
        school_map = _school_to_id(teams, season)
        # Fall back to any-season school map for ID resolution.
        if not school_map:
            school_map = {
                str(r.school): int(r.team_id)
                for r in teams.drop_duplicates("team_id").itertuples(index=False)
            }
        frames.append(
            normalize(
                body,
                season=season,
                ingested_at=now,
                school_to_id=school_map,
                team_map=team_map,
            )
        )
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(path, index=False)
    return out


def main() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    history = pd.read_parquet(SS14 / "history.parquet")
    print(f"history rows={len(history)} seasons={sorted(history['season'].unique())}", flush=True)

    client = _client()
    teams = _load_teams(client)
    # FBS-only roster universe for priors.
    fbs_teams = teams.loc[teams["classification"].astype(str).str.lower() == "fbs"].copy()

    returning = _fetch_table(
        client,
        name="returning",
        seasons=ROSTER_SEASONS,
        fetch_fn="fetch_returning",
        normalize=normalize_returning_payload,
        teams=teams,
    )
    talent = _fetch_table(
        client,
        name="talent",
        seasons=ROSTER_SEASONS,
        fetch_fn="fetch_talent",
        normalize=normalize_talent_payload,
        teams=teams,
    )
    recruiting = _fetch_table(
        client,
        name="recruiting",
        seasons=list(range(2012, 2025)),  # 4-yr window needs lookback
        fetch_fn="fetch_recruiting",
        normalize=normalize_recruiting_payload,
        teams=teams,
    )
    portal = _fetch_table(
        client,
        name="portal",
        seasons=[s for s in ROSTER_SEASONS if s >= 2021],
        fetch_fn="fetch_portal",
        normalize=normalize_portal_payload,
        teams=teams,
    )
    coaches = _fetch_table(
        client,
        name="coaches",
        seasons=ROSTER_SEASONS,
        fetch_fn="fetch_coaches",
        normalize=normalize_coaches_payload,
        teams=teams,
    )

    print("building roster frame...", flush=True)
    roster = build_roster_frame(
        teams=fbs_teams,
        returning=returning,
        talent=talent,
        recruiting=recruiting,
        portal=portal,
        coaches=coaches,
        seasons=ROSTER_SEASONS,
    )
    CACHE.mkdir(parents=True, exist_ok=True)
    roster.to_parquet(CACHE / "roster.parquet", index=False)
    print(f"roster rows={len(roster)}", flush=True)

    cfg = PriorConfig(
        conf_regression=float(load_config().ratings.prior_regression_to_conf_mean),
    )
    print("building design matrix (off_epa)...", flush=True)
    design_off = build_design_frame(
        history=history,
        roster=roster,
        teams=fbs_teams,
        seasons=TRAIN_SEASONS + HOLD_SEASONS,
        dim="off_epa",
        config=cfg,
    )
    design_def = build_design_frame(
        history=history,
        roster=roster,
        teams=fbs_teams,
        seasons=TRAIN_SEASONS + HOLD_SEASONS,
        dim="def_epa",
        config=cfg,
    )
    design = pd.concat([design_off, design_def], ignore_index=True)
    design.to_parquet(CACHE / "design.parquet", index=False)
    print(
        f"design rows={len(design)} "
        f"off={len(design_off)} def={len(design_def)} "
        f"seasons={sorted(design['season'].unique())}",
        flush=True,
    )

    fitted_off = fit_prior_weights(
        design, dim="off_epa", seasons_train=TRAIN_SEASONS, config=cfg, seed=42
    )
    fitted_def = fit_prior_weights(
        design, dim="def_epa", seasons_train=TRAIN_SEASONS, config=cfg, seed=43
    )

    def _report(fitted) -> dict:
        print(f"\nFITTED_WEIGHTS dim={fitted.dim} n={fitted.n_obs} inR2={fitted.r_squared:.4f}")
        print(f"  intercept={fitted.intercept:.5f} (se={fitted.intercept_se:.5f})")
        for name in PREDICTOR_NAMES:
            w, se = fitted.weights[name], fitted.std_errors[name]
            se_s = f"{se:.5f}" if se == se else "nan"
            print(f"  {name:16s} w={w:+.5f}  se={se_s}")
        oos = {
            s: out_of_sample_r2(design, fitted, seasons_test=[s]) for s in HOLD_SEASONS
        }
        for s, r2 in oos.items():
            print(f"  OOS_R2 season={s}: {r2:.4f}")
        return {
            "dim": fitted.dim,
            "n_obs": fitted.n_obs,
            "in_sample_r2": fitted.r_squared,
            "intercept": fitted.intercept,
            "intercept_se": fitted.intercept_se,
            "weights": fitted.weights,
            "std_errors": {k: (None if v != v else v) for k, v in fitted.std_errors.items()},
            "oos_r2": oos,
            "seasons_train": list(fitted.seasons_train),
        }

    report = {
        "off_epa": _report(fitted_off),
        "def_epa": _report(fitted_def),
        "config": {
            "conf_regression": cfg.conf_regression,
            "base_var": cfg.base_var,
            "turnover_scale": cfg.turnover_scale,
            "missing_var_penalty": cfg.missing_var_penalty,
            "obs_var_eff": cfg.obs_var_eff,
        },
    }

    # Week-1 predictions for 2023 and 2024.
    prior_frames = []
    for season in HOLD_SEASONS:
        pf = build_preseason_priors_frame(
            history=history,
            roster=roster,
            teams=fbs_teams,
            season=season,
            fitted_by_dim={"off_epa": fitted_off, "def_epa": fitted_def},
            config=cfg,
            dims=["off_epa", "def_epa"],
        )
        prior_frames.append(pf)
        print(f"week1 priors season={season} rows={len(pf)}", flush=True)
    priors = pd.concat(prior_frames, ignore_index=True)
    store_week1_predictions(priors, PRED_PATH, seasons=HOLD_SEASONS)
    priors.to_parquet(CACHE / "week1_priors.parquet", index=False)
    print(f"stored Week-1 predictions -> {PRED_PATH}", flush=True)

    # Prior-vs-evidence crossover: high- and low-continuity teams near the
    # 85th / 15th percentiles (avoid single-digit returning outliers).
    sub = priors.loc[(priors["season"] == 2023) & (priors["dim"] == "off_epa")].copy()
    sub = sub.dropna(subset=["returning_pct"])
    sub = sub.loc[sub["returning_pct"] >= 0.0]
    lo_cut = float(sub["returning_pct"].quantile(0.15))
    hi_cut = float(sub["returning_pct"].quantile(0.85))
    lo_pool = sub.loc[sub["returning_pct"] <= lo_cut].sort_values("returning_pct")
    hi_pool = sub.loc[sub["returning_pct"] >= hi_cut].sort_values(
        "returning_pct", ascending=False
    )
    lo = lo_pool.iloc[len(lo_pool) // 2]
    hi = hi_pool.iloc[len(hi_pool) // 2]
    school = {
        int(r.team_id): str(r.school)
        for r in fbs_teams.loc[fbs_teams["season"] == 2023].itertuples(index=False)
    }

    def _cross(row) -> None:
        tid = int(row.team_id)
        name = school.get(tid, str(tid))
        print(
            f"CROSSOVER team={name} returning={row.returning_pct:.3f} "
            f"prior_var={row.prior_var:.5f} games_50_50={row.crossover_games:.2f}",
            flush=True,
        )

    print("\nPRIOR_VS_EVIDENCE_CROSSOVER season=2023 dim=off_epa", flush=True)
    _cross(hi)
    _cross(lo)
    # Also show theoretical 40% vs 85% under config (spec illustration).
    v40 = prior_variance(0.40, n_missing=0, config=cfg)
    v85 = prior_variance(0.85, n_missing=0, config=cfg)
    print(
        f"ILLUSTRATION ret=0.40 var={v40:.5f} crossover={prior_evidence_crossover_games(v40, config=cfg):.2f}",
        flush=True,
    )
    print(
        f"ILLUSTRATION ret=0.85 var={v85:.5f} crossover={prior_evidence_crossover_games(v85, config=cfg):.2f}",
        flush=True,
    )

    report["crossover"] = {
        "high_continuity": {
            "team_id": int(hi.team_id),
            "school": school.get(int(hi.team_id)),
            "returning_pct": float(hi.returning_pct),
            "prior_var": float(hi.prior_var),
            "crossover_games": float(hi.crossover_games),
        },
        "low_continuity": {
            "team_id": int(lo.team_id),
            "school": school.get(int(lo.team_id)),
            "returning_pct": float(lo.returning_pct),
            "prior_var": float(lo.prior_var),
            "crossover_games": float(lo.crossover_games),
        },
        "illustration_40": {
            "var": v40,
            "crossover": prior_evidence_crossover_games(v40, config=cfg),
        },
        "illustration_85": {
            "var": v85,
            "crossover": prior_evidence_crossover_games(v85, config=cfg),
        },
    }
    (CACHE / "summary.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print("\nDONE", flush=True)


if __name__ == "__main__":
    main()
