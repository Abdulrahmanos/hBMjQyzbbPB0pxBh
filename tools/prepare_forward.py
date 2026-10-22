"""Create independent, frozen local forward settings without resetting past demos."""

import json
from pathlib import Path
from bitcoin_agent.config import load_config, validate_config

root = Path(__file__).resolve().parents[1]
data = load_config(root / "config/paper.json").data
data["integrations"].update(telegram_enabled=True, gmail_enabled=True, google_sheet_enabled=False, llm_enabled=False)
config = validate_config(data)
settings = {"schema_version": 1, "delivery_mode": "demo", "config_source": "none", "adviser_provider": "none",
            "local_rows_path": "config/sheet-rows.json", "cache_path": "var/forward/config-cache.json", "inbox_path": "var/forward/inbox"}
for name, payload in (("forward.json", config.data), ("integrations.forward.json", settings)):
    target = root / "config" / name
    text = json.dumps(payload, indent=2) + "\n"
    if target.exists() and target.read_text(encoding="utf-8") != text:
        raise ValueError("Preserve existing forward settings; review changes before creating another experiment")
    target.write_text(text, encoding="utf-8")
print(json.dumps({"config_checksum": config.checksum, "initial_cash_usd": str(config.initial_cash), "funding_unchanged": True, "external_delivery": False}))
