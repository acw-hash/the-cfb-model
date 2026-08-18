"""Artifact builders for Ridge public JSON exports (docs/webapp/DESIGN.md §1–§2)."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

import pandas as pd  # type: ignore[import-untyped]

from ncaa_quant.config import AppConfig, load_config
from ncaa_quant.pipelines.predict import RefreshKind

SCHEMA_VERSION = "1.2.0"

ConvictionTier = Literal["strong_lean", "clear_lean", "lean", "toss_up"]

#: W1A tier enter thresholds (docs/webapp/DESIGN.md §2.2, amended 2026-08-13).
TIER_STRONG_ENTER = 0.85
TIER_CLEAR_ENTER = 0.70
TIER_LEAN_ENTER = 0.575
HYSTERESIS_BAND = 0.03

#: Per-game cover/over probs vs the CFBD close. Computed internally; not published.
#: ADR 0015 / schema 1.2.0 WITHDRAWAL.
WITHDRAWN_FIELDS: frozenset[str] = frozenset(
    {
        "p_cover_home",
        "p_cover_home_credible",
        "p_over",
        "p_over_credible",
    }
)

#: Exact keys of a published ``GamePrediction`` object (schema 1.2.0).
PUBLISHED_GAME_PREDICTION_KEYS: frozenset[str] = frozenset(
    {
        "away_team",
        "away_team_id",
        "conference_game",
        "conviction_basis",
        "conviction_label",
        "conviction_team",
        "conviction_tier",
        "ensemble_scope_label",
        "feature_time_label",
        "game_id",
        "home_team",
        "home_team_id",
        "is_stale",
        "kickoff_utc",
        "margin_interval_hi",
        "margin_interval_lo",
        "margin_interval_nominal",
        "mu_margin",
        "mu_total",
        "neutral_site",
        "null_reason",
        "p_win_home",
        "p_win_home_credible",
        "published_at",
        "refresh_kind",
        "season",
        "sigma_margin",
        "sigma_margin_credible",
        "sigma_total",
        "sigma_total_credible",
        "stale_sources",
        "stale_stamp",
        "tier_primary",
        "tier_revised_since_primary",
        "total_interval_hi",
        "total_interval_lo",
        "total_interval_nominal",
        "vintage_label",
        "week",
    }
)


class PublishedKeyAllowlistError(ValueError):
    """A published object contains a key outside the sanctioned allowlist."""


def assert_game_prediction_allowlist(game: Mapping[str, Any]) -> None:
    """Fail on any unknown or withdrawn key in a published game object."""
    present = set(game.keys())
    extra = present - PUBLISHED_GAME_PREDICTION_KEYS
    if extra:
        msg = f"unpublished or withdrawn keys in GamePrediction: {sorted(extra)}"
        raise PublishedKeyAllowlistError(msg)
    missing = PUBLISHED_GAME_PREDICTION_KEYS - present
    if missing:
        msg = f"published GamePrediction missing required keys: {sorted(missing)}"
        raise PublishedKeyAllowlistError(msg)


#: Odds / market / bet-candidate field names that must never appear in artifacts.
ODDS_FIELD_DENYLIST: frozenset[str] = frozenset(
    {
        # Walkforward line columns (evaluation only — never exported).
        "spread_asof",
        "total_asof",
        "line_source_asof",
        "n_books_asof",
        "spread_close",
        "total_close",
        "line_source_close",
        "n_books_close",
        "p_mkt_ats_home",
        "p_mkt_ou_over",
        "p_mkt_ml_home",
        # Generic market / odds vocabulary.
        "spread",
        "total_line",
        "total",
        "price",
        "book",
        "bookmaker",
        "implied_prob",
        "implied_probability",
        "edge",
        "ev",
        "expected_value",
        "clv",
        "kelly",
        "stake",
        # Bet-candidate / publish-internal fields.
        "n_candidates",
        "n_accepted",
        "n_rejected",
        "stale_rejections",
        "accepted",
        "rejected",
        "bet_candidate",
        "bet_candidates",
        "candidates",
        "market",
        "min_edge",
    }
)

DEFAULT_VINTAGE_LABEL = "REGRADED_V2"
DEFAULT_ENSEMBLE_SCOPE_LABEL = "REDUCED_PER_ADR_0013"
DEFAULT_FEATURE_TIME_LABEL = "FEATURE_TIME=TUESDAY_DECISION"
TRACK_RECORD_VINTAGE_LABEL = "W9A_REVAL"
FIXTURE_WEEK5_AS_OF = datetime(2024, 9, 24, 10, 0, 0, tzinfo=UTC)
FIXTURE_WALKFORWARD_PATH = "data/registry/artifacts/v2/week_predictions.parquet"

REFRESH_KIND_PRECEDENCE: dict[str, int] = {
    RefreshKind.T_MINUS_1H: 4,
    RefreshKind.T_MINUS_6H: 3,
    RefreshKind.DAILY_REFRESH: 2,
    RefreshKind.TUESDAY_PRIMARY: 1,
}


def _json_safe(value: Any) -> Any:
    """Convert NaN/Inf to JSON null; preserve explicit None."""
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def _iso_utc(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _field(row: Mapping[str, Any], *names: str) -> Any:
    """First present key wins (stub vs production column bridge)."""
    for name in names:
        if name in row:
            return row[name]
    return None


def sigma_margin_credible(row: Mapping[str, Any]) -> bool:
    sigma_missing = _field(row, "sigma_m_is_missing", "sigma_margin_is_missing")
    null_reason = _field(row, "null_reason")
    if sigma_missing is True:
        return False
    if null_reason is not None and null_reason != "":
        return False
    return _optional_float(_field(row, "sigma_margin", "sigma_m")) is not None


def sigma_total_credible(row: Mapping[str, Any]) -> bool:
    sigma_missing = _field(row, "sigma_t_is_missing", "sigma_total_is_missing")
    null_reason = _field(row, "null_reason")
    if sigma_missing is True:
        return False
    if null_reason is not None and null_reason != "":
        return False
    return _optional_float(_field(row, "sigma_total", "sigma_t")) is not None


def probability_credible(row: Mapping[str, Any], *flag_names: str) -> bool:
    if not sigma_margin_credible(row):
        return False
    return all(not (flag in row and row[flag] is True) for flag in flag_names)


def compute_favored_side(
    mu_margin: float | None,
    p_win_home: float | None,
) -> Literal["home", "away"]:
    if mu_margin is not None and mu_margin == 0:
        if p_win_home is not None and p_win_home >= 0.5:
            return "home"
        return "away"
    if mu_margin is not None and mu_margin >= 0:
        return "home"
    return "away"


def compute_p_favored(
    mu_margin: float | None,
    p_win_home: float | None,
) -> float | None:
    if p_win_home is None:
        return None
    side = compute_favored_side(mu_margin, p_win_home)
    if side == "home":
        return p_win_home
    return 1.0 - p_win_home


def raw_tier_from_p_favored(p_favored: float) -> ConvictionTier:
    if p_favored >= TIER_STRONG_ENTER:
        return "strong_lean"
    if p_favored >= TIER_CLEAR_ENTER:
        return "clear_lean"
    if p_favored >= TIER_LEAN_ENTER:
        return "lean"
    return "toss_up"


def apply_hysteresis(
    *,
    p_favored: float,
    raw_tier: ConvictionTier,
    previous_tier: ConvictionTier | None,
) -> tuple[ConvictionTier, bool]:
    """Return (tier, hysteresis_applied)."""
    if previous_tier is None:
        return raw_tier, False

    if previous_tier == "strong_lean" and p_favored >= TIER_STRONG_ENTER - HYSTERESIS_BAND:
        return "strong_lean", raw_tier != "strong_lean"
    if previous_tier == "clear_lean" and p_favored >= TIER_CLEAR_ENTER - HYSTERESIS_BAND:
        if p_favored >= TIER_STRONG_ENTER:
            return "strong_lean", True
        return "clear_lean", raw_tier != "clear_lean"
    if previous_tier == "lean" and p_favored >= TIER_LEAN_ENTER - HYSTERESIS_BAND:
        if p_favored >= TIER_STRONG_ENTER:
            return "strong_lean", True
        if p_favored >= TIER_CLEAR_ENTER:
            return "clear_lean", True
        return "lean", raw_tier != "lean"
    if previous_tier == "toss_up" and p_favored < TIER_LEAN_ENTER + HYSTERESIS_BAND:
        return "toss_up", raw_tier != "toss_up"
    return raw_tier, False


def tier_suppressed(
    row: Mapping[str, Any],
    *,
    stale_max_age_hours: float = 6.0,
) -> bool:
    mu = _optional_float(_field(row, "mu_margin", "pred_margin"))
    p_win = _optional_float(_field(row, "p_win_home", "p_ml_home"))
    if mu is None or p_win is None:
        return True
    if not sigma_margin_credible(row):
        return True
    if not probability_credible(row, "p_ml_home_is_missing", "p_win_home_is_missing"):
        return True
    is_stale = bool(row.get("is_stale", False))
    if is_stale:
        for src in row.get("stale_sources") or []:
            age = src.get("age_hours")
            if age is not None and float(age) > stale_max_age_hours:
                return True
    return False


def tier_label(tier: ConvictionTier | None, team: str | None) -> str | None:
    if tier is None:
        return None
    if tier == "strong_lean" and team:
        return f"Strong lean {team}"
    if tier == "clear_lean" and team:
        return f"Clear lean {team}"
    if tier == "lean" and team:
        return f"Lean {team}"
    if tier == "toss_up":
        return "Toss-up"
    return None


def compute_conviction(
    row: Mapping[str, Any],
    *,
    home_team: str,
    away_team: str,
    previous_tier: ConvictionTier | None,
    stale_max_age_hours: float = 6.0,
) -> dict[str, Any]:
    mu = _optional_float(_field(row, "mu_margin", "pred_margin"))
    sigma = _optional_float(_field(row, "sigma_margin", "sigma_m"))
    p_win_home = _optional_float(_field(row, "p_win_home", "p_ml_home"))
    p_favored = compute_p_favored(mu, p_win_home)
    favored = compute_favored_side(mu, p_win_home)
    favored_team: str | None = home_team if favored == "home" else away_team

    raw: ConvictionTier | None = None
    tier: ConvictionTier | None = None
    hysteresis_applied = False
    if p_favored is not None:
        raw = raw_tier_from_p_favored(p_favored)
        tier, hysteresis_applied = apply_hysteresis(
            p_favored=p_favored,
            raw_tier=raw,
            previous_tier=previous_tier,
        )

    if tier_suppressed(row, stale_max_age_hours=stale_max_age_hours):
        tier = None
        favored_team = None

    mu_sigma_ratio: float | None = None
    if mu is not None and sigma is not None and sigma != 0:
        mu_sigma_ratio = abs(mu) / sigma

    return {
        "conviction_tier": tier,
        "conviction_team": favored_team if tier is not None else None,
        "conviction_label": tier_label(tier, favored_team),
        "conviction_basis": {
            "p_favored": p_favored,
            "p_win_home": p_win_home,
            "mu_margin": mu,
            "sigma_margin": sigma,
            "mu_sigma_ratio": mu_sigma_ratio,
            "favored_side": favored,
            "hysteresis_applied": hysteresis_applied,
            "previous_tier": previous_tier,
            "raw_tier": raw,
        },
    }


@dataclass
class TierStateStore:
    """Workstation tier state keyed by ``(season, game_id)`` for hysteresis."""

    path: Path

    @classmethod
    def default_path(cls, config: AppConfig | None = None) -> TierStateStore:
        cfg = config or load_config()
        return cls(Path(cfg.webapp.tier_state_path))

    def load(self) -> dict[str, ConvictionTier]:
        if not self.path.is_file():
            return {}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        tiers = data.get("tiers", {})
        out: dict[str, ConvictionTier] = {}
        for key, tier in tiers.items():
            if tier in ("strong_lean", "clear_lean", "lean", "toss_up"):
                out[str(key)] = cast(ConvictionTier, tier)
        return out

    def load_tier_primary(self) -> dict[str, ConvictionTier]:
        if not self.path.is_file():
            return {}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        primaries = data.get("tier_primary", {})
        out: dict[str, ConvictionTier] = {}
        for key, tier in primaries.items():
            if tier in ("strong_lean", "clear_lean", "lean", "toss_up"):
                out[str(key)] = cast(ConvictionTier, tier)
        return out

    def save(
        self,
        *,
        tiers: Mapping[str, ConvictionTier | None],
        tier_primary: Mapping[str, ConvictionTier | None] | None = None,
        refresh_kind: str,
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing = {}
        existing_primary = {}
        if self.path.is_file():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            existing = data.get("tiers", {})
            existing_primary = data.get("tier_primary", {})

        merged = {**existing}
        for key, tier in tiers.items():
            if tier is None:
                merged.pop(key, None)
            else:
                merged[key] = tier

        merged_primary = {**existing_primary}
        if tier_primary is not None:
            for key, tier in tier_primary.items():
                if tier is None:
                    continue
                merged_primary[key] = tier
        elif refresh_kind == RefreshKind.TUESDAY_PRIMARY:
            for key, tier in tiers.items():
                if tier is not None:
                    merged_primary[key] = tier

        payload = {
            "updated_at": datetime.now(tz=UTC).isoformat(),
            "tiers": merged,
            "tier_primary": merged_primary,
        }
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _tier_state_key(season: int, game_id: str) -> str:
    return f"{season}:{game_id}"


def merge_prediction_rows(
    stamped: Mapping[str, Any],
    production: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Merge Task-24 stub stamp onto production walkforward row."""
    merged: dict[str, Any] = dict(production or {})
    merged.update(stamped)
    return merged


