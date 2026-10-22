"""Non-secret Sheet controls, full candidate validation and atomic local cache."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from .config import ConfigError, _reject_constant, _unique_object, validate_config
from .integration_io import http_json, oauth_token, provider_id, require_env
from .paper_types import utc

# Everything absent here (including funding, credentials and all core risk rules)
# is owned by the local baseline. Remote input is data, never executable code.
CONTROLS = {
    "dca.enabled": "Enable new scheduled/dip paper buys",
    "dca.schedule_hours": "Hours between scheduled buys",
    "dca.cooldown_hours": "Minimum hours between dip buys",
    "dca.dip_trigger_fraction": "Drop from the last filled DCA price (0.03 = 3%)",
    "dca.tranche_fraction_of_dca_budget": "Budget fraction per buy; cannot exceed the local limit",
    "dca.max_spend_fraction_per_7_days": "Rolling seven-day cap; cannot exceed the local limit",
    "swing.enabled": "Enable new swing entries; existing protection stays active",
    "swing.atr_multiplier": "Initial stop distance in ATR; applies to new entries",
    "swing.take_profit_r_multiple": "Net profit target in units of initial risk",
    "swing.relative_volume_threshold": "Volume confirmation for a new swing entry",
    "swing.risk_fraction_of_equity": "Risk per swing entry; cannot exceed the local limit",
    "swing.max_holding_hours": "Maximum holding time; cannot exceed the local limit",
    "operational.bull_targets": "DCA, swing, cash fractions; sum to 1",
    "operational.mixed_targets": "DCA, swing, cash fractions; sum to 1",
    "operational.bear_targets": "DCA, swing, cash fractions; local baseline requires cash",
    "operational.unknown_targets": "DCA, swing, cash fractions; local baseline requires cash",
}


def control_rows(config):
    data = config.data
    rows = [["Setting", "Value", "Meaning"]]
    for key, meaning in CONTROLS.items():
        section, field = key.split(".")
        value = data[section][field]
        rows.append([key, json.dumps(value) if isinstance(value, list) else value, meaning])
    return rows


def parse_rows(rows):
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], list) or rows[0][:2] != ["Setting", "Value"] or len(rows) > 60:
        raise ConfigError("Sheet requires Setting and Value headers and at most 60 rows")
    patch = {}
    for row in rows[1:]:
        if not isinstance(row, list):
            raise ConfigError("Invalid Sheet row")
        if not row or all(value in ("", None) for value in row):
            continue
        if len(row) < 2 or len(row) > 3 or not isinstance(row[0], str) or row[0] not in CONTROLS or row[0] in patch:
            raise ConfigError("Unknown, duplicate or incomplete Sheet setting")
        value = row[1]
        if isinstance(value, str):
            if len(value) > 200:
                raise ConfigError("Sheet value too long")
            literal = value.lower() if value.upper() in {"TRUE", "FALSE"} else value
            try:
                value = json.loads(literal, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
            except json.JSONDecodeError as exc:
                raise ConfigError("Sheet values must be JSON numbers, booleans or arrays") from exc
        patch[row[0]] = value
    if set(patch) != set(CONTROLS):
        raise ConfigError("Sheet must contain every supported setting")
    return patch


def candidate_from_patch(baseline, patch):
    if not isinstance(patch, dict) or set(patch) != set(CONTROLS):
        raise ConfigError("Remote patch must contain exactly the supported controls")
    data, original = baseline.data, baseline.data
    for key, value in patch.items():
        section, field = key.split(".")
        # Google may return 2 for a locally declared 2.0. Preserve baseline
        # numeric representation so a semantically unchanged Sheet is stable.
        if type(original[section][field]) is float and type(value) is int:
            value = float(value)
        data[section][field] = value
    candidate = validate_config(data)
    # Keep caps anchored to the owner's local baseline across repeated edits.
    for section, field in (("dca", "tranche_fraction_of_dca_budget"), ("dca", "max_spend_fraction_per_7_days"),
                           ("swing", "risk_fraction_of_equity"), ("swing", "max_holding_hours")):
        if data[section][field] > original[section][field]:
            raise ConfigError(f"Remote setting exceeds local bound: {section}.{field}")
    for key in ("bear_targets", "unknown_targets"):
        if data["operational"][key] != original["operational"][key]:
            raise ConfigError(f"Remote setting cannot alter protected {key}")
    if candidate.funding_checksum != baseline.funding_checksum:
        raise ConfigError("Remote configuration cannot change funding")
    return candidate


def atomic_json(path, value):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps(value, indent=2, allow_nan=False))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, target)


class LocalRows:
    def __init__(self, path):
        self.path = Path(path)

    def fetch(self):
        return json.loads(self.path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)


class GoogleSheetRows:
    def fetch(self):
        identity = provider_id(require_env("GOOGLE_SHEET_ID"), "Sheet ID")
        cell_range = quote("'Settings'!A5:C64", safe="")
        result = http_json(f"https://sheets.googleapis.com/v4/spreadsheets/{identity}/values/{cell_range}?valueRenderOption=UNFORMATTED_VALUE",
                           headers={"Authorization": "Bearer " + oauth_token("GOOGLE")})
        return result.get("values", [])


class ConfigManager:
    """Last validated cache wins after a remote error; local baseline seeds first run."""
    def __init__(self, baseline, cache, source=None):
        self.baseline, self.cache, self.source = baseline, Path(cache), source
        self.current = baseline
        self.last_success = None
        self.status = "local_baseline"
        if self.cache.is_file():
            try:
                payload = json.loads(self.cache.read_text(encoding="utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
                if payload["baseline_checksum"] != baseline.checksum:
                    raise ConfigError("Cache belongs to another baseline")
                candidate = candidate_from_patch(baseline, payload["patch"])
                if candidate.checksum != payload["candidate_checksum"]:
                    raise ConfigError("Cache checksum mismatch")
                self.current, self.last_success = candidate, utc(payload["fetched_at"])
                self.status = "validated_cache"
            except (OSError, ValueError, KeyError, TypeError):
                self.status = "invalid_cache_using_local_baseline"

    def due(self, now):
        return self.last_success is None or (utc(now) - self.last_success).total_seconds() >= self.baseline.data["integrations"]["config_refresh_seconds"]

    def refresh(self, now=None):
        now = utc(now or datetime.now(timezone.utc))
        if self.source is None:
            return {"status": self.status, "checksum": self.current.checksum, "changed": False}
        try:
            patch = parse_rows(self.source.fetch())
            candidate = candidate_from_patch(self.baseline, patch)
            atomic_json(self.cache, {"baseline_checksum": self.baseline.checksum, "candidate_checksum": candidate.checksum,
                                     "fetched_at": now.isoformat(), "patch": patch})
            changed = candidate.checksum != self.current.checksum
            self.current, self.last_success, self.status = candidate, now, "source_validated"
            return {"status": self.status, "source": type(self.source).__name__, "checksum": candidate.checksum, "changed": changed}
        except (OSError, ValueError, KeyError, TypeError):
            # Do not copy remote contents or raw exception strings into telemetry.
            self.status = "remote_rejected_using_last_valid"
            return {"status": self.status, "checksum": self.current.checksum, "changed": False}
