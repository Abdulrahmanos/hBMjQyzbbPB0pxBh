"""Checks for configuration rejection and preservation of simulated capital."""

import copy
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from decimal import Decimal
from pathlib import Path

from bitcoin_agent.config import ConfigError, load_config, validate_config
from bitcoin_agent.storage import StorageError, initialize_database, read_status

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "config" / "default.json"


def temporary_directory():
    # Keep generated test artifacts inside the workspace on Windows as well.
    base = DEFAULT_PATH.parents[1] / "var" / "test-temp"
    base.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=base)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(DEFAULT_PATH.read_text(encoding="utf-8"))

    def test_default_allocations_conserve_cash(self):
        config = validate_config(self.data)
        self.assertEqual(config.initial_allocations(), {"dca": Decimal("7000.00"), "swing": Decimal("2000.00"), "reserve": Decimal("1000.00")})
        self.assertEqual(sum(config.initial_allocations().values()), config.initial_cash)

    def test_rounding_residue_stays_in_reserve(self):
        self.data["portfolio"]["initial_cash_usd"] = "100.01"
        config = validate_config(self.data)
        self.assertEqual(sum(config.initial_allocations().values()), Decimal("100.01"))
        self.assertEqual(config.initial_allocations()["reserve"], Decimal("10.01"))

    def test_unsafe_or_inconsistent_configuration_rejected(self):
        cases = [
            ("execution_mode", None, "live"),
            ("portfolio", "dca_fraction", 0.8),
            ("portfolio", "initial_cash_usd", "-100"),
            ("portfolio", "initial_cash_usd", "100.001"),
            ("portfolio", "initial_cash_usd", "NaN"),
            ("portfolio", "reserve_fraction", True),
            ("swing", "atr_multiplier", 0),
            ("swing", "ema_fast_period", 50),
            ("swing", "risk_fraction_of_equity", float("inf")),
            ("swing", "max_open_positions", 2),
            ("dca", "cooldown_hours", 0),
            ("dca", "tranche_fraction_of_dca_budget", 0.5),
            ("risk", "stop_monitor_seconds", True),
            ("integrations", "llm_enabled", True),
            ("reporting", "weekly_time", "25:00"),
            ("reporting", "timezone", "Unknown/Zone"),
        ]
        for section, field, value in cases:
            with self.subTest(section=section, field=field, value=value):
                candidate = copy.deepcopy(self.data)
                if field is None:
                    candidate[section] = value
                else:
                    candidate[section][field] = value
                with self.assertRaises(ConfigError):
                    validate_config(candidate)

    def test_unknown_fields_rejected(self):
        self.data["market"]["api_secret"] = "must-not-enter-config"
        with self.assertRaisesRegex(ConfigError, "unknown fields"):
            validate_config(self.data)

    def test_duplicate_keys_rejected(self):
        with temporary_directory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text('{"schema_version": 1, "schema_version": 2}', encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "Duplicate"):
                load_config(path)

    def test_nonstandard_json_numbers_rejected(self):
        with temporary_directory() as directory:
            path = Path(directory) / "nan.json"
            path.write_text('{"schema_version": NaN}', encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "numeric constant"):
                load_config(path)

    def test_validated_config_cannot_be_mutated_through_data(self):
        config = validate_config(self.data)
        config.data["execution_mode"] = "live"
        self.assertEqual(config.data["execution_mode"], "paper")


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = temporary_directory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "paper.sqlite3"
        self.config = load_config(DEFAULT_PATH)

    def test_reinitialization_preserves_modified_balances_and_event_count(self):
        self.assertTrue(initialize_database(self.path, self.config))
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE balances SET cash_usd = '6800.00', btc_units = '0.002' WHERE bucket = 'dca'")
        self.assertFalse(initialize_database(self.path, self.config))
        status = read_status(self.path)
        dca = next(row for row in status["balances"] if row["bucket"] == "dca")
        self.assertEqual(dca["cash_usd"], "6800.00")
        self.assertEqual(dca["btc_units"], "0.002")
        self.assertEqual(status["event_count"], 1)

    def test_changed_funding_does_not_reset_existing_portfolio(self):
        initialize_database(self.path, self.config)
        changed = self.config.data
        changed["portfolio"]["initial_cash_usd"] = "20000.00"
        with self.assertRaisesRegex(StorageError, "different initial funding"):
            initialize_database(self.path, validate_config(changed))
        self.assertEqual(read_status(self.path)["initial_cash_usd"], "10000.00")

    def test_reading_missing_database_does_not_create_it(self):
        with self.assertRaisesRegex(StorageError, "not initialized"):
            read_status(self.path)
        self.assertFalse(self.path.exists())

    def test_unrelated_database_is_not_modified(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("CREATE TABLE unrelated (id INTEGER)")
        with self.assertRaisesRegex(StorageError, "unrelated"):
            initialize_database(self.path, self.config)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        self.assertEqual(tables, ["unrelated"])

    def test_unknown_schema_is_not_modified(self):
        initialize_database(self.path, self.config)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE app_metadata SET value = '999' WHERE key = 'schema_version'")
        with self.assertRaisesRegex(StorageError, "Unsupported"):
            initialize_database(self.path, self.config)


if __name__ == "__main__":
    unittest.main()
