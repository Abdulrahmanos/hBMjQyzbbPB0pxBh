"""Public Coinbase Exchange candles, cached requests, and explicit quality checks."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

BASE_URL = "https://api.exchange.coinbase.com/products/BTC-USD/candles"
PRICE_COLUMNS = ["open", "high", "low", "close", "volume"]


class DataError(ValueError):
    """Market data cannot be used safely."""


def utc_timestamp(value: str) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    return timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")


def quality_report(frame: pd.DataFrame, start=None, end=None) -> dict:
    if frame.empty:
        raise DataError("No candles were returned")
    required = set(PRICE_COLUMNS)
    if required - set(frame.columns):
        raise DataError(f"Missing candle columns: {sorted(required - set(frame.columns))}")
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise DataError("Candle timestamps must be timezone-aware")
    numeric = frame[PRICE_COLUMNS].apply(pd.to_numeric, errors="coerce")
    invalid = (~np.isfinite(numeric).all(axis=1) | (numeric[["open", "high", "low", "close"]] <= 0).any(axis=1)
               | (numeric["volume"] < 0) | (numeric["low"] > numeric[["open", "close", "high"]].min(axis=1))
               | (numeric["high"] < numeric[["open", "close", "low"]].max(axis=1)))
    duplicates = int(frame.index.duplicated().sum())
    off_grid = int((frame.index != frame.index.floor("h")).sum())
    expected_start = start if start is not None else frame.index.min()
    expected_end = end if end is not None else frame.index.max() + pd.Timedelta(hours=1)
    expected = pd.date_range(expected_start, expected_end, freq="h", inclusive="left")
    missing = expected.difference(frame.index)
    gaps = []
    for timestamp in missing:
        if gaps and timestamp == pd.Timestamp(gaps[-1]["end_utc"]) + pd.Timedelta(hours=1):
            gaps[-1]["end_utc"] = timestamp.isoformat()
            gaps[-1]["hours"] += 1
        else:
            gaps.append({"start_utc": timestamp.isoformat(), "end_utc": timestamp.isoformat(), "hours": 1})
    return {"rows": len(frame), "first_candle_utc": frame.index.min().isoformat(),
            "last_candle_utc": frame.index.max().isoformat(), "invalid_rows": int(invalid.sum()),
            "duplicate_timestamps": duplicates, "off_grid_timestamps": off_grid,
            "sorted": frame.index.is_monotonic_increasing, "missing_hours": len(missing), "gaps": gaps,
            "gap_policy": "No price imputation. Reset hourly indicators and prohibit entries across a missing candle."}


def validate_candles(frame: pd.DataFrame) -> dict:
    report = quality_report(frame)
    if report["invalid_rows"] or report["duplicate_timestamps"] or report["off_grid_timestamps"] or not report["sorted"]:
        raise DataError(f"Invalid candles: {json.dumps(report)}")
    return report


def load_candles(path: str | Path) -> pd.DataFrame:
    try:
        frame = pd.read_csv(path)
        frame["time_utc"] = pd.to_datetime(frame["time_utc"], utc=True)
        frame = frame.set_index("time_utc")
        for column in PRICE_COLUMNS:
            frame[column] = pd.to_numeric(frame[column], errors="raise")
    except (OSError, KeyError, ValueError) as exc:
        raise DataError(f"Cannot load candle CSV: {exc}") from exc
    validate_candles(frame)
    return frame[PRICE_COLUMNS]


def _request_page(url: str, cache_path: Path, timeout=25) -> tuple[list, bool]:
    if cache_path.is_file():
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise DataError("Cached candle response must be an array")
        return payload, True
    for attempt in range(4):
        try:
            request = Request(url, headers={"User-Agent": "BitcoinResearch/0.2", "Accept": "application/json"})
            with urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read())
            if not isinstance(payload, list):
                raise DataError("Coinbase candle response must be an array")
            cache_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
            return payload, False
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise DataError(f"Coinbase request failed with HTTP {exc.code}") from exc
        except URLError as exc:
            if attempt == 3:
                raise DataError(f"Coinbase network request failed: {exc.reason}") from exc
        time.sleep(2 ** attempt)
    raise DataError("Candle request failed")


def fetch_candles(start: str, end: str, output: str | Path, progress=print, request_timeout=25) -> dict:
    start_time, end_time = utc_timestamp(start), utc_timestamp(end)
    completed_before = pd.Timestamp(datetime.now(timezone.utc)).floor("h")
    if start_time != start_time.floor("h") or end_time != end_time.floor("h"):
        raise DataError("Requested timestamps must align to full UTC hours")
    if start_time >= end_time or end_time > completed_before:
        raise DataError("Request a positive date range containing only completed candles")
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cache_directory = output_path.parent / "coinbase-pages"
    cache_directory.mkdir(exist_ok=True)
    cursor = start_time
    rows: dict[int, list] = {}
    requests, cache_hits, duplicates = 0, 0, 0
    while cursor < end_time:
        # At most 300 buckets even when Coinbase includes both endpoints.
        batch_end = min(cursor + pd.Timedelta(hours=299), end_time)
        query = urlencode({"granularity": 3600, "start": cursor.isoformat(), "end": batch_end.isoformat()})
        url = BASE_URL + "?" + query
        key = hashlib.sha256(url.encode()).hexdigest()
        payload, cached = _request_page(url, cache_directory / f"{key}.json", request_timeout)
        requests += 1
        cache_hits += int(cached)
        for row in payload:
            if not isinstance(row, list) or len(row) != 6:
                raise DataError("Unexpected Coinbase candle schema")
            timestamp = pd.Timestamp(row[0], unit="s", tz="UTC")
            if cursor <= timestamp < batch_end:
                if row[0] in rows:
                    duplicates += 1
                    if rows[row[0]] != row:
                        raise DataError("Conflicting candle values for the same timestamp")
                rows[row[0]] = row
        cursor = batch_end
        if requests == 1 or requests % 20 == 0 or cursor == end_time:
            progress(json.dumps({"event": "download_progress", "through_utc": cursor.isoformat(),
                                 "requests": requests, "cache_hits": cache_hits, "candles": len(rows)}))
        if not cached:
            time.sleep(0.15)
    frame = pd.DataFrame(list(rows.values()), columns=["epoch", "low", "high", "open", "close", "volume"])
    if frame.empty:
        raise DataError("No data returned for requested dates")
    frame["time_utc"] = pd.to_datetime(frame["epoch"], unit="s", utc=True)
    frame = frame.set_index("time_utc").sort_index()[PRICE_COLUMNS]
    validate_candles(frame)
    initial_report = quality_report(frame, start_time, end_time)
    repaired_hours = 0
    # One focused retry for observed short gaps; never invent absent prices.
    for gap in initial_report["gaps"]:
        if gap["hours"] > 24:
            continue
        repair_start = pd.Timestamp(gap["start_utc"]) - pd.Timedelta(hours=1)
        repair_end = pd.Timestamp(gap["end_utc"]) + pd.Timedelta(hours=2)
        query = urlencode({"granularity": 3600, "start": repair_start.isoformat(), "end": repair_end.isoformat()})
        url = BASE_URL + "?" + query
        key = hashlib.sha256(url.encode()).hexdigest()
        payload, cached = _request_page(url, cache_directory / f"{key}.json", request_timeout)
        requests += 1
        cache_hits += int(cached)
        for row in payload:
            if not isinstance(row, list) or len(row) != 6:
                raise DataError("Unexpected Coinbase candle schema during gap recovery")
            timestamp = pd.Timestamp(row[0], unit="s", tz="UTC")
            if start_time <= timestamp < end_time and timestamp not in frame.index:
                frame.loc[timestamp] = [row[3], row[2], row[1], row[4], row[5]]
                repaired_hours += 1
        if not cached:
            time.sleep(0.15)
    frame = frame.sort_index()
    validate_candles(frame)
    frame.to_csv(output_path, float_format="%.12g")
    report = quality_report(frame, start_time, end_time)
    report.update({"source": "Coinbase Exchange public REST candles", "source_url": BASE_URL,
                   "product_id": "BTC-USD", "granularity_seconds": 3600,
                   "requested_start_utc": start_time.isoformat(), "requested_end_exclusive_utc": end_time.isoformat(),
                   "retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "requests": requests,
                   "cache_hits": cache_hits, "duplicate_response_rows": duplicates,
                   "gap_recovery_attempts": len([gap for gap in initial_report["gaps"] if gap["hours"] <= 24]),
                   "recovered_hours": repaired_hours,
                   "csv_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest()})
    output_path.with_suffix(".manifest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
