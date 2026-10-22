"""Offline research simulator with next-open entries and adverse execution costs.

This is not an exchange broker. Research uses floating-point arithmetic with cash
invariant checks; operational monetary accounting remains decimal-based.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import asdict, dataclass
from typing import Callable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Profile:
    dca: float
    swing: float
    reserve: float
    dip_threshold: float | None = 0.03
    atr_multiplier: float = 1.5
    defensive_dca_exit: bool = False

    def __post_init__(self):
        weights = (self.dca, self.swing, self.reserve)
        if any(not math.isfinite(value) or value < 0 or value > 1 for value in weights) or not math.isclose(sum(weights), 1, abs_tol=1e-9):
            raise ValueError("Profile weights must be finite, nonnegative, and sum to one")
        if self.dip_threshold is not None and (not math.isfinite(self.dip_threshold) or not 0 < self.dip_threshold <= 1):
            raise ValueError("Dip threshold must be in (0, 1]")
        if not math.isfinite(self.atr_multiplier) or self.atr_multiplier <= 0:
            raise ValueError("ATR multiplier must be positive")

    @property
    def identifier(self) -> str:
        dip = "scheduled" if self.dip_threshold is None else f"dip{self.dip_threshold:g}"
        return f"d{self.dca:g}_s{self.swing:g}_r{self.reserve:g}_{dip}_atr{self.atr_multiplier:g}_exit{int(self.defensive_dca_exit)}"

    def to_dict(self) -> dict:
        return {"profile_id": self.identifier, **asdict(self)}


@dataclass
class PreparedData:
    frame: pd.DataFrame
    seconds: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    atr: np.ndarray
    swing_signal: np.ndarray
    ready: np.ndarray
    contiguous: np.ndarray
    bear: np.ndarray
    regime: np.ndarray

    @classmethod
    def from_frame(cls, frame: pd.DataFrame):
        arrays = {key: frame[key].to_numpy() for key in ("open", "high", "low", "close", "atr")}
        seconds = ((frame.index - pd.Timestamp("1970-01-01", tz="UTC")) / pd.Timedelta(seconds=1)).to_numpy()
        return cls(frame, seconds, **arrays, swing_signal=frame["swing_signal"].to_numpy(dtype=bool),
                   ready=frame["hourly_ready"].to_numpy(dtype=bool),
                   contiguous=frame["contiguous_previous"].to_numpy(dtype=bool),
                   bear=frame["confirmed_bear"].to_numpy(dtype=bool), regime=frame["regime"].to_numpy())


@dataclass
class BacktestResult:
    name: str
    curve: pd.DataFrame
    trades: list[dict]
    decisions: list[dict]
    initial_cash: float

    def metrics(self) -> dict:
        equity = self.curve["equity_usd"]
        peak = equity.cummax().clip(lower=self.initial_cash)
        drawdown = 1 - equity / peak
        daily = equity.resample("1D").last()
        daily_returns = daily.pct_change(fill_method=None).dropna()
        daily_std = float(daily_returns.std(ddof=1)) if len(daily_returns) > 1 else 0.0
        sharpe = (float(daily_returns.mean()) / daily_std * math.sqrt(365)) if daily_std > 0 else None
        elapsed_days = max((equity.index[-1] - equity.index[0]).total_seconds() / 86400, 1 / 24)
        growth = float(equity.iloc[-1] / self.initial_cash)
        cagr = growth ** (365 / elapsed_days) - 1
        buys = [trade for trade in self.trades if trade["side"] == "buy"]
        sells = [trade for trade in self.trades if trade["side"] == "sell"]
        return {"strategy": self.name, "final_equity_usd": float(equity.iloc[-1]), "net_return": growth - 1,
                "max_drawdown": float(drawdown.max()), "cagr": cagr, "daily_sharpe_zero_rf": sharpe,
                "fees_usd": sum(trade["fee_usd"] for trade in self.trades),
                "modeled_slippage_usd": sum(trade["slippage_usd"] for trade in self.trades),
                "fills": len(self.trades), "buy_fills": len(buys), "sell_fills": len(sells),
                "gross_btc_bought": sum(trade["btc_units"] for trade in buys),
                "mean_btc_exposure": float(self.curve["btc_exposure"].mean()),
                "paused_at_end": bool(self.curve["paused"].iloc[-1]), "days": elapsed_days}


def simulate(data: PreparedData, start: int, end: int, app: dict, research: dict, *,
             name: str, profile: Profile | None = None, policy: Callable[[int], tuple[Profile, dict]] | None = None,
             buy_and_hold: bool = False, guard_enabled: bool = True, detailed: bool = True) -> BacktestResult:
    """Carry inventory continuously across policy changes; liquidate only at experiment end."""
    if not 1 <= start < end <= len(data.frame):
        raise ValueError("Backtest requires a preceding signal candle and a nonempty valid range")
    if policy is None and profile is None:
        raise ValueError("Provide a profile or an allocation policy")
    initial = float(app["portfolio"]["initial_cash_usd"])
    cash = {"dca": 0.0, "swing": 0.0, "reserve": initial}
    dca_btc, swing_btc = 0.0, 0.0
    stop, target, swing_opened = 0.0, 0.0, 0.0
    current_profile = profile
    last_dca_price = None
    last_dca_time = -math.inf
    next_scheduled = data.seconds[start]
    next_allocation = data.seconds[start]
    spending = deque()
    peak, paused = initial, False
    trades, decisions, records = [], [], []
    fee = float(app["risk"]["fee_fraction_per_fill"])
    slippage = float(app["risk"]["slippage_fraction_per_fill"])
    dca_settings, swing_settings = app["dca"], app["swing"]
    cooldown = dca_settings["cooldown_hours"] * 3600
    schedule_seconds = dca_settings["schedule_hours"] * 3600
    dca_budget_reference = 0.0

    def value(price):
        return sum(cash.values()) + (dca_btc + swing_btc) * price

    def record_fill(index, side, sleeve, quantity, reference, reason, at_close=False):
        execution = reference * (1 + slippage if side == "buy" else 1 - slippage)
        notional, fee_paid = quantity * execution, quantity * execution * fee
        cash[sleeve] += -(notional + fee_paid) if side == "buy" else notional - fee_paid
        if cash[sleeve] < -1e-7:
            raise ArithmeticError("Research cash invariant violated")
        cash[sleeve] = max(0.0, cash[sleeve])
        trades.append({"time_utc": (data.frame.index[index] + (pd.Timedelta(hours=1) if at_close else pd.Timedelta(0))).isoformat(),
                       "side": side, "sleeve": sleeve, "btc_units": quantity, "reference_price": reference,
                       "fill_price": execution, "fee_usd": fee_paid,
                       "slippage_usd": quantity * abs(execution - reference), "reason": reason,
                       "profile_id": current_profile.identifier})
        return execution

    for index in range(start, end):
        now, opening = data.seconds[index], float(data.open[index])
        opening_value = value(opening)
        peak = max(peak, opening_value)
        if guard_enabled and 1 - opening_value / peak >= app["risk"]["max_portfolio_drawdown_fraction"]:
            paused = True

        if now >= next_allocation:
            previous_profile = current_profile
            if policy is not None:
                current_profile, details = policy(index)
            else:
                details = {"selection_source": "fixed"}
            equity = value(opening)
            pool = sum(cash.values())
            # Move uncommitted cash only. A lower target does not force a BTC sale.
            reserve_cash = min(pool, current_profile.reserve * equity)
            available = max(0.0, pool - reserve_cash)
            deficits = {"dca": max(0.0, current_profile.dca * equity - dca_btc * opening),
                        "swing": max(0.0, current_profile.swing * equity - swing_btc * opening)}
            denominator = sum(deficits.values())
            allocated = min(available, denominator)
            for sleeve in ("dca", "swing"):
                cash[sleeve] = allocated * deficits[sleeve] / denominator if denominator > 0 else 0.0
            cash["reserve"] = pool - cash["dca"] - cash["swing"]
            if index == start or current_profile != previous_profile:
                dca_budget_reference = current_profile.dca * equity
            if detailed:
                decisions.append({"time_utc": data.frame.index[index].isoformat(), "regime": str(data.regime[index]),
                                  **current_profile.to_dict(), **details})
            next_allocation = now + research["allocation_update_days"] * 86400

        # Existing inventory follows the current explicit defensive rule.
        if current_profile.defensive_dca_exit and data.bear[index] and dca_btc > 0:
            record_fill(index, "sell", "dca", dca_btc, opening, "confirmed_bear_exit")
            dca_btc = 0.0

        # Exits for already-open swings are checked at the open before any new entries.
        if swing_btc > 0 and (opening <= stop or now - swing_opened >= swing_settings["max_holding_hours"] * 3600):
            reason = "gap_stop" if opening <= stop else "time_exit"
            record_fill(index, "sell", "swing", swing_btc, opening, reason)
            swing_btc = 0.0
        elif swing_btc > 0 and opening >= target:
            record_fill(index, "sell", "swing", swing_btc, target, "profit_target")
            swing_btc = 0.0

        can_enter = data.contiguous[index] and data.ready[index - 1] and not paused
        if buy_and_hold and index == start:
            quantity = cash["dca"] / (opening * (1 + slippage) * (1 + fee))
            if quantity > 0:
                record_fill(index, "buy", "dca", quantity, opening, "buy_and_hold")
                dca_btc += quantity
        elif not buy_and_hold and can_enter:
            while spending and spending[0][0] <= now - 7 * 86400:
                spending.popleft()
            scheduled = now >= next_scheduled
            dip = (current_profile.dip_threshold is not None and last_dca_price is not None
                   and data.close[index - 1] <= last_dca_price * (1 - current_profile.dip_threshold))
            bearish_dca_pause = current_profile.defensive_dca_exit and data.bear[index]
            if current_profile.dca > 0 and (scheduled or dip) and now - last_dca_time >= cooldown and not bearish_dca_pause:
                weekly_room = dca_budget_reference * dca_settings["max_spend_fraction_per_7_days"] - sum(amount for _, amount in spending)
                gross = min(cash["dca"], dca_budget_reference * dca_settings["tranche_fraction_of_dca_budget"], max(0.0, weekly_room))
                if gross >= 1.0:
                    quantity = gross / (opening * (1 + slippage) * (1 + fee))
                    last_dca_price = record_fill(index, "buy", "dca", quantity, opening, "scheduled_dca" if scheduled else "dip_dca")
                    dca_btc += quantity
                    last_dca_time = now
                    spending.append((now, gross))
                    if scheduled:
                        next_scheduled = now + schedule_seconds

            if current_profile.swing > 0 and swing_btc == 0 and data.swing_signal[index - 1]:
                entry = opening * (1 + slippage)
                proposed_stop = entry - current_profile.atr_multiplier * data.atr[index - 1]
                if proposed_stop > 0:
                    expected_exit = proposed_stop * (1 - slippage)
                    unit_risk = entry - expected_exit + fee * (entry + expected_exit)
                    equity = value(opening)
                    risk_size = swing_settings["risk_fraction_of_equity"] * equity / unit_risk
                    cash_size = cash["swing"] / (entry * (1 + fee))
                    notional_size = current_profile.swing * equity * swing_settings["max_notional_fraction_of_swing_budget"] / entry
                    quantity = min(risk_size, cash_size, notional_size)
                    if quantity * entry >= 1.0:
                        record_fill(index, "buy", "swing", quantity, opening, "trend_volume_breakout")
                        swing_btc, swing_opened = quantity, now
                        stop = proposed_stop
                        target = entry + swing_settings["take_profit_r_multiple"] * (entry - stop)

        # On unresolved hourly bars, assume the stop occurs first if both exits are touched.
        if swing_btc > 0:
            if data.low[index] <= stop:
                record_fill(index, "sell", "swing", swing_btc, min(opening, stop), "intrabar_stop")
                swing_btc = 0.0
            elif data.high[index] >= target:
                record_fill(index, "sell", "swing", swing_btc, target, "profit_target")
                swing_btc = 0.0

        closing = float(data.close[index])
        equity = value(closing)
        peak = max(peak, equity)
        if guard_enabled and 1 - equity / peak >= app["risk"]["max_portfolio_drawdown_fraction"]:
            paused = True
        records.append((equity, sum(cash.values()), dca_btc, swing_btc,
                        (dca_btc + swing_btc) * closing / equity, paused,
                        current_profile.dca, current_profile.swing, current_profile.reserve))

    # End-of-experiment liquidation includes costs for every strategy, including buy-and-hold.
    for sleeve, quantity in (("dca", dca_btc), ("swing", swing_btc)):
        if quantity > 0:
            record_fill(end - 1, "sell", sleeve, quantity, float(data.close[end - 1]), "experiment_end", at_close=True)
    final_cash = sum(cash.values())
    last = records[-1]
    records[-1] = (final_cash, final_cash, 0.0, 0.0, 0.0, last[5], *last[6:])
    curve = pd.DataFrame(records, index=data.frame.index[start:end] + pd.Timedelta(hours=1),
                         columns=["equity_usd", "cash_usd", "dca_btc", "swing_btc", "btc_exposure", "paused",
                                  "target_dca", "target_swing", "target_reserve"])
    curve.index.name = "time_utc"
    return BacktestResult(name, curve, trades, decisions, initial)
