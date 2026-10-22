"""Strict validation for non-secret application configuration.

Foundation commands validate these settings and initialize simulated cash.
Offline research also uses them for costs and risk; no live broker is implemented.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Configuration cannot be accepted."""


SECTION_FIELDS = {
    "market": {"provider", "product_id", "candle_seconds", "poll_seconds", "stale_after_seconds"},
    "portfolio": {"initial_cash_usd", "dca_fraction", "swing_fraction", "reserve_fraction"},
    "dca": {"enabled", "schedule_hours", "tranche_fraction_of_dca_budget", "dip_trigger_fraction", "dip_reference", "cooldown_hours", "max_spend_fraction_per_7_days"},
    "swing": {"enabled", "atr_period", "atr_multiplier", "ema_fast_period", "ema_slow_period", "breakout_lookback", "relative_volume_lookback", "relative_volume_threshold", "risk_fraction_of_equity", "max_notional_fraction_of_swing_budget", "max_open_positions", "take_profit_r_multiple", "max_holding_hours"},
    "risk": {"max_portfolio_drawdown_fraction", "stop_monitor_seconds", "fee_fraction_per_fill", "slippage_fraction_per_fill"},
    "integrations": {"config_refresh_seconds", "google_sheet_enabled", "telegram_enabled", "gmail_enabled", "llm_enabled"},
    "reporting": {"weekly_day", "weekly_time", "timezone"},
}

OPERATIONAL_FIELDS = {
    "allocation_mode", "bear_exit_enabled", "drawdown_action", "max_btc_exposure_fraction",
    "exposure_buffer_fraction", "minimum_cash_fraction", "max_lifetime_loss_fraction", "crash_drop_fraction",
    "crash_window_hours", "resume_mode", "resume_cooldown_hours", "recovery_days", "feature_stale_after_seconds",
    "max_target_atr_multiple", "maximum_spread_fraction", "paper_fill_cap_usd", "order_timeout_seconds", "allocation_update_hours",
    "trend_days", "momentum_days", "bear_confirmation_days", "high_volatility_atr_fraction",
    "bull_targets", "mixed_targets", "bear_targets", "unknown_targets",
}


def _check_keys(data: Any, expected: set[str], label: str) -> None:
    if not isinstance(data, dict):
        raise ConfigError(f"{label} must be an object")
    missing, extra = expected - data.keys(), data.keys() - expected
    if missing:
        raise ConfigError(f"{label}: missing fields: {', '.join(sorted(missing))}")
    if extra:
        raise ConfigError(f"{label}: unknown fields: {', '.join(sorted(extra))}")


def _number(value: Any, label: str, *, minimum: str = "0", maximum: str | None = None,
            allow_string: bool = False, positive: bool = False) -> Decimal:
    allowed = (int, float, str) if allow_string else (int, float)
    if isinstance(value, bool) or not isinstance(value, allowed):
        raise ConfigError(f"{label} must be a number")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ConfigError(f"{label} must be a finite number") from exc
    if not result.is_finite():
        raise ConfigError(f"{label} must be a finite number")
    if result < Decimal(minimum) or (positive and result <= 0):
        raise ConfigError(f"{label} is below its allowed minimum")
    if maximum is not None and result > Decimal(maximum):
        raise ConfigError(f"{label} exceeds {maximum}")
    return result


def _integer(value: Any, label: str, minimum: int = 1) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigError(f"{label} must be an integer >= {minimum}")


def _boolean(value: Any, label: str) -> None:
    if not isinstance(value, bool):
        raise ConfigError(f"{label} must be true or false")


