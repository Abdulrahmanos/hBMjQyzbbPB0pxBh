"""Transactional paper orders, fills, inventory, and independent reconciliation."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .config import AgentConfig
from .paper_types import ZERO, decimal
from .storage import StorageError, initialize_database


@contextmanager
def connection(path, write=False):
    target = Path(path).resolve()
    if not write and not target.is_file():
        raise StorageError("Paper database is not initialized")
    database = sqlite3.connect(target if write else target.as_uri() + "?mode=ro", uri=not write, timeout=15)
    database.row_factory = sqlite3.Row
    database.execute("PRAGMA foreign_keys = ON")
    try:
        if write:
            with database:
                database.execute("BEGIN IMMEDIATE")
                yield database
        else:
            yield database
    finally:
        database.close()


def initialize_paper(path, config: AgentConfig):
    if "operational" not in config.data:
        raise StorageError("Operational paper commands require config/paper.json")
    initialize_database(path, config)
    with connection(path, write=True) as database:
        existing = database.execute("SELECT value FROM app_metadata WHERE key = 'paper_schema_version'").fetchone()
        if existing:
            if existing[0] != "1":
                raise StorageError("Unsupported operational paper schema")
            return
        balances = database.execute("SELECT cash_usd, btc_units FROM balances").fetchall()
        if any(decimal(row["btc_units"]) != 0 for row in balances) or sum((decimal(row["cash_usd"]) for row in balances), ZERO) != config.initial_cash:
            raise StorageError("Cannot attach an operational ledger to unexplained legacy balances")
        database.execute("CREATE TABLE paper_state (id INTEGER PRIMARY KEY CHECK(id=1), state_json TEXT NOT NULL)")
        database.execute("CREATE TABLE paper_orders (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, side TEXT NOT NULL CHECK(side IN ('buy','sell')), sleeve TEXT NOT NULL CHECK(sleeve IN ('dca','swing')), quantity TEXT NOT NULL, filled_quantity TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','partial','filled','canceled')), reason TEXT NOT NULL, reserved_usd TEXT NOT NULL, atr TEXT, risk_budget_usd TEXT, last_error TEXT)")
        database.execute("CREATE TABLE paper_fills (id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES paper_orders(id), quote_id TEXT NOT NULL, occurred_at TEXT NOT NULL, side TEXT NOT NULL, sleeve TEXT NOT NULL, quantity TEXT NOT NULL, price TEXT NOT NULL, notional_usd TEXT NOT NULL, fee_usd TEXT NOT NULL, slippage_usd TEXT NOT NULL, rounding_usd TEXT NOT NULL, realized_pnl_usd TEXT NOT NULL, UNIQUE(order_id,quote_id))")
        database.execute("CREATE TABLE paper_positions (sleeve TEXT PRIMARY KEY, quantity TEXT NOT NULL, cost_basis_usd TEXT NOT NULL, average_entry TEXT NOT NULL, opened_at TEXT, stop_price TEXT, target_price TEXT)")
        database.execute("CREATE TABLE paper_quotes (id TEXT PRIMARY KEY, observed_at TEXT NOT NULL, accepted INTEGER NOT NULL, quote_json TEXT NOT NULL)")
        database.execute("CREATE TABLE paper_marks (quote_id TEXT PRIMARY KEY REFERENCES paper_quotes(id), observed_at TEXT NOT NULL, price TEXT NOT NULL, equity_usd TEXT NOT NULL, cash_usd TEXT NOT NULL, btc_units TEXT NOT NULL, exposure TEXT NOT NULL, lifetime_drawdown TEXT NOT NULL, phase TEXT NOT NULL)")
        database.execute("CREATE INDEX paper_marks_time ON paper_marks(observed_at)")
        database.execute("CREATE INDEX paper_fills_time ON paper_fills(occurred_at)")
        database.execute("CREATE INDEX paper_orders_status ON paper_orders(status)")
        for sleeve in ("dca", "swing"):
            database.execute("INSERT INTO paper_positions VALUES (?, '0', '0', '0', NULL, NULL, NULL)", (sleeve,))
        state = {"phase": "active", "pause_reason": None, "pause_started_at": None, "liquidation_required": False,
                 "epoch_peak": str(config.initial_cash), "lifetime_peak": str(config.initial_cash), "max_lifetime_drawdown": "0",
                 "risk_epoch": 0, "recovery_days": 0, "recovery_last_day": None,
                 "last_tick_at": None, "last_price": None, "last_signal_asof": None,
                 "last_dca_buy_at": None, "last_dca_price": None, "next_dca_due_at": None,
                 "allocation_updated_at": None, "targets": None, "dca_budget_reference": "0", "config_checksum": config.checksum}
        database.execute("INSERT INTO paper_state VALUES (1, ?)", (json.dumps(state),))
        database.execute("INSERT INTO app_metadata VALUES ('paper_schema_version', '1')")
    # WAL is selected outside the initialization transaction.
    database = sqlite3.connect(path)
    try:
        database.execute("PRAGMA journal_mode=WAL")
    finally:
        database.close()


def state_from(database):
    row = database.execute("SELECT state_json FROM paper_state WHERE id=1").fetchone()
    return json.loads(row[0])


def save_state(database, state):
    database.execute("UPDATE paper_state SET state_json=? WHERE id=1", (json.dumps(state, sort_keys=True),))


def balances_from(database):
    return {row["bucket"]: {"cash": decimal(row["cash_usd"]), "btc": decimal(row["btc_units"])}
            for row in database.execute("SELECT * FROM balances")}


def save_balances(database, balances):
    for sleeve, values in balances.items():
        if min(values.values()) < 0:
            raise ArithmeticError("Negative paper balance")
        database.execute("UPDATE balances SET cash_usd=?, btc_units=? WHERE bucket=?", (str(values["cash"]), str(values["btc"]), sleeve))


def active_orders(database, side=None, sleeve=None):
    query, parameters = "SELECT * FROM paper_orders WHERE status IN ('pending','partial')", []
    if side:
        query += " AND side=?"
        parameters.append(side)
    if sleeve:
        query += " AND sleeve=?"
        parameters.append(sleeve)
    return [dict(row) for row in database.execute(query + " ORDER BY created_at,id", parameters)]


def event(database, now, kind, payload):
    database.execute("INSERT INTO system_events (occurred_at,event_type,payload_json) VALUES (?,?,?)",
                     (now.isoformat(), kind, json.dumps(payload, sort_keys=True)))


def status_from(database):
    state = state_from(database)
    balances = balances_from(database)
    price = decimal(state["last_price"]) if state["last_price"] is not None else ZERO
    total_cash = sum((value["cash"] for value in balances.values()), ZERO)
    total_btc = sum((value["btc"] for value in balances.values()), ZERO)
    return {"execution_mode": "paper", "phase": state["phase"], "pause_reason": state["pause_reason"],
            "last_quote_at": state["last_tick_at"], "last_price": state["last_price"],
            "cash_usd": str(total_cash), "btc_units": str(total_btc), "equity_usd": str(total_cash + total_btc * price),
            "max_lifetime_drawdown": state["max_lifetime_drawdown"], "risk_epoch": state["risk_epoch"],
            "recovery_days": state["recovery_days"], "targets": state["targets"],
            "balances": {key: {field: str(value) for field, value in item.items()} for key, item in balances.items()},
            "positions": [dict(row) for row in database.execute("SELECT * FROM paper_positions")],
            "active_orders": active_orders(database), "fills": database.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0]}


def paper_status(path):
    with connection(path) as database:
        return status_from(database)


def audit_paper(path):
    """Reconstruct cash and BTC from the immutable fill ledger using Decimal."""
    with connection(path) as database:
        initial = decimal(database.execute("SELECT value FROM app_metadata WHERE key='initial_cash_usd'").fetchone()[0])
        expected_cash = initial
        inventory = {"dca": ZERO, "swing": ZERO, "reserve": ZERO}
        fees, rounding = ZERO, ZERO
        basis = {"dca": ZERO, "swing": ZERO}
        fill_totals = {}
        for row in database.execute("SELECT * FROM paper_fills ORDER BY occurred_at,id"):
            quantity, notional, fee = (decimal(row[field]) for field in ("quantity", "notional_usd", "fee_usd"))
            if min(quantity, notional) <= 0 or fee < 0:
                raise ArithmeticError("Invalid ledger fill")
            delta = -(notional + fee) if row["side"] == "buy" else notional - fee
            expected_cash += delta
            inventory[row["sleeve"]] += quantity if row["side"] == "buy" else -quantity
            basis[row["sleeve"]] += notional + fee if row["side"] == "buy" else -(notional - fee - decimal(row["realized_pnl_usd"]))
            fees += fee
            rounding += decimal(row["rounding_usd"])
            fill_totals[row["order_id"]] = fill_totals.get(row["order_id"], ZERO) + quantity
        balances = balances_from(database)
        if any(min(value.values()) < 0 for value in balances.values()):
            raise ArithmeticError("Negative paper balance")
        actual_cash = sum((value["cash"] for value in balances.values()), ZERO)
        if actual_cash != expected_cash or any(inventory[key] != balances[key]["btc"] for key in inventory):
            raise ArithmeticError("Paper ledger does not reconcile")
        for position in database.execute("SELECT * FROM paper_positions"):
            if decimal(position["quantity"]) != inventory[position["sleeve"]]:
                raise ArithmeticError("Position inventory does not reconcile")
            if decimal(position["cost_basis_usd"]) != basis[position["sleeve"]] or basis[position["sleeve"]] < 0:
                raise ArithmeticError("Position cost basis does not reconcile")
        for order in database.execute("SELECT * FROM paper_orders"):
            quantity, filled = decimal(order["quantity"]), decimal(order["filled_quantity"])
            if filled != fill_totals.get(order["id"], ZERO) or filled > quantity:
                raise ArithmeticError("Order fill totals do not reconcile")
            if order["status"] == "filled" and filled != quantity:
                raise ArithmeticError("A filled order has an unfilled remainder")
        for sleeve in ("dca", "swing"):
            buys = active_orders(database, "buy", sleeve)
            sells = active_orders(database, "sell", sleeve)
            if sum((decimal(order["reserved_usd"]) for order in buys), ZERO) > balances[sleeve]["cash"]:
                raise ArithmeticError("Buy reservations exceed cash")
            if sum((decimal(order["quantity"]) - decimal(order["filled_quantity"]) for order in sells), ZERO) > balances[sleeve]["btc"]:
                raise ArithmeticError("Sell reservations exceed inventory")
        return {"passed": True, "initial_cash_usd": str(initial), "reconstructed_cash_usd": str(expected_cash),
                "fees_usd": str(fees), "rounding_cost_usd": str(rounding), "cash_difference_usd": str(actual_cash - expected_cash),
                "inventory": {key: str(value) for key, value in inventory.items()},
                "fills": database.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0]}
