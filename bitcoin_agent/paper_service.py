"""Bounded or continuous public-data paper operation with independent quote polling."""

from __future__ import annotations

import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd

from .config import AgentConfig
from .features import build_features
from .market_data import DataError, fetch_candles, load_candles
from .paper_engine import PaperEngine
from .paper_store import audit_paper, connection, event
from .paper_types import Features, ProductRules, Quote

PRODUCT_URL = "https://api.exchange.coinbase.com/products/BTC-USD"


def public_json(url):
    if url not in {PRODUCT_URL, PRODUCT_URL + "/ticker"}:
        raise ValueError("Unsupported public market endpoint")
    for attempt in range(3):
        try:
            request = Request(url, headers={"User-Agent": "BitcoinPaperAgent/0.3", "Accept": "application/json", "Cache-Control": "no-cache"})
            with urlopen(request, timeout=10) as response:
                payload = json.loads(response.read())
            if not isinstance(payload, dict):
                raise DataError("Public API response must be an object")
            return payload
        except HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise DataError(f"Public market API failed: HTTP {exc.code}") from exc
        except URLError as exc:
            if attempt == 2:
                raise DataError(f"Public market API connection failed: {exc.reason}") from exc
        time.sleep(2 ** attempt)


def refresh_features(config, history_path, runtime_directory, now):
    """Extend a separate runtime cache; never overwrite milestone-2 evidence."""
    runtime_directory.mkdir(parents=True, exist_ok=True)
    live_path = runtime_directory / "btc_usd_1h.csv"
    base = load_candles(live_path if live_path.is_file() else history_path) if live_path.is_file() or Path(history_path).is_file() else None
    end = pd.Timestamp(now).floor("h")
    op, swing = config.data["operational"], config.data["swing"]
    hourly_warmup = max(swing[field] for field in ("atr_period", "ema_slow_period", "breakout_lookback", "relative_volume_lookback"))
    keep_days = max(op["trend_days"], op["momentum_days"], (hourly_warmup + 23) // 24, 200) + 30
    required_start = end - pd.Timedelta(days=keep_days)
    start = required_start if base is None or base.index.min() > required_start else max(base.index[-1] - pd.Timedelta(hours=2), required_start)
    update_path = runtime_directory / "updates.csv"
    fetch_candles(start.isoformat(), end.isoformat(), update_path, progress=lambda _: None, request_timeout=5)
    updates = load_candles(update_path)
    combined = pd.concat([base, updates]) if base is not None else updates
    combined = combined.loc[~combined.index.duplicated(keep="last")].sort_index()
    # Retain enough calendar history for daily context, without repeatedly loading years.
    combined = combined.loc[combined.index >= required_start]
    combined.to_csv(live_path, float_format="%.12g")
    research = {"bear_trend_days": op["trend_days"], "bear_momentum_days": op["momentum_days"],
                "bear_confirmation_days": op["bear_confirmation_days"], "high_volatility_atr_fraction": op["high_volatility_atr_fraction"]}
    (runtime_directory / "feature_definitions.json").write_text(json.dumps({"swing": config.data["swing"], "daily": research}, indent=2), encoding="utf-8")
    result = build_features(combined, config.data, research)
    row, timestamp = result.iloc[-1], result.index[-1]
    ready = bool(row["hourly_ready"])
    snapshot = Features.from_dict({"asof": (timestamp + pd.Timedelta(hours=1)).isoformat(),
                                  "daily_asof": timestamp.floor("1D").isoformat(), "close": str(row["close"]),
                                  "atr": str(row["atr"]) if ready else "0", "ready": ready,
                                  "swing_signal": bool(row["swing_signal"]), "regime": str(row["regime"]),
                                  "confirmed_bear": bool(row["confirmed_bear"]),
                                  "return_24h": str(row["return_24h"]) if pd.notna(row["return_24h"]) else "0"})
    (runtime_directory / "features.json").write_text(json.dumps(snapshot.to_dict(), indent=2), encoding="utf-8")
    return snapshot


def run_paper(path, config: AgentConfig, *, cycles=1, history="data/raw/btc_usd_1h.csv", runtime="var/live", progress=print, integration_settings=None,
              observer=None, stop_requested=None, heartbeat=None):
    """cycles=0 runs until interrupted; CLI defaults to a bounded single cycle."""
    if type(cycles) is not int or cycles < 0:
        raise ValueError("Cycles must be a nonnegative integer")
    if integration_settings is None and any(config.data["integrations"][key] for key in ("google_sheet_enabled", "telegram_enabled", "gmail_enabled", "llm_enabled")):
        raise ValueError("Enabled integrations require --integrations settings; use config/integrations.demo.json for a local demo")
    runtime_directory = Path(runtime)
    runtime_directory.mkdir(parents=True, exist_ok=True)
    metadata = public_json(PRODUCT_URL)
    (runtime_directory / "product_rules.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    rules = ProductRules.from_dict(metadata)
    engine = PaperEngine(path, config, rules)
    coordinator, integration_worker, pending_integration = None, None, None
    if integration_settings is not None:
        from .integrations import IntegrationCoordinator
        coordinator = IntegrationCoordinator(path, config, integration_settings)
        engine.apply_config(coordinator.manager.current, datetime.now(timezone.utc))
        config = engine.config
        integration_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="paper-integrations")
    snapshot = None
    refresh_at = 0.0
    completed, failures, accepted, filled, feature_failures = 0, 0, 0, 0, 0
    interval = config.data["risk"]["stop_monitor_seconds"]
    worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="paper-features")
    pending_refresh = None
    def refresh_bundle(refresh_config):
        metadata = public_json(PRODUCT_URL)
        new_rules = ProductRules.from_dict(metadata)
        (runtime_directory / "product_rules.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        try:
            new_features = refresh_features(refresh_config, history, runtime_directory, datetime.now(timezone.utc))
            return new_rules, new_features, None, refresh_config.checksum
        except (DataError, OSError, ValueError) as exc:
            return new_rules, None, str(exc), refresh_config.checksum
    try:
        while (cycles == 0 or completed < cycles) and not (stop_requested and stop_requested()):
            started = time.monotonic()
            now = datetime.now(timezone.utc)
            if pending_integration is not None and pending_integration.done():
                try:
                    integration_result = pending_integration.result()
                    if engine.apply_config(integration_result["config"], now):
                        config = engine.config
                        snapshot = None
                        refresh_at = 0.0
                    progress(json.dumps({"event": "paper_integrations", **{key: value for key, value in integration_result.items() if key != "config"}}))
                except (OSError, ValueError, sqlite3.Error):
                    with connection(path, write=True) as database:
                        event(database, now, "paper_integration_failure", {"reason": "integration_worker_failed"})
                    progress(json.dumps({"event": "paper_integration_failure", "reason": "integration_worker_failed"}))
                pending_integration = None
            if pending_refresh is None and started >= refresh_at:
                pending_refresh = worker.submit(refresh_bundle, config)
                refresh_at = started + config.data["market"]["poll_seconds"]
            if pending_refresh is not None and pending_refresh.done():
                try:
                    engine.rules, snapshot, error, feature_config_checksum = pending_refresh.result()
                    if feature_config_checksum != config.checksum:
                        snapshot = None
                        refresh_at = 0.0
                    if error:
                        raise DataError(error)
                    if snapshot is not None:
                        progress(json.dumps({"event": "paper_features_refreshed", **snapshot.to_dict()}))
                except (DataError, OSError, ValueError) as exc:
                    feature_failures += 1
                    snapshot = None
                    # A successful product response remains usable for exits even
                    # if candle/feature loading fails. Failed metadata disables fills.
                    if pending_refresh.exception() is not None:
                        engine.rules = replace(engine.rules, market_enabled=False)
                    with connection(path, write=True) as database:
                        event(database, now, "paper_feature_refresh_failed", {"reason": str(exc)})
                    progress(json.dumps({"event": "paper_feature_refresh_failed", "reason": str(exc)}))
                pending_refresh = None
            try:
                payload = public_json(PRODUCT_URL + "/ticker")
                received = datetime.now(timezone.utc)
                payload["received_at"] = received.isoformat()
                quote = Quote.from_dict(payload)
                result = engine.tick(quote, snapshot, received)
                if observer is not None:
                    observer(quote, snapshot, result, received, engine.config, engine.rules)
                accepted += int(result["accepted"])
                filled += result["new_fills"]
                progress(json.dumps({"event": "paper_tick", "accepted": result["accepted"], "duplicate": result["duplicate"],
                                     "new_fills": result["new_fills"], "phase": result["portfolio"]["phase"],
                                     "cash_usd": result["portfolio"]["cash_usd"], "btc_units": result["portfolio"]["btc_units"],
                                     "equity_usd": result["portfolio"]["equity_usd"]}))
                if coordinator is not None and pending_integration is None:
                    pending_integration = integration_worker.submit(coordinator.poll, snapshot, result["portfolio"], received)
            except (DataError, OSError, ValueError) as exc:
                failures += 1
                with connection(path, write=True) as database:
                    engine._cancel_buys(database, datetime.now(timezone.utc), "market_feed_failure")
                    event(database, datetime.now(timezone.utc), "paper_market_failure", {"reason": str(exc)})
                progress(json.dumps({"event": "paper_market_failure", "reason": str(exc)}))
            if heartbeat is not None:
                heartbeat()
            completed += 1
            if cycles == 0 or completed < cycles:
                time.sleep(max(0, interval - (time.monotonic() - started)))
    except KeyboardInterrupt:
        progress(json.dumps({"event": "paper_service_stopped", "reason": "keyboard_interrupt"}))
    finally:
        worker.shutdown(wait=True, cancel_futures=True)
        if integration_worker is not None:
            integration_worker.shutdown(wait=True, cancel_futures=True)
    return {"execution_mode": "paper", "cycles_completed": completed, "accepted_quotes": accepted, "new_fills": filled,
            "market_failures": failures, "feature_failures": feature_failures, "running": False, "audit": audit_paper(path),
            "active_config_checksum": engine.config.checksum,
            "integrations": coordinator.preview() if coordinator else None,
            "last_features": snapshot.to_dict() if snapshot else None,
            "product_rules": {"base_increment": str(engine.rules.base_increment), "quote_increment": str(engine.rules.quote_increment),
                              "minimum_notional_usd": str(engine.rules.minimum_notional), "market_enabled": engine.rules.market_enabled}}
