"""Seed nonsecret demo controls and workbook source from the local paper baseline."""

import json
from pathlib import Path

from bitcoin_agent.config import load_config, validate_config
from bitcoin_agent.remote_config import control_rows

root = Path(__file__).resolve().parents[1]
paper = load_config(root / "config/paper.json")
data = paper.data
for key in ("google_sheet_enabled", "telegram_enabled", "gmail_enabled", "llm_enabled"):
    data["integrations"][key] = True
demo = validate_config(data)
(root / "config/demo.json").write_text(json.dumps(demo.data, indent=2), encoding="utf-8")
settings = {"schema_version": 1, "delivery_mode": "demo", "config_source": "local",
            "local_rows_path": "config/sheet-rows.json", "cache_path": "var/integrations/config-cache.json",
            "inbox_path": "reports/milestone-4/inbox", "adviser_provider": "fixture"}
(root / "config/integrations.demo.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")
live = {**settings, "delivery_mode": "live", "config_source": "google", "adviser_provider": "openai",
        "cache_path": "var/integrations/live-config-cache.json", "inbox_path": "var/integrations/live-preview"}
(root / "config/integrations.live.example.json").write_text(json.dumps(live, indent=2), encoding="utf-8")
rows = control_rows(demo)
(root / "config/sheet-rows.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
output = root / "var/artifact-build"
output.mkdir(parents=True, exist_ok=True)
(output / "sheet-source.json").write_text(json.dumps({"rows": rows, "baseline_checksum": paper.checksum,
    "locked": [["Maximum BTC exposure", .60], ["Minimum cash", .20], ["Lifetime loss trigger", .30],
               ["Drawdown exit trigger", .25], ["Crash drop trigger", .15]],
    "notes": ["Edit Value cells only. Fractions use decimals: 0.03 means 3%.",
              "Allocation arrays list DCA, swing and cash. Example: [0.5, 0.3, 0.2].",
              "Validated hourly when runtime Google OAuth is configured.",
              "Demo starts from local controls. Google connection in Codex does not authorize the Python runtime.",
              "Funding and risk limits remain local. Existing stops cannot be widened by Sheet edits.",
              "All execution is simulated. The LLM records shadow advice only."]}, indent=2), encoding="utf-8")
print(json.dumps({"controls": len(rows)-1, "demo_checksum": demo.checksum, "funding_preserved": demo.funding_checksum == paper.funding_checksum}))