def _model_identity_from_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Pass walkforward ``run_id`` / ``model_version`` through when present."""
    identity: dict[str, Any] = {
        "registry_name": "ncaa-quant",
        "champion_version": 3,
        "model_version": "production-v0_reduced_v1",
        "run_id": None,
    }
    if not rows:
        return identity
    row = rows[0]
    if row.get("model_version") is not None:
        identity["model_version"] = str(row["model_version"])
    if row.get("run_id") is not None:
        identity["run_id"] = str(row["run_id"])
    return identity


def build_game_prediction(
    row: Mapping[str, Any],
    schedule: Mapping[str, Any],
    *,
    season: int,
    week: int,
    published_at: datetime,
    refresh_kind: str,
    vintage_label: str,
    ensemble_scope_label: str,
    feature_time_label: str,
    previous_tier: ConvictionTier | None,
    tier_primary: ConvictionTier | None,
    stale_max_age_hours: float = 6.0,
) -> dict[str, Any]:
    home_team = str(schedule.get("home_team", ""))
    away_team = str(schedule.get("away_team", ""))
    conviction = compute_conviction(
        row,
        home_team=home_team,
        away_team=away_team,
        previous_tier=previous_tier,
        stale_max_age_hours=stale_max_age_hours,
    )
    current_tier = conviction["conviction_tier"]
    revised = False
    if refresh_kind != RefreshKind.TUESDAY_PRIMARY and tier_primary is not None:
        revised = current_tier != tier_primary

    stale_sources = row.get("stale_sources")
    if stale_sources is None and row.get("is_stale"):
        stale_sources = []

    game: dict[str, Any] = {
        "game_id": str(_field(row, "game_id") or schedule.get("game_id", "")),
        "season": season,
        "week": week,
        "home_team": home_team,
        "away_team": away_team,
        "home_team_id": int(schedule.get("home_team_id", 0)),
        "away_team_id": int(schedule.get("away_team_id", 0)),
        "kickoff_utc": _iso_utc(schedule.get("kickoff_utc") or schedule.get("start_date")),
        "neutral_site": bool(schedule.get("neutral_site", False)),
        "conference_game": bool(schedule.get("conference_game", False)),
        "mu_margin": _optional_float(_field(row, "mu_margin", "pred_margin")),
        "sigma_margin": _optional_float(_field(row, "sigma_margin", "sigma_m")),
        "sigma_margin_credible": sigma_margin_credible(row),
        "margin_interval_lo": _optional_float(
            _field(row, "margin_interval_lo", "cqr_lo", "pred_margin_q05")
        ),
        "margin_interval_hi": _optional_float(
            _field(row, "margin_interval_hi", "cqr_hi", "pred_margin_q95")
        ),
        "margin_interval_nominal": _optional_float(
            _field(row, "margin_interval_nominal", "cqr_nominal")
        ),
        "mu_total": _optional_float(_field(row, "mu_total", "pred_total")),
        "sigma_total": _optional_float(_field(row, "sigma_total", "sigma_t")),
        "sigma_total_credible": sigma_total_credible(row),
        "total_interval_lo": _optional_float(_field(row, "total_interval_lo")),
        "total_interval_hi": _optional_float(_field(row, "total_interval_hi")),
        "total_interval_nominal": _optional_float(_field(row, "total_interval_nominal")),
        "p_win_home": _optional_float(_field(row, "p_win_home", "p_ml_home")),
        "p_win_home_credible": probability_credible(
            row, "p_ml_home_is_missing", "p_win_home_is_missing"
        ),
        "conviction_tier": conviction["conviction_tier"],
        "conviction_team": conviction["conviction_team"],
        "conviction_label": conviction["conviction_label"],
        "conviction_basis": conviction["conviction_basis"],
        "tier_primary": tier_primary,
        "tier_revised_since_primary": revised,
        "is_stale": bool(row.get("is_stale", False)),
        "stale_stamp": row.get("stale_stamp"),
        "stale_sources": stale_sources or [],
        "null_reason": _field(row, "null_reason"),
        "vintage_label": vintage_label,
        "ensemble_scope_label": ensemble_scope_label,
        "feature_time_label": feature_time_label,
        "published_at": _iso_utc(published_at),
        "refresh_kind": refresh_kind,
    }
    safe = cast(dict[str, Any], _json_safe(game))
    assert_game_prediction_allowlist(safe)
    return safe


def append_tier_change_records(
    records: Sequence[Mapping[str, Any]],
    *,
    path: Path,
) -> None:
    """Append per-game tier instrumentation as JSONL (workstation-only).

    Not published to R2 or the site — measurement for the W1A flap-exposure
    successor (four live 2026 publish weeks).
    """
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(dict(record), sort_keys=True) + "\n")


def build_week_predictions(
    *,
    season: int,
    week: int,
    refresh_kind: str,
    published_at: datetime,
    prediction_rows: Sequence[Mapping[str, Any]],
    schedule_by_game: Mapping[str, Mapping[str, Any]],
    stale_context: Mapping[str, Any] | None = None,
    model_identity: Mapping[str, Any] | None = None,
    vintage_label: str = DEFAULT_VINTAGE_LABEL,
    ensemble_scope_label: str = DEFAULT_ENSEMBLE_SCOPE_LABEL,
    feature_time_label: str = DEFAULT_FEATURE_TIME_LABEL,
    tier_store: TierStateStore | None = None,
    stale_max_age_hours: float = 6.0,
    fixture: bool = False,
    tier_changes_path: Path | None = None,
    record_tier_changes: bool = False,
) -> dict[str, Any]:
    store = tier_store or TierStateStore.default_path()
    previous_tiers = store.load()
    tier_primaries = store.load_tier_primary()

    games: list[dict[str, Any]] = []
    new_tiers: dict[str, ConvictionTier | None] = {}
    tier_change_records: list[dict[str, Any]] = []

    for row in prediction_rows:
        game_id = str(_field(row, "game_id", "game_key") or "")
        schedule = schedule_by_game.get(game_id, {"game_id": game_id})
        key = _tier_state_key(season, game_id)
        prior_tier = previous_tiers.get(key)
        game = build_game_prediction(
            row,
            schedule,
            season=season,
            week=week,
            published_at=published_at,
            refresh_kind=refresh_kind,
            vintage_label=vintage_label,
            ensemble_scope_label=ensemble_scope_label,
            feature_time_label=feature_time_label,
            previous_tier=prior_tier,
            tier_primary=tier_primaries.get(key),
            stale_max_age_hours=stale_max_age_hours,
        )
        games.append(game)
        tier_val = game.get("conviction_tier")
        if tier_val in ("strong_lean", "clear_lean", "lean", "toss_up"):
            new_tiers[key] = cast(ConvictionTier, tier_val)
        else:
            new_tiers[key] = None

        basis = game.get("conviction_basis") or {}
        tier_change_records.append(
            {
                "published_at": _iso_utc(published_at),
                "season": season,
                "week": week,
                "refresh_kind": refresh_kind,
                "game_id": game_id,
                "prior_tier": prior_tier,
                "new_tier": tier_val,
                "hysteresis_applied": bool(basis.get("hysteresis_applied", False)),
                "p_favored": basis.get("p_favored"),
            }
        )

    store.save(tiers=new_tiers, refresh_kind=refresh_kind)

    if record_tier_changes:
        cfg_path = tier_changes_path
        if cfg_path is None:
            cfg_path = Path(load_config().webapp.tier_changes_path)
        append_tier_change_records(tier_change_records, path=cfg_path)

    stale = stale_context or {}
    artifact: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "season": season,
        "week": week,
        "refresh_kind": refresh_kind,
        "published_at": _iso_utc(published_at),
        "feature_time_label": feature_time_label,
        "ensemble_scope_label": ensemble_scope_label,
        "vintage_label": vintage_label,
        "model_identity": model_identity
        or {
            "registry_name": "ncaa-quant",
            "champion_version": 3,
            "model_version": "production-v0_reduced_v1",
            "run_id": None,
        },
        "publish_stale": {
            "is_stale": bool(stale.get("is_stale", False)),
            "combined_stamp": stale.get("combined_stamp"),
            "sources": [
                {
                    "source": s.get("source"),
                    "age_hours": s.get("age_hours"),
                    "last_good_at": s.get("last_good_at"),
                }
                for s in stale.get("sources") or []
            ],
        },
        "games": games,
    }
    if fixture:
        artifact["fixture"] = True
    return cast(dict[str, Any], _json_safe(artifact))


def next_expected_publish_utc(published_at: datetime, refresh_kind: str) -> str:
    base = published_at.astimezone(UTC)
    if refresh_kind == RefreshKind.TUESDAY_PRIMARY:
        nxt = base + timedelta(days=2)
    else:
        nxt = base + timedelta(days=1)
    return _iso_utc(nxt) or ""


def build_meta(
    *,
    season: int,
    week: int,
    refresh_kind: str,
    published_at: datetime,
    vintage_label: str = DEFAULT_VINTAGE_LABEL,
    ensemble_scope_label: str = DEFAULT_ENSEMBLE_SCOPE_LABEL,
    feature_time_label: str = DEFAULT_FEATURE_TIME_LABEL,
    fixture: bool = False,
) -> dict[str, Any]:
    artifact: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "published_at": _iso_utc(published_at),
        "season": season,
        "week": week,
        "refresh_kind": refresh_kind,
        "next_expected_publish_utc": next_expected_publish_utc(published_at, refresh_kind),
        "champion_model": {
            "registry_name": "ncaa-quant",
            "champion_version": 3,
            "model_version": "production-v0_reduced_v1",
            "registered_at": "2024-08-01T12:00:00Z",
        },
        "publish_schedule": {
            "primary": "Tue 06:00 UTC",
            "refresh": "Thu–Sat 06:00 UTC",
            "postgame_ratings": "Sun 06:00 UTC",
        },
        "artifact_pointers": {
            "week_predictions": "latest/week_predictions.json",
            "track_record": "latest/track_record.json",
            "results_current_season": f"latest/results_{season}.json",
            "team_ratings": f"latest/team_ratings_{season}.json",
        },
        "feature_time_label": feature_time_label,
        "ensemble_scope_label": ensemble_scope_label,
        "vintage_label": vintage_label,
    }
    if fixture:
        artifact["fixture"] = True
    return artifact


def build_track_record(
    *, published_at: datetime | None = None, fixture: bool = False
) -> dict[str, Any]:
    """Frozen 23-reval metrics — verbatim from the amended memo, no recomputation."""
    ts = _iso_utc(published_at or datetime(2026, 8, 13, tzinfo=UTC))
    metrics: list[dict[str, Any]] = [
        {
            "id": "fund_ats_snapshots",
            "label": "Fundamental ATS snapshots 2021–24",
            "value": 48.9,
            "unit": "percent",
            "ci_lower": 47.5,
            "ci_upper": 50.5,
            "ci_kind": "bootstrap_95",
            "n": 3496,
            "regime": "snapshots 2021–24",
            "vintage": "W9G_REGRADE",
            "run": "fundamental",
            "notes": None,
        },
        {
            "id": "fund_ats_2019",
            "label": "Fundamental ATS CFBD 2019",
            "value": 49.9,
            "unit": "percent",
            "ci_lower": 46.9,
            "ci_upper": 52.3,
            "ci_kind": "bootstrap_95",
            "n": 553,
            "regime": "CFBD 2019",
            "vintage": "W9G_REGRADE",
            "run": "fundamental",
            "notes": (
                "sample excludes rows where the model recorded no ATS "
                "probability; no probability is imputed."
            ),
        },
        {
            "id": "fund_ou_snapshots",
            "label": "Fundamental OU snapshots 2021–24",
            "value": 51.5,
            "unit": "percent",
            "ci_lower": 49.7,
            "ci_upper": 53.5,
            "ci_kind": "bootstrap_95",
            "n": 3136,
            "regime": "snapshots 2021–24",
            "vintage": "W9A_REVAL",
            "run": "fundamental",
            "notes": (
                "Possessions structurally null outside partial 2023; "
                "OU without §4.5 key totals feature on almost all rows."
            ),
        },
        {
            "id": "fund_ou_2019",
            "label": "Fundamental OU CFBD 2019",
            "value": 51.4,
            "unit": "percent",
            "ci_lower": 46.5,
            "ci_upper": 55.3,
            "ci_kind": "bootstrap_95",
            "n": 551,
            "regime": "CFBD 2019",
            "vintage": "W9A_REVAL",
            "run": "fundamental",
            "notes": (
                "same basis (rows without a recorded probability "
                "or a usable σ are not scored; nothing is imputed)."
            ),
        },
        {
            "id": "mae_margin_fund",
            "label": "MAE margin continual (fundamental)",
            "value": 14.53,
            "unit": "points",
            "ci_lower": None,
            "ci_upper": None,
            "ci_kind": "none",
            "n": 4285,
            "regime": "all-season",
            "vintage": "W9A_REVAL",
            "run": "fundamental",
            "notes": (
                "90 rows (2019 weeks 2–4) carry no credible ensemble member "
                "and are not scored; on the matched sample point accuracy is "
                "essentially unchanged. 2019 weeks 2–4 are a partially degraded "
                "cohort per ADR 0014."
            ),
        },
        {
            "id": "mae_margin_a2",
            "label": "MAE margin A2 frozen",
            "value": 15.51,
            "unit": "points",
            "ci_lower": None,
            "ci_upper": None,
            "ci_kind": "none",
            "n": 4290,
            "regime": "all-season",
            "vintage": "W9A_REVAL",
            "run": "A2",
            "notes": None,
        },
        {
            "id": "crps_margin_fund",
            "label": "CRPS margin continual (fundamental)",
            "value": 10.02,
            "unit": "points",
            "ci_lower": None,
            "ci_upper": None,
            "ci_kind": "none",
            "n": 4175,
            "regime": "all-season",
            "vintage": "W9A_REVAL",
            "run": "fundamental",
            "notes": (
                "90 rows (2019 weeks 2–4) carry no credible ensemble member "
                "and are not scored; on the matched sample point accuracy is "
                "essentially unchanged."
            ),
        },
        {
            "id": "crps_margin_a2",
            "label": "CRPS margin A2 frozen",
            "value": 10.75,
            "unit": "points",
            "ci_lower": None,
            "ci_upper": None,
            "ci_kind": "none",
            "n": 4175,
            "regime": "all-season",
            "vintage": "W9A_REVAL",
            "run": "A2",
            "notes": (
                "same basis (rows without a recorded probability "
                "or a usable σ are not scored; nothing is imputed)."
            ),
        },
        {
            "id": "ats_logloss_band",
            "label": "ATS log-loss band (fundamental)",
            "value": "0.78–0.93",
            "unit": "ratio",
            "ci_lower": None,
            "ci_upper": None,
            "ci_kind": "none",
            "n": None,
            "regime": "2019 + snapshots 2021–24",
            "vintage": "W9G_REGRADE",
            "run": "fundamental",
            "notes": "vs market baseline 0.693",
        },
        {
            "id": "scorecard_clv",
            "label": "Mean same-book CLV > 0, 95% CI excludes 0, n≥300",
            "value": "UNMEASURABLE",
            "unit": "none",
            "ci_lower": None,
            "ci_upper": None,
            "ci_kind": "none",
            "n": None,
            "regime": None,
            "vintage": "W9A_REVAL",
            "run": None,
            "notes": "NOT COMPUTED — no bets/settle path",
        },
        {
            "id": "scorecard_fund_ats",
            "label": "Fundamental ATS ≥ 51.5%",
            "value": "MISSED",
            "unit": "none",
            "ci_lower": None,
            "ci_upper": None,
            "ci_kind": "none",
            "n": None,
            "regime": "snapshots + 2019",
            "vintage": "W9G_REGRADE",
            "run": "fundamental",
            "notes": (
                "Snapshots 48.9% [47.5%, 50.5%] (n=3496); 2019 49.9% "
                "[46.9%, 52.3%] (n=553) — neither CI clears 51.5%"
            ),
        },
        {
            "id": "scorecard_fund_ou",
            "label": "Fundamental OU ≥ 51.5%",
            "value": "MISSED",
            "unit": "none",
            "ci_lower": None,
            "ci_upper": None,
            "ci_kind": "none",
            "n": None,
            "regime": "snapshots + 2019",
            "vintage": "W9A_REVAL",
            "run": "fundamental",
            "notes": (
                "MISSED / uninterpretable — Snapshots 51.5% [49.7%, 53.5%] "
                "(CI includes 51.5%); 2019 51.4% — possessions structurally "
                "null outside partial 2023"
            ),
        },
        {
            "id": "scorecard_logloss",
            "label": "Brier / log-loss ≤ market baseline",
            "value": "MISSED",
            "unit": "none",
            "ci_lower": None,
            "ci_upper": None,
            "ci_kind": "none",
            "n": None,
            "regime": None,
            "vintage": "W9G_REGRADE",
            "run": None,
            "notes": "ATS LL 0.78–0.93 (fundamental) vs market 0.693",
        },
    ]
    artifact: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "published_at": ts,
        "source_memo": "docs/notes/23-reval.md",
        "ensemble_scope_label": DEFAULT_ENSEMBLE_SCOPE_LABEL,
        "vintage_labels": [TRACK_RECORD_VINTAGE_LABEL],
        "verdict": {
            "label": "NOT CURRENTLY FIT TO BET",
            "plain_language": (
                "Point-prediction machinery remains credible (weekly MAE curve still "
                "declines through mid-season, MAE/CRPS sane, A2 Clause A confirms in-season "
                "learning) but no edge vs the close is demonstrated (fundamental snapshot "
                "ATS 48.9% [47.5%, 50.5%]; 2019 49.9% [46.9%, 52.3%]; log-loss 0.78–0.93 vs "
                "0.693; CLV unmeasurable) and two §1.6 instruments remain unmeasurable "
                "(CLV; honest OU via possessions)."
            ),
        },
        "metrics": metrics,
    }
    if fixture:
        artifact["fixture"] = True
    return artifact


def build_team_ratings(
    *,
    season: int,
    published_at: datetime,
    filter_history: pd.DataFrame,
    teams: pd.DataFrame,
    fixture: bool = False,
) -> dict[str, Any]:
    school_by_id = {
        int(row.team_id): str(row.school)
        for row in teams.itertuples(index=False)
        if hasattr(row, "team_id") and hasattr(row, "school")
    }
    hist = filter_history[filter_history["season"] == season].copy()
    teams_out: dict[str, Any] = {}
    for team_id, group in hist.groupby("team_id"):
        tid = int(team_id)
        weeks = []
        for row in group.sort_values("week").itertuples(index=False):
            weeks.append(
                {
                    "week": int(row.week),
                    "as_of_utc": _iso_utc(getattr(row, "event_time", None)),
                    "off_epa": _optional_float(getattr(row, "off_epa", None)),
                    "def_epa": _optional_float(getattr(row, "def_epa", None)),
                    "pace": _optional_float(getattr(row, "pace", None)),
                    "off_sd": _optional_float(getattr(row, "sd_off_epa", None)),
                    "def_sd": _optional_float(getattr(row, "sd_def_epa", None)),
                }
            )
        teams_out[str(tid)] = {
            "school": school_by_id.get(tid, f"Team {tid}"),
            "weeks": weeks,
        }
    artifact: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "season": season,
        "published_at": _iso_utc(published_at),
        "teams": teams_out,
    }
    if fixture:
        artifact["fixture"] = True
    return cast(dict[str, Any], _json_safe(artifact))


def load_schedule_frame(
    *,
    season: int,
    week: int,
    config: AppConfig | None = None,
) -> pd.DataFrame:
    cfg = config or load_config()
    path = (
        Path(cfg.paths.staged_dir) / "games" / f"season={season}" / f"week={week}" / "part.parquet"
    )
    if not path.is_file():
        msg = f"schedule not found: {path}"
        raise FileNotFoundError(msg)
    return pd.read_parquet(path)


def load_teams_frame(*, season: int, config: AppConfig | None = None) -> pd.DataFrame:
    cfg = config or load_config()
    path = Path(cfg.paths.staged_dir) / "teams" / f"season={season}" / "part.parquet"
    if not path.is_file():
        msg = f"teams not found: {path}"
        raise FileNotFoundError(msg)
    return pd.read_parquet(path)


def schedule_lookup(
    games: pd.DataFrame,
    teams: pd.DataFrame,
) -> dict[str, dict[str, Any]]:
    school: dict[int, str] = {}
    if "team_id" in teams.columns:
        school = {int(r.team_id): str(r.school) for r in teams.itertuples(index=False)}
    out: dict[str, dict[str, Any]] = {}
    for row in games.itertuples(index=False):
        gid = str(int(row.game_id))
        home_id = int(row.home_team_id)
        away_id = int(row.away_team_id)
        kickoff = row.start_date if hasattr(row, "start_date") else row.event_time
        out[gid] = {
            "game_id": gid,
            "week": int(getattr(row, "week", 0)),
            "home_team_id": home_id,
            "away_team_id": away_id,
            "home_team": school.get(home_id, f"Team {home_id}"),
            "away_team": school.get(away_id, f"Team {away_id}"),
            "kickoff_utc": kickoff,
            "start_date": kickoff,
            "neutral_site": bool(row.neutral_site),
            "conference_game": bool(row.conference_game),
            "home_points": getattr(row, "home_points", None),
            "away_points": getattr(row, "away_points", None),
            "completed": bool(getattr(row, "completed", False)),
        }
    return out


def assert_no_denylisted_fields(payload: Any, *, path: str = "") -> list[str]:
    """Return denylist key paths found in serialized tree."""
    hits: list[str] = []
    if isinstance(payload, Mapping):
        for key, val in payload.items():
            key_str = str(key)
            child_path = f"{path}.{key_str}" if path else key_str
            if key_str in ODDS_FIELD_DENYLIST:
                hits.append(child_path)
            hits.extend(assert_no_denylisted_fields(val, path=child_path))
    elif isinstance(payload, list):
        for idx, item in enumerate(payload):
            hits.extend(assert_no_denylisted_fields(item, path=f"{path}[{idx}]"))
    return hits


def tier_distribution(games: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = {"strong_lean": 0, "clear_lean": 0, "lean": 0, "toss_up": 0, "suppressed": 0}
    for game in games:
        tier = game.get("conviction_tier")
        if tier == "strong_lean":
            counts["strong_lean"] += 1
        elif tier == "clear_lean":
            counts["clear_lean"] += 1
        elif tier == "lean":
            counts["lean"] += 1
        elif tier == "toss_up":
            counts["toss_up"] += 1
        else:
            counts["suppressed"] += 1
    total = len(games)
    pct = {k: (100.0 * v / total if total else 0.0) for k, v in counts.items()}
    strong_frac = counts["strong_lean"] / total if total else 0.0
    toss_up_frac = counts["toss_up"] / total if total else 0.0
    degeneracy = strong_frac > 0.5 or toss_up_frac < 0.05
    return {
        "counts": counts,
        "percentages": pct,
        "total": total,
        "degeneracy_flag": degeneracy,
        "degeneracy_notes": (
            "Strong >50% of slate or Toss-up near-empty — §2 boundary review recommended"
            if degeneracy
            else "Within expected tier spread"
        ),
    }


def export_publish_artifacts(
    publish_result: Mapping[str, Any],
    *,
    config: AppConfig | None = None,
    published_at: datetime | None = None,
    schedule_by_game: Mapping[str, Mapping[str, Any]] | None = None,
    filter_history: pd.DataFrame | None = None,
    push: bool = False,
    notifier: Any | None = None,
) -> dict[str, Any]:
    """Build Ridge artifacts from a predict_publish result payload."""
    cfg = config or load_config()
    season = int(publish_result["season"])
    week = int(publish_result["week"])
    refresh_kind = str(publish_result["refresh_kind"])
    clock = published_at or datetime.now(tz=UTC)

    stamped_rows = list(publish_result.get("predictions") or [])
    production_rows = list(publish_result.get("prediction_rows") or [])
    merged_rows: list[dict[str, Any]] = []
    for idx, stamped in enumerate(stamped_rows):
        prod = production_rows[idx] if idx < len(production_rows) else None
        merged_rows.append(merge_prediction_rows(stamped, prod))

    if schedule_by_game is None:
        games_df = load_schedule_frame(season=season, week=week, config=cfg)
        teams_df = load_teams_frame(season=season, config=cfg)
        schedule_by_game = schedule_lookup(games_df, teams_df)

    stale_ctx = publish_result.get("stale") or {}
    if stale_ctx.get("is_stale"):
        for row in merged_rows:
            row.setdefault("is_stale", True)
            row.setdefault("stale_stamp", stale_ctx.get("combined_stamp"))
            row.setdefault("stale_sources", stale_ctx.get("sources") or [])

    identity = _model_identity_from_rows(production_rows)
    week_preds = build_week_predictions(
        season=season,
        week=week,
        refresh_kind=refresh_kind,
        published_at=clock,
        prediction_rows=merged_rows,
        schedule_by_game=schedule_by_game,
        stale_context=stale_ctx,
        model_identity=identity,
        tier_store=TierStateStore(Path(cfg.webapp.tier_state_path)),
        stale_max_age_hours=float(cfg.pipeline.stale_odds_max_age_hours),
        tier_changes_path=Path(cfg.webapp.tier_changes_path),
        record_tier_changes=True,
    )
    meta = build_meta(
        season=season,
        week=week,
        refresh_kind=refresh_kind,
        published_at=clock,
    )
    track = build_track_record(published_at=clock)

    team_ratings: dict[str, Any] | None = None
    if filter_history is not None:
        teams_df = load_teams_frame(season=season, config=cfg)
        team_ratings = build_team_ratings(
            season=season,
            published_at=clock,
            filter_history=filter_history,
            teams=teams_df,
        )

    artifacts: dict[str, str] = {
        "week_predictions.json": json.dumps(week_preds, indent=2, sort_keys=True) + "\n",
        "track_record.json": json.dumps(track, indent=2, sort_keys=True) + "\n",
        f"team_ratings_{season}.json": json.dumps(
            team_ratings
            or {
                "schema_version": SCHEMA_VERSION,
                "season": season,
                "published_at": _iso_utc(clock),
                "teams": {},
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    }
    meta["artifact_pointers"]["team_ratings"] = f"latest/team_ratings_{season}.json"
    artifacts["meta.json"] = json.dumps(meta, indent=2, sort_keys=True) + "\n"

    push_result: dict[str, Any] | None = None
    if push and cfg.webapp.export_enabled:
        from ncaa_quant.webapp.push import push_artifacts_to_r2

        push_result = push_artifacts_to_r2(
            artifacts,
            season=season,
            week=week,
            refresh_kind=refresh_kind,
            schema_version=SCHEMA_VERSION,
            config=cfg,
            notifier=notifier,
        )

    return {
        "artifacts": artifacts,
        "week_predictions": week_preds,
        "meta": meta,
        "track_record": track,
        "team_ratings": team_ratings,
        "tier_distribution": tier_distribution(week_preds.get("games") or []),
        "push": push_result,
    }


def generate_fixture_week_artifacts(
    *,
    season: int = 2024,
    week: int = 5,
    config: AppConfig | None = None,
    output_dir: Path | str | None = None,
    walkforward_path: Path | str | None = None,
) -> dict[str, Any]:
    """Build labeled fixture artifacts from real 2024 week-5 walkforward outputs."""
    cfg = config or load_config()
    out_dir = Path(output_dir or cfg.webapp.fixture_artifacts_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    wf_path = Path(walkforward_path or FIXTURE_WALKFORWARD_PATH)
    if not wf_path.is_file():
        msg = f"walkforward fixture source missing: {wf_path}"
        raise FileNotFoundError(msg)

    wf = pd.read_parquet(wf_path)
    games_df = load_schedule_frame(season=season, week=week, config=cfg)
    teams_df = load_teams_frame(season=season, config=cfg)
    sched = schedule_lookup(games_df, teams_df)
    for _, wf_row in wf.iterrows():
        gid = str(int(wf_row["game_id"]))
        if gid in sched:
            sched[gid]["home_points"] = wf_row.get("home_points")
            sched[gid]["away_points"] = wf_row.get("away_points")
            sched[gid]["completed"] = (
                _optional_float(wf_row.get("home_points")) is not None
                and _optional_float(wf_row.get("away_points")) is not None
            )

    filter_path = Path(cfg.paths.data_dir) / "artifacts" / "state_space" / "filter_history.parquet"
    filter_history = pd.read_parquet(filter_path) if filter_path.is_file() else pd.DataFrame()

    published_at = FIXTURE_WEEK5_AS_OF
    prediction_rows: list[dict[str, Any]] = wf.to_dict(orient="records")
    model_version = str(wf["model_version"].iloc[0])
    run_id = str(wf["run_id"].iloc[0])

    tier_store = TierStateStore(Path(cfg.webapp.tier_state_path))
    week_preds = build_week_predictions(
        season=season,
        week=week,
        refresh_kind=RefreshKind.TUESDAY_PRIMARY,
        published_at=published_at,
        prediction_rows=prediction_rows,
        schedule_by_game=sched,
        model_identity={
            "registry_name": "ncaa-quant",
            "champion_version": 2,
            "model_version": model_version,
            "run_id": run_id,
        },
        vintage_label=TRACK_RECORD_VINTAGE_LABEL,
        tier_store=tier_store,
        stale_max_age_hours=float(cfg.pipeline.stale_odds_max_age_hours),
        fixture=True,
    )
    meta = build_meta(
        season=season,
        week=week,
        refresh_kind=RefreshKind.TUESDAY_PRIMARY,
        published_at=published_at,
        vintage_label=TRACK_RECORD_VINTAGE_LABEL,
        fixture=True,
    )
    meta["champion_model"] = {
        "registry_name": "ncaa-quant",
        "champion_version": 2,
        "model_version": model_version,
        "registered_at": "2026-08-17T20:41:49Z",
    }
    track = build_track_record(published_at=published_at, fixture=True)
    ratings = build_team_ratings(
        season=season,
        published_at=published_at,
        filter_history=filter_history,
        teams=teams_df,
        fixture=True,
    )

    from ncaa_quant.webapp.grade import build_results_season

    results = build_results_season(
        season=season,
        published_at=published_at,
        completed_games=games_df,
        schedule_by_game=sched,
        publish_history=[week_preds],
        fixture=True,
        allow_historical_fixture=True,
    )

    files = {
        "week_predictions.json": week_preds,
        "meta.json": meta,
        "track_record.json": track,
        f"team_ratings_{season}.json": ratings,
        f"results_{season}.json": results,
    }
    for name, content in files.items():
        path = out_dir / name
        path.write_text(json.dumps(content, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    dist = tier_distribution(week_preds.get("games") or [])
    return {"output_dir": str(out_dir), "tier_distribution": dist, "artifacts": files}
