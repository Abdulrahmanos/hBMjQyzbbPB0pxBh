"""Causal hourly features and UTC daily context available only after day end."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .market_data import validate_candles


def wilder_mean(values: pd.Series, period: int) -> pd.Series:
    """Wilder smoothing with a simple-average seed and resets on missing input."""
    result = np.full(len(values), np.nan)
    seed = []
    previous = None
    for index, value in enumerate(values.to_numpy(dtype=float)):
        if not np.isfinite(value):
            seed, previous = [], None
            continue
        if previous is None:
            seed.append(value)
            if len(seed) == period:
                previous = float(np.mean(seed))
                result[index] = previous
        else:
            previous = (previous * (period - 1) + value) / period
            result[index] = previous
    return pd.Series(result, index=values.index)


def _hourly_features(frame: pd.DataFrame, settings: dict) -> pd.DataFrame:
    result = frame.copy()
    previous_close = frame["close"].shift(1)
    tr = pd.concat([frame["high"] - frame["low"], (frame["high"] - previous_close).abs(),
                    (frame["low"] - previous_close).abs()], axis=1).max(axis=1)
    result["atr"] = wilder_mean(tr, settings["atr_period"])
    result["atr_pct"] = result["atr"] / result["close"]
    delta = frame["close"].diff()
    gains = wilder_mean(delta.clip(lower=0), 14)
    losses = wilder_mean((-delta).clip(lower=0), 14)
    result["rsi"] = 100 - 100 / (1 + gains / losses.replace(0, np.nan))
    result.loc[(losses == 0) & (gains > 0), "rsi"] = 100
    result.loc[(losses == 0) & (gains == 0), "rsi"] = 50
    result["ema_fast"] = frame["close"].ewm(span=settings["ema_fast_period"], adjust=False,
                                          min_periods=settings["ema_fast_period"]).mean()
    result["ema_slow"] = frame["close"].ewm(span=settings["ema_slow_period"], adjust=False,
                                          min_periods=settings["ema_slow_period"]).mean()
    result["breakout_high"] = frame["high"].shift(1).rolling(settings["breakout_lookback"]).max()
    result["relative_volume"] = frame["volume"] / frame["volume"].shift(1).rolling(settings["relative_volume_lookback"]).mean().replace(0, np.nan)
    result["return_24h"] = frame["close"].pct_change(24, fill_method=None)
    result["swing_signal"] = ((result["ema_fast"] > result["ema_slow"])
                              & (frame["close"] > result["breakout_high"])
                              & (result["relative_volume"] >= settings["relative_volume_threshold"]))
    result["hourly_ready"] = result[["atr", "ema_slow", "breakout_high", "relative_volume"]].notna().all(axis=1)
    return result


def build_features(candles: pd.DataFrame, app_settings: dict, research_settings: dict) -> pd.DataFrame:
    validate_candles(candles)
    groups = candles.index.to_series().diff().ne(pd.Timedelta(hours=1)).cumsum()
    result = pd.concat([_hourly_features(group, app_settings["swing"]) for _, group in candles.groupby(groups)])
    result["contiguous_previous"] = candles.index.to_series().diff().eq(pd.Timedelta(hours=1))
    daily = candles.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    counts = candles["close"].resample("1D").count()
    daily.loc[counts != 24, ["open", "high", "low", "volume"]] = np.nan
    # A verified final-hour close remains usable for trend features even when an
    # earlier hour is absent. Daily range/volume require all 24 hourly buckets.
    final_hours = candles.loc[candles.index.hour == 23, "close"].copy()
    final_hours.index = final_hours.index.floor("1D")
    daily["close"] = final_hours.reindex(daily.index)
    daily["daily_sma200"] = daily["close"].rolling(research_settings["bear_trend_days"]).mean()
    daily["daily_return30"] = daily["close"].pct_change(research_settings["bear_momentum_days"], fill_method=None)
    previous_close = daily["close"].shift(1)
    tr = pd.concat([daily["high"] - daily["low"], (daily["high"] - previous_close).abs(),
                    (daily["low"] - previous_close).abs()], axis=1).max(axis=1)
    daily["daily_atr_pct"] = wilder_mean(tr, 14) / daily["close"]
    daily["daily_sma200_distance"] = daily["close"] / daily["daily_sma200"] - 1
    bear = (daily["daily_sma200_distance"] < 0) & (daily["daily_return30"] < 0)
    daily["confirmed_bear"] = bear.rolling(research_settings["bear_confirmation_days"]).sum().eq(research_settings["bear_confirmation_days"])
    daily["regime"] = "mixed"
    daily.loc[(daily["daily_sma200_distance"] > 0) & (daily["daily_return30"] > 0), "regime"] = "bull"
    daily.loc[daily["daily_atr_pct"] > research_settings.get("high_volatility_atr_fraction", 0.06), "regime"] = "high_volatility"
    daily.loc[bear, "regime"] = "bear"
    daily.loc[daily[["daily_sma200", "daily_return30", "daily_atr_pct"]].isna().any(axis=1), "regime"] = "unknown"
    # A daily candle dated Jan 1 becomes available at Jan 2 00:00, never earlier.
    context = daily[["daily_sma200_distance", "daily_return30", "daily_atr_pct", "confirmed_bear", "regime"]].copy()
    context.index = context.index + pd.Timedelta(days=1)
    result = result.join(context.reindex(result.index, method="ffill"))
    result["regime"] = result["regime"].fillna("unknown")
    result["confirmed_bear"] = result["confirmed_bear"].fillna(False).astype(bool)
    return result