def _choice(value: Any, choices: set[str], label: str) -> None:
    if not isinstance(value, str) or value not in choices:
        raise ConfigError(f"{label} must be one of: {', '.join(sorted(choices))}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError(f"Duplicate configuration field: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ConfigError(f"Invalid JSON numeric constant: {value}")


@dataclass(frozen=True)
class AgentConfig:
    """Validated configuration, stored canonically to prevent later mutation."""

    canonical_json: str

    @property
    def data(self) -> dict[str, Any]:
        return json.loads(self.canonical_json)

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.canonical_json.encode("utf-8")).hexdigest()

    @property
    def funding_checksum(self) -> str:
        funding = json.dumps(self.data["portfolio"], sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(funding.encode("utf-8")).hexdigest()

    @property
    def initial_cash(self) -> Decimal:
        return Decimal(str(self.data["portfolio"]["initial_cash_usd"]))

    def initial_allocations(self) -> dict[str, Decimal]:
        portfolio = self.data["portfolio"]
        cents = Decimal("0.01")
        dca = (self.initial_cash * Decimal(str(portfolio["dca_fraction"]))).quantize(cents, rounding=ROUND_DOWN)
        swing = (self.initial_cash * Decimal(str(portfolio["swing_fraction"]))).quantize(cents, rounding=ROUND_DOWN)
        # Reserve receives rounding residue, so cash is neither lost nor created.
        return {"dca": dca, "swing": swing, "reserve": self.initial_cash - dca - swing}


def validate_config(data: Any) -> AgentConfig:
    expected = set(SECTION_FIELDS) | {"schema_version", "execution_mode"}
    if isinstance(data, dict) and "operational" in data:
        expected.add("operational")
    _check_keys(data, expected, "config")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ConfigError("schema_version must be 1")
    _choice(data["execution_mode"], {"paper"}, "execution_mode")
    for section, fields in SECTION_FIELDS.items():
        _check_keys(data[section], fields, section)

    market = data["market"]
    _choice(market["provider"], {"coinbase"}, "market.provider")
    _choice(market["product_id"], {"BTC-USD"}, "market.product_id")
    _integer(market["candle_seconds"], "market.candle_seconds")
    if market["candle_seconds"] != 3600:
        raise ConfigError("The initial market specification uses 3600-second candles")
    for field in ("poll_seconds", "stale_after_seconds"):
        _integer(market[field], f"market.{field}")

    portfolio = data["portfolio"]
    cash = _number(portfolio["initial_cash_usd"], "portfolio.initial_cash_usd", minimum="1", allow_string=True)
    if cash != cash.quantize(Decimal("0.01")):
        raise ConfigError("portfolio.initial_cash_usd must have at most two decimal places")
    fractions = {
        field: _number(portfolio[field], f"portfolio.{field}", maximum="1")
        for field in ("dca_fraction", "swing_fraction", "reserve_fraction")
    }
    if sum(fractions.values(), Decimal("0")) != 1:
        raise ConfigError("Portfolio allocation fractions must sum to exactly 1")

    dca = data["dca"]
    _boolean(dca["enabled"], "dca.enabled")
    _choice(dca["dip_reference"], {"last_filled_dca_buy"}, "dca.dip_reference")
    for field in ("schedule_hours", "cooldown_hours"):
        _integer(dca[field], f"dca.{field}")
    for field in ("tranche_fraction_of_dca_budget", "dip_trigger_fraction", "max_spend_fraction_per_7_days"):
        _number(dca[field], f"dca.{field}", maximum="1", positive=True)
    if dca["tranche_fraction_of_dca_budget"] > dca["max_spend_fraction_per_7_days"]:
        raise ConfigError("A DCA tranche cannot exceed the seven-day spending cap")
    if dca["enabled"] and fractions["dca_fraction"] == 0:
        raise ConfigError("Enabled DCA requires a positive DCA allocation")

    swing = data["swing"]
    _boolean(swing["enabled"], "swing.enabled")
    for field in ("atr_period", "ema_fast_period", "ema_slow_period", "breakout_lookback", "relative_volume_lookback", "max_open_positions", "max_holding_hours"):
        _integer(swing[field], f"swing.{field}")
    if swing["ema_fast_period"] >= swing["ema_slow_period"]:
        raise ConfigError("The fast EMA period must be smaller than the slow EMA period")
    for field in ("atr_multiplier", "relative_volume_threshold", "take_profit_r_multiple"):
        _number(swing[field], f"swing.{field}", positive=True)
    for field in ("risk_fraction_of_equity", "max_notional_fraction_of_swing_budget"):
        _number(swing[field], f"swing.{field}", maximum="1", positive=True)
    if swing["max_open_positions"] != 1:
        raise ConfigError("The initial strategy supports one open swing position")
    if swing["enabled"] and fractions["swing_fraction"] == 0:
        raise ConfigError("Enabled swing trading requires a positive swing allocation")

    risk = data["risk"]
    _number(risk["max_portfolio_drawdown_fraction"], "risk.max_portfolio_drawdown_fraction", maximum="1", positive=True)
    _integer(risk["stop_monitor_seconds"], "risk.stop_monitor_seconds")
    for field in ("fee_fraction_per_fill", "slippage_fraction_per_fill"):
        _number(risk[field], f"risk.{field}", maximum="1")

    integrations = data["integrations"]
    _integer(integrations["config_refresh_seconds"], "integrations.config_refresh_seconds")
    for field in ("google_sheet_enabled", "telegram_enabled", "gmail_enabled", "llm_enabled"):
        _boolean(integrations[field], f"integrations.{field}")
        if integrations[field] and "operational" not in data:
            raise ConfigError(f"integrations.{field} requires the operational paper engine")

    reporting = data["reporting"]
    _choice(reporting["weekly_day"], {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}, "reporting.weekly_day")
    _choice(reporting["timezone"], {"UTC", "Asia/Riyadh"}, "reporting.timezone")
    if not isinstance(reporting["weekly_time"], str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", reporting["weekly_time"]):
        raise ConfigError("reporting.weekly_time must use 24-hour HH:MM")

    if "operational" in data:
        op = data["operational"]
        _check_keys(op, OPERATIONAL_FIELDS, "operational")
        _choice(op["allocation_mode"], {"fixed", "regime"}, "operational.allocation_mode")
        _choice(op["drawdown_action"], {"pause", "liquidate"}, "operational.drawdown_action")
        _choice(op["resume_mode"], {"manual", "confirmed_recovery"}, "operational.resume_mode")
        _boolean(op["bear_exit_enabled"], "operational.bear_exit_enabled")
        for field in ("max_btc_exposure_fraction", "minimum_cash_fraction", "max_lifetime_loss_fraction", "crash_drop_fraction", "maximum_spread_fraction", "high_volatility_atr_fraction"):
            value = _number(op[field], f"operational.{field}", positive=True, maximum="1")
            if value == 1:
                raise ConfigError(f"operational.{field} must be below 1")
        buffer = _number(op["exposure_buffer_fraction"], "operational.exposure_buffer_fraction", maximum="1")
        if Decimal(str(op["max_btc_exposure_fraction"])) + buffer >= 1:
            raise ConfigError("Exposure cap plus buffer must be below 1")
        if Decimal(str(op["max_btc_exposure_fraction"])) + Decimal(str(op["minimum_cash_fraction"])) > 1:
            raise ConfigError("BTC cap and minimum cash fractions cannot sum above 1")
        for field in ("crash_window_hours", "resume_cooldown_hours", "recovery_days", "feature_stale_after_seconds",
                      "order_timeout_seconds", "allocation_update_hours", "trend_days", "momentum_days", "bear_confirmation_days"):
            _integer(op[field], f"operational.{field}")
        _number(op["max_target_atr_multiple"], "operational.max_target_atr_multiple", positive=True)
        _number(op["paper_fill_cap_usd"], "operational.paper_fill_cap_usd", minimum="1", allow_string=True)
        for field in ("bull_targets", "mixed_targets", "bear_targets", "unknown_targets"):
            weights = op[field]
            if not isinstance(weights, list) or len(weights) != 3:
                raise ConfigError(f"operational.{field} requires three allocation fractions")
            fractions = [_number(value, f"operational.{field}", maximum="1") for value in weights]
            if sum(fractions, Decimal(0)) != 1 or fractions[2] < Decimal(str(op["minimum_cash_fraction"])):
                raise ConfigError(f"operational.{field} must sum to 1 and respect minimum cash")
        if risk["fee_fraction_per_fill"] >= 1 or risk["slippage_fraction_per_fill"] >= 1:
            raise ConfigError("Operational fee and slippage fractions must be below 1")

    return AgentConfig(json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False))


def load_config(path: str | Path) -> AgentConfig:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique_object,
                          parse_constant=_reject_constant)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Cannot read configuration: {exc}") from exc
    return validate_config(data)
