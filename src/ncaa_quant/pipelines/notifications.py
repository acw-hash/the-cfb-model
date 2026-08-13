"""Flow notifications via ntfy or Telegram (DESIGN §10)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

import httpx

from ncaa_quant.config import AppConfig, NotificationConfig, load_config, load_secrets
from ncaa_quant.utils.logging import get_logger

log = get_logger(__name__)


class AlertKind(StrEnum):
    """Notification categories wired to §10 alarms."""

    FLOW_FAILURE = "flow_failure"
    QUALITY_GATE_FAILURE = "quality_gate_failure"
    RATING_INNOVATION = "rating_innovation"
    CADENCE_SHORTFALL = "cadence_shortfall"
    NEW_BET_CANDIDATE = "new_bet_candidate"
    CALIBRATION_ALARM = "calibration_alarm"
    CLV_WEEKLY_SUMMARY = "clv_weekly_summary"
    WEBAPP_EXPORT_FAILURE = "webapp_export_failure"


@dataclass(frozen=True, slots=True)
class Alert:
    """One outbound notification."""

    kind: AlertKind
    title: str
    body: str
    priority: int = 3


class Notifier(Protocol):
    """Send alerts; implementations must be safe to call when disabled."""

    def send(self, alert: Alert) -> bool:
        """Return True when the alert was dispatched."""
        ...


class NullNotifier:
    """No-op notifier used when provider is ``null`` or unconfigured."""

    def send(self, alert: Alert) -> bool:
        log.info(
            "notification_suppressed",
            kind=alert.kind,
            title=alert.title,
            body=alert.body[:200],
        )
        return False


class RecordingNotifier:
    """Test double that records all alerts in memory."""

    def __init__(self) -> None:
        self.sent: list[Alert] = []

    def send(self, alert: Alert) -> bool:
        self.sent.append(alert)
        return True


class NtfyNotifier:
    """Post to an ntfy.sh topic (or self-hosted server)."""

    def __init__(
        self,
        *,
        server: str,
        topic: str,
        auth_token: str = "",
        client: httpx.Client | None = None,
    ) -> None:
        if not topic:
            msg = "ntfy topic is required"
            raise ValueError(msg)
        self._server = server.rstrip("/")
        self._topic = topic
        self._auth_token = auth_token
        self._client = client or httpx.Client(timeout=10.0)

    def send(self, alert: Alert) -> bool:
        url = f"{self._server}/{self._topic}"
        headers = {
            "Title": alert.title[:250],
            "Priority": str(alert.priority),
            "Tags": alert.kind,
        }
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"
        resp = self._client.post(url, content=alert.body.encode("utf-8"), headers=headers)
        resp.raise_for_status()
        log.info("ntfy_sent", kind=alert.kind, topic=self._topic)
        return True


class TelegramNotifier:
    """Send via Telegram Bot API."""

    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: str,
        client: httpx.Client | None = None,
    ) -> None:
        if not bot_token or not chat_id:
            msg = "telegram bot_token and chat_id are required"
            raise ValueError(msg)
        self._token = bot_token
        self._chat_id = chat_id
        self._client = client or httpx.Client(timeout=10.0)

    def send(self, alert: Alert) -> bool:
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        text = f"*{alert.title}*\n[{alert.kind}]\n{alert.body}"
        payload = {
            "chat_id": self._chat_id,
            "text": text[:4000],
            "parse_mode": "Markdown",
        }
        resp = self._client.post(url, json=payload)
        resp.raise_for_status()
        log.info("telegram_sent", kind=alert.kind, chat_id=self._chat_id)
        return True


def build_notifier(
    config: AppConfig | None = None,
    *,
    override: Notifier | None = None,
) -> Notifier:
    """Construct the configured notifier (``NullNotifier`` when disabled)."""
    if override is not None:
        return override
    cfg = config or load_config()
    notif = cfg.pipeline.notifications
    provider = notif.provider.strip().lower()
    if provider in ("", "null", "none", "disabled"):
        return NullNotifier()
    secrets = load_secrets()
    if provider == "ntfy":
        return NtfyNotifier(
            server=notif.ntfy_server,
            topic=notif.ntfy_topic,
            auth_token=secrets.ntfy_auth_token.get_secret_value(),
        )
    if provider == "telegram":
        return TelegramNotifier(
            bot_token=secrets.telegram_bot_token.get_secret_value(),
            chat_id=notif.telegram_chat_id,
        )
    msg = f"unknown notification provider: {provider!r}"
    raise ValueError(msg)


def notify(
    kind: AlertKind,
    title: str,
    body: str,
    *,
    config: AppConfig | None = None,
    notifier: Notifier | None = None,
    priority: int = 3,
) -> bool:
    """Send one alert through the configured notifier."""
    n = notifier or build_notifier(config)
    return n.send(Alert(kind=kind, title=title, body=body, priority=priority))


def notification_config_snapshot(notif: NotificationConfig) -> dict[str, Any]:
    """Non-secret notification config for logging."""
    return {
        "provider": notif.provider,
        "ntfy_server": notif.ntfy_server,
        "ntfy_topic_set": bool(notif.ntfy_topic),
        "telegram_chat_id_set": bool(notif.telegram_chat_id),
    }
