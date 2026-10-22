"""Validated market observations and decimal trade arithmetic."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_DOWN

ZERO = Decimal(0)
ONE = Decimal(1)
CENT = Decimal("0.01")


def decimal(value) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("Boolean cannot be a financial amount")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Invalid financial amount") from exc
    if not amount.is_finite():
        raise ValueError("Financial amounts must be finite")
    return amount


def floor_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def ceil_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step


def utc(value: str | datetime) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if result.tzinfo is None:
        raise ValueError("Observation timestamps require a timezone")
    return result.astimezone(timezone.utc)


@dataclass(frozen=True)
class Quote:
    quote_id: str
    market_time: datetime
    received_at: datetime
    bid: Decimal
    ask: Decimal
    price: Decimal

    @classmethod
    def from_dict(cls, value: dict):
        if not isinstance(value, dict) or {"time", "received_at", "bid", "ask", "price"} - value.keys():
            raise ValueError("Quote is missing required fields")
        if not value.get("quote_id") and "trade_id" not in value:
            raise ValueError("Quote requires an observation ID or trade ID")
        market_time, received = utc(value["time"]), utc(value["received_at"])
        bid, ask, price = (decimal(value[field]) for field in ("bid", "ask", "price"))
        if min(bid, ask, price) <= 0 or bid > ask:
            raise ValueError("Quote prices must be positive and bid cannot exceed ask")
        identity = value.get("quote_id") or hashlib.sha256(json.dumps(
            {key: value[key] for key in ("time", "received_at", "bid", "ask", "price", "trade_id")}, sort_keys=True).encode()).hexdigest()
        return cls(str(identity), market_time, received, bid, ask, price)

    def check_fresh(self, now: datetime, seconds: int):
        for timestamp in (self.market_time, self.received_at):
            age = (utc(now) - timestamp).total_seconds()
            if age < -5 or age > seconds:
                raise ValueError("Quote is stale or from the future")

    def to_dict(self):
        return {"quote_id": self.quote_id, "time": self.market_time.isoformat(), "received_at": self.received_at.isoformat(),
                "bid": str(self.bid), "ask": str(self.ask), "price": str(self.price)}


@dataclass(frozen=True)
class ProductRules:
    base_increment: Decimal = Decimal("0.00000001")
    quote_increment: Decimal = CENT
    minimum_notional: Decimal = Decimal("10")
    market_enabled: bool = True

    def __post_init__(self):
        if any(not value.is_finite() or value <= 0 for value in (self.base_increment, self.quote_increment, self.minimum_notional)) or type(self.market_enabled) is not bool:
            raise ValueError("Invalid product increments, minimum, or market flag")

    @classmethod
    def from_dict(cls, value: dict):
        if not isinstance(value, dict) or {"id", "base_increment", "quote_increment", "min_market_funds", "status"} - value.keys():
            raise ValueError("Product response is missing required fields")
        increments = [decimal(value[field]) for field in ("base_increment", "quote_increment", "min_market_funds")]
        if min(increments) <= 0 or value.get("id") != "BTC-USD":
            raise ValueError("Invalid BTC-USD product rules")
        enabled = value.get("status") == "online" and not any(value.get(flag, False) for flag in
                    ("post_only", "limit_only", "cancel_only", "trading_disabled", "auction_mode"))
        return cls(*increments, market_enabled=enabled)


@dataclass(frozen=True)
class Features:
    asof: datetime
    daily_asof: datetime
    close: Decimal
    atr: Decimal
    ready: bool
    swing_signal: bool
    regime: str
    confirmed_bear: bool
    return_24h: Decimal = ZERO

    @classmethod
    def from_dict(cls, value: dict):
        if not isinstance(value, dict) or {"asof", "daily_asof", "close", "atr", "ready", "swing_signal", "regime", "confirmed_bear"} - value.keys():
            raise ValueError("Feature snapshot is missing required fields")
        if value["regime"] not in {"bull", "bear", "mixed", "high_volatility", "unknown"}:
            raise ValueError("Unknown market regime")
        for field in ("ready", "swing_signal", "confirmed_bear"):
            if type(value[field]) is not bool:
                raise ValueError("Feature flags must be booleans")
        close, atr = decimal(value["close"]), decimal(value["atr"])
        if close <= 0 or atr < 0 or (value["ready"] and atr <= 0):
            raise ValueError("Feature prices or ATR are invalid")
        asof, daily = utc(value["asof"]), utc(value["daily_asof"])
        if daily > asof:
            raise ValueError("Daily context cannot come from after the signal")
        change = decimal(value.get("return_24h", 0))
        if change <= -1:
            raise ValueError("24-hour return must be above -100%")
        return cls(asof, daily, close, atr, value["ready"], value["swing_signal"], value["regime"], value["confirmed_bear"], change)

    def is_fresh(self, now: datetime, seconds: int):
        age = (utc(now) - self.asof).total_seconds()
        return self.ready and 0 <= age <= seconds and self.daily_asof <= self.asof

    def to_dict(self):
        return {"asof": self.asof.isoformat(), "daily_asof": self.daily_asof.isoformat(), "close": str(self.close),
                "atr": str(self.atr), "ready": self.ready, "swing_signal": self.swing_signal,
                "regime": self.regime, "confirmed_bear": self.confirmed_bear, "return_24h": str(self.return_24h)}


def swing_plan(quote: Quote, atr: Decimal, config: dict, rules: ProductRules) -> dict:
    fee, slip = (decimal(config["risk"][field]) for field in ("fee_fraction_per_fill", "slippage_fraction_per_fill"))
    entry = ceil_step(quote.ask * (ONE + slip), rules.quote_increment)
    stop = floor_step(entry - decimal(config["swing"]["atr_multiplier"]) * atr, rules.quote_increment)
    if stop <= 0:
        raise ValueError("ATR stop would be nonpositive")
    entry_cost = entry * (ONE + fee)
    stop_proceeds = floor_step(stop * (ONE - slip), rules.quote_increment) * (ONE - fee)
    loss_per_btc = entry_cost - stop_proceeds
    target = ceil_step((entry_cost + decimal(config["swing"]["take_profit_r_multiple"]) * loss_per_btc)
                       / ((ONE - slip) * (ONE - fee)), rules.quote_increment)
    return {"entry": entry, "stop": stop, "target": target, "loss_per_btc": loss_per_btc,
            "target_atr_multiple": (target - entry) / atr}
