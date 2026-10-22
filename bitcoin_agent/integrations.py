"""One optional background coordinator; market/risk ticks do not wait for delivery."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .adviser import FixtureAdviser, OpenAIAdviser, ShadowAdviser
from .config import ConfigError, _reject_constant, _unique_object
from .notifications import (DemoInbox, GmailSender, TelegramSender, dispatch, initialize_outbox,
                            outbox_status, queue_fills, queue_weekly, render_inbox)
from .paper_store import connection, event
from .remote_config import ConfigManager, GoogleSheetRows, LocalRows


def load_integration_settings(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    expected = {"schema_version", "delivery_mode", "config_source", "local_rows_path", "cache_path", "inbox_path", "adviser_provider"}
    if not isinstance(data, dict) or set(data) != expected or type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ConfigError("Invalid integration settings schema")
    for key, choices in {"delivery_mode": {"demo", "live"}, "config_source": {"local", "google", "none"}, "adviser_provider": {"fixture", "openai", "none"}}.items():
        if not isinstance(data[key], str) or data[key] not in choices:
            raise ConfigError(f"Invalid {key}")
    for key in ("local_rows_path", "cache_path", "inbox_path"):
        if not isinstance(data[key], str) or not data[key]:
            raise ConfigError(f"Invalid {key}")
    return data


class IntegrationCoordinator:
    def __init__(self, path, baseline, settings):
        self.path, self.baseline, self.settings = path, baseline, settings
        enabled = baseline.data["integrations"]
        source = None
        if enabled["google_sheet_enabled"]:
            source = LocalRows(settings["local_rows_path"]) if settings["config_source"] == "local" else GoogleSheetRows() if settings["config_source"] == "google" else None
        self.manager = ConfigManager(baseline, settings["cache_path"], source)
        initialize_outbox(path)
        demo = DemoInbox(settings["inbox_path"])
        self.senders = {}
        if enabled["telegram_enabled"]:
            self.senders["telegram"] = demo if settings["delivery_mode"] == "demo" else TelegramSender()
        if enabled["gmail_enabled"]:
            self.senders["gmail"] = demo if settings["delivery_mode"] == "demo" else GmailSender()
        self.adviser = None
        if enabled["llm_enabled"] and settings["adviser_provider"] != "none":
            provider = FixtureAdviser() if settings["adviser_provider"] == "fixture" else OpenAIAdviser()
            self.adviser = ShadowAdviser(path, provider)
        self.next_config_attempt = None

    def poll(self, features=None, portfolio=None, now=None):
        now = now or datetime.now(timezone.utc)
        config_result = None
        if self.manager.due(now) and (self.next_config_attempt is None or now >= self.next_config_attempt):
            from datetime import timedelta
            config_result = self.manager.refresh(now)
            # Failures do not cause a new remote request every five seconds.
            self.next_config_attempt = now + timedelta(seconds=self.baseline.data["integrations"]["config_refresh_seconds"])
            with connection(self.path, write=True) as database:
                event(database, now, "integration_config_refresh", config_result)
        config = self.manager.current
        if "telegram" in self.senders:
            queue_fills(self.path, now)
        if "gmail" in self.senders:
            queue_weekly(self.path, config, now)
        delivery = dispatch(self.path, self.senders, now, limit=2)
        advice = self.adviser.observe(config, features, portfolio, now) if self.adviser and portfolio else None
        return {"config": config, "config_refresh": config_result, "delivery": delivery, "advice": advice}

    def preview(self):
        return {"inbox": str(render_inbox(self.path, self.settings["inbox_path"])), "outbox": outbox_status(self.path),
                "delivery_mode": self.settings["delivery_mode"], "config_status": self.manager.status,
                "config_source": self.settings["config_source"], "config_checksum": self.manager.current.checksum,
                "llm_role": "shadow_only", "adviser_provider": self.settings["adviser_provider"]}
