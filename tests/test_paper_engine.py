"""Persistent execution and risk controls exercised against adversarial observations."""

import copy
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from bitcoin_agent.config import ConfigError, load_config, validate_config
from bitcoin_agent.paper_engine import PaperEngine
from bitcoin_agent.paper_store import audit_paper, connection, paper_status
from bitcoin_agent.paper_types import Features, ProductRules, Quote, swing_plan

ROOT = Path(__file__).resolve().parents[1]
START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def quote(now=START, price="100", identity=None):
    price = Decimal(str(price))
    return Quote.from_dict({"quote_id": identity or now.isoformat(), "time": now.isoformat(), "received_at": now.isoformat(),
                            "bid": str(price - Decimal("0.01")), "ask": str(price + Decimal("0.01")), "price": str(price)})


def features(now=START, *, atr="2", bear=False, swing=False, regime="bull"):
    return Features.from_dict({"asof": now.isoformat(), "daily_asof": now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
                               "close": "100", "atr": atr, "ready": True, "swing_signal": swing,
                               "regime": regime, "confirmed_bear": bear})


class PaperTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / "var/test-temp"
        base.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "paper.sqlite3"
        self.settings = load_config(ROOT / "config/paper.json").data
        self.settings["operational"]["allocation_mode"] = "fixed"

    def engine(self, rules=None):
        return PaperEngine(self.path, validate_config(self.settings), rules)

    def large_dca(self):
        self.settings["portfolio"].update(dca_fraction=0.7, swing_fraction=0.2, reserve_fraction=0.1)
        self.settings["dca"].update(tranche_fraction_of_dca_budget=0.98, max_spend_fraction_per_7_days=1.0)
        self.settings["operational"].update(max_btc_exposure_fraction=0.95, exposure_buffer_fraction=0.01,
                                             minimum_cash_fraction=0.05, paper_fill_cap_usd="10000")
        engine = self.engine()
        engine.tick(quote(), features(), START)
        return engine

    def test_decimal_ledger_and_restart_do_not_duplicate_quote(self):
        engine = self.engine()
        result = engine.tick(quote(), features(), START)
        self.assertEqual(result["new_fills"], 1)
        self.assertTrue(audit_paper(self.path)["passed"])
        self.assertEqual(self.engine().tick(quote(), features(), START)["duplicate"], True)
        self.assertEqual(paper_status(self.path)["fills"], 1)

    def test_partial_fill_survives_restart_and_completes_without_extra_cash(self):
        self.settings["operational"]["paper_fill_cap_usd"] = "20"
        engine = self.engine()
        first = engine.tick(quote(), features(), START)
        self.assertEqual(first["portfolio"]["active_orders"][0]["status"], "partial")
        engine = self.engine()
        for index in range(1, 8):
            now = START + timedelta(seconds=5 * index)
            engine.tick(quote(now), features(), now)
        self.assertEqual(paper_status(self.path)["active_orders"], [])
        self.assertTrue(audit_paper(self.path)["passed"])

    def test_buy_remainder_times_out_and_releases_reservation(self):
        self.settings["operational"]["paper_fill_cap_usd"] = "20"
        engine = self.engine()
        engine.tick(quote(), features(), START)
        now = START + timedelta(seconds=301)
        result = engine.tick(quote(now), features(), now)
        self.assertEqual(result["new_fills"], 0)
        self.assertEqual(result["portfolio"]["active_orders"], [])
        self.assertTrue(audit_paper(self.path)["passed"])

    def test_stale_quote_cancels_pending_buys_and_does_not_mark_false_loss(self):
        self.settings["operational"]["paper_fill_cap_usd"] = "20"
        engine = self.engine()
        engine.tick(quote(), features(), START)
        now = START + timedelta(hours=1)
        result = engine.tick(quote(START + timedelta(seconds=1), "1"), features(now), now)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["portfolio"]["active_orders"], [])
        self.assertEqual(result["portfolio"]["last_price"], "100.00")

    def test_future_features_cannot_authorize_buy(self):
        result = self.engine().tick(quote(), features(START + timedelta(hours=1)), START)
        self.assertFalse(result["entries_enabled"])
        self.assertEqual(result["new_fills"], 0)

    def test_market_halt_prevents_paper_fills(self):
        engine = self.engine(ProductRules(market_enabled=False))
        result = engine.tick(quote(), features(), START)
        self.assertEqual(result["new_fills"], 0)
        self.assertFalse(result["entries_enabled"])

    def test_excessive_spread_blocks_entries(self):
        wide = Quote.from_dict({"quote_id": "wide", "time": START.isoformat(), "received_at": START.isoformat(),
                               "bid": "90", "ask": "110", "price": "100"})
        self.assertEqual(self.engine().tick(wide, features(), START)["new_fills"], 0)

    def test_swing_target_is_net_positive_after_modeled_costs(self):
        self.settings["dca"]["enabled"] = False
        self.settings["swing"]["enabled"] = True
        engine = self.engine()
        first = engine.tick(quote(), features(swing=True), START)
        position = next(row for row in first["portfolio"]["positions"] if row["sleeve"] == "swing")
        self.assertGreater(Decimal(position["quantity"]), 0)
        target = Decimal(position["target_price"]) + Decimal("0.02")
        now = START + timedelta(seconds=5)
        engine.tick(quote(now, str(target)), None, now)
        with connection(self.path) as database:
            pnl = sum((Decimal(row[0]) for row in database.execute("SELECT realized_pnl_usd FROM paper_fills WHERE side='sell'")), Decimal(0))
        self.assertGreater(pnl, 0)
        self.assertTrue(audit_paper(self.path)["passed"])

    def test_costly_narrow_atr_signal_is_filtered(self):
        self.settings["dca"]["enabled"] = False
        self.settings["swing"]["enabled"] = True
        result = self.engine().tick(quote(), features(atr="0.01", swing=True), START)
        self.assertEqual(result["new_fills"], 0)

    def test_swing_stop_executes_even_without_feature_refresh(self):
        self.settings["dca"]["enabled"] = False
        self.settings["swing"]["enabled"] = True
        engine = self.engine()
        engine.tick(quote(), features(swing=True), START)
        now = START + timedelta(seconds=5)
        result = engine.tick(quote(now, "95"), None, now)
        self.assertEqual(result["new_fills"], 1)
        self.assertEqual(result["portfolio"]["balances"]["swing"]["btc"], "0E-8")

    def test_flash_crash_liquidates_existing_dca_instead_of_only_pausing(self):
        engine = self.large_dca()
        self.settings["operational"]["paper_fill_cap_usd"] = "1000"
        engine = self.engine()
        for index in range(1, 8):
            now = START + timedelta(seconds=5 * index)
            result = engine.tick(quote(now, "70"), None, now)
        self.assertLess(Decimal(result["portfolio"]["btc_units"]), Decimal("0.000001"))
        self.assertEqual(result["portfolio"]["phase"], "cooldown")
        self.assertEqual(result["portfolio"]["pause_reason"], "rapid_price_decline")
        self.assertTrue(audit_paper(self.path)["passed"])

    def test_capital_floor_halts_and_cannot_automatically_resume(self):
        engine = self.large_dca()
        now = START + timedelta(seconds=5)
        result = engine.tick(quote(now, "10"), None, now)
        self.assertEqual(result["portfolio"]["phase"], "halted")
        for days in range(1, 10):
            now = START + timedelta(days=days)
            result = engine.tick(quote(now, "100"), features(now), now)
        self.assertEqual(result["portfolio"]["phase"], "halted")
        self.assertEqual(result["new_fills"], 0)

    def test_bear_exit_and_recovery_count_distinct_days(self):
        self.settings["operational"].update(recovery_days=2, resume_cooldown_hours=1)
        engine = self.engine()
        engine.tick(quote(), features(), START)
        now = START + timedelta(hours=1)
        engine.tick(quote(now), features(now, bear=True, regime="bear"), now)
        day_two = START + timedelta(days=1)
        result = engine.tick(quote(day_two), features(day_two), day_two)
        self.assertEqual(result["portfolio"]["recovery_days"], 1)
        for index in range(1, 8):
            now = day_two + timedelta(seconds=5 * index)
            result = engine.tick(quote(now), features(day_two), now)
        self.assertEqual(result["portfolio"]["recovery_days"], 1)
        day_three = START + timedelta(days=2)
        result = engine.tick(quote(day_three), features(day_three), day_three)
        self.assertEqual(result["portfolio"]["phase"], "active")
        self.assertEqual(result["portfolio"]["risk_epoch"], 1)

    def test_exposure_trim_can_sell_existing_inventory_after_price_rise(self):
        self.large_dca()
        self.settings["operational"].update(max_btc_exposure_fraction=0.6, exposure_buffer_fraction=0.02)
        engine = self.engine()
        result = engine.tick(quote(START + timedelta(seconds=5), "200"), None, START + timedelta(seconds=5))
        status = result["portfolio"]
        exposure = Decimal(status["btc_units"]) * Decimal(status["last_price"]) / Decimal(status["equity_usd"])
        self.assertLess(exposure, Decimal("0.62"))
        self.assertTrue(audit_paper(self.path)["passed"])

    def test_failed_transaction_leaves_no_phantom_fill(self):
        class CrashingEngine(PaperEngine):
            def _fill_orders(self, *args, **kwargs):
                super()._fill_orders(*args, **kwargs)
                raise RuntimeError("simulated process failure before commit")
        engine = CrashingEngine(self.path, validate_config(self.settings))
        with self.assertRaises(RuntimeError):
            engine.tick(quote(), features(), START)
        self.assertEqual(paper_status(self.path)["fills"], 0)
        self.assertEqual(paper_status(self.path)["cash_usd"], "10000.00")
        self.assertEqual(self.engine().tick(quote(), features(), START)["new_fills"], 1)

    def test_tampered_ledger_blocks_restart(self):
        engine = self.engine()
        engine.tick(quote(), features(), START)
        with connection(self.path, write=True) as database:
            database.execute("UPDATE balances SET cash_usd='0' WHERE bucket='reserve'")
        with self.assertRaises(ArithmeticError):
            self.engine()

    def test_configuration_rejects_inconsistent_cash_and_exposure_limits(self):
        self.settings["operational"]["minimum_cash_fraction"] = 0.5
        with self.assertRaises(ConfigError):
            validate_config(self.settings)

    def test_poll_observations_have_distinct_ids_but_exact_replays_do_not(self):
        raw = {"time": START.isoformat(), "received_at": START.isoformat(), "trade_id": 1,
               "bid": "99.99", "ask": "100.01", "price": "100"}
        first = Quote.from_dict(raw)
        self.assertEqual(first.quote_id, Quote.from_dict(raw).quote_id)
        raw["received_at"] = (START + timedelta(seconds=5)).isoformat()
        self.assertNotEqual(first.quote_id, Quote.from_dict(raw).quote_id)

    def test_cold_start_gap_from_recent_candle_blocks_first_purchase(self):
        result = self.engine().tick(quote(START, "70"), features(), START)
        self.assertEqual(result["new_fills"], 0)
        self.assertEqual(result["portfolio"]["pause_reason"], "rapid_price_decline")

    def test_capital_floor_remains_after_recovery_resets_risk_epoch(self):
        self.settings["operational"].update(recovery_days=1, resume_cooldown_hours=1)
        engine = self.large_dca()
        engine.tick(quote(START + timedelta(seconds=5), "70"), None, START + timedelta(seconds=5))
        # Let the first buy leave the rolling seven-day cap before buying again.
        now = START + timedelta(days=8)
        recovered = engine.tick(quote(now), features(now), now)
        self.assertEqual(recovered["portfolio"]["risk_epoch"], 1)
        now += timedelta(seconds=5)
        engine.tick(quote(now), features(now), now)
        now += timedelta(seconds=5)
        halted = engine.tick(quote(now, "70"), None, now)
        self.assertEqual(halted["portfolio"]["phase"], "halted")
        self.assertEqual(halted["portfolio"]["pause_reason"], "lifetime_capital_floor")
        self.assertGreater(Decimal(halted["portfolio"]["max_lifetime_drawdown"]), Decimal("0.3"))

    def test_malformed_market_payloads_are_rejected(self):
        with self.assertRaises(ValueError):
            Quote.from_dict({"price": "100"})
        with self.assertRaises(ValueError):
            ProductRules.from_dict({"id": "BTC-USD"})

    def test_owner_liquidation_stays_paused_until_explicit_resume(self):
        engine = self.engine()
        engine.tick(quote(), features(), START)
        engine.request_liquidation("owner review", START + timedelta(seconds=1))
        now = START + timedelta(seconds=5)
        engine.tick(quote(now), None, now)
        for day in range(1, 9):
            now = START + timedelta(days=day)
            result = engine.tick(quote(now), features(now), now)
        self.assertEqual(result["portfolio"]["phase"], "cooldown")
        resumed = engine.manual_resume("review completed", now)
        self.assertEqual(resumed["phase"], "active")
        self.assertEqual(resumed["risk_epoch"], 1)
        self.assertTrue(audit_paper(self.path)["passed"])

    def test_manual_resume_cannot_override_capital_floor_halt(self):
        engine = self.large_dca()
        now = START + timedelta(seconds=5)
        engine.tick(quote(now, "10"), None, now)
        with self.assertRaises(ValueError):
            engine.manual_resume("attempted override", now)

    def test_restart_preserves_last_observed_product_minimum(self):
        engine = self.engine(ProductRules(minimum_notional=Decimal("1")))
        engine.tick(quote(), features(), START)
        self.assertEqual(self.engine().rules.minimum_notional, Decimal("1"))


if __name__ == "__main__":
    unittest.main()
