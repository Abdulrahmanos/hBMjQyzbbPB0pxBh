"""Persistent simulated cash state. No exchange access or trading occurs here."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import AgentConfig

SCHEMA_VERSION = "1"


class StorageError(ValueError):
    """Stored state is incompatible with the requested operation."""


def _check_version(connection: sqlite3.Connection) -> None:
    row = connection.execute("SELECT value FROM app_metadata WHERE key = 'schema_version'").fetchone()
    if row is None or row[0] != SCHEMA_VERSION:
        raise StorageError("Unsupported database schema; do not overwrite existing state")


def initialize_database(path: str | Path, config: AgentConfig) -> bool:
    """Create state once, returning False on an identical subsequent initialization."""
    database_path = Path(path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if "app_metadata" in tables:
                _check_version(connection)
                stored = connection.execute("SELECT value FROM app_metadata WHERE key = 'funding_checksum'").fetchone()
                if stored is None or stored[0] != config.funding_checksum:
                    raise StorageError("Existing database has different initial funding settings. Use a new database path for a separate experiment.")
                return False
            if tables:
                raise StorageError("The database already contains unrelated tables; choose a new database path")

            connection.execute("CREATE TABLE app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute("CREATE TABLE balances (bucket TEXT PRIMARY KEY CHECK (bucket IN ('dca', 'swing', 'reserve')), cash_usd TEXT NOT NULL, btc_units TEXT NOT NULL)")
            connection.execute("CREATE TABLE config_snapshots (checksum TEXT PRIMARY KEY, recorded_at TEXT NOT NULL, config_json TEXT NOT NULL)")
            connection.execute("CREATE TABLE system_events (id INTEGER PRIMARY KEY, occurred_at TEXT NOT NULL, event_type TEXT NOT NULL, payload_json TEXT NOT NULL)")
            created_at = datetime.now(timezone.utc).isoformat()
            metadata = {
                "schema_version": SCHEMA_VERSION,
                "execution_mode": "paper",
                "initial_cash_usd": format(config.initial_cash, ".2f"),
                "funding_checksum": config.funding_checksum,
                "created_at": created_at,
            }
            connection.executemany("INSERT INTO app_metadata (key, value) VALUES (?, ?)", metadata.items())
            connection.executemany("INSERT INTO balances (bucket, cash_usd, btc_units) VALUES (?, ?, ?)",
                                   [(bucket, format(amount, ".2f"), "0") for bucket, amount in config.initial_allocations().items()])
            connection.execute("INSERT INTO config_snapshots VALUES (?, ?, ?)", (config.checksum, created_at, config.canonical_json))
            connection.execute("INSERT INTO system_events (occurred_at, event_type, payload_json) VALUES (?, ?, ?)",
                               (created_at, "paper_portfolio_initialized", json.dumps(metadata, sort_keys=True)))
        return True
    finally:
        connection.close()


def read_status(path: str | Path) -> dict:
    """Read state without creating a missing database or changing its contents."""
    database_path = Path(path).resolve()
    if not database_path.is_file():
        raise StorageError("Database is not initialized. Run init-db first.")
    connection = sqlite3.connect(database_path.as_uri() + "?mode=ro", uri=True)
    try:
        _check_version(connection)
        metadata = dict(connection.execute("SELECT key, value FROM app_metadata"))
        balances = [
            {"bucket": row[0], "cash_usd": row[1], "btc_units": row[2]}
            for row in connection.execute("SELECT bucket, cash_usd, btc_units FROM balances ORDER BY bucket")
        ]
        return {"execution_mode": metadata["execution_mode"], "initial_cash_usd": metadata["initial_cash_usd"],
                "created_at": metadata["created_at"], "balances": balances,
                "event_count": connection.execute("SELECT COUNT(*) FROM system_events").fetchone()[0]}
    finally:
        connection.close()
