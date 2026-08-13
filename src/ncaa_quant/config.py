"""Application configuration loader (OmegaConf YAML + pydantic-settings).

Precedence (lowest → highest): ``base.yaml`` < domain YAML
(``data`` / ``ratings`` / ``betting`` / ``pipeline``) < environment variables
< explicit CLI overrides passed to :func:`load_config`.

Secrets (``CFBD_API_KEY``, ``ODDS_API_KEY``) are loaded only from the
environment via :class:`SecretsSettings` and are never fields on
:class:`AppConfig`, so they cannot appear in a config dump.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from omegaconf import OmegaConf
from pydantic import BaseModel, Field, SecretStr
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

# Repo-root ``configs/`` relative to this file: src/ncaa_quant/config.py → ../../configs
_DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs"

_DOMAIN_FILES = ("data.yaml", "ratings.yaml", "betting.yaml", "pipeline.yaml")


class PathsConfig(BaseModel):
    """Filesystem layout under the project root."""

    data_dir: str = "data"
    raw_dir: str = "data/raw"
    staged_dir: str = "data/staged"
    features_dir: str = "data/features"
    predictions_dir: str = "data/predictions"
    mlruns_dir: str = "mlruns"


class DataConfig(BaseModel):
    """Ingestion and feature-construction parameters."""

    start_season: int = 2014
    end_season: int = 2025
    garbage_wp_low: float = 0.02
    garbage_wp_high: float = 0.98
    ewma_half_life_efficiency: float = 6.5
    ewma_half_life_tempo: float = 10.0
    ewma_half_life_explosiveness: float = 10.0
    shrinkage_k_efficiency: float = 8.0
    # Ridge λ for §4.3 opponent adjustment (Task 10). Untuned PLACEHOLDER.
    ridge_lambda_efficiency: float = 5.0
    cfbd_requests_per_second: float = 2.0
    odds_api_requests_per_second: float = 1.0
    # The Odds API client (Task 4).
    odds_books: list[str] = Field(
        default_factory=lambda: ["draftkings", "fanduel", "betmgm", "williamhill_us"]
    )
    odds_markets: list[str] = Field(default_factory=lambda: ["h2h", "spreads", "totals"])
    odds_regions: str = "us"
    odds_rate_limit_reserve: int = 50
    # Historical odds backfill (Task 5B). Changing decision points invalidates
    # backtest comparability with earlier runs.
    odds_historical_decision_points: list[str] = Field(
        default_factory=lambda: [
            "tuesday_0600_et",
            "saturday_0600_et",
            "slot_close",
        ]
    )
    odds_historical_credits_per_call: int = 30
    odds_historical_credit_ceiling: int = 60000
    odds_asof_tolerance_minutes_pre_2022_09: int = 10
    odds_asof_tolerance_minutes_post_2022_09: int = 5
    team_names_path: str = "configs/team_names.yaml"
    venues_overrides_path: str = "configs/venues_overrides.yaml"
    open_meteo_requests_per_second: float = 2.0


class RatingsConfig(BaseModel):
    """Stage-1 state-space rating parameters."""

    state_dims: list[str] = Field(
        default_factory=lambda: ["off_epa", "def_epa", "st_value", "pace"]
    )
    residual_winsor_sigma: float = 2.5
    process_noise_scale: float = 0.05
    obs_noise_scale: float = 0.15
    prior_regression_to_conf_mean: float = 0.30
    rating_posterior_draws: int = 50


class BettingConfig(BaseModel):
    """Edge filters and Kelly staking (DESIGN §12)."""

    min_edge_sides: float = 0.025
    min_edge_totals: float = 0.03
    kelly_fraction: float = 0.25
    max_stake_pct: float = 0.015
    bowl_edge_multiplier: float = 1.5
    max_bets_per_week: int = 10
    max_exposure_per_team: float = 0.05
    min_model_market_agreement: float = 7.0
    no_bet_on_stale: bool = True
    no_bet_on_qb_unknown: bool = True
    max_weekly_exposure: float = 0.10


class NotificationConfig(BaseModel):
    """Alert routing for Prefect flows (DESIGN §10).

    Provider tokens live in :class:`SecretsSettings`; only non-secret routing
    fields appear here.
    """

    provider: str = "null"
    """``null`` | ``ntfy`` | ``telegram`` — disabled when ``null`` or empty."""

    ntfy_server: str = "https://ntfy.sh"
    ntfy_topic: str = ""
    telegram_chat_id: str = ""


class WebappConfig(BaseModel):
    """Ridge public webapp export + R2 push (docs/webapp/DESIGN.md §3)."""

    export_enabled: bool = False
    """When False (default), predict_publish skips export/push.

    Preview scope: set ``NCAA_QUANT_WEBAPP__EXPORT_ENABLED=true`` only on the
    operator workstation used for private-preview publishes — never as a
    committed default, and never on machines that should not write R2.
    """

    r2_bucket: str = ""
    r2_endpoint_url: str = ""
    r2_public_base_url: str = ""
    """Unused in private preview (server-side R2). Kept for §3.3 public-read launch."""

    revalidate_url: str = ""
    """Vercel on-demand revalidation endpoint (e.g. https://….vercel.app/api/revalidate)."""

    tier_state_path: str = "data/webapp/tier_state.json"
    tier_changes_path: str = "data/webapp/tier_changes.jsonl"
    """Per-publish tier instrumentation (JSONL; workstation-only, not pushed to R2)."""

    fixture_artifacts_dir: str = "webapp/fixtures"


class PipelineConfig(BaseModel):
    """Schedules, promotion gates, and monitoring thresholds."""

    odds_snapshots_per_day: int = 6
    # 6×/day UTC (DESIGN §10); overridable.
    odds_ingest_cron: str = "0 0,4,8,12,16,20 * * *"
    postgame_ingest_cron_sat: str = "30 23 * * 6"
    postgame_ingest_cron_hourly: str = "0 0-3 * * 0"
    weekly_update_cron: str = "0 6 * * 0"
    predict_publish_cron_tuesday: str = "0 6 * * 2"
    predict_publish_cron_refresh: str = "0 6 * * 4-6"
    settle_clv_cron: str = "0 8 * * 0"
    retrain_gate_weeks: list[int] = Field(default_factory=lambda: [5, 10])
    ensemble_weight_dampen: float = 0.7
    feature_drift_psi_warn: float = 0.2
    feature_drift_psi_alarm: float = 0.3
    calibration_slope_low: float = 0.85
    calibration_slope_high: float = 1.15
    task_retries: int = 3
    weekly_pipeline_timeout_hours: float = 2.0
    # STALE mode: max age before odds are considered stale (DESIGN §10).
    stale_odds_max_age_hours: float = 6.0
    # Cadence shortfall: alert when snapshots < expected − tolerance within 24h.
    odds_cadence_tolerance: int = 1
    # Bet confirmation: void if market line moved more than this (§16 item 3).
    bet_line_move_void_points: float = 0.5
    notifications: NotificationConfig = Field(default_factory=NotificationConfig)
    idempotency_dir: str = "data/pipeline_state"
    dead_letter_dir: str = "data/pipeline_state/dead_letter"


class AppConfig(BaseSettings):
    """Typed root config. Prefer attribute access; never treat as a raw dict."""

    model_config = SettingsConfigDict(
        env_prefix="NCAA_QUANT_",
        env_nested_delimiter="__",
        extra="forbid",
    )

    seed: int = 42
    log_level: str = "INFO"
    paths: PathsConfig = Field(default_factory=PathsConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    ratings: RatingsConfig = Field(default_factory=RatingsConfig)
    betting: BettingConfig = Field(default_factory=BettingConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    webapp: WebappConfig = Field(default_factory=WebappConfig)


class SecretsSettings(BaseSettings):
    """API credentials from the environment / ``.env`` only — never from YAML."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    cfbd_api_key: SecretStr = Field(default=SecretStr(""), validation_alias="CFBD_API_KEY")
    odds_api_key: SecretStr = Field(default=SecretStr(""), validation_alias="ODDS_API_KEY")
    ntfy_auth_token: SecretStr = Field(default=SecretStr(""), validation_alias="NTFY_AUTH_TOKEN")
    telegram_bot_token: SecretStr = Field(
        default=SecretStr(""), validation_alias="TELEGRAM_BOT_TOKEN"
    )
    r2_access_key_id: SecretStr = Field(default=SecretStr(""), validation_alias="R2_ACCESS_KEY_ID")
    r2_secret_access_key: SecretStr = Field(
        default=SecretStr(""), validation_alias="R2_SECRET_ACCESS_KEY"
    )
    webapp_revalidate_secret: SecretStr = Field(
        default=SecretStr(""), validation_alias="WEBAPP_REVALIDATE_SECRET"
    )


class _YamlSettingsSource(PydanticBaseSettingsSource):
    """Inject merged OmegaConf YAML as a low-precedence settings source."""

    def __init__(self, settings_cls: type[BaseSettings], data: dict[str, Any]) -> None:
        super().__init__(settings_cls)
        self._data = data

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return self._data.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for field_name, field in self.settings_cls.model_fields.items():
            value, _key, _is_complex = self.get_field_value(field, field_name)
            if value is not None:
                result[field_name] = value
        return result


def _merge_yaml_dir(config_dir: Path) -> dict[str, Any]:
    base_path = config_dir / "base.yaml"
    if not base_path.is_file():
        msg = f"missing required config file: {base_path}"
        raise FileNotFoundError(msg)

    merged = OmegaConf.load(base_path)
    for name in _DOMAIN_FILES:
        path = config_dir / name
        if not path.is_file():
            msg = f"missing required config file: {path}"
            raise FileNotFoundError(msg)
        merged = OmegaConf.merge(merged, OmegaConf.load(path))

    container = OmegaConf.to_container(merged, resolve=True)
    if not isinstance(container, dict):
        msg = "merged config must be a mapping"
        raise TypeError(msg)
    return dict(container)


def load_config(
    config_dir: Path | str | None = None,
    overrides: dict[str, Any] | None = None,
) -> AppConfig:
    """Load layered YAML, then apply env vars, then ``overrides`` (CLI).

    Parameters
    ----------
    config_dir:
        Directory containing ``base.yaml`` and domain files. Defaults to the
        repository ``configs/`` directory.
    overrides:
        Explicit key/value overrides (highest precedence). Nested keys may be
        passed as nested dicts matching :class:`AppConfig` fields.
    """
    directory = Path(config_dir) if config_dir is not None else _DEFAULT_CONFIG_DIR
    yaml_data = _merge_yaml_dir(directory)
    cli_overrides = overrides or {}

    class _ConfiguredAppConfig(AppConfig):
        @classmethod
        def settings_customise_sources(
            cls,
            settings_cls: type[BaseSettings],
            init_settings: PydanticBaseSettingsSource,
            env_settings: PydanticBaseSettingsSource,
            dotenv_settings: PydanticBaseSettingsSource,
            file_secret_settings: PydanticBaseSettingsSource,
        ) -> tuple[PydanticBaseSettingsSource, ...]:
            # First source wins: CLI/init > env > dotenv > YAML.
            return (
                init_settings,
                env_settings,
                dotenv_settings,
                _YamlSettingsSource(settings_cls, yaml_data),
                file_secret_settings,
            )

    return _ConfiguredAppConfig(**cli_overrides)


def dump_config(config: AppConfig) -> dict[str, Any]:
    """Serialize config for logging/MLflow. Secrets are not part of AppConfig."""
    return config.model_dump(mode="json")


def load_secrets() -> SecretsSettings:
    """Load API keys from the environment (and optional ``.env``)."""
    return SecretsSettings()
